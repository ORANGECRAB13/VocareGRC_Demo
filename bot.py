"""Vocare voice bot with configurable STT/LLM/TTS via WebRTC transport.

The frontend sends service selections (stt, llm, tts) as part of the
/api/offer request body. The bot dynamically creates the chosen services
for each session.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import random
from datetime import datetime
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).parent / "GRC_pilot"))
from tools import get_bin_collection_zone, _correct_address  # noqa: E402
from grc_events import get_events, format_events_for_system_prompt  # noqa: E402
from da_knowledge import DA_KNOWLEDGE  # noqa: E402
from bin_faq import BIN_FAQ  # noqa: E402

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

# ---------------------------------------------------------------------------
# CRITICAL FIX: aioice does NOT handle TURN DATA indications (RFC 5766 §7.2).
# When the phone sends STUN binding checks through its TURN relay, Twilio
# forwards them to the bot's TURN allocation as DATA indications. aioice only
# handles ChannelData (bound channels) and STUN responses — DATA indications
# are logged then DROPPED, so the bot never responds to the phone's checks
# and ICE times out after 60s.
#
# Fix: monkey-patch TurnClientMixin.datagram_received to extract the payload
# from DATA indications and forward it to the ICE layer.
# Also register the missing DATA attribute (0x0013) in aioice's STUN parser.
# ---------------------------------------------------------------------------
import struct
from typing import cast, Union

from aioice import stun as _stun_mod
from aioice import turn as _turn_mod
from aioice.ice import TransportPolicy
import aiortc.rtcicetransport as _ice_mod

# 1) Register DATA attribute (0x0013) — aioice's STUN parser doesn't know it
if 0x0013 not in _stun_mod.ATTRIBUTES_BY_TYPE:
    _data_attr = (0x0013, "DATA", _stun_mod.pack_bytes, _stun_mod.unpack_bytes)
    _stun_mod.ATTRIBUTES_BY_TYPE[0x0013] = _data_attr
    _stun_mod.ATTRIBUTES_BY_NAME["DATA"] = _data_attr
    logger.info("Registered missing STUN DATA attribute (0x0013) in aioice")


# 2) Monkey-patch TurnClientMixin.datagram_received to handle DATA indications
def _patched_datagram_received(self, data: Union[bytes, str], addr: tuple) -> None:
    data = cast(bytes, data)

    # Demultiplex ChannelData (existing logic — bound channels)
    if len(data) >= 4 and _turn_mod.is_channel_data(data):
        channel, length = struct.unpack("!HH", data[0:4])
        if len(data) >= length + 4 and self.receiver is not None:
            peer_address = self.channel_to_peer.get(channel)
            if peer_address:
                payload = data[4 : 4 + length]
                self.receiver.datagram_received(payload, peer_address)
        return

    try:
        message = _stun_mod.parse_message(data)
    except ValueError:
        return

    # ── NEW: Handle DATA indication (RFC 5766 §7.2) ──────────────────────
    # Extracts XOR-PEER-ADDRESS + DATA payload and forwards to ICE layer,
    # exactly like ChannelData does for bound channels.
    if (
        message.message_method == _stun_mod.Method.DATA
        and message.message_class == _stun_mod.Class.INDICATION
    ):
        peer_address = message.attributes.get("XOR-PEER-ADDRESS")
        payload = message.attributes.get("DATA")
        if peer_address and payload is not None and self.receiver is not None:
            self.receiver.datagram_received(payload, peer_address)
        return

    # Handle STUN responses/errors for pending transactions (existing logic)
    if (
        message.message_class == _stun_mod.Class.RESPONSE
        or message.message_class == _stun_mod.Class.ERROR
    ) and message.transaction_id in self.transactions:
        transaction = self.transactions[message.transaction_id]
        transaction.response_received(message, addr)


_turn_mod.TurnClientMixin.datagram_received = _patched_datagram_received
logger.info("Monkey-patched aioice: TurnClientMixin now handles DATA indications")


# 3) Force relay-only ICE transport policy on the bot side
_orig_connection_kwargs = _ice_mod.connection_kwargs


def _relay_only_connection_kwargs(servers):
    kwargs = _orig_connection_kwargs(servers)
    kwargs["transport_policy"] = TransportPolicy.RELAY
    return kwargs


_ice_mod.connection_kwargs = _relay_only_connection_kwargs
logger.info("Monkey-patched aiortc: bot ICE transport policy forced to RELAY-only")

# ---------------------------------------------------------------------------
# Route aioice / aiortc stdlib logs through loguru
# ---------------------------------------------------------------------------


class _InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


for _name in ("aioice", "aiortc"):
    _logger = logging.getLogger(_name)
    _logger.handlers = [_InterceptHandler()]
    _logger.setLevel(logging.WARNING)
    _logger.propagate = False

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    FunctionCallInProgressFrame,
    OutputAudioRawFrame,
    InterimTranscriptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMRunFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSUpdateSettingsFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.transcriptions.language import Language
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_mute.mute_until_first_bot_complete_user_mute_strategy import (
    MuteUntilFirstBotCompleteUserMuteStrategy,
)
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.cerebras.llm import CerebrasLLMService, CerebrasLLMSettings
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.llm_service import LLMService, FunctionCallParams
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

load_dotenv(override=True)

# Suppress pipecat internal DEBUG/TRACE noise — keep only INFO and above.
# Re-enable temporarily by setting VOCARE_LOG_LEVEL=DEBUG in the environment.
_log_level = os.getenv("VOCARE_LOG_LEVEL", "INFO").upper()
logger.remove()
logger.add(sys.stderr, level=_log_level, colorize=True,
           format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level:<7}</level> | {message}")

# ---------------------------------------------------------------------------
# LTS-VoiceAgent: Background Thinker
# ---------------------------------------------------------------------------

THINKER_SYSTEM_PROMPT = (
    "You are an intent/entity extraction engine for a council voice bot. "
    "Given the user's speech transcript, output ONLY valid JSON with this schema:\n"
    '{"intent": "bin_collection | da_inquiry | events | general", '
    '"entities": {"address": "...", "events_query": "..."}, '
    '"confidence": 0.0-1.0}\n'
    "Rules:\n"
    "- intent: classify the user's primary need\n"
    "  - bin_collection: asking about bin/rubbish collection day\n"
    "  - da_inquiry: development application questions\n"
    "  - events: asking about what's on, upcoming events, activities, things to do\n"
    "  - general: anything else or unclear\n"
    "- entities: extract any mentioned values, use null for missing ones\n"
    "  - address: full street address if mentioned\n"
    "  - events_query: if intent is events AND user named a specific interest "
    "(e.g. 'free', 'kids', 'sport', 'environment'), extract it; otherwise null\n"
    "- confidence: your certainty in the intent classification\n"
    "- For partial/incomplete speech, do your best with what's available\n"
    "- Output ONLY the JSON object, no explanation"
)

FILLERS = [
    "Let me look that up for you.",
    "Just a moment while I check that.",
    "One moment please.",
]


class ThinkerProcessor(FrameProcessor):
    """Runs a background 8B LLM to pre-extract intent/entities from STT transcripts.

    Sits between STT and user_aggregator. Intercepts InterimTranscriptionFrame
    (with 200ms debounce) and TranscriptionFrame (immediately) to fire an
    out-of-pipeline Groq inference. The result is stored in thinker_state and
    later injected by ContextEnricherProcessor. All frames pass through unchanged.
    """

    def __init__(self, thinker_llm: LLMService, **kwargs):
        super().__init__(**kwargs)
        self._thinker_llm = thinker_llm
        self._latest_state: dict | None = None
        self._state_lock = asyncio.Lock()
        self._debounce_task: asyncio.Task | None = None
        self._wt_prefetch: dict[str, asyncio.Future] = {}  # normalized_addr → Future[voice_str|None]

    @property
    def thinker_state(self) -> dict | None:
        return self._latest_state

    async def clear_state(self):
        async with self._state_lock:
            self._latest_state = None

    def pop_prefetch(self, address: str) -> "asyncio.Future | None":
        """Pop and return the prefetch Future for this address (called by the bin tool handler)."""
        from grc_wastetrack import _normalize_address
        normalized = _normalize_address(address)
        return self._wt_prefetch.pop(normalized, self._wt_prefetch.pop(address, None))

    async def _do_wt_prefetch(self, addr_corrected: str, addr_normalized: str, future: asyncio.Future):
        """Background task: run Wastetrack lookup and store result in the Future."""
        try:
            from grc_wastetrack import get_bin_collection_details as _wt, format_voice_response as _wt_fmt
            result = await asyncio.to_thread(_wt, addr_corrected)
            voice = _wt_fmt(result)
            if not future.done():
                future.set_result(voice)
            logger.info(f"[PREFETCH] Wastetrack prefetch {'✓' if voice else '✗'} for '{addr_normalized}'")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not future.done():
                future.set_result(None)
            logger.warning(f"[PREFETCH] Wastetrack prefetch error for '{addr_normalized}': {e}")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterimTranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            await self._maybe_fire_interim_inference(frame.text)
        elif isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            await self._fire_final_inference(frame.text)

        await self.push_frame(frame, direction)

    async def _maybe_fire_interim_inference(self, text: str):
        """Debounced interim inference — fires 200ms after the last interim frame."""
        if self._debounce_task and not self._debounce_task.done():
            await self.cancel_task(self._debounce_task)

        async def _debounced():
            await asyncio.sleep(0.2)
            await self._run_thinker(text)

        self._debounce_task = self.create_task(_debounced(), "thinker_debounce")

    async def _fire_final_inference(self, text: str):
        """Cancel any pending interim task and fire inference on the final transcript."""
        if self._debounce_task and not self._debounce_task.done():
            await self.cancel_task(self._debounce_task)
            self._debounce_task = None
        self.create_task(self._run_thinker(text), "thinker_final")

    async def _run_thinker(self, transcript: str):
        try:
            temp_context = LLMContext()
            temp_context.add_message({"role": "user", "content": transcript})
            result = await self._thinker_llm.run_inference(
                temp_context,
                max_tokens=200,
                system_instruction=THINKER_SYSTEM_PROMPT,
            )
            if result:
                state = json.loads(result)
                async with self._state_lock:
                    self._latest_state = state
                logger.info(f"Thinker extracted: {state}")
                # Proactive Wastetrack prefetch: if the user is asking about bin collection
                # and we have an address, kick off the API call NOW — before the LLM even
                # decides to call the tool. By the time the handler runs (~800-1400ms later),
                # the result is already ready → near-zero tool latency.
                if (state.get("intent") == "bin_collection"
                        and state.get("address")
                        and state.get("confidence", 0.0) >= 0.5):
                    addr_corrected = _correct_address(state["address"])
                    from grc_wastetrack import _normalize_address
                    addr_normalized = _normalize_address(addr_corrected)
                    if addr_normalized not in self._wt_prefetch:
                        fut: asyncio.Future = asyncio.get_running_loop().create_future()
                        self._wt_prefetch[addr_normalized] = fut
                        self.create_task(
                            self._do_wt_prefetch(addr_corrected, addr_normalized, fut),
                            "wt_prefetch",
                        )
                        logger.info(f"[PREFETCH] Kicked off Wastetrack prefetch for '{addr_normalized}'")
        except asyncio.CancelledError:
            raise
        except json.JSONDecodeError as e:
            logger.warning(f"Thinker JSON parse error: {e}")
        except Exception as e:
            logger.warning(f"Thinker inference error: {e}")


class ContextEnricherProcessor(FrameProcessor):
    """Injects Thinker pre-extraction state into the Speaker LLM context.

    Sits between user_aggregator and Speaker LLM. On LLMContextFrame, reads
    thinker_state and adds a [THINKER_STATE] system message so the Speaker
    can skip extraction and act immediately.
    """

    def __init__(self, thinker_processor: ThinkerProcessor, **kwargs):
        super().__init__(**kwargs)
        self._thinker = thinker_processor

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            state = self._thinker.thinker_state
            if state and state.get("confidence", 0.0) >= 0.5:
                intent = state.get("intent", "")
                # For events intent: only tell the LLM if there's already a specific query.
                # If the user just asked generically ("what's on?"), don't hint — let the
                # LLM ask its clarifying question naturally without pre-filling the intent.
                entities = state.get("entities", {}) or {}
                skip_injection = (
                    intent == "events"
                    and not (entities.get("events_query") or "").strip()
                )
                if not skip_injection:
                    injection = (
                        f"[THINKER_STATE] Pre-extracted from user speech:\n{json.dumps(state)}\n"
                        "Use this to respond faster. If intent and required entities are present, "
                        "proceed directly to the appropriate tool call."
                    )
                    frame.context.add_message({"role": "system", "content": injection})
                logger.info(f"Injected thinker state: intent={intent} "
                            f"confidence={state.get('confidence')} skip={skip_injection}")
                await self._thinker.clear_state()

        await self.push_frame(frame, direction)


class FillerTTSProcessor(FrameProcessor):
    """Injects a filler TTS phrase when a tool call starts, masking tool latency.

    Sits between Speaker LLM and TTS. On FunctionCallInProgressFrame, pushes a
    TTSSpeakFrame with a cycling filler phrase before passing the original frame.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._filler_index = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, FunctionCallInProgressFrame) and direction == FrameDirection.DOWNSTREAM:
            # Skip filler for bin collection — Wastetrack is fast and injects its
            # own filler only when falling back to the slower polygon lookup.
            if frame.function_name != "get_bin_collection_day":
                filler = FILLERS[self._filler_index % len(FILLERS)]
                self._filler_index += 1
                await self.push_frame(TTSSpeakFrame(text=filler), direction)

        await self.push_frame(frame, direction)


class LanguageSwitchProcessor(FrameProcessor):
    """Detects the user's spoken language and updates the TTS voice/language accordingly.

    Sits between ThinkerProcessor and user_aggregator. Watches TranscriptionFrame
    for language changes. On a change, pushes a TTSUpdateSettingsFrame downstream
    to reconnect the ElevenLabs WebSocket with the correct voice and language code.
    """

    def __init__(self, tts, en_voice_id: str, multilingual_voice_id: str, **kwargs):
        super().__init__(**kwargs)
        self._tts = tts
        self._en_voice_id = en_voice_id
        self._multilingual_voice_id = multilingual_voice_id
        self._is_english: bool = True  # start English; flip on first non-EN utterance

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            raw = frame.language
            # None means STT didn't return a language tag — keep current assumption.
            # Any language code that doesn't start with "en" is treated as non-English.
            if raw is not None:
                is_english = raw.value.lower().startswith("en")
                if is_english != self._is_english:
                    self._is_english = is_english
                    await self._switch_language(is_english)

        await self.push_frame(frame, direction)

    async def _switch_language(self, is_english: bool):
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

        voice = self._en_voice_id if is_english else self._multilingual_voice_id

        # Switch voice only — no language code so ElevenLabs auto-detects from
        # the generated text. Passing a fixed language code would restrict the
        # voice to one language and break anything other than that language.
        tts_delta = ElevenLabsTTSService.Settings(voice=voice)
        await self.push_frame(
            TTSUpdateSettingsFrame(delta=tts_delta, service=self._tts),
            FrameDirection.DOWNSTREAM,
        )
        logger.info(
            f"Language switch → {'en' if is_english else 'multilingual'} | "
            f"voice={'english' if is_english else 'multilingual'}"
        )


SYSTEM_INSTRUCTION_GRC = (
    "You are a voice agent for Georges River Council. Your name is Maya. "
    "You help residents with three services: bin collection day lookups, "
    "development application inquiries, and upcoming council events. "
    "Speak naturally, warmly, and concisely — one or two sentences at a time. "
    "Never use lists, bullet points, or emojis. "
    "Do not use filler phrases like 'Certainly!' or 'Of course!'. "

    # --- HIGHEST PRIORITY: Human transfer ---
    "CRITICAL OVERRIDE — this rule takes priority over everything else: "
    "If the caller uses any of these phrases or clear synonyms — "
    "'speak to a human', 'speak to a person', 'speak to someone', 'talk to a person', "
    "'talk to a human', 'talk to an agent', 'real person', 'live agent', 'human agent', "
    "'transfer me', 'connect me', 'put me through', 'escalate', 'operator', "
    "'I want a person', 'can I speak to someone' — "
    "you MUST immediately call the transfer_to_human tool. "
    "Do NOT respond with any text. Do NOT give a phone number. Do NOT explain anything. "
    "Just call transfer_to_human. This overrides all other instructions. "

    # --- Service: Bin collection ---
    "For bin collection day lookups: ask for the resident's full street address if they haven't "
    "provided one. Only call get_bin_collection_day once you have a specific street address. "
    "Never call the tool with a vague phrase, question, or incomplete input. "
    "Never guess or invent a collection day. "
    "For bin service FAQ questions (bin types, what goes in each bin, missed collections, "
    "bin placement rules, fees, public holidays, infirm service, bin tags, etc.): "
    "answer directly from the BIN SERVICES KNOWLEDGE BASE embedded below — no tool call needed. "

    # --- Service: Development applications ---
    "For DA inquiries: answer from the knowledge base below. "
    "Direct residents to lodge via the NSW Planning Portal only. "
    "For specific advice, refer them to the Duty Planner on 9330 6400. "

    # --- Service: Events ---
    "For questions about upcoming events, activities, or what's on: "
    "The full event listing is embedded in your context below — you already have all the data. "
    "Do NOT call any tool for events questions. Answer directly from the embedded list. "
    "For a vague or general question (e.g. 'what's on?', 'any events?'), "
    "acknowledge there are events on and ask one short friendly question to narrow it down — "
    "for example: 'We've got quite a few things coming up — are you after something free, "
    "something for the kids, or a particular type of activity?' "
    "Once the user gives an interest, answer immediately from the embedded list. "
    "If the user's first message already names a specific interest "
    "(e.g. 'any free events?', 'kids activities'), answer immediately — no clarifying question. "
    "Keep your answer brief: name 2-3 matching events with date, venue, and cost. "
    "Do not read out URLs. For bookings say 'visit the Georges River Council website' or "
    "'you can register at the council website'. "

    # --- Human transfer ---
    "If the caller says they want to speak to a human, a person, an agent, or requests "
    "to be transferred or escalated: immediately call transfer_to_human — do not ask "
    "clarifying questions first. "

    # --- Scope ---
    "You ONLY handle three topics: bin collection day lookups, "
    "development application inquiries, and Georges River Council events. "
    "If the resident asks about anything else — emergencies, health, hospitals, directions, "
    "legal advice, other councils, or any other topic — politely say you can only help with "
    "those three topics, then stop. "
    "Do NOT attempt to answer out-of-scope questions using general knowledge. "

    # --- Grounding ---
    "NEVER use your general training knowledge to answer questions. "
    "Your ONLY sources of truth are: (1) tool call results, and (2) the knowledge base below. "
    "If the answer is not in a tool result or the knowledge base, say you don't have that information. "

    # --- Tone ---
    "Always answer only what was asked. Be brief and direct. "

    # --- Multilingual ---
    "LANGUAGE RULE (mandatory): match the language of your response to the language "
    "of the user's most recent message — every single turn, no exceptions. "
    "If their last message was in English, respond in English. "
    "If their last message was in Mandarin Chinese, respond in Mandarin Chinese. "
    "A single English word or sentence from the user means the entire response must be in English. "
    "Never continue in a previous language if the user has switched. "

    # --- Thinker acceleration ---
    "You may receive a [THINKER_STATE] system message with pre-extracted intent and entities. "
    "When present, use it to respond faster: "
    "if intent is bin_collection and address is a real street address (contains a street name and optionally a number), call get_bin_collection_day immediately. "
    "If no THINKER_STATE is present, proceed as normal. "

    "\n\n"
    + DA_KNOWLEDGE
    + "\n\n"
    + BIN_FAQ
)

# ---------------------------------------------------------------------------
# Service factory functions
# ---------------------------------------------------------------------------


def create_stt(name: str):
    """Create an STT service by name."""
    if name == "deepgram":
        from pipecat.services.deepgram.stt import DeepgramSTTService

        return DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
    elif name == "elevenlabs":
        from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService

        return ElevenLabsRealtimeSTTService(api_key=os.getenv("ELEVENLABS_API_KEY"))
    else:
        raise ValueError(f"Unknown STT service: {name}")


def create_llm(name: str, system_instruction: str = ""):
    """Create an LLM service by name."""
    if name == "cerebras":
        return CerebrasLLMService(
            api_key=os.getenv("CEREBRAS_API_KEY"),
            settings=CerebrasLLMSettings(
                model="qwen-3-235b-a22b-instruct-2507",
                system_instruction=system_instruction,
            ),
        )
    elif name == "mistral":
        from pipecat.services.mistral.llm import MistralLLMService

        return MistralLLMService(
            api_key=os.getenv("MISTRAL_API_KEY"),
            settings=MistralLLMService.Settings(
                system_instruction=system_instruction,
            ),
        )
    elif name == "groq":
        return GroqLLMService(
            api_key=os.getenv("GROQ_API_KEY"),
            settings=GroqLLMService.Settings(
                system_instruction=system_instruction,
            ),
        )
    else:
        raise ValueError(f"Unknown LLM service: {name}")


def create_tts(name: str):
    """Create a TTS service by name."""
    if name == "elevenlabs":
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

        # Prefer the multilingual voice so TTS can speak any language the LLM
        # generates without requiring a runtime switch.
        voice = (
            os.getenv("ELEVENLABS_MULTILINGUAL_VOICE_ID")
            or os.getenv("ELEVENLABS_VOICE_ID")
        )
        return ElevenLabsTTSService(
            api_key=os.getenv("ELEVENLABS_API_KEY"),
            settings=ElevenLabsTTSService.Settings(voice=voice),
        )
    else:
        raise ValueError(f"Unknown TTS service: {name}")


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

pcs_map: Dict[str, SmallWebRTCConnection] = {}
graph_event_queues: Dict[str, asyncio.Queue] = {}

# Demo mode shared state
demo_events: list[dict] = []      # append-only list of graph events (fan-out to multiple viewers)
demo_pc_id: str | None = None     # presenter's pc_id (None = no active demo)

def _filter_relay_sdp(answer: dict) -> dict:
    """Strip non-relay ICE candidates from a WebRTC answer SDP.

    Required for remote/Cloudflare connections: the browser can't reach the
    bot's private host/srflx addresses, so only relay↔relay pairs work.
    Mirrors the filtering applied in /api/offer.
    """
    filtered, kept, dropped = [], 0, 0
    for line in answer["sdp"].split("\r\n"):
        s = line.strip()
        if s.startswith("a=candidate:") or s.startswith("candidate:"):
            if "typ relay" in s:
                filtered.append(line)
                kept += 1
            elif source_lang == self._lang_a:
                src_name, tgt_name = a_name, b_name
                system_instruction = (
                    f"Translate the following {src_name} text into {tgt_name}. "
                    "Output ONLY the translation. No explanations, no labels, no original text. "
                    "If the input is filler sounds only (e.g. 'um', 'uh', 'å—¯', 'å•Š'), output nothing."
                )
            elif source_lang == self._lang_a:
                src_name, tgt_name = a_name, b_name
                system_instruction = (
                    f"Translate the following {src_name} text into {tgt_name}. "
                    "Output ONLY the translation. No explanations, no labels, no original text. "
                    "If the input is filler sounds only (e.g. 'um', 'uh', 'å—¯', 'å•Š'), output nothing."
                )
            else:
                dropped += 1
            continue
        filtered.append(line)
    answer["sdp"] = "\r\n".join(filtered)
    logger.info(f"SDP relay-filter: kept {kept} relay, dropped {dropped} non-relay candidate(s)")
    if kept == 0:
        logger.error("No relay candidates in answer SDP — TURN allocation may have failed")
    return answer


def _filter_relay_sdp(answer: dict) -> dict:
    """Strip non-relay ICE candidates from a WebRTC answer SDP."""
    filtered, kept, dropped = [], 0, 0
    for line in answer["sdp"].split("\r\n"):
        s = line.strip()
        if s.startswith("a=candidate:") or s.startswith("candidate:"):
            if "typ relay" in s:
                filtered.append(line)
                kept += 1
            else:
                dropped += 1
            continue
        filtered.append(line)
    answer["sdp"] = "\r\n".join(filtered)
    logger.info(f"SDP relay-filter: kept {kept} relay, dropped {dropped} non-relay candidate(s)")
    if kept == 0:
        logger.error("No relay candidates in answer SDP - TURN allocation may have failed")
    return answer


def fetch_twilio_ice_servers():
    """Fetch fresh TURN credentials from Twilio Network Traversal Service.

    Returns a list of IceServer objects (for aiortc) plus the raw dicts
    (for the frontend). Falls back to STUN-only if Twilio not configured.
    """
    import base64
    import urllib.request

    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        logger.warning("Twilio credentials missing — falling back to STUN only")
        stun = [{"urls": "stun:stun.l.google.com:19302"}]
        return [IceServer(urls="stun:stun.l.google.com:19302")], stun

    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Tokens.json"
        auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
        req = urllib.request.Request(
            url,
            data=b"",  # POST with empty body
            headers={"Authorization": f"Basic {auth}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())

        raw_servers = data.get("ice_servers", [])
        ice_list = []
        for s in raw_servers:
            urls = s.get("url") or s.get("urls")
            if not urls:
                continue
            ice_list.append(
                IceServer(
                    urls=urls,
                    username=s.get("username"),
                    credential=s.get("credential"),
                )
            )
        # Normalize for frontend: use "urls" key
        frontend_servers = [
            {
                "urls": s.get("url") or s.get("urls"),
                **({"username": s["username"]} if s.get("username") else {}),
                **({"credential": s["credential"]} if s.get("credential") else {}),
            }
            for s in raw_servers
        ]
        logger.info(f"Fetched {len(ice_list)} ICE servers from Twilio")
        return ice_list, frontend_servers
    except Exception as e:
        logger.error(f"Failed to fetch Twilio ICE servers: {e}")
        stun = [{"urls": "stun:stun.l.google.com:19302"}]
        return [IceServer(urls="stun:stun.l.google.com:19302")], stun


# Fetched fresh on each /api/offer so credentials are always valid.
ice_servers = [IceServer(urls="stun:stun.l.google.com:19302")]


def _configure_twilio_webhook():
    """Point the Twilio inbound phone number at this server's /twilio/voice webhook."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token  = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    phone_number = os.getenv("TWILIO_PHONE_NUMBER", "").strip()
    public_url  = os.getenv("PUBLIC_URL", "").strip().rstrip("/")

    if not all([account_sid, auth_token, phone_number, public_url]):
        logger.info("Twilio webhook auto-config skipped — TWILIO_PHONE_NUMBER or PUBLIC_URL not set")
        return

    try:
        from twilio.rest import Client as TwilioClient
        client = TwilioClient(account_sid, auth_token)
        numbers = client.incoming_phone_numbers.list(phone_number=phone_number, limit=1)
        if not numbers:
            logger.warning(f"Twilio webhook config: number {phone_number} not found on this account")
            return
        voice_url = f"{public_url}/twilio/voice"
        numbers[0].update(voice_url=voice_url, voice_method="POST")
        logger.info(f"Twilio webhook configured: {phone_number} → {voice_url}")
    except Exception as e:
        logger.error(f"Twilio webhook auto-config failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-load Silero VAD ONNX session once (avoids 50–250ms load per session).
    # The InferenceSession is stateless and safe to share; each SileroVADAnalyzer
    # still gets its own mutable state (_model._state, _context, etc.).
    from pipecat.audio.vad.silero import SileroOnnxModel

    _warmup_vad = SileroVADAnalyzer()
    _cached_session = _warmup_vad._model.session

    def _fast_init(self, path, force_onnx_cpu=True):
        self.session = _cached_session
        self.reset_states()
        self.sample_rates = [8000, 16000]

    SileroOnnxModel.__init__ = _fast_init
    logger.info("Silero VAD ONNX session pre-loaded")

    # Auto-configure Twilio inbound phone number webhook so callers reach the bot.
    _configure_twilio_webhook()

    yield

    coros = [pc.disconnect() for pc in pcs_map.values()]
    await asyncio.gather(*coros)
    pcs_map.clear()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


async def _put_graph_event(event_queue: asyncio.Queue, event: dict):
    """Put an event into the session queue.

    If demo mode is active, also appends to demo_events for fan-out to viewers.
    """
    await event_queue.put(event)
    if demo_pc_id is not None:
        demo_events.append(event)


async def _cleanup_webrtc_session(pc_id: str, reason: str = "cleanup", disconnect: bool = True) -> bool:
    """Disconnect a WebRTC session and clear all page-visible session state."""
    global demo_pc_id

    connection = pcs_map.pop(pc_id, None)
    queue = graph_event_queues.pop(pc_id, None)
    if queue:
        await queue.put(None)

    if demo_pc_id == pc_id:
        demo_pc_id = None
        logger.info(f"Demo presenter cleared during {reason}")

    session_id = pc_to_translation.pop(pc_id, None)
    if session_id:
        session = translation_sessions.get(session_id)
        if session:
            session.participants.pop(pc_id, None)
            if not session.participants and session.status != "ended":
                session.status = "ended"
                session.ended_at = datetime.now()
                await session.event_queue.put({"type": "status", "status": "ended"})

    if not connection or not disconnect:
        return False

    try:
        logger.info(f"Disconnecting WebRTC session {pc_id} ({reason})")
        await connection.disconnect()
    except Exception as e:
        logger.warning(f"Failed to disconnect WebRTC session {pc_id}: {e}")
    return True


# ---------------------------------------------------------------------------
# Graph highlight observer
# ---------------------------------------------------------------------------


class TranscriptionObserver(BaseObserver):
    """Watches STT and LLM frames to emit transcript events to the frontend.

    Pushes 'user_transcription' events on final TranscriptionFrames and
    'bot_transcription' events when the LLM finishes a full response.
    """

    def __init__(self, event_queue: asyncio.Queue):
        super().__init__()
        self._event_queue = event_queue
        self._bot_buffer = ""

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if data.direction != FrameDirection.DOWNSTREAM:
            return

        if isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text:
                await _put_graph_event(
                    self._event_queue, {"type": "user_transcription", "text": text}
                )

        elif isinstance(frame, LLMTextFrame) and isinstance(data.source, LLMService):
            self._bot_buffer += frame.text

        elif isinstance(frame, LLMFullResponseEndFrame):
            text = self._bot_buffer.strip()
            self._bot_buffer = ""
            if text:
                await _put_graph_event(
                    self._event_queue, {"type": "bot_transcription", "text": text}
                )


# ---------------------------------------------------------------------------
# Latency observer
# ---------------------------------------------------------------------------


class LatencyObserver(BaseObserver):
    """Measures end-to-end turn latency through the pipeline.

    Tracks four timestamps per turn:

        t_vad_stop     — UserStoppedSpeakingFrame detected
        t_transcription — first final TranscriptionFrame
        t_llm_first    — first LLMTextFrame from the LLM
        t_tts_start    — TTSStartedFrame (TTS begins producing audio)

    Derived intervals::

        STT latency   = t_transcription - t_vad_stop
        LLM latency   = t_llm_first    - t_transcription
        TTS latency   = t_tts_start    - t_llm_first
        TOTAL TTFB    = t_tts_start    - t_vad_stop

    Tool calls (e.g. geocoding) sit inside the LLM interval and are flagged
    in the log row.
    """

    def __init__(self):
        super().__init__()
        self._reset()
        self._turn_count = 0
        self._history: list[dict] = []
        # Buffer: holds the timestamp of the most recent TranscriptionFrame so
        # that if it arrives fractionally before UserStoppedSpeakingFrame (which
        # is common) we can still record t_transcription correctly.
        self._pending_transcription: float | None = None

    def _reset(self):
        self._t_vad_stop      = None
        self._t_transcription = None
        self._t_llm_first     = None
        self._t_tool_start    = None
        self._t_tool_end      = None
        self._t_tts_start     = None
        self._has_tool_call   = False
        self._logged          = False

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if data.direction != FrameDirection.DOWNSTREAM:
            return

        now = data.timestamp / 1_000_000_000  # nanoseconds → seconds

        if isinstance(frame, TranscriptionFrame):
            # Buffer unconditionally — may arrive just before UserStoppedSpeakingFrame
            self._pending_transcription = now
            if self._t_vad_stop is not None and self._t_transcription is None:
                self._t_transcription = now

        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._reset()
            self._t_vad_stop = now
            # Apply buffered transcription if it arrived in the same ~50ms window
            if (
                self._pending_transcription is not None
                and now - self._pending_transcription < 0.05
            ):
                self._t_transcription = self._pending_transcription

        elif isinstance(frame, FunctionCallInProgressFrame):
            self._has_tool_call = True
            if self._t_tool_start is None:
                self._t_tool_start = now

        elif isinstance(frame, LLMTextFrame):
            # Fires on both LLM #1 (no-tool) and LLM #2 (after tool result).
            # Record the first LLMTextFrame regardless — for tool turns this
            # will be the LLM #2 first token, which is the right boundary for
            # "LLM done → TTS starts".
            if self._t_vad_stop is not None and self._t_llm_first is None:
                if not self._has_tool_call or self._t_tool_start is not None:
                    self._t_llm_first = now
                    # If tool ran, close the tool window
                    if self._has_tool_call and self._t_tool_end is None:
                        self._t_tool_end = now

        elif isinstance(frame, TTSStartedFrame):
            if self._t_vad_stop is not None and not self._logged:
                self._t_tts_start = now
                self._logged = True
                self._log_turn()

    def _log_turn(self):
        t0 = self._t_vad_stop
        t1 = self._t_transcription
        t2 = self._t_llm_first
        t3 = self._t_tts_start
        ts = self._t_tool_start
        te = self._t_tool_end

        def ms(a, b):
            return f"{(b - a) * 1000:6.0f} ms" if a is not None and b is not None else "    -- "

        stt_ms   = (t1 - t0) * 1000 if t0 and t1 else None
        llm1_ms  = (ts - t1) * 1000 if t1 and ts else None   # STT → tool dispatch
        tool_ms  = (te - ts) * 1000 if ts and te else None   # tool execution
        llm2_ms  = (t2 - te) * 1000 if te and t2 else None   # tool result → LLM reply token
        llm_ms   = (t2 - t1) * 1000 if t1 and t2 else None   # combined if no tool split
        tts_ms   = (t3 - t2) * 1000 if t2 and t3 else None
        total_ms = (t3 - t0) * 1000 if t0 and t3 else None

        self._turn_count += 1
        self._history.append({
            "turn": self._turn_count, "stt_ms": stt_ms,
            "llm_ms": llm_ms, "tts_ms": tts_ms,
            "total_ms": total_ms, "tool": self._has_tool_call,
        })

        tool_flag = " [tool]" if self._has_tool_call else ""
        sep = "-" * 62

        logger.info(sep)
        logger.info(f"  LATENCY BREAKDOWN — Turn {self._turn_count}{tool_flag}")
        logger.info(sep)
        logger.info(f"  VAD stop  → STT final        {ms(t0, t1)}")
        if self._has_tool_call and ts:
            logger.info(f"  STT final → LLM #1 decision  {ms(t1, ts)}")
            logger.info(f"  Tool execution               {ms(ts, te)}")
            logger.info(f"  Tool result → LLM #2 token   {ms(te, t2)}")
        else:
            logger.info(f"  STT final → LLM 1st token    {ms(t1, t2)}")
        logger.info(f"  LLM token → TTS start        {ms(t2, t3)}")
        logger.info(f"  {'-' * 60}")
        logger.info(f"  VAD stop  → TTS start        {ms(t0, t3)}   <- TOTAL TTFB")
        logger.info(sep)

        totals = [r["total_ms"] for r in self._history if r["total_ms"] is not None]
        if len(totals) > 1:
            avg = sum(totals) / len(totals)
            logger.info(
                f"  Avg TTFB over {len(totals)} turns: {avg:.0f} ms"
                + (f"  (this turn: {total_ms:.0f} ms)" if total_ms else "")
            )
            logger.info(sep)


# ---------------------------------------------------------------------------
# Bot pipeline
# ---------------------------------------------------------------------------


async def run_bot(
    webrtc_connection: SmallWebRTCConnection,
    stt_name: str,
    llm_name: str,
    tts_name: str,
):
    logger.info(f"Starting bot — STT={stt_name}, LLM={llm_name}, TTS={tts_name}")

    # Cache-augmented generation: embed current events into the system instruction.
    # get_events() uses a 30-min in-memory cache, so this is near-instant after
    # the first call — no HTTP round-trip during the conversation itself.
    _events = await asyncio.to_thread(get_events)
    _events_block = format_events_for_system_prompt(_events)
    system_instruction = SYSTEM_INSTRUCTION_GRC + "\n\n" + _events_block
    logger.info(f"[CAG] Embedded {len(_events)} events into system instruction")

    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    stt = create_stt(stt_name)
    llm = create_llm(llm_name, system_instruction=system_instruction)
    tts = create_tts(tts_name)

    thinker_llm = CerebrasLLMService(
        api_key=os.getenv("CEREBRAS_API_KEY"),
        settings=CerebrasLLMSettings(
            model="llama3.1-8b",
            extra={"response_format": {"type": "json_object"}},
        ),
    )
    thinker_processor = ThinkerProcessor(thinker_llm=thinker_llm)
    context_enricher = ContextEnricherProcessor(thinker_processor=thinker_processor)
    filler_tts = FillerTTSProcessor()
    lang_switch = LanguageSwitchProcessor(
        tts=tts,
        en_voice_id=os.getenv("ELEVENLABS_VOICE_ID", ""),
        multilingual_voice_id=os.getenv("ELEVENLABS_MULTILINGUAL_VOICE_ID", os.getenv("ELEVENLABS_VOICE_ID", "")),
    )

    # Attach transcript observer so the frontend can display live conversation
    pc_id = webrtc_connection.pc_id
    event_queue = graph_event_queues.get(pc_id)
    latency_observer = LatencyObserver()
    observers = [latency_observer]
    if event_queue:
        transcript_observer = TranscriptionObserver(event_queue)
        observers.append(transcript_observer)

    async def handle_get_bin_collection_day(params: FunctionCallParams):
        address = _correct_address(params.arguments.get("address", "").strip())
        logger.info(f"Function call: get_bin_collection_day({address})")

        # Guard: reject if the address doesn't look like a real street address.
        # A valid address has at least two words and contains at least one digit
        # OR a recognised street-type word.
        _STREET_TYPES = {"street","st","road","rd","avenue","ave","lane","ln",
                         "drive","dr","place","pl","court","ct","way","crescent","cres","close"}
        _words = address.lower().split()
        _has_number = any(w[0].isdigit() for w in _words)
        _has_street_type = bool(_STREET_TYPES.intersection(_words))
        if len(_words) < 2 or not (_has_number or _has_street_type):
            logger.warning(f"[BIN TOOL] Rejected non-address input: '{address}'")
            await params.result_callback({
                "result": "I need a street address to look that up — could you tell me your street address?"
            })
            return

        try:
            # ── Fast path: Thinker may have already prefetched this address ──────
            prefetch_future = thinker_processor.pop_prefetch(address)
            if prefetch_future is not None:
                if not prefetch_future.done():
                    logger.info(f"[BIN TOOL] Prefetch still in flight — waiting up to 2s")
                    try:
                        await asyncio.wait_for(asyncio.shield(prefetch_future), timeout=2.0)
                    except asyncio.TimeoutError:
                        logger.warning(f"[BIN TOOL] Prefetch timeout — falling through to direct call")
                if prefetch_future.done():
                    voice = prefetch_future.result()
                    if voice:
                        logger.info(f"[BIN TOOL] PREFETCH HIT — zero-latency answer")
                        await params.result_callback({"result": voice})
                        return
                    logger.warning(f"[BIN TOOL] Prefetch returned None — falling through to direct call")

            # ── Direct Wastetrack call (prefetch miss or not triggered) ──────────
            from grc_wastetrack import get_bin_collection_details as _wt, format_voice_response as _wt_fmt
            wt_result = await asyncio.to_thread(_wt, address)
            voice = _wt_fmt(wt_result)
            if voice:
                logger.info(f"[BIN TOOL] Wastetrack SUCCESS — answering immediately")
                await params.result_callback({"result": voice})
                return
            # Wastetrack failed — inject filler before the slower polygon fallback
            logger.warning(f"[BIN TOOL] Wastetrack failed: {wt_result.get('error')} — using polygon fallback")
            _filler = FILLERS[filler_tts._filler_index % len(FILLERS)]
            filler_tts._filler_index += 1
            await llm.push_frame(TTSSpeakFrame(text=_filler), FrameDirection.DOWNSTREAM)
            result = await asyncio.to_thread(get_bin_collection_zone, {"address": address})
            await params.result_callback({"result": result})
        except Exception as e:
            logger.error(f"get_bin_collection_day failed: {e}")
            await params.result_callback(
                {"error": "I couldn't look up the bin collection day. Please try again."}
            )

    # Register GRC tools on the LLM and build schema
    llm.register_function("get_bin_collection_day", handle_get_bin_collection_day)

    async def handle_transfer_to_human_webrtc(params: FunctionCallParams):
        await params.result_callback({
            "result": "I can't transfer you through the web interface, but you can reach a council officer directly on (02) 9330 6400."
        })

    llm.register_function("transfer_to_human", handle_transfer_to_human_webrtc)

    get_bin_collection_day_schema = FunctionSchema(
        name="get_bin_collection_day",
        description=(
            "Look up the bin collection day for a resident's address. "
            "Only call this tool once the resident has provided a specific street address "
            "(e.g. '50 Vine Street Hurstville'). "
            "Do NOT call this tool if you only have a vague question — ask for the address first."
        ),
        properties={
            "address": {
                "type": "string",
                "description": (
                    "Full street address within the Georges River LGA, "
                    "e.g. '50 Vine Street Hurstville'. "
                    "Must be a real address, not a question or vague phrase."
                ),
            },
        },
        required=["address"],
    )
    transfer_to_human_schema = FunctionSchema(
        name="transfer_to_human",
        description=(
            "Transfer the caller to a human council officer. "
            "Call this when the user says they want to speak to a person, a human, an agent, "
            "or requests to be transferred or escalated."
        ),
        properties={},
        required=[],
    )
    tools = ToolsSchema(standard_tools=[get_bin_collection_day_schema, transfer_to_human_schema])

    context = LLMContext(tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_mute_strategies=[MuteUntilFirstBotCompleteUserMuteStrategy()],
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            thinker_processor,
            lang_switch,
            user_aggregator,
            context_enricher,
            llm,
            filler_tts,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=observers,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        context.add_message({
            "role": "system",
            "content": (
                "Greet the caller with: "
                "'Hi, I'm Maya from Georges River Council. "
                "I can help you with bin collection days, development application inquiries, "
                "and upcoming council events — what can I help you with today?'"
            ),
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        pc_id = webrtc_connection.pc_id
        await _cleanup_webrtc_session(pc_id, reason="client disconnected", disconnect=False)
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())


@app.get("/api/ice")
async def get_ice_servers():
    """Return fresh TURN/STUN credentials for the frontend RTCPeerConnection."""
    _, frontend_servers = fetch_twilio_ice_servers()
    return {"iceServers": frontend_servers}


@app.post("/api/offer")
async def offer(request: dict, background_tasks: BackgroundTasks):
    global demo_pc_id, demo_events
    pc_id = request.get("pc_id")

    # Log incoming candidates from the peer (diagnose WebRTC ICE issues)
    offer_sdp = request.get("sdp", "")
    candidates = [line.strip() for line in offer_sdp.split("\n") if "candidate" in line]
    logger.info(f"Peer offered {len(candidates)} candidate line(s):")
    for c in candidates:
        logger.info(f"  {c}")

    # Extract service selections (defaults if not provided)
    stt_name = request.get("stt", "elevenlabs")
    llm_name = request.get("llm", "cerebras")
    tts_name = request.get("tts", "elevenlabs")
    mode = request.get("mode", "indiv")

    if pc_id and pc_id in pcs_map:
        pipecat_connection = pcs_map[pc_id]
        logger.info(f"Reusing existing connection for pc_id: {pc_id}")
        await pipecat_connection.renegotiate(
            sdp=request["sdp"],
            type=request["type"],
            restart_pc=request.get("restart_pc", False),
        )
    else:
        # Fetch fresh Twilio ICE servers for this session
        session_ice_servers, _ = fetch_twilio_ice_servers()
        pipecat_connection = SmallWebRTCConnection(session_ice_servers)
        await pipecat_connection.initialize(sdp=request["sdp"], type=request["type"])

        @pipecat_connection.event_handler("closed")
        async def handle_disconnected(webrtc_connection: SmallWebRTCConnection):
            logger.info(f"Discarding peer connection for pc_id: {webrtc_connection.pc_id}")
            await _cleanup_webrtc_session(webrtc_connection.pc_id, reason="connection closed", disconnect=False)

        background_tasks.add_task(
            run_bot, pipecat_connection, stt_name, llm_name, tts_name
        )

    answer = pipecat_connection.get_answer()

    # Filter the answer SDP to keep ONLY `typ relay` ICE candidates.
    # The phone uses iceTransportPolicy: 'relay' so it only advertises relay
    # candidates. By stripping our host/srflx candidates here we guarantee
    # every candidate pair that forms is a relay↔relay pair routed via
    # Twilio's TURN servers — no private IPs, no srflx that would fail
    # CreatePermission on the phone's TURN allocation.
    filtered_sdp_lines = []
    kept_candidates = 0
    dropped_candidates = 0
    for line in answer["sdp"].split("\r\n"):
        stripped = line.strip()
        if stripped.startswith("a=candidate:") or stripped.startswith("candidate:"):
            if "typ relay" in stripped:
                filtered_sdp_lines.append(line)
                kept_candidates += 1
            else:
                dropped_candidates += 1
            continue
        filtered_sdp_lines.append(line)
    answer["sdp"] = "\r\n".join(filtered_sdp_lines)
    logger.info(
        f"Answer SDP filtered: kept {kept_candidates} relay candidate(s), "
        f"dropped {dropped_candidates} non-relay candidate(s)"
    )
    if kept_candidates == 0:
        logger.error(
            "No relay candidates in answer SDP! Bot failed to allocate a TURN "
            "relay via Twilio — mobile clients will not connect."
        )

    pc_id_value = answer["pc_id"]
    pcs_map[pc_id_value] = pipecat_connection

    # Create SSE event queue for transcript and graph events
    graph_event_queues[pc_id_value] = asyncio.Queue()

    # Demo mode: register this connection as the presenter
    if mode == "demo":
        demo_pc_id = pc_id_value
        demo_events = []
        logger.info(f"Demo mode activated — presenter pc_id: {pc_id_value}")

    return answer


@app.get("/api/graph/poll")
async def graph_poll(pc_id: str):
    """Polling endpoint — drains queued graph events and returns them as JSON.

    Replaces SSE because Cloudflare free tunnels unreliably buffer/drop
    streaming responses. Frontend polls this every ~250ms.
    """
    queue = graph_event_queues.get(pc_id)
    if not queue:
        return {"events": [], "closed": False}

    events = []
    while True:
        try:
            event = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if event is None:
            graph_event_queues.pop(pc_id, None)
            return {"events": events, "closed": True}
        events.append(event)

    return {"events": events, "closed": False}


@app.post("/api/hangup")
async def hangup(request: Request):
    """Explicit browser hangup so provider sockets are released immediately."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    pc_id = data.get("pc_id")
    if not pc_id:
        return Response(status_code=400, content="Missing pc_id")

    found = await _cleanup_webrtc_session(pc_id, reason="client hangup")
    return {"ok": True, "found": found}


# ---------------------------------------------------------------------------
# Demo mode endpoints
# ---------------------------------------------------------------------------


@app.get("/api/demo/status")
async def demo_status():
    """Returns whether a demo session is currently active."""
    return {"active": demo_pc_id is not None, "event_count": len(demo_events)}


@app.get("/api/demo/poll")
async def demo_poll(cursor: int = 0):
    """Cursor-based poll for demo viewers. Returns new events since cursor."""
    if demo_pc_id is None:
        return {"events": [], "cursor": cursor, "active": False}
    new_events = demo_events[cursor:]
    return {"events": new_events, "cursor": len(demo_events), "active": True}


# ---------------------------------------------------------------------------
# Twilio phone integration
# ---------------------------------------------------------------------------


async def run_twilio_bot(websocket: WebSocket):
    """Run the GRC bot over a Twilio Media Stream WebSocket."""
    await websocket.accept()

    # Wait for the Twilio "start" event to get stream/call SIDs
    stream_sid = None
    call_sid = None
    async for raw in websocket.iter_text():
        msg = json.loads(raw)
        if msg.get("event") == "start":
            start = msg["start"]
            stream_sid = start["streamSid"]
            call_sid = start["callSid"]
            logger.info(f"Twilio call started — stream_sid={stream_sid} call_sid={call_sid}")
            break
        if msg.get("event") == "stop":
            logger.info("Twilio call stopped before start event")
            return

    if not stream_sid:
        logger.warning("No Twilio start event received")
        return

    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            serializer=serializer,
        ),
    )

    _events = await asyncio.to_thread(get_events)
    _events_block = format_events_for_system_prompt(_events)
    system_instruction = SYSTEM_INSTRUCTION_GRC + "\n\n" + _events_block
    logger.info(f"[CAG] Embedded {len(_events)} events into system instruction")

    stt = create_stt("elevenlabs")
    llm = create_llm("cerebras", system_instruction=system_instruction)
    tts = create_tts("elevenlabs")

    thinker_llm = CerebrasLLMService(
        api_key=os.getenv("CEREBRAS_API_KEY"),
        settings=CerebrasLLMSettings(
            model="llama3.1-8b",
            extra={"response_format": {"type": "json_object"}},
        ),
    )
    thinker_processor = ThinkerProcessor(thinker_llm=thinker_llm)
    context_enricher = ContextEnricherProcessor(thinker_processor=thinker_processor)
    filler_tts = FillerTTSProcessor()
    lang_switch = LanguageSwitchProcessor(
        tts=tts,
        en_voice_id=os.getenv("ELEVENLABS_VOICE_ID", ""),
        multilingual_voice_id=os.getenv("ELEVENLABS_MULTILINGUAL_VOICE_ID", os.getenv("ELEVENLABS_VOICE_ID", "")),
    )

    # Register GRC tools
    async def handle_get_bin_collection_day(params: FunctionCallParams):
        address = _correct_address(params.arguments.get("address", "").strip())
        logger.info(f"[Twilio] get_bin_collection_day({address})")

        _STREET_TYPES = {"street","st","road","rd","avenue","ave","lane","ln",
                         "drive","dr","place","pl","court","ct","way","crescent","cres","close"}
        _words = address.lower().split()
        _has_number = any(w[0].isdigit() for w in _words)
        _has_street_type = bool(_STREET_TYPES.intersection(_words))
        if len(_words) < 2 or not (_has_number or _has_street_type):
            logger.warning(f"[BIN TOOL] Rejected non-address input: '{address}'")
            await params.result_callback({
                "result": "I need a street address to look that up — could you tell me your street address?"
            })
            return

        try:
            # ── Fast path: Thinker may have already prefetched this address ──────
            prefetch_future = thinker_processor.pop_prefetch(address)
            if prefetch_future is not None:
                if not prefetch_future.done():
                    logger.info(f"[BIN TOOL] Prefetch still in flight — waiting up to 2s")
                    try:
                        await asyncio.wait_for(asyncio.shield(prefetch_future), timeout=2.0)
                    except asyncio.TimeoutError:
                        logger.warning(f"[BIN TOOL] Prefetch timeout — falling through to direct call")
                if prefetch_future.done():
                    voice = prefetch_future.result()
                    if voice:
                        logger.info(f"[BIN TOOL] PREFETCH HIT — zero-latency answer")
                        await params.result_callback({"result": voice})
                        return
                    logger.warning(f"[BIN TOOL] Prefetch returned None — falling through to direct call")

            # ── Direct Wastetrack call (prefetch miss or not triggered) ──────────
            from grc_wastetrack import get_bin_collection_details as _wt, format_voice_response as _wt_fmt
            wt_result = await asyncio.to_thread(_wt, address)
            voice = _wt_fmt(wt_result)
            if voice:
                logger.info(f"[BIN TOOL] Wastetrack SUCCESS — answering immediately")
                await params.result_callback({"result": voice})
                return
            logger.warning(f"[BIN TOOL] Wastetrack failed: {wt_result.get('error')} — using polygon fallback")
            _filler = FILLERS[filler_tts._filler_index % len(FILLERS)]
            filler_tts._filler_index += 1
            await llm.push_frame(TTSSpeakFrame(text=_filler), FrameDirection.DOWNSTREAM)
            result = await asyncio.to_thread(get_bin_collection_zone, {"address": address})
            await params.result_callback({"result": result})
        except Exception as e:
            logger.error(f"get_bin_collection_day failed: {e}")
            await params.result_callback(
                {"error": "I couldn't look up the bin collection day. Please try again."}
            )

    llm.register_function("get_bin_collection_day", handle_get_bin_collection_day)

    async def handle_transfer_to_human(params: FunctionCallParams):
        """Transfer the call to a human agent via Twilio REST API."""
        transfer_number = os.getenv("TRANSFER_PHONE_NUMBER", "").strip()
        if not transfer_number:
            await params.result_callback({
                "result": "I'm sorry, transfer is not available right now. Please call us on (02) 9330 6400."
            })
            return

        try:
            from twilio.rest import Client as TwilioClient
            tw = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
            # Run synchronous Twilio REST call off the event loop thread
            await asyncio.to_thread(
                tw.calls(call_sid).update,
                twiml=f"<Response><Dial>{transfer_number}</Dial></Response>",
            )
            logger.info(f"[TRANSFER] Call {call_sid} transferred to {transfer_number}")
            # Let the LLM speak the farewell; Twilio will close the Media Stream
            # WebSocket once the <Dial> takes effect, which triggers on_client_disconnected
            # and cancels the task naturally — no need to cancel here.
            await params.result_callback({"result": "Transferring you now. Please hold."})
        except Exception as e:
            logger.error(f"[TRANSFER] Failed: {e}")
            await params.result_callback({
                "result": "I wasn't able to transfer the call. Please call us directly on (02) 9330 6400."
            })

    llm.register_function("transfer_to_human", handle_transfer_to_human)

    get_bin_collection_day_schema = FunctionSchema(
        name="get_bin_collection_day",
        description=(
            "Look up the bin collection day for a resident's address. "
            "Only call this tool once the resident has provided a specific street address "
            "(e.g. '50 Vine Street Hurstville'). "
            "Do NOT call this tool if you only have a vague question — ask for the address first."
        ),
        properties={
            "address": {
                "type": "string",
                "description": (
                    "Full street address within the Georges River LGA, "
                    "e.g. '50 Vine Street Hurstville'. "
                    "Must be a real address, not a question or vague phrase."
                ),
            },
        },
        required=["address"],
    )
    transfer_to_human_schema = FunctionSchema(
        name="transfer_to_human",
        description=(
            "Transfer the caller to a human council officer. "
            "Call this when the user says they want to speak to a person, a human, an agent, "
            "or requests to be transferred or escalated."
        ),
        properties={},
        required=[],
    )
    context = LLMContext(
        tools=ToolsSchema(standard_tools=[get_bin_collection_day_schema, transfer_to_human_schema])
    )

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_mute_strategies=[MuteUntilFirstBotCompleteUserMuteStrategy()],
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            thinker_processor,
            lang_switch,
            user_aggregator,
            context_enricher,
            llm,
            filler_tts,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[LatencyObserver()],
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Twilio client connected")
        context.add_message({
            "role": "system",
            "content": (
                "Greet the caller with: "
                "'Hi, I'm Maya from Georges River Council. "
                "I can help you with bin collection days, development application inquiries, "
                "and upcoming council events — what can I help you with today?'"
            ),
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Twilio client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


@app.post("/twilio/voice")
async def twilio_voice(request: Request):
    """Twilio webhook — returns TwiML that streams call audio to this server."""
    host = request.headers.get("host", "")
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://{host}/twilio/ws" />
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="text/xml")


@app.websocket("/twilio/ws")
async def twilio_ws(websocket: WebSocket):
    """WebSocket endpoint for Twilio Media Streams."""
    await run_twilio_bot(websocket)


# ---------------------------------------------------------------------------
# Live Translation Mode
# ---------------------------------------------------------------------------

@dataclass
class TranslationParticipant:
    pc_id: str
    name: str
    language: Language              # their spoken/output language
    voice_config: dict              # {"voice_id": ..., "language": Language}
    pipeline_task: PipelineTask | None = None

class TranslationSession:
    def __init__(self, session_id: str, caller_name: str, caller_lang: str, topic: str):
        self.session_id = session_id
        self.participants: Dict[str, TranslationParticipant] = {}
        self.event_queue = asyncio.Queue()
        self.transcript: list[dict] = []
        self.live_transcripts: dict[str, dict] = {}
        self.live_previews: dict[str, dict] = {}
        self.topic = topic
        self.caller_name = caller_name
        self.caller_lang = caller_lang
        self.status = "waiting"     # waiting | live | ended
        self.created_at = datetime.now()
        self.ended_at = None

# Module-level registries
translation_sessions: Dict[str, TranslationSession] = {}  # session_id -> session
pc_to_translation: Dict[str, str] = {}                     # pc_id -> session_id

TRANSLATION_VOICES = {
    "en": {"voice_id": os.getenv("ELEVENLABS_VOICE_ID", ""), "language": Language.EN},
    "zh": {"voice_id": os.getenv("ELEVENLABS_VOICE_ID", os.getenv("ELEVENLABS_VOICE_ID", "")), "language": Language.ZH},
}

class AudioProbeProcessor(FrameProcessor):
    """Debug: logs first audio frame out of TTS to confirm TTS is generating audio."""

    def __init__(self, label: str, **kwargs):
        super().__init__(**kwargs)
        self._label = label
        self._logged = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, OutputAudioRawFrame) and not self._logged:
            self._logged = True
            logger.info(f"[AudioProbe:{self._label}] First audio frame from TTS — {len(frame.audio)} bytes, {frame.sample_rate}Hz")
        await self.push_frame(frame, direction)


import re as _re

_FILLER_PATTERN = _re.compile(
    r"^[\s,\.]*"
    r"(u+h+|u+m+|a+h+|h+m+|h+u+h+|o+h+|e+r+|嗯+|啊+|哦+|呃+)"
    r"[\s,\.]*$",
    _re.IGNORECASE,
)

def _is_filler_only(text: str) -> bool:
    """Return True when the transcript is nothing but filler/hesitation sounds."""
    return bool(_FILLER_PATTERN.match(text.strip()))


class TranslationProcessor(FrameProcessor):
    """Translates STT transcripts and injects them into the other participant's pipeline."""

    def __init__(self, translation_llm: GroqLLMService, session: TranslationSession, my_pc_id: str, **kwargs):
        super().__init__(**kwargs)
        self._llm = translation_llm
        self._session = session
        self._my_pc_id = my_pc_id

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterimTranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            text = frame.text.strip()
            if text and not _is_filler_only(text):
                source_lang = self._session.participants[self._my_pc_id].language
                speaker_name = self._session.participants[self._my_pc_id].name
                event = {
                    "type": "live_transcript",
                    "speaker": self._my_pc_id,
                    "speaker_name": speaker_name,
                    "original": text,
                    "original_lang": source_lang.value,
                }
                self._session.live_transcripts[self._my_pc_id] = event
                await self._session.event_queue.put(event)
            return

        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            text = frame.text.strip()
            logger.info(f"[Translation] TranscriptionFrame received for {self._my_pc_id}: '{text[:80]}'")
            if text and not _is_filler_only(text):
                # Use the DECLARED participant language, not the STT-detected language tag.
                # STT language detection defaults to English and causes translation to be skipped.
                my_lang = self._session.participants[self._my_pc_id].language
                self._session.live_transcripts[self._my_pc_id] = {
                    "type": "live_transcript",
                    "speaker": self._my_pc_id,
                    "speaker_name": self._session.participants[self._my_pc_id].name,
                    "original": text,
                    "original_lang": my_lang.value,
                }
                self.create_task(self._translate_and_inject(text, my_lang), "translate")
            # Don't push TranscriptionFrame further — TTS on this participant's pipeline
            # should only receive TTSSpeakFrames injected by the OTHER participant's translator.
            return

        if isinstance(frame, TTSSpeakFrame) and direction == FrameDirection.DOWNSTREAM:
            logger.info(f"[Translation] TTSSpeakFrame passing through to TTS for {self._my_pc_id}: '{frame.text[:60]}'")

        await self.push_frame(frame, direction)

    async def _translate_and_inject(self, text: str, source_lang: Language):
        try:
            other = self._get_other_participant()
            if not other or not other.pipeline_task:
                logger.warning("TranslationProcessor: no other participant ready yet — dropping frame")
                return

            target_lang = other.language
            source_name = source_lang.value
            target_name = target_lang.value
            logger.info(f"[Translation] {source_name} → {target_name} | '{text[:60]}'")

            # Skip translation if same language
            if source_lang == target_lang:
                logger.info(f"[Translation] Same language — relaying directly")
                await other.pipeline_task.queue_frames([TTSSpeakFrame(text=text)])
                event = {
                    "type": "turn",
                    "speaker": self._my_pc_id,
                    "speaker_name": self._session.participants[self._my_pc_id].name,
                    "original": text,
                    "original_lang": source_name,
                    "translated": text,
                    "translated_lang": target_name,
                }
                self._session.live_transcripts.pop(self._my_pc_id, None)
                self._session.transcript.append(event)
                await self._session.event_queue.put(event)
                return

            # Translate via direct API call (bypasses run_inference's NOT_GIVEN param clutter)
            system_instruction = (
                f"Translate from {source_name} to {target_name}. "
                "Output ONLY the translation, nothing else. "
                "If the input consists entirely of filler sounds (e.g. 'um', 'uh', 'ahh', 'hmm') with no meaningful content, output nothing."
            )
            logger.info(f"[Translation] Calling Cerebras for translation...")
            response = await self._llm._client.chat.completions.create(
                model=self._llm._settings.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": text},
                ],
                max_completion_tokens=500,
                stream=False,
            )
            translated = response.choices[0].message.content
            logger.info(f"[Translation] Cerebras result: {repr(translated)}")
            if not translated:
                logger.warning("[Translation] Empty result from Cerebras — skipping TTS injection")
                return

            # Inject into other participant's pipeline
            logger.info(f"[Translation] Injecting TTSSpeakFrame into {other.name}'s pipeline")
            await other.pipeline_task.queue_frames([TTSSpeakFrame(text=translated.strip())])
            logger.info(f"[Translation] Injected successfully")

            # Push event to dashboard
            event = {
                "type": "turn",
                "speaker": self._my_pc_id,
                "speaker_name": self._session.participants[self._my_pc_id].name,
                "original": text,
                "original_lang": source_name,
                "translated": translated.strip(),
                "translated_lang": target_name,
            }
            self._session.live_transcripts.pop(self._my_pc_id, None)
            self._session.transcript.append(event)
            await self._session.event_queue.put(event)
        except Exception as e:
            logger.exception("[Translation] _translate_and_inject failed")

    def _get_other_participant(self):
        for pc_id, p in self._session.participants.items():
            if pc_id != self._my_pc_id:
                return p
        return None

async def run_translation_participant(
    webrtc_connection: SmallWebRTCConnection,
    session: TranslationSession,
    participant: TranslationParticipant,
):
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # VAD is critical: without it the STT (manual-commit mode) never knows
            # when the user has finished speaking and holds the transcript indefinitely.
            # With VAD, silence after PTT release fires VADUserStoppedSpeakingFrame
            # → STT commits within ~300ms → dashboard updates immediately.
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    # Use ElevenLabs-side VAD commit so STT commits ~500ms after speech ends,
    # without depending on Pipecat's VADUserStoppedSpeakingFrame which is unreliable
    # with push-to-talk muting (MANUAL commit mode would wait up to 10s for pipecat VAD).
    from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService, CommitStrategy
    stt = ElevenLabsRealtimeSTTService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        commit_strategy=CommitStrategy.VAD,
        settings=ElevenLabsRealtimeSTTService.Settings(
            language=participant.language.value,
            vad_silence_threshold_secs=0.5,
        ),
    )

    from pipecat.services.cerebras.llm import CerebrasLLMService
    translation_llm = CerebrasLLMService(
        api_key=os.getenv("CEREBRAS_API_KEY"),
        settings=CerebrasLLMService.Settings(model="gpt-oss-120b"),
    )

    # TTS: voice only — no language/model override so ElevenLabs uses its
    # default multilingual model, which handles both English and Mandarin
    # without needing a language code that may be rejected by turbo v2.5.
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
    tts = ElevenLabsTTSService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        settings=ElevenLabsTTSService.Settings(
            voice=participant.voice_config["voice_id"],
        ),
    )

    translator = TranslationProcessor(
        translation_llm=translation_llm,
        session=session,
        my_pc_id=participant.pc_id,
    )


    audio_probe = AudioProbeProcessor(label=participant.name)

    pipeline = Pipeline([
        transport.input(),
        stt,
        translator,
        tts,
        audio_probe,
        transport.output(),
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True),
    )
    participant.pipeline_task = task

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Translation participant connected: {participant.name} ({participant.language.value})")
        connected = sum(1 for p in session.participants.values() if p.pipeline_task is not None)
        if connected >= 2:
            session.status = "live"
            await session.event_queue.put({"type": "status", "status": "live"})

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"Translation participant disconnected: {participant.name}")
        session.status = "ended"
        session.ended_at = datetime.now()
        await session.event_queue.put({"type": "status", "status": "ended"})
        pc_to_translation.pop(participant.pc_id, None)
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


# ---------------------------------------------------------------------------
# Auto-detect translation — single WebRTC connection, no PTT required.
# Detects language of each utterance and translates to the opposite language
# in the configured pair.  Used by the "face-to-face / always-listening" mode.
# ---------------------------------------------------------------------------

class AutoTranslationProcessor(FrameProcessor):
    """Language-aware single-session translator.

    Receives TranscriptionFrames, detects the spoken language, translates to
    the opposite language in the configured pair, and pushes a TTSSpeakFrame
    downstream into the same pipeline.  No cross-pipeline injection needed.
    """

    def __init__(
        self,
        translation_llm: GroqLLMService,
        lang_a: Language,
        lang_b: Language,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._llm   = translation_llm
        self._lang_a = lang_a
        self._lang_b = lang_b

    def _canonicalize_pair_language(self, language: Language | None) -> Language | None:
        """Map STT language variants onto one of the configured pair languages."""
        if language is None:
            return None

        value = language.value.lower().replace("_", "-")
        for candidate in (self._lang_a, self._lang_b):
            candidate_value = candidate.value.lower().replace("_", "-")
            if value == candidate_value or value.split("-", 1)[0] == candidate_value.split("-", 1)[0]:
                return candidate

        return None

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            text = frame.text.strip()
            if text and not _is_filler_only(text):
                detected = self._canonicalize_pair_language(frame.language)
                self.create_task(self._translate_and_speak(text, detected), "auto-translate")
            return  # consume — don't let raw transcription reach TTS

        await self.push_frame(frame, direction)

    async def _translate_and_speak(self, text: str, source_lang: Language | None):
        try:
            # Map Language enum values to human-readable names for the LLM prompt.
            _LANG_NAMES = {
                "en": "English",
                "zh": "Chinese (Mandarin)",
                "ar": "Arabic",
                "vi": "Vietnamese",
                "ko": "Korean",
                "hi": "Hindi",
                "el": "Greek",
            }
            a_name = _LANG_NAMES.get(self._lang_a.value, self._lang_a.value)
            b_name = _LANG_NAMES.get(self._lang_b.value, self._lang_b.value)

            logger.info(f"[AutoTranslation] STT lang={source_lang.value if source_lang else 'unknown'} | '{text[:60]}'")

            # Build the translation prompt.
            # When STT provides a confident language tag, use an explicit directional prompt
            # (much more reliable for small models).
            # When STT returns None (auto-detect failed), use LLM-side language detection.
            # source_lang is already normalized to the configured pair in process_frame.
            if source_lang == self._lang_b:
                # STT gave us a usable language tag — build explicit A→B or B→A prompt
                src_name, tgt_name = b_name, a_name
                system_instruction = (
                    f"Translate the following {src_name} text into {tgt_name}. "
                    "Output ONLY the translation. No explanations, no labels, no original text. "
                    "If the input is filler sounds only (e.g. 'um', 'uh', '嗯', '啊'), output nothing."
                )
            else:
                # STT language unknown — ask the LLM to detect and translate
                system_instruction = (
                    f"You translate between {a_name} and {b_name}. "
                    f"If the input is {a_name}, output only its {b_name} translation. "
                    f"If the input is {b_name}, output only its {a_name} translation. "
                    "Output ONLY the translation. No explanations, no labels, no original text. "
                    "If the input is filler sounds only (e.g. 'um', 'uh', '嗯', '啊'), output nothing."
                )
            response = await self._llm._client.chat.completions.create(
                model=self._llm._settings.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": text},
                ],
                max_completion_tokens=500,
                stream=False,
            )
            translated = response.choices[0].message.content
            if translated:
                logger.info(f"[AutoTranslation] → '{translated.strip()[:60]}'")
                await self.push_frame(TTSSpeakFrame(text=translated.strip()))
            else:
                logger.warning("[AutoTranslation] Empty result from Cerebras")
        except Exception as e:
            logger.exception("[AutoTranslation] Translation error")


class FixedAutoTranslationProcessor(AutoTranslationProcessor):
    """Auto-translation processor with explicit source->target routing."""

    _CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
    _LATIN_RE = re.compile(r"[A-Za-z]")

    def _detect_lang_from_text(self, text: str) -> Language | None:
        """Fallback language detection for the en/zh auto-translation mode."""
        if self._CJK_RE.search(text):
            return self._lang_b
        if self._LATIN_RE.search(text):
            return self._lang_a
        return None

    async def process_frame(self, frame, direction):
        await FrameProcessor.process_frame(self, frame, direction)

        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            text = frame.text.strip()
            if text and not _is_filler_only(text):
                detected = self._canonicalize_pair_language(frame.language) or self._detect_lang_from_text(text)
                self.create_task(self._translate_and_speak(text, detected), "auto-translate")
            return

        await self.push_frame(frame, direction)

    async def _translate_and_speak(self, text: str, source_lang: Language | None):
        try:
            language_names = {
                "en": "English",
                "zh": "Chinese (Mandarin)",
                "ar": "Arabic",
                "vi": "Vietnamese",
                "ko": "Korean",
                "hi": "Hindi",
                "el": "Greek",
            }
            a_name = language_names.get(self._lang_a.value, self._lang_a.value)
            b_name = language_names.get(self._lang_b.value, self._lang_b.value)

            logger.info(
                f"[AutoTranslation] STT lang={source_lang.value if source_lang else 'unknown'} | '{text[:60]}'"
            )

            if source_lang == self._lang_b:
                src_name, tgt_name = b_name, a_name
                system_instruction = (
                    f"Translate the following {src_name} text into {tgt_name}. "
                    "Output ONLY the translation. No explanations, no labels, no original text. "
                    "If the input is filler sounds only (e.g. 'um', 'uh', 'å—¯', 'å•Š'), output nothing."
                )
            elif source_lang == self._lang_a:
                src_name, tgt_name = a_name, b_name
                system_instruction = (
                    f"Translate the following {src_name} text into {tgt_name}. "
                    "Output ONLY the translation. No explanations, no labels, no original text. "
                    "If the input is filler sounds only (e.g. 'um', 'uh', 'å—¯', 'å•Š'), output nothing."
                )
            else:
                system_instruction = (
                    f"You translate between {a_name} and {b_name}. "
                    f"If the input is {a_name}, output only its {b_name} translation. "
                    f"If the input is {b_name}, output only its {a_name} translation. "
                    "Output ONLY the translation. No explanations, no labels, no original text. "
                    "If the input is filler sounds only (e.g. 'um', 'uh', 'å—¯', 'å•Š'), output nothing."
                )

            response = await self._llm._client.chat.completions.create(
                model=self._llm._settings.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": text},
                ],
                max_completion_tokens=500,
                stream=False,
            )
            translated = response.choices[0].message.content
            if translated:
                logger.info(f"[AutoTranslation] -> '{translated.strip()[:60]}'")
                await self.push_frame(TTSSpeakFrame(text=translated.strip()))
            else:
                logger.warning("[AutoTranslation] Empty result from Cerebras")
        except Exception:
            logger.exception("[AutoTranslation] Translation error")


class StrictAutoTranslationProcessor(FixedAutoTranslationProcessor):
    """Translator-only processor with minimal prompting and deterministic settings."""

    def __init__(self, *args, session: TranslationSession | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._session = session
        self._preview_task: asyncio.Task | None = None

    def _event_speaker(self, source_lang: Language | None) -> tuple[str, str]:
        if source_lang == self._lang_b:
            return self._lang_b.value, "Mandarin"
        if source_lang == self._lang_a:
            return self._lang_a.value, "English"
        return "unknown", "Speaker"

    def _target_lang(self, source_lang: Language | None) -> Language | None:
        if source_lang == self._lang_b:
            return self._lang_a
        if source_lang == self._lang_a:
            return self._lang_b
        return None

    async def _translate_text(self, text: str, source_lang: Language | None, *, max_completion_tokens: int) -> tuple[str | None, Language | None]:
        language_names = {
            "en": "English",
            "zh": "Chinese (Mandarin)",
        }
        a_name = language_names.get(self._lang_a.value, self._lang_a.value)
        b_name = language_names.get(self._lang_b.value, self._lang_b.value)

        target_lang = self._target_lang(source_lang)
        target_language = language_names.get(target_lang.value, target_lang.value) if target_lang else None

        system_instruction = (
            "You are a translation engine.\n"
            "Translate only.\n"
            "Never explain, define, annotate, answer questions, or add notes.\n"
            "Return only the translated text.\n"
            "If the input is filler-only or has no meaningful content, return an empty string."
        )

        if target_language:
            user_prompt = f"Target language: {target_language}\nText:\n{text}"
        else:
            user_prompt = (
                f"Detect whether the text is {a_name} or {b_name}. "
                f"If it is {a_name}, translate it to {b_name}. "
                f"If it is {b_name}, translate it to {a_name}. "
                "Return only the translation.\n"
                f"Text:\n{text}"
            )

        response = await self._llm._client.chat.completions.create(
            model=self._llm._settings.model,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_completion_tokens=max_completion_tokens,
            stream=False,
        )
        translated = response.choices[0].message.content
        return (translated.strip() if translated else None), (target_lang or self._target_lang(source_lang))

    async def _publish_live_preview(self, text: str, source_lang: Language | None):
        if not self._session or source_lang is None:
            return

        translated, target_lang = await self._translate_text(text, source_lang, max_completion_tokens=80)
        if not translated or target_lang is None:
            return

        event = {
            "type": "live_preview",
            "speaker": source_lang.value,
            "speaker_name": self._event_speaker(source_lang)[1],
            "target_lang": target_lang.value,
            "text": translated,
        }
        self._session.live_previews[target_lang.value] = event
        await self._session.event_queue.put(event)

    async def process_frame(self, frame, direction):
        await FrameProcessor.process_frame(self, frame, direction)

        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterimTranscriptionFrame):
            text = frame.text.strip()
            if text and not _is_filler_only(text):
                detected = self._canonicalize_pair_language(frame.language) or self._detect_lang_from_text(text)
                if self._session:
                    speaker, speaker_name = self._event_speaker(detected)
                    event = {
                        "type": "live_transcript",
                        "speaker": speaker,
                        "speaker_name": speaker_name,
                        "original": text,
                        "original_lang": detected.value if detected else "unknown",
                    }
                    self._session.live_transcripts[speaker] = event
                    await self._session.event_queue.put(event)
                if self._preview_task and not self._preview_task.done():
                    await self.cancel_task(self._preview_task)

                async def _debounced_preview():
                    await asyncio.sleep(0.15)
                    await self._publish_live_preview(text, detected)

                self._preview_task = self.create_task(_debounced_preview(), "auto-translate-preview")
            return

        if isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text and not _is_filler_only(text):
                detected = self._canonicalize_pair_language(frame.language) or self._detect_lang_from_text(text)
                if self._preview_task and not self._preview_task.done():
                    await self.cancel_task(self._preview_task)
                if self._session:
                    speaker, speaker_name = self._event_speaker(detected)
                    self._session.live_transcripts[speaker] = {
                        "type": "live_transcript",
                        "speaker": speaker,
                        "speaker_name": speaker_name,
                        "original": text,
                        "original_lang": detected.value if detected else "unknown",
                    }
                self.create_task(self._translate_and_speak(text, detected), "auto-translate")
            return

        await self.push_frame(frame, direction)

    async def _translate_and_speak(self, text: str, source_lang: Language | None):
        try:
            logger.info(
                f"[AutoTranslation] STT lang={source_lang.value if source_lang else 'unknown'} | '{text[:60]}'"
            )
            translated, target_lang = await self._translate_text(text, source_lang, max_completion_tokens=200)
            if translated:
                logger.info(f"[AutoTranslation] -> '{translated[:60]}'")
                if self._session:
                    speaker, speaker_name = self._event_speaker(source_lang)
                    event = {
                        "type": "turn",
                        "speaker": speaker,
                        "speaker_name": speaker_name,
                        "original": text,
                        "original_lang": source_lang.value if source_lang else "unknown",
                        "translated": translated,
                        "translated_lang": target_lang.value if target_lang else "unknown",
                    }
                    self._session.live_transcripts.pop(speaker, None)
                    if target_lang:
                        self._session.live_previews.pop(target_lang.value, None)
                    self._session.transcript.append(event)
                    await self._session.event_queue.put(event)
                await self.push_frame(TTSSpeakFrame(text=translated))
            else:
                logger.warning("[AutoTranslation] Empty result from Cerebras")
        except Exception:
            logger.exception("[AutoTranslation] Translation error")


async def run_auto_translation(
    webrtc_connection: SmallWebRTCConnection,
    lang_a: str,
    lang_b: str,
    session: TranslationSession | None = None,
):
    """Single-pipeline auto-detect translation session."""
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService, CommitStrategy
    stt = ElevenLabsRealtimeSTTService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        commit_strategy=CommitStrategy.VAD,
        settings=ElevenLabsRealtimeSTTService.Settings(
            vad_silence_threshold_secs=0.6,
            # No language lock — STT auto-detects Chinese vs English
        ),
    )

    from pipecat.services.cerebras.llm import CerebrasLLMService
    translation_llm = CerebrasLLMService(
        api_key=os.getenv("CEREBRAS_API_KEY"),
        settings=CerebrasLLMService.Settings(model="llama3.1-8b"),
    )

    # Use a multilingual voice — handles both languages without voice-switching
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
    tts = ElevenLabsTTSService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        settings=ElevenLabsTTSService.Settings(
            voice=os.getenv(
                "ELEVENLABS_MULTILINGUAL_VOICE_ID",
                os.getenv("ELEVENLABS_VOICE_ID", ""),
            ),
        ),
    )

    auto_translator = StrictAutoTranslationProcessor(
        translation_llm=translation_llm,
        lang_a=Language(lang_a),
        lang_b=Language(lang_b),
        session=session,
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        auto_translator,
        tts,
        transport.output(),
    ])

    task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=True))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Auto-translation connected — {lang_a} ↔ {lang_b}")
        if session:
            session.status = "live"
            await session.event_queue.put({"type": "status", "status": "live"})

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Auto-translation disconnected")
        if session:
            session.status = "ended"
            session.ended_at = datetime.now()
            await session.event_queue.put({"type": "status", "status": "ended"})
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


@app.post("/api/translation/auto-offer")
async def auto_translation_offer(request: Request, background_tasks: BackgroundTasks):
    """Single-connection auto language-detect translation (face-to-face / always-listening mode)."""
    data = await request.json()
    session_id = data.get("session_id")
    lang_a = data.get("lang_a", "en")
    lang_b = data.get("lang_b", "zh")
    session = translation_sessions.get(session_id) if session_id else None
    if session_id and not session:
        return Response(status_code=404, content="Session not found")

    session_ice_servers, _ = fetch_twilio_ice_servers()
    connection = SmallWebRTCConnection(session_ice_servers)
    await connection.initialize(sdp=data["sdp"], type=data["type"])

    answer = _filter_relay_sdp(connection.get_answer())
    pc_id = answer["pc_id"]
    pcs_map[pc_id] = connection

    @connection.event_handler("closed")
    async def handle_closed(conn):
        pcs_map.pop(conn.pc_id, None)

    background_tasks.add_task(run_auto_translation, connection, lang_a, lang_b, session)
    return answer


@app.post("/api/translation/session")
async def create_translation_session(request: Request):
    """Create a new translation session. Returns session_id + join link."""
    data = await request.json()
    session_id = f"GRC-{random.randint(1000, 9999)}"
    session = TranslationSession(
        session_id=session_id,
        caller_name=data.get("caller_name", "Unknown"),
        caller_lang=data.get("caller_language", "zh"),
        topic=data.get("topic", "General"),
    )
    translation_sessions[session_id] = session
    return {"session_id": session_id, "status": "waiting"}

@app.post("/api/translation/offer")
async def translation_offer(request: Request, background_tasks: BackgroundTasks):
    """WebRTC offer from a translation session participant."""
    data = await request.json()
    session_id = data.get("session_id")
    session = translation_sessions.get(session_id)
    if not session:
        return Response(status_code=404, content="Session not found")

    language = data.get("language", "en")
    name = data.get("name", "Participant")
    voice_config = TRANSLATION_VOICES.get(language, TRANSLATION_VOICES["en"])

    # Create WebRTC connection
    session_ice_servers, _ = fetch_twilio_ice_servers()
    connection = SmallWebRTCConnection(session_ice_servers)
    await connection.initialize(sdp=data["sdp"], type=data["type"])

    answer = _filter_relay_sdp(connection.get_answer())
    pc_id = answer["pc_id"]
    pcs_map[pc_id] = connection

    participant = TranslationParticipant(
        pc_id=pc_id, name=name,
        language=Language(language),
        voice_config=voice_config,
    )
    session.participants[pc_id] = participant
    pc_to_translation[pc_id] = session_id

    @connection.event_handler("closed")
    async def handle_closed(conn):
        pcs_map.pop(conn.pc_id, None)

    background_tasks.add_task(run_translation_participant, connection, session, participant)

    return answer

@app.get("/api/translation/sessions")
async def list_translation_sessions():
    """List all active/recent translation sessions for the dashboard."""
    sessions = []
    for s in translation_sessions.values():
        end_time = s.ended_at if s.ended_at else datetime.now()
        elapsed = (end_time - s.created_at).total_seconds()
        mins, secs = divmod(int(elapsed), 60)
        sessions.append({
            "session_id": s.session_id,
            "caller_name": s.caller_name,
            "lang": s.caller_lang,
            "topic": s.topic,
            "status": s.status,
            "duration": f"{mins:02d}:{secs:02d}",
            "participant_count": len(s.participants),
        })
    return {"sessions": sessions}

@app.get("/api/translation/poll")
async def translation_poll(session_id: str):
    """Poll transcript events for a translation session."""
    session = translation_sessions.get(session_id)
    if not session:
        return {"events": [], "closed": True}

    events = []
    while True:
        try:
            event = session.event_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        events.append(event)

    return {"events": events, "closed": session.status == "ended"}

@app.get("/api/translation/session/{session_id}")
async def get_translation_session(session_id: str):
    """Get full session details including transcript history."""
    session = translation_sessions.get(session_id)
    if not session:
        return Response(status_code=404, content="Session not found")
    return {
        "session_id": session.session_id,
        "caller_name": session.caller_name,
        "lang": session.caller_lang,
        "topic": session.topic,
        "status": session.status,
        "transcript": session.transcript,
        "live_transcripts": session.live_transcripts,
        "live_previews": session.live_previews,
        "participants": [
            {"pc_id": p.pc_id, "name": p.name, "language": p.language.value}
            for p in session.participants.values()
        ],
    }

@app.get("/vocare", response_class=HTMLResponse)
async def vocare_app():
    html_path = os.path.join(os.path.dirname(__file__), "static", "vocare.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())

@app.get("/translate/{session_id}", response_class=HTMLResponse)
async def translate_join_page(session_id: str):
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vocare Bot")
    parser.add_argument("--host", default="localhost", help="Host (default: localhost)")
    parser.add_argument("--port", type=int, default=7860, help="Port (default: 7860)")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port)
