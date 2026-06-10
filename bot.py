"""Vocare voice bot with configurable STT/LLM/TTS via WebRTC transport.

The frontend sends service selections (stt, llm, tts) as part of the
/api/offer request body. The bot dynamically creates the chosen services
for each session.
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import sys
import random
import time
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable, Dict

sys.path.insert(0, str(Path(__file__).parent / "GRC_pilot"))
from tools import _correct_address  # noqa: E402
from grc_events import get_events, format_events_for_system_prompt, get_future_events  # noqa: E402
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
from pipecat.audio.dtmf.types import KeypadEntry
from pipecat.audio.utils import create_stream_resampler, pcm_to_ulaw, ulaw_to_pcm
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    Frame,
    FunctionCallInProgressFrame,
    InputAudioRawFrame,
    InputDTMFFrame,
    InterruptionFrame,
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
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy, TranscriptionUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import LLMService, FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.serializers.telnyx import TelnyxFrameSerializer
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

load_dotenv(override=True)


class VobizFrameSerializer(FrameSerializer):
    """Vobiz bidirectional <Stream> websocket serializer.

    Vobiz sends inbound audio as JSON `media` events and expects outbound bot
    audio as `playAudio` events. We use 8 kHz PCMU because it is the common
    phone-call codec and is supported by Vobiz's XML Stream API.
    """

    class InputParams(FrameSerializer.InputParams):
        vobiz_sample_rate: int = 8000
        vobiz_encoding: str = "audio/x-mulaw"
        sample_rate: int | None = None

    def __init__(self, stream_id: str, params: InputParams | None = None):
        super().__init__(params or VobizFrameSerializer.InputParams())
        self._stream_id = stream_id
        self._vobiz_sample_rate = self._params.vobiz_sample_rate
        self._vobiz_encoding = self._params.vobiz_encoding
        self._sample_rate = 0
        self._input_resampler = create_stream_resampler()
        self._output_resampler = create_stream_resampler()

    async def setup(self, frame):
        self._sample_rate = self._params.sample_rate or frame.audio_in_sample_rate

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, (EndFrame, CancelFrame)):
            return None

        if isinstance(frame, InterruptionFrame):
            return json.dumps({"event": "clearAudio", "streamId": self._stream_id})

        if isinstance(frame, AudioRawFrame):
            if self._vobiz_encoding == "audio/x-mulaw":
                encoded = await pcm_to_ulaw(
                    frame.audio,
                    frame.sample_rate,
                    self._vobiz_sample_rate,
                    self._output_resampler,
                )
            else:
                encoded = await self._output_resampler.resample(
                    frame.audio,
                    frame.sample_rate,
                    self._vobiz_sample_rate,
                )
            if not encoded:
                return None

            return json.dumps({
                "event": "playAudio",
                "streamId": self._stream_id,
                "media": {
                    "contentType": self._vobiz_encoding,
                    "sampleRate": self._vobiz_sample_rate,
                    "payload": base64.b64encode(encoded).decode("utf-8"),
                },
            })

        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        try:
            message = json.loads(data)
        except json.JSONDecodeError:
            logger.warning(f"[Vobiz] Failed to parse websocket JSON: {data}")
            return None

        event = message.get("event")

        if event == "media":
            payload_base64 = (message.get("media") or {}).get("payload")
            if not payload_base64:
                return None

            payload = base64.b64decode(payload_base64)
            if self._vobiz_encoding == "audio/x-mulaw":
                pcm = await ulaw_to_pcm(
                    payload,
                    self._vobiz_sample_rate,
                    self._sample_rate,
                    self._input_resampler,
                )
            else:
                pcm = await self._input_resampler.resample(
                    payload,
                    self._vobiz_sample_rate,
                    self._sample_rate,
                )
            if not pcm:
                return None

            return InputAudioRawFrame(audio=pcm, num_channels=1, sample_rate=self._sample_rate)

        if event == "dtmf":
            digit = (message.get("dtmf") or {}).get("digit")
            if digit:
                try:
                    return InputDTMFFrame(KeypadEntry(digit))
                except ValueError:
                    logger.warning(f"[Vobiz] Ignoring invalid DTMF digit: {digit}")

        return None


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _llm_provider(name: str | None = None) -> str:
    provider = (name or _env("LLM_PROVIDER")).strip().lower()
    if not provider:
        raise RuntimeError("LLM_PROVIDER is not set")
    return provider


def _llm_model(provider: str) -> str:
    return (
        _env("LLM_MODEL")
        or _env(f"{provider.upper()}_MODEL")
        or {
            "openai": "gpt-4o-mini",
            "azure": "gpt-5.4-mini",
            "azure_openai": "gpt-5.4-mini",
            "foundry": "gpt-5.4-mini",
            "cerebras": "gpt-oss-120b",
            "deepseek": "deepseek-v4-pro",
        }.get(provider, "")
    )


def _llm_api_key(provider: str) -> str:
    if provider == "openai":
        return _env("OPENAI_API") or _env("OPENAI_API_KEY")
    if provider in {"azure", "azure_openai", "foundry"}:
        return (
            _env("AZURE_OPENAI_API_KEY")
            or _env("AZURE_AI_FOUNDRY_API_KEY")
            or _env("AZURE_API_KEY")
        )
    return _env(f"{provider.upper()}_API_KEY") or _env(f"{provider.upper()}_API")


def _validate_llm_key(provider: str, api_key: str) -> None:
    if not api_key:
        raise RuntimeError(f"{provider.upper()} API key is not set")
    if provider != "openai" and api_key.startswith("sk-proj-"):
        raise RuntimeError(
            f"LLM_PROVIDER={provider} is configured with an OpenAI project key. "
            f"Set {provider.upper()}_API_KEY to a real {provider} key."
        )


def _azure_openai_base_url() -> str:
    return (
        _env("LLM_BASE_URL")
        or _env("AZURE_OPENAI_ENDPOINT")
        or _env("AZURE_AI_FOUNDRY_ENDPOINT")
        or _env("AZURE_OPENAI_BASE_URL")
    )


def _float_env(name: str, default: float) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Invalid float for {name}={raw!r}; using {default}")
        return default


def _tts_float_env(language: str, setting: str, default: float) -> float:
    return _float_env(
        f"ELEVENLABS_TTS_{language.upper()}_{setting.upper()}",
        _float_env(f"ELEVENLABS_TTS_{setting.upper()}", default),
    )


def create_vad_analyzer() -> SileroVADAnalyzer:
    # Telephony audio (8 kHz µ-law) is companded and often low-gain, so the
    # default min_volume=0.6 / confidence=0.7 reject the quiet onset of short
    # phrases. With use_interim turn starts, that means STT still shows the text
    # but VAD never fires a speech-stop, so the turn never closes and the bot
    # doesn't reply until the caller speaks louder ("Hello?"). Lower, env-tunable
    # thresholds fix that without making the VAD trigger on line noise.
    return SileroVADAnalyzer(
        params=VADParams(
            confidence=_float_env("VAD_CONFIDENCE", 0.6),
            start_secs=_float_env("VAD_START_SECS", 0.15),
            stop_secs=_float_env("VAD_STOP_SECS", 0.6),
            min_volume=_float_env("VAD_MIN_VOLUME", 0.3),
        )
    )

# Suppress pipecat internal DEBUG/TRACE noise — keep only INFO and above.
# Re-enable temporarily by setting VOCARE_LOG_LEVEL=DEBUG in the environment.
_log_level = _env("VOCARE_LOG_LEVEL", "INFO").upper()
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

FILLERS_EN = [
    "Let me look that up for you.",
    "Just a moment while I check that.",
    "One moment please.",
]

FILLERS_ZH = [
    "让我帮您查一下。",
    "请稍等一下。",
    "我来为您查询。",
]

WEB_INTERFACE_SYSTEM_OVERRIDE = (
    "WEB INTERFACE LIMITATION: This session is running in the browser web interface, "
    "not on a live Twilio phone call. You cannot transfer the user to a human from "
    "this interface. If the user asks for a human, person, operator, agent, transfer, "
    "or escalation, do not say that you can transfer them. Say that transfer is not "
    "available in the web interface and provide the council phone number 9330 6400."
)

TRANSFER_REQUEST_RE = re.compile(
    r"\b("
    r"speak\s+(?:to|with)\s+(?:a\s+)?(?:human|person|someone|agent|operator|representative)|"
    r"talk\s+(?:to|with)\s+(?:a\s+)?(?:human|person|someone|agent|operator|representative)|"
    r"(?:real|live|human)\s+(?:person|agent)|"
    r"(?:need|want)\s+(?:a\s+)?(?:human|person|agent|operator|representative)|"
    r"(?:human|person|agent|operator|representative)\s+please|"
    r"transfer\s+me|connect\s+me|put\s+me\s+through|"
    r"transfer|human|escalate|operator|representative|customer\s+service"
    r")\b",
    re.IGNORECASE,
)


def _is_transfer_request(text: str) -> bool:
    return bool(TRANSFER_REQUEST_RE.search(text or ""))


_HESITATION_FILLER_RE = re.compile(
    r"^[\s,\.!\?，。！？、]*(?:"
    r"u+h+|u+m+|a+h+|h+m+|h+u+h+|e+r+|o+h+|"
    r"嗯+|啊+|呃+|哦+|唔+|诶+"
    r")(?:[\s,\.!\?，。！？、]+(?:"
    r"u+h+|u+m+|a+h+|h+m+|h+u+h+|e+r+|o+h+|"
    r"嗯+|啊+|呃+|哦+|唔+|诶+"
    r"))*[\s,\.!\?，。！？、]*$",
    re.IGNORECASE,
)

_HESITATION_WAIT_RE = re.compile(
    r"^\s*(?:"
    r"(?:sorry\s+)?(?:hold\s+on|hang\s+on|wait|one\s+(?:sec|second|moment)|"
    r"just\s+(?:a\s+)?(?:sec|second|moment)|give\s+me\s+(?:a\s+)?(?:sec|second|moment)|"
    r"let\s+me\s+(?:think|see|check))"
    r"|(?:等一下|稍等|等等|等一等|请稍等|让我想一下|我想一下)"
    r")\s*(?:please|thanks|谢谢|麻烦你)?\s*$",
    re.IGNORECASE,
)

_HESITATION_PARTIAL_RE = re.compile(
    r"^\s*(?:"
    r"(?:u+h+|u+m+|a+h+|h+m+|h+u+h+|e+r+|o+h+)\b|"
    r"(?:yeah|yes|okay|ok|right)\s+(?:u+h+|u+m+|a+h+|h+m+|e+r+)\b|"
    r"(?:嗯+|啊+|呃+|哦+|唔+|诶+)"
    r")",
    re.IGNORECASE,
)

_ACTIONABLE_HINT_RE = re.compile(
    r"\b(?:bin|bins|rubbish|garbage|waste|recycling|collection|event|events|"
    r"activity|activities|da|development|application|planner|address|street|"
    r"english|mandarin|chinese|yes|no)\b|"
    r"(?:垃圾|回收|活动|开发申请|地址|街|中文|普通话|国语|英文|英语|是|不是)",
    re.IGNORECASE,
)

_BACKCHANNEL_ONLY_RE = re.compile(
    r"^\s*(?:"
    r"i\s+(?:see|understand|get\s+it|hear\s+you|am\s+listening)|"
    r"understood|got\s+it|okay|ok|right|sure|mm[-\s]?hmm|"
    r"go\s+on|please\s+go\s+on|continue|please\s+continue|"
    r"take\s+your\s+time|no\s+worries|"
    r"silence|silent|"
    r"no\s+(?:response|reply|output|answer)(?:\s+(?:this\s+time|needed|required|for\s+now))?|"
    r"(?:nothing|no\s+need)\s+(?:to\s+say|to\s+respond|to\s+reply)|"
    r"do\s+not\s+respond|none|null|n/?a|"
    r"(?:i(?:'m| am)\s+)?waiting\s+for\s+(?:the\s+)?caller\s+to\s+finish|"
    r"(?:i(?:'m| am)\s+)?waiting\s+for\s+you\s+to\s+finish|"
    r"我明白|明白|好的|好|请继续|继续说|慢慢来|我在听|我等您说完"
    r")\s*[\.\!,，。！]*\s*$",
    re.IGNORECASE,
)

_BACKCHANNEL_PREFIXES = (
    "i see",
    "i understand",
    "i get it",
    "i hear you",
    "i am listening",
    "i'm listening",
    "understood",
    "got it",
    "okay",
    "ok",
    "right",
    "sure",
    "mm-hmm",
    "mm hmm",
    "go on",
    "please go on",
    "continue",
    "please continue",
    "take your time",
    "no worries",
    "silence",
    "silent",
    "no response",
    "no response this time",
    "no reply",
    "no output",
    "no answer",
    "nothing to say",
    "nothing to respond",
    "do not respond",
    "none",
    "null",
    "n/a",
    "waiting for caller to finish",
    "waiting for the caller to finish",
    "waiting for you to finish",
    "i'm waiting for",
    "i am waiting for",
    "我明白",
    "明白",
    "好的",
    "好",
    "请继续",
    "继续说",
    "慢慢来",
    "我在听",
    "我等您说完",
)


def _wordish_count(text: str) -> int:
    words = re.findall(r"[A-Za-z0-9']+|[\u4e00-\u9fff]", text or "")
    return len(words)


def _hesitation_turn_action(text: str) -> tuple[str, float]:
    """Return ('drop'|'release'|'', delay) for caller hesitation handling."""
    stripped = (text or "").strip()
    if not stripped:
        return ("", 0.0)
    if _HESITATION_FILLER_RE.match(stripped):
        return ("drop", _float_env("HESITATION_FILLER_HOLDOFF_SECS", 1.4))
    if _HESITATION_WAIT_RE.match(stripped):
        return ("drop", _float_env("HESITATION_WAIT_HOLDOFF_SECS", 2.6))
    if (
        _wordish_count(stripped) <= 6
        and _HESITATION_PARTIAL_RE.search(stripped)
        and not _ACTIONABLE_HINT_RE.search(stripped)
    ):
        return ("drop", _float_env("HESITATION_PARTIAL_HOLDOFF_SECS", 1.6))
    return ("", 0.0)


def _hesitation_turn_delay(text: str) -> float:
    return _hesitation_turn_action(text)[1]


class HesitationTurnGateProcessor(FrameProcessor):
    """Prevents filler/hold-on utterances from becoming completed user turns."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pending_drop_task: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TranscriptionFrame):
            action, delay = _hesitation_turn_action(frame.text)
            if delay > 0:
                await self._cancel_pending_drop()
                self._pending_drop_task = self.create_task(
                    self._complete_after_delay(frame, action, delay),
                    "hesitation_turn_drop",
                )
                logger.info(
                    f"[TURN GATE] Holding {action} hesitation transcript for {delay:.1f}s: {frame.text!r}"
                )
                return

            await self._cancel_pending_drop()

        elif direction == FrameDirection.DOWNSTREAM and isinstance(frame, InterimTranscriptionFrame):
            if self._pending_drop_task and not self._pending_drop_task.done():
                text = (frame.text or "").strip()
                if text and _hesitation_turn_delay(text) == 0:
                    await self._cancel_pending_drop()
                    logger.info(f"[TURN GATE] Caller continued after hesitation: {text[:80]!r}")

        await self.push_frame(frame, direction)

    async def _cancel_pending_drop(self):
        if self._pending_drop_task and not self._pending_drop_task.done():
            await self.cancel_task(self._pending_drop_task)
        self._pending_drop_task = None

    async def _complete_after_delay(self, frame: TranscriptionFrame, action: str, delay: float):
        try:
            await asyncio.sleep(delay)
            if action == "release":
                logger.info(f"[TURN GATE] Releasing delayed partial transcript: {frame.text!r}")
                await self.push_frame(frame, FrameDirection.DOWNSTREAM)
            else:
                logger.info(f"[TURN GATE] Dropped hesitation-only transcript: {frame.text!r}")
        except asyncio.CancelledError:
            raise


def _is_backchannel_only(text: str) -> bool:
    stripped = (text or "").strip()
    return len(stripped) <= 120 and bool(_BACKCHANNEL_ONLY_RE.match(stripped))


def _is_unspeakable_status_text(text: str) -> bool:
    stripped = (text or "").strip()
    return not stripped or _is_backchannel_only(stripped)


def _normalize_backchannel_candidate(text: str) -> str:
    return re.sub(r"[\s\.,!\?，。！、]+", " ", (text or "").strip().lower()).strip()


def _could_be_backchannel(text: str) -> bool:
    normalized = _normalize_backchannel_candidate(text)
    if not normalized:
        return True
    return any(prefix.startswith(normalized) or normalized.startswith(prefix) for prefix in _BACKCHANNEL_PREFIXES)


class BackchannelSuppressorProcessor(FrameProcessor):
    """Drops short LLM acknowledgement/status replies before they reach TTS."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._buffered_text_frames: list[LLMTextFrame] = []
        self._passthrough_response = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMTextFrame):
            if self._passthrough_response:
                await self.push_frame(frame, direction)
                return

            self._buffered_text_frames.append(frame)
            text = "".join(getattr(f, "text", "") for f in self._buffered_text_frames)
            if len(text.strip()) > 120 or not _could_be_backchannel(text):
                for buffered in self._buffered_text_frames:
                    await self.push_frame(buffered, direction)
                self._buffered_text_frames.clear()
                self._passthrough_response = True
            return

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TTSSpeakFrame):
            if _is_unspeakable_status_text(getattr(frame, "text", "")):
                logger.info(f"[BACKCHANNEL] Suppressed TTSSpeakFrame status/null response: {frame.text!r}")
                return

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, LLMFullResponseEndFrame):
            if self._buffered_text_frames:
                text = "".join(getattr(f, "text", "") for f in self._buffered_text_frames)
                if _is_unspeakable_status_text(text):
                    logger.info(f"[BACKCHANNEL] Suppressed LLM status/null response: {text!r}")
                    self._buffered_text_frames.clear()
                    await self.push_frame(frame, direction)
                    return

                for buffered in self._buffered_text_frames:
                    await self.push_frame(buffered, direction)
                self._buffered_text_frames.clear()
            self._passthrough_response = False

        await self.push_frame(frame, direction)


class TransferRequestProcessor(FrameProcessor):
    """Deterministically handles human-transfer requests before the LLM."""

    def __init__(
        self,
        on_transfer_request: Callable[[], Awaitable[str | None]],
        immediate_message: str | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._on_transfer_request = on_transfer_request
        self._immediate_message = immediate_message
        self._transfer_requested = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM and isinstance(
            frame, (InterimTranscriptionFrame, TranscriptionFrame)
        ):
            if self._transfer_requested:
                return

            if isinstance(frame, TranscriptionFrame) and _is_transfer_request(frame.text):
                self._transfer_requested = True
                logger.info(f"[TRANSFER] Direct transfer intent detected: {frame.text!r}")
                self.create_task(self._handle_transfer(), "direct_transfer")
                return

        await self.push_frame(frame, direction)

    async def _handle_transfer(self):
        try:
            if self._immediate_message:
                await self.push_frame(
                    TTSSpeakFrame(text=self._immediate_message),
                    FrameDirection.DOWNSTREAM,
                )
                await asyncio.sleep(1.25)
            message = await self._on_transfer_request()
            if message:
                await self.push_frame(TTSSpeakFrame(text=message), FrameDirection.DOWNSTREAM)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[TRANSFER] Direct transfer handler failed: {e}")
            await self.push_frame(
                TTSSpeakFrame(
                    text="I wasn't able to transfer the call. Please call us directly on 9330 6400."
                ),
                FrameDirection.DOWNSTREAM,
            )


class ThinkerProcessor(FrameProcessor):
    """Runs a background 8B LLM to pre-extract intent/entities from STT transcripts.

    Sits between STT and user_aggregator. Intercepts InterimTranscriptionFrame
    (with 200ms debounce) and TranscriptionFrame (immediately) to fire an
    out-of-pipeline LLM inference. The result is stored in thinker_state and
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
            if state and float(state.get("confidence", 0.0)) >= 0.5:
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
        self.is_english = True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, FunctionCallInProgressFrame) and direction == FrameDirection.DOWNSTREAM:
            # Skip filler for bin collection (has its own filler) and transfer
            # (filler loop would play repeatedly before Twilio redirects the call).
            _NO_FILLER_TOOLS = {"get_bin_collection_day", "transfer_to_human"}
            if frame.function_name not in _NO_FILLER_TOOLS:
                fillers = FILLERS_EN if self.is_english else FILLERS_ZH
                filler = fillers[self._filler_index % len(fillers)]
                self._filler_index += 1
                await self.push_frame(TTSSpeakFrame(text=filler), direction)

        await self.push_frame(frame, direction)


class LanguageSwitchProcessor(FrameProcessor):
    """Detects the user's spoken language and updates the TTS voice/language accordingly.

    Sits between ThinkerProcessor and user_aggregator. Watches TranscriptionFrame
    for language changes. On a change, pushes a TTSUpdateSettingsFrame downstream
    to reconnect the ElevenLabs WebSocket with the correct voice and language code.
    """

    def __init__(self, tts, voice_id: str, filler_tts=None, context=None, **kwargs):
        super().__init__(**kwargs)
        self._tts = tts
        self._voice_id = voice_id
        self._filler_tts = filler_tts
        self._context = context
        self._is_english: bool = True  # start English; flip on first non-EN utterance
        # Dedup synthetic language-choice turns. Keyed on the *canonical* rewritten
        # sentence (stable) rather than the raw STT text (which varies between
        # "english", "in english", "english please"...), so repeats and the delayed
        # final that follows an interim collapse to one turn. Reset when the caller
        # says something unrelated, so a genuine later re-selection still works.
        self._last_injected_canonical = ""
        self._last_injected_language_at = 0.0

    # How long a language choice stays deduped. Generous because the failure mode
    # is a runaway re-greet loop; a real re-selection clears the guard via a normal
    # turn anyway.
    _LANGUAGE_DEDUP_SECS = 30.0

    # Keywords that signal a language preference regardless of the STT language tag.
    # "Mandarin" is an English word so ElevenLabs STT returns language="en" or None — we
    # catch it via text content instead so the switch always fires on the first utterance.
    _MANDARIN_KEYWORDS = frozenset({"mandarin", "chinese", "中文", "普通话", "国语"})
    _ENGLISH_KEYWORDS  = frozenset({"english", "英文", "英语"})

    def _closed_slot_language_rewrite(self, text: str, is_english: bool) -> str | None:
        """Turn one-word language choices into a full user turn for the LLM."""
        normalized = re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z\u4e00-\u9fff]+", " ", text).lower()).strip()
        token_count = len(normalized.split())
        if token_count > 4:
            return None

        if is_english and any(kw in normalized for kw in self._ENGLISH_KEYWORDS):
            return "I would like to continue in English."
        if not is_english and any(kw in normalized for kw in self._MANDARIN_KEYWORDS):
            return "I would like to continue in Mandarin Chinese."
        return None

    def _detect_language_preference(self, text: str) -> tuple[bool | None, str | None]:
        text_lower = (text or "").strip().lower()
        if any(kw in text_lower for kw in self._MANDARIN_KEYWORDS):
            return False, self._closed_slot_language_rewrite(text, False)
        if any(kw in text_lower for kw in self._ENGLISH_KEYWORDS):
            return True, self._closed_slot_language_rewrite(text, True)
        return None, None

    def _recently_injected_language_choice(self, canonical: str) -> bool:
        """True if `canonical` matches the language turn we last emitted, recently."""
        return (
            bool(canonical)
            and canonical == self._last_injected_canonical
            and (time.monotonic() - self._last_injected_language_at) < self._LANGUAGE_DEDUP_SECS
        )

    async def _emit_language_choice_turn(self, frame, is_english: bool, rewritten_text: str):
        original_text = getattr(frame, "text", "")
        self._record_language_preference(is_english, original_text)
        logger.info(
            f"Language preference interim finalized for LLM: "
            f"{original_text!r} -> {rewritten_text!r}"
        )

        self._last_injected_canonical = rewritten_text
        self._last_injected_language_at = time.monotonic()

        if is_english != self._is_english:
            self._is_english = is_english
            await self._switch_language(is_english)
            await asyncio.sleep(1.0)

        try:
            language_turn = TranscriptionFrame(
                text=rewritten_text,
                user_id=getattr(frame, "user_id", ""),
                timestamp=getattr(frame, "timestamp", _now_iso()),
                language=getattr(frame, "language", None),
            )
        except TypeError:
            language_turn = TranscriptionFrame(
                text=rewritten_text,
                user_id=getattr(frame, "user_id", ""),
                timestamp=getattr(frame, "timestamp", _now_iso()),
            )

        await self.push_frame(language_turn, FrameDirection.DOWNSTREAM)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterimTranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            explicit_language_preference, rewritten_language_turn = self._detect_language_preference(frame.text)
            if explicit_language_preference is not None and rewritten_language_turn:
                if not self._recently_injected_language_choice(rewritten_language_turn):
                    await self._emit_language_choice_turn(
                        frame,
                        explicit_language_preference,
                        rewritten_language_turn,
                    )
                return

        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            switched = False

            # 1. Text-based keyword detection (catches "Mandarin" spoken in English)
            explicit_language_preference, rewritten_language_turn = self._detect_language_preference(frame.text)

            # Suppress the delayed final that trails an interim we already turned
            # into a synthetic turn, and any rapid repeat of the same choice — this
            # is what stops the runaway re-greet loop.
            if rewritten_language_turn and self._recently_injected_language_choice(rewritten_language_turn):
                logger.info(f"Suppressing duplicate language transcript: {frame.text!r}")
                return
            # A genuine non-language turn clears the guard so a later re-selection works.
            if explicit_language_preference is None:
                self._last_injected_canonical = ""

            if explicit_language_preference is False:
                if self._is_english:
                    self._is_english = False
                    await self._switch_language(False)
                    switched = True
            elif explicit_language_preference is True:
                if not self._is_english:
                    self._is_english = True
                    await self._switch_language(True)
                    switched = True
            else:
                # 2. STT language-tag detection for ongoing conversation
                raw = frame.language
                if raw is not None:
                    # frame.language may be a Language enum or a plain string
                    lang_str = raw.value if hasattr(raw, "value") else str(raw)
                    is_english = lang_str.lower().startswith("en")
                    if is_english != self._is_english:
                        self._is_english = is_english
                        await self._switch_language(is_english)
                        switched = True

            if explicit_language_preference is not None:
                self._record_language_preference(explicit_language_preference, frame.text)
                if rewritten_language_turn:
                    logger.info(
                        f"Language preference transcript rewritten for LLM: "
                        f"{frame.text!r} -> {rewritten_language_turn!r}"
                    )
                    frame.text = rewritten_language_turn
                    # Dedup any repeat of this same choice that arrives shortly after.
                    self._last_injected_canonical = rewritten_language_turn
                    self._last_injected_language_at = time.monotonic()

            if switched:
                # ElevenLabs closes and reopens its WebSocket on a voice/language change.
                # Wait for the reconnect before the LLM generates audio, otherwise the
                # first TTS chunk is sent to a reconnecting socket and gets dropped.
                await asyncio.sleep(1.0)

        await self.push_frame(frame, direction)

    def _record_language_preference(self, is_english: bool, transcript: str):
        """Record closed-slot language intent so one-word answers cannot be missed."""
        if self._context is None:
            return

        if is_english:
            msg = (
                "CALLER_LANGUAGE_SELECTION: The caller explicitly selected English. "
                "Treat this as the complete answer to your language preference question. "
                "Do not ask for the language again. In one warm, natural sentence, tell them "
                "you can help with bin collection days, development applications, and what's on "
                "around the council, then ask what they'd like help with. Continue in English."
            )
        else:
            msg = (
                "CALLER_LANGUAGE_SELECTION: The caller explicitly selected Mandarin Chinese (普通话). "
                "Treat this as the complete answer to your language preference question. "
                "Do not ask for the language again. In one warm, natural sentence in simplified "
                "Chinese, tell them you can help with bin collection days (垃圾收集日), development "
                "applications (开发申请), and council events (社区活动), then ask what they'd like help "
                "with. Continue using Mandarin Chinese only."
            )

        self._context.add_message({"role": "system", "content": msg})
        logger.info(
            f"Language preference intent captured → {'en' if is_english else 'zh'} | "
            f"transcript={transcript!r}"
        )

    async def _switch_language(self, is_english: bool):
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

        # Same voice ID for both languages — language= in the WebSocket URL
        # tells ElevenLabs which accent/phoneme set to use.
        # "en" → English accent, "zh" → Mandarin Chinese accent.
        # Higher stability + similarity_boost for Chinese keeps the accent
        # anchored when the model encounters embedded English (street addresses).
        if is_english:
            tts_delta = ElevenLabsTTSService.Settings(
                voice=self._voice_id,
                model=_env("ELEVENLABS_TTS_MODEL", "eleven_turbo_v2_5"),
                language="en",
                speed=_tts_float_env("en", "speed", 1.0),
                stability=_tts_float_env("en", "stability", 0.35),
                similarity_boost=_tts_float_env("en", "similarity_boost", 0.75),
            )
        else:
            tts_delta = ElevenLabsTTSService.Settings(
                voice=self._voice_id,
                model=_env("ELEVENLABS_TTS_MODEL", "eleven_turbo_v2_5"),
                language="zh",
                speed=_tts_float_env("zh", "speed", 0.9),
                stability=_tts_float_env("zh", "stability", 0.55),
                similarity_boost=_tts_float_env("zh", "similarity_boost", 0.85),
            )
        await self.push_frame(
            TTSUpdateSettingsFrame(delta=tts_delta, service=self._tts),
            FrameDirection.DOWNSTREAM,
        )
        if self._filler_tts is not None:
            self._filler_tts.is_english = is_english

        # Inject a system message into the LLM context so the LLM actually
        # responds in the new language — changing TTS voice alone isn't enough.
        if self._context is not None:
            if is_english:
                msg = (
                    "LANGUAGE SWITCH — ACTIVE LANGUAGE IS NOW: ENGLISH. "
                    "You MUST write every word of every response in English from this point forward. "
                    "Do not use any other language."
                )
            else:
                msg = (
                    "LANGUAGE SWITCH — ACTIVE LANGUAGE IS NOW: MANDARIN CHINESE (普通话). "
                    "你必须用中文回答每一个问题，不论用户用什么语言提问。 "
                    "You MUST write every word of every response in Chinese characters from this point forward. "
                    "Zero English words are permitted except street addresses. "
                    "Do NOT write pinyin, romanisation, or mixed-language sentences. "
                    "If you are about to write English, stop and rewrite in Chinese."
                )
            self._context.add_message({"role": "system", "content": msg})

        logger.info(
            f"Language switch → {'en' if is_english else 'zh'} | "
            f"voice={'english' if is_english else 'multilingual'}"
        )


SYSTEM_INSTRUCTION_GRC = (
    "You are a voice agent for Georges River Council. Your name is Maya. "
    "You help residents with three services: bin collection day lookups, "
    "development application inquiries, and upcoming council events. "
    "Speak naturally, warmly, and concisely — one or two sentences at a time. "
    "Never use lists, bullet points, or emojis. "
    "Do not use filler phrases like 'Certainly!' or 'Of course!'. "
    "Never backchannel while the caller is thinking or speaking. "
    "Do NOT say phrases like 'I understand', 'go on', 'take your time', "
    "'I'm listening', 'continue', 'I see', 'waiting for caller to finish', "
    "'silence', or similar acknowledgements/status messages. "
    "If the caller only says a filler sound, hesitation, or asks you to wait, stay completely silent. "
    "Never write or say internal status phrases such as 'silence', 'no response', 'no output', 'None', 'null', or 'N/A'. "

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
    "The bin lookup tool may correct a noisy or misspelled transcript to the closest council address. "
    "If the tool asks you to confirm an address, ask only that confirmation question and wait. "
    "If the resident confirms, call get_bin_collection_day again with confirmed=true and the address value set to the confirmation text. "
    "Only tell the resident the collection days after the confirmation call returns them. "
    "If the resident rejects the address, ask for the corrected full street address. "
    "For bin service FAQ questions (bin types, what goes in each bin, missed collections, "
    "bin placement rules, fees, public holidays, infirm service, bin tags, etc.): "
    "answer directly from the BIN SERVICES KNOWLEDGE BASE embedded below — no tool call needed. "

    # --- Service: Development applications ---
    "For DA inquiries: answer from the knowledge base below. "
    "Direct residents to lodge via the NSW Planning Portal only. "
    "For specific advice, refer them to the Duty Planner on 9330 6400. "

    # --- Service: Events ---
    "For questions about upcoming events, activities, or what's on: "
    "The next 30 days of events are embedded in your context below — answer directly from that list. "
    "If the user asks about events beyond 30 days (e.g. 'anything in July?', 'what about next month?'), "
    "call the get_future_events tool to fetch those. "
    "For a vague or general question (e.g. 'what's on?', 'any events?'), "
    "acknowledge there are events on and ask one short friendly question to narrow it down — "
    "for example: 'We've got quite a few things coming up — are you after something free, "
    "something for the kids, or a particular type of activity?' "
    "Once the user gives an interest, answer immediately from the embedded list or tool result. "
    "If the user's first message already names a specific interest "
    "(e.g. 'any free events?', 'kids activities'), answer immediately — no clarifying question. "
    "Keep your answer brief: name 2-3 matching events with date and any available details. "
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
    "LANGUAGE RULE — ABSOLUTE, NON-NEGOTIABLE: "
    "This service supports EXACTLY TWO languages: English and Mandarin Chinese (普通话). "
    "No other language is permitted under any circumstances — not Cantonese, not any other dialect or language. "
    "At the very start of every call ask the caller which language they prefer: English or Mandarin. "
    "Once the caller chooses, or a system message instructs you to switch, "
    "EVERY single word you produce must be in that language — zero exceptions. "
    "If the active language is Mandarin Chinese (普通话): "
    "  • Write ALL output in simplified Chinese characters (普通话). "
    "  • Do NOT produce any English words, romanisation, or pinyin. "
    "  • The ONLY permitted English is a street address token embedded inside an otherwise fully Chinese sentence "
    "  • If you are about to write English, stop and rewrite in Chinese. "
    "If the active language is English: write ALL output in English only. "
    "Switching languages or mixing languages mid-response is strictly forbidden. "
    "If the caller speaks or asks in any language other than English or Mandarin, "
    "respond in English: 'I'm sorry, this service is only available in English or Mandarin Chinese.' "


    # --- Thinker acceleration (disabled) ---
    # "You may receive a [THINKER_STATE] system message with pre-extracted intent and entities. "
    # "When present, use it to respond faster: "
    # "if intent is bin_collection and address is a real street address (contains a street name and optionally a number), call get_bin_collection_day immediately. "
    # "If no THINKER_STATE is present, proceed as normal. "

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

        return DeepgramSTTService(api_key=_env("DEEPGRAM_API_KEY"))
    elif name == "elevenlabs":
        from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService

        return ElevenLabsRealtimeSTTService(api_key=_env("ELEVENLABS_API_KEY"))
    else:
        raise ValueError(f"Unknown STT service: {name}")


def create_llm(name: str, system_instruction: str = ""):
    """Create an LLM service by name."""
    provider = _llm_provider(name)
    model = _llm_model(provider)
    api_key = _llm_api_key(provider)
    _validate_llm_key(provider, api_key)
    logger.info(f"Creating LLM provider={provider} model={model}")

    if provider == "openai":
        kwargs = {}
        if _env("LLM_BASE_URL") or _env("OPENAI_BASE_URL"):
            kwargs["base_url"] = _env("LLM_BASE_URL") or _env("OPENAI_BASE_URL")
        return OpenAILLMService(
            api_key=api_key,
            settings=OpenAILLMService.Settings(
                model=model,
                system_instruction=system_instruction,
            ),
            **kwargs,
        )
    if provider in {"azure", "azure_openai", "foundry"}:
        base_url = _azure_openai_base_url()
        if not base_url:
            raise RuntimeError(
                "Azure AI Foundry endpoint is not set. "
                "Set AZURE_OPENAI_ENDPOINT or AZURE_AI_FOUNDRY_ENDPOINT."
            )
        return OpenAILLMService(
            api_key=api_key,
            base_url=base_url,
            settings=OpenAILLMService.Settings(
                model=model,
                system_instruction=system_instruction,
            ),
        )
    if provider == "deepseek":
        return OpenAILLMService(
            api_key=api_key,
            base_url=_env("LLM_BASE_URL") or _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            settings=OpenAILLMService.Settings(
                model=model,
                system_instruction=system_instruction,
                extra={
                    "thinking": {
                        "type": _env("DEEPSEEK_THINKING_TYPE", "enabled"),
                        "reasoning_effort": _env("DEEPSEEK_REASONING_EFFORT", "max"),
                    },
                },
            ),
        )
    if provider == "cerebras":
        from pipecat.services.cerebras.llm import CerebrasLLMService, CerebrasLLMSettings

        return CerebrasLLMService(
            api_key=api_key,
            settings=CerebrasLLMSettings(
                model=model,
                system_instruction=system_instruction,
            ),
        )
    if provider == "mistral":
        from pipecat.services.mistral.llm import MistralLLMService

        return MistralLLMService(
            api_key=api_key,
            settings=MistralLLMService.Settings(
                model=model,
                system_instruction=system_instruction,
            ),
        )
    if provider == "groq":
        from pipecat.services.groq.llm import GroqLLMService

        return GroqLLMService(
            api_key=api_key,
            settings=GroqLLMService.Settings(
                model=model,
                system_instruction=system_instruction,
            ),
        )
    raise ValueError(f"Unknown LLM provider: {provider}")


async def _chat_completion_with_token_fallback(client, **kwargs):
    """Call an OpenAI-compatible chat API across models that rename max_tokens."""
    try:
        return await client.chat.completions.create(**kwargs)
    except Exception as exc:
        message = str(exc)
        if "max_tokens" in kwargs and "max_tokens" in message and "max_completion_tokens" in message:
            retry_kwargs = dict(kwargs)
            retry_kwargs["max_completion_tokens"] = retry_kwargs.pop("max_tokens")
            return await client.chat.completions.create(**retry_kwargs)
        raise


def create_tts(name: str):
    """Create a TTS service by name."""
    if name == "elevenlabs":
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

        # Prefer the multilingual voice so TTS can speak any language the LLM
        # generates without requiring a runtime switch.
        voice = (
            _env("ELEVENLABS_MULTILINGUAL_VOICE_ID")
            or _env("ELEVENLABS_VOICE_ID")
        )
        api_key = _env("ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set in the container environment")
        if not voice:
            raise RuntimeError("ELEVENLABS_VOICE_ID or ELEVENLABS_MULTILINGUAL_VOICE_ID is not set")
        return ElevenLabsTTSService(
            api_key=api_key,
            settings=ElevenLabsTTSService.Settings(
                voice=voice,
                model=_env("ELEVENLABS_TTS_MODEL", "eleven_turbo_v2_5"),
                language="en",
                speed=_tts_float_env("en", "speed", 1.0),
                stability=_tts_float_env("en", "stability", 0.35),
                similarity_boost=_tts_float_env("en", "similarity_boost", 0.75),
            ),
        )
    else:
        raise ValueError(f"Unknown TTS service: {name}")


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

pcs_map: Dict[str, SmallWebRTCConnection] = {}
graph_event_queues: Dict[str, asyncio.Queue] = {}
live_call_sessions: Dict[str, dict] = {}
LIVE_CALL_LIMIT = 80
LIVE_TRANSCRIPT_LIMIT = 240
synthetic_test_jobs: Dict[str, dict] = {}
synthetic_audio_segments: Dict[str, list[dict]] = {}
SYNTHETIC_JOB_LIMIT = 40
SYNTHETIC_AUDIO_LIMIT_BYTES = 8_000_000

# Demo mode shared state
demo_events: list[dict] = []      # append-only list of graph events (fan-out to multiple viewers)
demo_pc_id: str | None = None     # presenter's pc_id (None = no active demo)


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="milliseconds") + "Z"


def _live_call_key(provider: str, primary_id: str | None = None, fallback_id: str | None = None) -> str:
    raw_id = primary_id or fallback_id or f"{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
    return f"{provider.lower()}:{raw_id}"


def _trim_live_call_sessions() -> None:
    if len(live_call_sessions) <= LIVE_CALL_LIMIT:
        return
    ordered = sorted(
        live_call_sessions.items(),
        key=lambda item: item[1].get("last_update") or item[1].get("started_at") or "",
        reverse=True,
    )
    keep = {call_id for call_id, _ in ordered[:LIVE_CALL_LIMIT]}
    for call_id in list(live_call_sessions):
        if call_id not in keep:
            live_call_sessions.pop(call_id, None)


def _start_live_call(
    provider: str,
    call_id: str | None = None,
    stream_id: str | None = None,
    caller: str | None = None,
    meta: dict | None = None,
) -> str:
    monitor_id = _live_call_key(provider, call_id, stream_id)
    now = _now_iso()
    session = live_call_sessions.get(monitor_id)
    if not session:
        session = {
            "id": monitor_id,
            "provider": provider,
            "call_id": call_id,
            "stream_id": stream_id,
            "caller": caller or "Phone caller",
            "status": "active",
            "started_at": now,
            "ended_at": None,
            "last_update": now,
            "meta": meta or {},
            "transcript": [],
        }
        live_call_sessions[monitor_id] = session
    else:
        session.update({
            "provider": provider,
            "call_id": call_id or session.get("call_id"),
            "stream_id": stream_id or session.get("stream_id"),
            "caller": caller or session.get("caller") or "Phone caller",
            "status": "active",
            "ended_at": None,
            "last_update": now,
            "meta": {**session.get("meta", {}), **(meta or {})},
        })
    _trim_live_call_sessions()
    logger.info(f"[LIVE_CALLS] Started {provider} session monitor_id={monitor_id}")
    return monitor_id


def _append_live_transcript(monitor_id: str | None, speaker: str, text: str) -> None:
    if not monitor_id or not text:
        return
    now = _now_iso()
    session = live_call_sessions.get(monitor_id)
    if not session:
        session = {
            "id": monitor_id,
            "provider": monitor_id.split(":", 1)[0].title(),
            "call_id": None,
            "stream_id": None,
            "caller": "Phone caller",
            "status": "active",
            "started_at": now,
            "ended_at": None,
            "last_update": now,
            "meta": {},
            "transcript": [],
        }
        live_call_sessions[monitor_id] = session
    session["last_update"] = now
    session["transcript"].append({
        "speaker": speaker,
        "text": text,
        "timestamp": now,
    })
    if len(session["transcript"]) > LIVE_TRANSCRIPT_LIMIT:
        session["transcript"] = session["transcript"][-LIVE_TRANSCRIPT_LIMIT:]


def _end_live_call(monitor_id: str | None) -> None:
    if not monitor_id:
        return
    session = live_call_sessions.get(monitor_id)
    if not session:
        return
    now = _now_iso()
    session["status"] = "ended"
    session["ended_at"] = now
    session["last_update"] = now
    logger.info(f"[LIVE_CALLS] Ended session monitor_id={monitor_id}")

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


def _clean_public_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    match = re.search(r"https?://[^\s|#]+", raw)
    return match.group(0).rstrip("/") if match else raw.rstrip("/")


# Fetched fresh on each /api/offer so credentials are always valid.
ice_servers = [IceServer(urls="stun:stun.l.google.com:19302")]


def _configure_twilio_webhook():
    """Point the Twilio inbound phone number at this server's /twilio/voice webhook."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token  = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    phone_number = os.getenv("TWILIO_PHONE_NUMBER", "").strip()
    public_url  = _clean_public_url(os.getenv("PUBLIC_URL", ""))

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


def _configure_telnyx_webhook():
    """Point the Telnyx Call Control Application at this server's /telnyx/voice webhook."""
    api_key   = os.getenv("TELNYX_API_KEY", "").strip()
    app_id    = os.getenv("TELNYX_CALL_CONTROL_APP_ID", "").strip()
    public_url = _clean_public_url(os.getenv("PUBLIC_URL_TELNYX", ""))

    if not all([api_key, app_id, public_url]):
        logger.info("Telnyx webhook auto-config skipped — TELNYX_API_KEY, TELNYX_CALL_CONTROL_APP_ID, or PUBLIC_URL_TELNYX not set")
        return

    try:
        import urllib.request, urllib.error
        webhook_url = f"{public_url}/telnyx/voice"
        payload = json.dumps({"webhook_event_url": webhook_url, "webhook_event_failover_url": ""}).encode()
        req = urllib.request.Request(
            f"https://api.telnyx.com/v2/call_control_applications/{app_id}",
            data=payload,
            method="PATCH",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                logger.info(f"Telnyx webhook configured: app {app_id} → {webhook_url}")
            else:
                logger.warning(f"Telnyx webhook config returned status {resp.status}")
    except Exception as e:
        logger.error(f"Telnyx webhook auto-config failed: {e}")


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
    # Auto-configure Telnyx Call Control Application webhook.
    _configure_telnyx_webhook()

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


_BIN_STREET_TYPES = {
    "street", "st", "road", "rd", "avenue", "ave", "lane", "ln",
    "drive", "dr", "place", "pl", "court", "ct", "way", "crescent",
    "cres", "close",
}

# Token-based, not exact-phrase, so natural confirmations like "yep that's correct"
# or "yes that's the one" are recognised. Negation always wins over affirmation.
_CONFIRM_YES_TOKENS = {
    "yes", "yeah", "yep", "yup", "correct", "right", "confirmed", "confirm",
    "sure", "ok", "okay", "perfect", "exactly", "definitely", "absolutely",
    "对", "对的", "是", "是的", "正确", "没错",
}
_CONFIRM_NO_TOKENS = {
    "no", "nope", "nah", "incorrect", "wrong", "不", "不是", "不对", "错",
}
# Multi-word negations that wouldn't survive single-token matching.
_CONFIRM_NO_PHRASES = ("not correct", "not right", "not that", "not the")


def _normalize_confirmation_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9'一-鿿]+", " ", text.lower())).strip()


def _is_confirmation_no(text: str) -> bool:
    normalized = _normalize_confirmation_text(text)
    if any(phrase in normalized for phrase in _CONFIRM_NO_PHRASES):
        return True
    return bool(set(normalized.split()) & _CONFIRM_NO_TOKENS)


def _is_confirmation_yes(text: str) -> bool:
    # A negation anywhere ("no, that's wrong") must not read as a yes.
    if _is_confirmation_no(text):
        return False
    normalized = _normalize_confirmation_text(text)
    return bool(set(normalized.split()) & _CONFIRM_YES_TOKENS)


def _looks_like_bin_address(address: str) -> bool:
    words = address.lower().split()
    has_number = any(w[0].isdigit() for w in words)
    has_street_type = bool(_BIN_STREET_TYPES.intersection(words))
    return len(words) >= 2 and (has_number or has_street_type)


def _display_address(address: str) -> str:
    cleaned = re.sub(r"\s+", " ", (address or "").replace(",", " ")).strip()
    return cleaned.title() if cleaned else "that address"


async def _select_bin_address_with_llm(llm, transcript: str, candidates: list[dict]) -> dict | None:
    if not llm or not candidates:
        return None

    max_candidates = max(1, min(int(_env("ADDRESS_LLM_MAX_CANDIDATES", "8")), len(candidates)))
    candidate_lines = []
    for i, candidate in enumerate(candidates[:max_candidates], start=1):
        candidate_lines.append(f"{i}. {candidate.get('address', '')}")

    system = (
        "You select the most likely Georges River Council address from a short candidate list. "
        "The input transcript may contain severe speech-to-text errors, phonetic spellings, "
        "misheard suburb names, or spoken number words. "
        "Choose ONLY from the numbered candidate list. Do not invent an address. "
        "If none are plausible, return index null. "
        "Output ONLY JSON: {\"index\": number|null, \"confidence\": 0.0-1.0, \"reason\": \"short\"}."
    )
    user = (
        f"Caller transcript: {transcript!r}\n\n"
        "Candidate addresses:\n"
        + "\n".join(candidate_lines)
    )

    try:
        timeout = _float_env("ADDRESS_LLM_SELECTION_TIMEOUT_SECS", 4.0)
        response = await asyncio.wait_for(
            _chat_completion_with_token_fallback(
                llm._client,
                model=llm._settings.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=120,
            ),
            timeout=timeout,
        )
        content = (response.choices[0].message.content or "").strip()
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        data = json.loads(match.group(0) if match else content)
        index = data.get("index")
        confidence = float(data.get("confidence") or 0)
        if index is None or confidence < _float_env("ADDRESS_LLM_MIN_CONFIDENCE", 0.55):
            logger.info(
                f"[ADDRESS LLM] No confident selection transcript={transcript!r} "
                f"confidence={confidence:.2f} response={content!r}"
            )
            return None
        index = int(index)
        if not 1 <= index <= max_candidates:
            logger.warning(f"[ADDRESS LLM] Invalid candidate index {index} response={content!r}")
            return None
        selected = candidates[index - 1]
        logger.info(
            f"[ADDRESS LLM] Selected candidate {index}/{max_candidates} "
            f"confidence={confidence:.2f} transcript={transcript!r} "
            f"address={selected.get('address')!r}"
        )
        return selected
    except Exception as e:
        logger.warning(f"[ADDRESS LLM] Selection failed; falling back to scoring: {e}")
        return None


async def _choose_bin_lookup_address(llm, address: str) -> str:
    if _env("ADDRESS_LLM_SELECTION", "true").lower() in {"0", "false", "no", "off"}:
        return address

    try:
        from grc_wastetrack import get_expanded_address_candidates

        candidates = await asyncio.to_thread(
            get_expanded_address_candidates,
            address,
            int(_env("ADDRESS_LLM_MAX_CANDIDATES", "8")),
        )
    except Exception as e:
        logger.warning(f"[ADDRESS LLM] Could not fetch address candidates for {address!r}: {e}")
        return address

    if not candidates:
        return address

    selected = await _select_bin_address_with_llm(llm, address, candidates)
    if selected and selected.get("address"):
        return selected["address"]

    logger.info(
        f"[ADDRESS LLM] Falling back to top scored candidate "
        f"score={candidates[0].get('score', 0):.2f} address={candidates[0].get('address')!r}"
    )
    return candidates[0].get("address") or address


async def _handle_bin_collection_lookup(params: FunctionCallParams, pending_lookup: dict, label: str, llm=None):
    """Lookup bin days, but require caller confirmation before releasing the schedule."""
    tool_t0 = time.perf_counter()
    raw_address = (params.arguments.get("address") or "").strip()
    confirmed = bool(params.arguments.get("confirmed"))

    if pending_lookup:
        if confirmed or _is_confirmation_yes(raw_address):
            pending = pending_lookup.pop("bin_collection", None)
            if pending:
                logger.info(f"[BIN TOOL] {label} confirmed address: {pending['address']}")
                await params.result_callback({"result": pending["voice"]})
                return

        if _is_confirmation_no(raw_address):
            pending_lookup.pop("bin_collection", None)
            logger.info(f"[BIN TOOL] {label} caller rejected guessed address")
            await params.result_callback({
                "result": "Okay, what is the correct full street address?"
            })
            return

    address = _correct_address(raw_address)
    logger.info(f"{label} get_bin_collection_day({address})")

    if not _looks_like_bin_address(address):
        logger.warning(f"[BIN TOOL] Rejected non-address input: '{address}'")
        await params.result_callback({
            "result": "I need a street address to look that up — could you tell me your street address?"
        })
        return

    try:
        from grc_wastetrack import get_bin_collection_details as _wt, format_voice_response as _wt_fmt

        lookup_address = await _choose_bin_lookup_address(llm, address)
        if lookup_address != address:
            logger.info(f"[BIN TOOL] Address selected for lookup: {address!r} -> {lookup_address!r}")
            address = lookup_address

        wt_t0 = time.perf_counter()
        wt_result = await asyncio.to_thread(_wt, address)
        logger.info(f"[BIN TOOL] Wastetrack elapsed {((time.perf_counter() - wt_t0) * 1000):.0f} ms")
        voice = _wt_fmt(wt_result)
        if voice:
            matched_address = _display_address(
                wt_result.get("matched_address") or wt_result.get("address") or address
            )
            pending_lookup["bin_collection"] = {
                "address": matched_address,
                "voice": voice,
                "result": wt_result,
            }
            logger.info(
                f"[BIN TOOL] Wastetrack SUCCESS; awaiting caller confirmation "
                f"query={address!r} matched={matched_address!r} "
                f"total={((time.perf_counter() - tool_t0) * 1000):.0f} ms"
            )
            await params.result_callback({
                "result": f"I found {matched_address}. Is that the correct address?"
            })
            return

        logger.warning(
            f"[BIN TOOL] Wastetrack failed after {((time.perf_counter() - tool_t0) * 1000):.0f} ms: "
            f"{wt_result.get('error')}"
        )
        await params.result_callback({
            "result": (
                "I couldn't find a bin collection record for that address in the council bin lookup. "
                "Could you please repeat the full street address?"
            )
        })
    except Exception as e:
        logger.error(f"{label} get_bin_collection_day failed: {e}")
        await params.result_callback(
            {"error": "I couldn't look up the bin collection day. Please try again."}
        )


# ---------------------------------------------------------------------------
# Graph highlight observer
# ---------------------------------------------------------------------------


class TranscriptionObserver(BaseObserver):
    """Watches STT and LLM frames to emit transcript events to the frontend.

    Pushes 'user_transcription' events on final TranscriptionFrames and
    'bot_transcription' events when the LLM finishes a full response.
    Also logs transcript text so phone-call debugging works from Azure logs.
    """

    def __init__(self, event_queue=None, monitor_id: str | None = None):
        super().__init__()
        self._event_queue = event_queue
        self._monitor_id = monitor_id
        self._bot_buffer = ""
        self._seen_transcription_frame_ids: set[int] = set()
        self._recent_transcripts: dict[str, tuple[str, float]] = {}

    def _is_duplicate_transcript(self, speaker: str, text: str, frame: Frame | None = None) -> bool:
        if frame is not None:
            frame_id = id(frame)
            if frame_id in self._seen_transcription_frame_ids:
                return True
            self._seen_transcription_frame_ids.add(frame_id)
            if len(self._seen_transcription_frame_ids) > 500:
                self._seen_transcription_frame_ids = set(list(self._seen_transcription_frame_ids)[-250:])

        now = time.monotonic()
        normalized = re.sub(r"\s+", " ", text.lower()).strip()
        last_text, last_time = self._recent_transcripts.get(speaker, ("", 0.0))
        if normalized == last_text and (now - last_time) < 2.5:
            return True
        self._recent_transcripts[speaker] = (normalized, now)
        return False

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if data.direction != FrameDirection.DOWNSTREAM:
            return

        if isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text and self._is_duplicate_transcript("caller", text, frame):
                return
            if text:
                logger.info(f"[CALL_TRANSCRIPT] Caller: {text}")
                _append_live_transcript(self._monitor_id, "caller", text)
                if self._event_queue:
                    await _put_graph_event(
                        self._event_queue, {"type": "user_transcription", "text": text}
                    )

        elif isinstance(frame, LLMTextFrame) and isinstance(data.source, LLMService):
            self._bot_buffer += frame.text

        elif isinstance(frame, LLMFullResponseEndFrame):
            text = self._bot_buffer.strip()
            self._bot_buffer = ""
            if text:
                logger.info(f"[CALL_TRANSCRIPT] Agent: {text}")
                _append_live_transcript(self._monitor_id, "agent", text)
                if self._event_queue:
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

    Tool calls (e.g. direct council bin lookup) sit inside the LLM interval and are flagged
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
    system_instruction = (
        SYSTEM_INSTRUCTION_GRC
        + "\n\n"
        + WEB_INTERFACE_SYSTEM_OVERRIDE
        + "\n\n"
        + _events_block
    )
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

    # thinker_llm = create_llm(_env("LLM_PROVIDER"))
    # thinker_processor = ThinkerProcessor(thinker_llm=thinker_llm)
    # context_enricher = ContextEnricherProcessor(thinker_processor=thinker_processor)
    filler_tts = FillerTTSProcessor()

    # Always attach transcript logging; frontend events are emitted when a queue exists.
    pc_id = webrtc_connection.pc_id
    event_queue = graph_event_queues.get(pc_id)
    monitor_id = _start_live_call("WebRTC", call_id=pc_id, caller="Web caller")
    latency_observer = LatencyObserver()
    transcript_observer = TranscriptionObserver(event_queue, monitor_id=monitor_id)
    observers = [latency_observer, transcript_observer]
    pending_bin_lookup = {}

    async def handle_get_bin_collection_day(params: FunctionCallParams):
        await _handle_bin_collection_lookup(params, pending_bin_lookup, "[WebRTC]", llm)

    # Register GRC tools on the LLM and build schema
    llm.register_function("get_bin_collection_day", handle_get_bin_collection_day)

    async def transfer_to_human_webrtc() -> str:
        logger.info("[TRANSFER] WebRTC transfer requested; returning council phone fallback")
        return "I can't transfer you through the web interface, but you can reach a council officer directly on 9330 6400."

    async def handle_transfer_to_human_webrtc(params: FunctionCallParams):
        await params.result_callback({"result": await transfer_to_human_webrtc()})

    llm.register_function("transfer_to_human", handle_transfer_to_human_webrtc)

    async def handle_get_future_events(params: FunctionCallParams):
        result = await asyncio.to_thread(get_future_events, 30)
        await params.result_callback({"result": result})

    llm.register_function("get_future_events", handle_get_future_events)

    get_bin_collection_day_schema = FunctionSchema(
        name="get_bin_collection_day",
        description=(
            "Look up the bin collection day for a resident's address using the direct council bin lookup. "
            "Only call this tool once the resident has provided a specific street address "
            "Do NOT call this tool if you only have a vague question — ask for the address first."
        ),
        properties={
            "address": {
                "type": "string",
                "description": (
                    "Full street address within the Georges River LGA for the direct council bin lookup, "
                    "Must be a real address, not a question or vague phrase. "
                    "When confirming a pending matched address, this can be the user's confirmation text."
                ),
            },
            "confirmed": {
                "type": "boolean",
                "description": (
                    "Set true only when the previous tool result asked the resident to confirm a matched address "
                    "and the resident has just confirmed it."
                ),
            },
        },
        required=["address"],
    )
    transfer_to_human_schema = FunctionSchema(
        name="transfer_to_human",
        description=(
            "Use this in the web interface when the user asks for a human, person, agent, "
            "operator, transfer, or escalation. This does not perform a real transfer; "
            "it returns council phone fallback details."
        ),
        properties={},
        required=[],
    )
    get_future_events_schema = FunctionSchema(
        name="get_future_events",
        description=(
            "Fetch GRC events beyond the next 30 days. Call this when the user asks about "
            "events further in the future (e.g. 'anything in July?', 'what about next month?'). "
            "Do NOT call this for events within the next 30 days — those are already in your context."
        ),
        properties={},
        required=[],
    )
    tools = ToolsSchema(standard_tools=[
        get_bin_collection_day_schema,
        transfer_to_human_schema,
        get_future_events_schema,
    ])

    context = LLMContext(tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=create_vad_analyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[
                    MinWordsUserTurnStartStrategy(min_words=1, use_interim=True),
                ],
            ),
            user_mute_strategies=[MuteUntilFirstBotCompleteUserMuteStrategy()],
        ),
    )
    lang_switch = LanguageSwitchProcessor(
        tts=tts,
        voice_id=_env("ELEVENLABS_VOICE_ID"),
        filler_tts=filler_tts,
        context=context,
    )
    transfer_processor = TransferRequestProcessor(
        on_transfer_request=transfer_to_human_webrtc,
    )
    hesitation_gate = HesitationTurnGateProcessor()
    backchannel_suppressor = BackchannelSuppressorProcessor()

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            transfer_processor,
            # thinker_processor,  # disabled
            lang_switch,
            hesitation_gate,
            user_aggregator,
            # context_enricher,   # disabled
            llm,
            backchannel_suppressor,
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
            "role": "user",
            "content": (
                "Greet the caller and ask for their language preference. Say exactly: "
                "'Hi, I'm Maya from Georges River Council — would you like to continue in English or Mandarin?'"
            ),
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        _end_live_call(monitor_id)
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


@app.get("/address-test", response_class=HTMLResponse)
async def address_test():
    html_path = os.path.join(os.path.dirname(__file__), "static", "address-test.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())


@app.post("/api/debug/address-lookup")
async def debug_address_lookup(request: Request):
    payload = await request.json()
    raw_address = (payload.get("address") or "").strip()
    if not raw_address:
        return Response(status_code=400, content="Missing address")

    corrected_address = _correct_address(raw_address)
    try:
        from grc_wastetrack import (
            get_address_candidates,
            get_expanded_address_candidates,
            get_bin_collection_details as _wt,
            format_voice_response as _wt_fmt,
        )

        candidates = await asyncio.to_thread(get_address_candidates, corrected_address, 12)
        expanded_candidates = await asyncio.to_thread(get_expanded_address_candidates, corrected_address, 12)
        resolved_address = corrected_address
        result = await asyncio.to_thread(_wt, resolved_address)
        if not result.get("success") and expanded_candidates:
            resolved_address = expanded_candidates[0].get("address") or corrected_address
            result = await asyncio.to_thread(_wt, resolved_address)
        return {
            "input": raw_address,
            "corrected_input": corrected_address,
            "resolved_input": resolved_address,
            "candidates": candidates,
            "expanded_candidates": expanded_candidates,
            "lookup": result,
            "voice_response": _wt_fmt(result),
        }
    except Exception as e:
        logger.error(f"[ADDRESS TEST] lookup failed for {corrected_address!r}: {e}")
        return {
            "input": raw_address,
            "corrected_input": corrected_address,
            "candidates": [],
            "expanded_candidates": [],
            "lookup": {"success": False, "error": str(e), "address_query": corrected_address},
            "voice_response": None,
        }


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
    llm_name = request.get("llm") or _env("LLM_PROVIDER")
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


@app.get("/api/live-calls")
async def live_calls():
    """Return active/recent phone and WebRTC sessions with live transcript lines."""
    calls = sorted(
        live_call_sessions.values(),
        key=lambda call: call.get("last_update") or call.get("started_at") or "",
        reverse=True,
    )
    active_count = sum(1 for call in calls if call.get("status") == "active")
    return {
        "active_count": active_count,
        "call_count": len(calls),
        "calls": calls,
        "server_time": _now_iso(),
    }


def _synthetic_scenario_path() -> Path:
    return Path(__file__).parent / "voice_tests" / "scenarios" / "grc_smoke.json"


def _load_synthetic_scenarios() -> dict:
    path = _synthetic_scenario_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[SYNTHETIC] Failed to load scenarios from {path}: {e}")
        data = {"name": "grc_smoke", "scenarios": []}
    return data


def _trim_synthetic_jobs() -> None:
    if len(synthetic_test_jobs) <= SYNTHETIC_JOB_LIMIT:
        return
    ordered = sorted(
        synthetic_test_jobs.items(),
        key=lambda item: item[1].get("updated_at") or item[1].get("created_at") or "",
        reverse=True,
    )
    keep = {job_id for job_id, _ in ordered[:SYNTHETIC_JOB_LIMIT]}
    for job_id in list(synthetic_test_jobs):
        if job_id not in keep:
            synthetic_test_jobs.pop(job_id, None)
            synthetic_audio_segments.pop(job_id, None)


def _append_synthetic_audio(job_id: str, speaker: str, pcm: bytes) -> None:
    if not pcm:
        return
    segments = synthetic_audio_segments.setdefault(job_id, [])
    segments.append({
        "speaker": speaker,
        "pcm": pcm,
        "created_at": _now_iso(),
    })
    total = sum(len(item.get("pcm") or b"") for item in segments)
    while total > SYNTHETIC_AUDIO_LIMIT_BYTES and segments:
        removed = segments.pop(0)
        total -= len(removed.get("pcm") or b"")


def _wav_from_pcm16(pcm: bytes, sample_rate: int = 16000) -> bytes:
    data_size = len(pcm)
    byte_rate = sample_rate * 2
    block_align = 2
    return b"".join([
        b"RIFF",
        struct.pack("<I", 36 + data_size),
        b"WAVE",
        b"fmt ",
        struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, byte_rate, block_align, 16),
        b"data",
        struct.pack("<I", data_size),
        pcm,
    ])


def _synthetic_public_target(request: Request) -> str:
    configured = _env("VOICE_TEST_TARGET")
    if configured:
        return configured.rstrip("/")
    port = _env("PORT", "8080").strip() or "8080"
    return f"http://127.0.0.1:{port}"


def _synthetic_args(payload: dict, target: str):
    from types import SimpleNamespace

    options = payload.get("options") or {}
    provider = _llm_provider(_env("LLM_PROVIDER"))
    llm_base_url = (
        _env("OPENAI_BASE_URL")
        or _env("LLM_BASE_URL")
        or (_azure_openai_base_url() if provider in {"azure", "azure_openai", "foundry"} else "")
    )
    llm_api_key = _llm_api_key(provider)
    llm_model = _llm_model(provider)
    return SimpleNamespace(
        target=target,
        scenarios=str(_synthetic_scenario_path()),
        only=None,
        report_dir=str(Path(__file__).parent / "voice_tests" / "reports"),
        audio_cache=str(Path(__file__).parent / "voice_tests" / "audio_cache"),
        elevenlabs_api_key=_env("ELEVENLABS_API_KEY"),
        voice_id=(
            options.get("voice_id")
            or _env("VOICE_TEST_ELEVENLABS_VOICE_ID")
            or _env("ELEVENLABS_VOICE_ID")
        ),
        elevenlabs_model=options.get("elevenlabs_model") or _env("VOICE_TEST_ELEVENLABS_MODEL", "eleven_turbo_v2_5"),
        force_audio=bool(options.get("force_audio", False)),
        speed=float(options.get("speed", _env("VOICE_TEST_SPEED", "1.0"))),
        stability=float(options.get("stability", _env("VOICE_TEST_STABILITY", "0.45"))),
        similarity_boost=float(options.get("similarity_boost", _env("VOICE_TEST_SIMILARITY_BOOST", "0.75"))),
        gain=float(options.get("gain", _env("VOICE_TEST_GAIN", "1.0"))),
        noise=float(options.get("noise", _env("VOICE_TEST_NOISE", "0.0"))),
        background_voice=options.get("background_voice", _env("VOICE_TEST_BACKGROUND_VOICE", "")),
        background_gain=float(options.get("background_gain", _env("VOICE_TEST_BACKGROUND_GAIN", "0.25"))),
        pre_silence=float(options.get("pre_silence", _env("VOICE_TEST_PRE_SILENCE", "0.25"))),
        post_silence=float(options.get("post_silence", _env("VOICE_TEST_POST_SILENCE", "0.45"))),
        between_utterances=float(options.get("between_utterances", _env("VOICE_TEST_BETWEEN_UTTERANCES", "1.2"))),
        wait_for_greeting=bool(options.get("wait_for_greeting", True)),
        greeting_timeout=float(options.get("greeting_timeout", _env("VOICE_TEST_GREETING_TIMEOUT", "8"))),
        greeting_quiet_secs=float(options.get("greeting_quiet_secs", _env("VOICE_TEST_GREETING_QUIET_SECS", "0.9"))),
        listen_secs=float(options.get("listen_secs", _env("VOICE_TEST_LISTEN_SECS", "14"))),
        quiet_secs=float(options.get("quiet_secs", _env("VOICE_TEST_QUIET_SECS", "2.0"))),
        evaluate=bool(payload.get("evaluate", False)),
        openai_api_key=llm_api_key,
        openai_base_url=llm_base_url,
        evaluator_model=options.get("evaluator_model") or _env("VOICE_TEST_EVALUATOR_MODEL", llm_model),
        resident_model=options.get("resident_model") or _env("VOICE_TEST_RESIDENT_MODEL", _env("VOICE_TEST_EVALUATOR_MODEL", llm_model)),
        resident_persona=options.get("resident_persona") or _env(
            "VOICE_TEST_RESIDENT_PERSONA",
            "A realistic Georges River Council resident who wants clear help and gives concise answers.",
        ),
        resident_goal=options.get("resident_goal") or _env(
            "VOICE_TEST_RESIDENT_GOAL",
            "Choose English, ask for bin collection help, provide 50 Warraba Street Hurstville, confirm the address, and check the answer.",
        ),
        autonomous_turns=int(options.get("autonomous_turns", _env("VOICE_TEST_AUTONOMOUS_TURNS", "6"))),
        autonomous_agent_timeout=float(options.get("autonomous_agent_timeout", _env("VOICE_TEST_AUTONOMOUS_AGENT_TIMEOUT", "18"))),
        agent_stt_model=options.get("agent_stt_model") or _env("VOICE_TEST_AGENT_STT_MODEL", "scribe_v2"),
    )


async def _run_synthetic_job(job_id: str, scenarios: list[dict], target: str, payload: dict):
    job = synthetic_test_jobs[job_id]
    try:
        from dataclasses import asdict
        from voice_tests.run_voice_tests import normalize_target, run_scenario

        args = _synthetic_args(payload, target)
        if not args.elevenlabs_api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set")
        if not args.voice_id:
            raise RuntimeError("ELEVENLABS_VOICE_ID or VOICE_TEST_ELEVENLABS_VOICE_ID is not set")

        synthetic_audio_segments[job_id] = []

        def on_audio_segment(speaker: str, pcm: bytes) -> None:
            _append_synthetic_audio(job_id, speaker, pcm)

        args.on_audio_segment = on_audio_segment

        ws_url, http_base = normalize_target(target)
        job.update({
            "status": "running",
            "started_at": _now_iso(),
            "updated_at": _now_iso(),
            "ws_url": ws_url,
            "http_base": http_base,
        })

        for index, scenario in enumerate(scenarios):
            def on_call_started(call_id: str, monitor_id: str, started_scenario: dict, *, current_index=index) -> None:
                job.update({
                    "current_call_id": call_id,
                    "current_monitor_id": monitor_id,
                    "current_scenario_id": started_scenario.get("id"),
                    "current_index": current_index,
                    "updated_at": _now_iso(),
                })

            args.on_call_started = on_call_started
            job["current_index"] = index
            job["current_id"] = scenario.get("id")
            job["current_call_id"] = None
            job["current_monitor_id"] = None
            job["current_scenario_id"] = scenario.get("id")
            job["updated_at"] = _now_iso()
            result = await run_scenario(ws_url, http_base, scenario, args)
            job["results"].append(asdict(result))
            job["last_call_id"] = result.call_id
            job["last_monitor_id"] = job.get("current_monitor_id")
            job["audio_available"] = bool(synthetic_audio_segments.get(job_id))
            job["updated_at"] = _now_iso()

        passed = sum(1 for result in job["results"] if result.get("status") == "PASS")
        job.update({
            "status": "complete",
            "completed_at": _now_iso(),
            "updated_at": _now_iso(),
            "audio_available": bool(synthetic_audio_segments.get(job_id)),
            "summary": {"passed": passed, "total": len(job["results"])},
        })
    except asyncio.CancelledError:
        job.update({"status": "cancelled", "updated_at": _now_iso()})
        raise
    except Exception as e:
        logger.error(f"[SYNTHETIC] Job {job_id} failed: {e}")
        job.update({
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "updated_at": _now_iso(),
        })


@app.get("/api/synthetic/scenarios")
async def synthetic_scenarios():
    data = _load_synthetic_scenarios()
    return {
        "suite": data.get("name", "grc_smoke"),
        "description": data.get("description", ""),
        "scenarios": data.get("scenarios", []),
    }


@app.get("/api/synthetic/jobs")
async def synthetic_jobs():
    jobs = sorted(
        synthetic_test_jobs.values(),
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )
    return {"jobs": jobs}


@app.get("/api/synthetic/jobs/{job_id}")
async def synthetic_job(job_id: str):
    job = synthetic_test_jobs.get(job_id)
    if not job:
        return Response(status_code=404, content="Synthetic test job not found")
    job["audio_available"] = bool(synthetic_audio_segments.get(job_id))
    return job


@app.get("/api/synthetic/jobs/{job_id}/audio.wav")
async def synthetic_job_audio(job_id: str):
    if job_id not in synthetic_test_jobs:
        return Response(status_code=404, content="Synthetic test job not found")
    segments = synthetic_audio_segments.get(job_id) or []
    if not segments:
        return Response(status_code=404, content="Synthetic call audio is not available yet")
    pcm = b"".join(item.get("pcm") or b"" for item in segments)
    headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'inline; filename="{job_id}.wav"',
    }
    return Response(content=_wav_from_pcm16(pcm), media_type="audio/wav", headers=headers)


@app.post("/api/synthetic/run")
async def synthetic_run(request: Request):
    payload = await request.json()
    scenario_ids = set(payload.get("scenario_ids") or [])
    custom_scenarios = payload.get("scenarios") or []
    data = _load_synthetic_scenarios()
    scenarios = custom_scenarios or [
        scenario for scenario in data.get("scenarios", [])
        if not scenario_ids or scenario.get("id") in scenario_ids
    ]
    if not scenarios:
        return Response(status_code=400, content="No synthetic scenarios selected")

    job_id = f"synthetic-{uuid.uuid4().hex[:10]}"
    target = (payload.get("target") or _synthetic_public_target(request)).rstrip("/")
    now = _now_iso()
    synthetic_test_jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "target": target,
        "evaluate": bool(payload.get("evaluate", False)),
        "total": len(scenarios),
        "current_index": None,
        "current_id": None,
        "results": [],
        "audio_available": False,
        "summary": {"passed": 0, "total": len(scenarios)},
    }
    _trim_synthetic_jobs()
    asyncio.create_task(_run_synthetic_job(job_id, scenarios, target, payload))
    return synthetic_test_jobs[job_id]


@app.post("/api/synthetic/generate")
async def synthetic_generate(request: Request):
    payload = await request.json()
    prompt = (payload.get("prompt") or "").strip()
    count = int(payload.get("count") or 3)
    if not prompt:
        return Response(status_code=400, content="Missing prompt")

    system = (
        "Generate synthetic phone-call test scenarios for the Georges River Council voice agent. "
        "The agent handles bin collection lookups, DA questions, events, language selection, "
        "turn-taking, noisy calls, short answers, and address correction. "
        "Return JSON only with key scenarios. Each scenario must have id, description, utterances array, "
        "and expectations array. Use concise utterances suitable for ElevenLabs text-to-speech."
    )
    try:
        llm = create_llm(_env("LLM_PROVIDER"), system_instruction=system)
        resp = await _chat_completion_with_token_fallback(
            llm._client,
            model=llm._settings.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"Create {count} scenarios for: {prompt}"},
            ],
            max_tokens=1200,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
        scenarios = data.get("scenarios") or []
        for i, scenario in enumerate(scenarios):
            scenario.setdefault("id", f"generated_{i + 1}")
            scenario.setdefault("description", prompt)
            scenario.setdefault("utterances", [])
            scenario.setdefault("expectations", [])
        return {"scenarios": scenarios[: max(1, min(count, 20))]}
    except Exception as e:
        logger.error(f"[SYNTHETIC] Scenario generation failed: {e}")
        return Response(status_code=500, content=f"Scenario generation failed: {e}")


@app.post("/api/translate")
async def translate_text(request: Request):
    """Translate Chinese text to English using the configured LLM provider."""
    try:
        data = await request.json()
    except Exception:
        return Response(status_code=400, content="Invalid JSON")
    text = (data.get("text") or "").strip()
    if not text:
        return {"translation": ""}
    try:
        llm = create_llm(_env("LLM_PROVIDER"))
        resp = await _chat_completion_with_token_fallback(
            llm._client,
            model=llm._settings.model,
            messages=[
                {"role": "system", "content": "Translate the following Chinese text to English. Output only the English translation, nothing else."},
                {"role": "user", "content": text},
            ],
            max_tokens=512,
        )
        translation = resp.choices[0].message.content.strip()
        return {"translation": translation}
    except Exception as e:
        logger.warning(f"[translate] failed: {e}")
        return {"translation": ""}


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
    monitor_id = _start_live_call(
        "Twilio",
        call_id=call_sid,
        stream_id=stream_sid,
        meta={"stream_sid": stream_sid, "call_sid": call_sid},
    )

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
    logger.info(f"[Twilio] LLM env provider={_env('LLM_PROVIDER')} model={_llm_model(_llm_provider())}")

    stt = create_stt("elevenlabs")
    llm = create_llm(_env("LLM_PROVIDER"), system_instruction=system_instruction)
    tts = create_tts("elevenlabs")

    # thinker_llm = create_llm(_env("LLM_PROVIDER"))
    # thinker_processor = ThinkerProcessor(thinker_llm=thinker_llm)
    # context_enricher = ContextEnricherProcessor(thinker_processor=thinker_processor)
    filler_tts = FillerTTSProcessor()
    pending_bin_lookup = {}

    # Register GRC tools
    async def handle_get_bin_collection_day(params: FunctionCallParams):
        await _handle_bin_collection_lookup(params, pending_bin_lookup, "[Twilio]", llm)

    llm.register_function("get_bin_collection_day", handle_get_bin_collection_day)

    direct_transfer_in_progress = False

    async def transfer_twilio_call_to_human() -> str | None:
        nonlocal direct_transfer_in_progress
        if direct_transfer_in_progress:
            return None
        direct_transfer_in_progress = True

        transfer_number = os.getenv("TRANSFER_PHONE_NUMBER", "").strip()
        if not transfer_number:
            direct_transfer_in_progress = False
            return "I'm sorry, transfer is not available right now. Please call us on 9330 6400."

        try:
            from twilio.rest import Client as TwilioClient
            logger.info(f"[TRANSFER] Requesting Twilio redirect for call_sid={call_sid} to {transfer_number}")
            tw = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
            updated_call = await asyncio.wait_for(
                asyncio.to_thread(
                    tw.calls(call_sid).update,
                    twiml=(
                        "<Response>"
                        f"<Dial>{transfer_number}</Dial>"
                        "</Response>"
                    ),
                ),
                timeout=10,
            )
            logger.info(
                f"[TRANSFER] Direct call {call_sid} redirected to {transfer_number}; "
                f"Twilio status={getattr(updated_call, 'status', 'unknown')}"
            )
            return None
        except asyncio.TimeoutError:
            logger.error("[TRANSFER] Direct transfer timed out while calling Twilio")
            direct_transfer_in_progress = False
            return "I wasn't able to transfer the call. Please call us directly on 9330 6400."
        except Exception as e:
            logger.error(f"[TRANSFER] Direct transfer failed: {e}")
            direct_transfer_in_progress = False
            return "I wasn't able to transfer the call. Please call us directly on 9330 6400."

    async def handle_transfer_to_human(params: FunctionCallParams):
        """Transfer the call to a human agent via Twilio REST API."""
        result = await transfer_twilio_call_to_human()
        await params.result_callback({"result": result or "Transferring you now. Please hold."})
        return

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

    async def handle_get_future_events_twilio(params: FunctionCallParams):
        result = await asyncio.to_thread(get_future_events, 30)
        await params.result_callback({"result": result})

    llm.register_function("get_future_events", handle_get_future_events_twilio)

    get_bin_collection_day_schema = FunctionSchema(
        name="get_bin_collection_day",
        description=(
            "Look up the bin collection day for a resident's address using the direct council bin lookup. "
            "Only call this tool once the resident has provided a specific street address "
            "Do NOT call this tool if you only have a vague question — ask for the address first."
        ),
        properties={
            "address": {
                "type": "string",
                "description": (
                    "Full street address within the Georges River LGA for the direct council bin lookup, "
                    "Must be a real address, not a question or vague phrase. "
                    "When confirming a pending matched address, this can be the user's confirmation text."
                ),
            },
            "confirmed": {
                "type": "boolean",
                "description": (
                    "Set true only when the previous tool result asked the resident to confirm a matched address "
                    "and the resident has just confirmed it."
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
    get_future_events_schema = FunctionSchema(
        name="get_future_events",
        description=(
            "Fetch GRC events beyond the next 30 days. Call this when the user asks about "
            "events further in the future (e.g. 'anything in July?', 'what about next month?'). "
            "Do NOT call this for events within the next 30 days — those are already in your context."
        ),
        properties={},
        required=[],
    )
    context = LLMContext(
        tools=ToolsSchema(standard_tools=[
            get_bin_collection_day_schema,
            transfer_to_human_schema,
            get_future_events_schema,
        ])
    )

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=create_vad_analyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[
                    MinWordsUserTurnStartStrategy(min_words=1, use_interim=True),
                ],
            ),
            user_mute_strategies=[MuteUntilFirstBotCompleteUserMuteStrategy()],
        ),
    )
    lang_switch = LanguageSwitchProcessor(
        tts=tts,
        voice_id=_env("ELEVENLABS_VOICE_ID"),
        filler_tts=filler_tts,
        context=context,
    )
    transfer_processor = TransferRequestProcessor(
        on_transfer_request=transfer_twilio_call_to_human,
        immediate_message="Transferring you now. Please hold.",
    )
    hesitation_gate = HesitationTurnGateProcessor()
    backchannel_suppressor = BackchannelSuppressorProcessor()

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            transfer_processor,
            # thinker_processor,  # disabled
            lang_switch,
            hesitation_gate,
            user_aggregator,
            # context_enricher,   # disabled
            llm,
            backchannel_suppressor,
            filler_tts,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[LatencyObserver(), TranscriptionObserver(monitor_id=monitor_id)],
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Twilio client connected")
        context.add_message({
            "role": "user",
            "content": (
                "Greet the caller and ask for their language preference. Say exactly: "
                "'Hi, I'm Maya from Georges River Council — would you like to continue in English or Mandarin?'"
            ),
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Twilio client disconnected")
        _end_live_call(monitor_id)
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
# Telnyx phone integration
# ---------------------------------------------------------------------------


async def run_telnyx_bot(websocket: WebSocket):
    """Run the GRC bot over a Telnyx Call Control media-streaming WebSocket.

    The call is answered (and streaming started) by the /telnyx/voice webhook via
    the Call Control REST API. Telnyx then connects to this WS using its native
    streaming protocol (stream_id, start.call_control_id, media_format), so we use
    TelnyxFrameSerializer.
    """
    await websocket.accept()

    stream_id = None
    call_control_id = None
    outbound_encoding = "PCMU"

    async for raw in websocket.iter_text():
        msg = json.loads(raw)
        event = msg.get("event")
        if event == "connected":
            continue
        if event == "start":
            stream_id = msg.get("stream_id")
            start = msg.get("start", {})
            call_control_id = start.get("call_control_id")
            enc = (start.get("media_format", {}).get("encoding") or "PCMU").upper()
            outbound_encoding = "PCMA" if ("PCMA" in enc or "ALAW" in enc) else "PCMU"
            logger.info(f"Telnyx call started — stream_id={stream_id} call_control_id={call_control_id} enc={outbound_encoding}")
            break
        if event == "stop":
            logger.info("Telnyx call stopped before start event")
            return

    if not stream_id:
        logger.warning("No Telnyx start event received")
        return
    monitor_id = _start_live_call(
        "Telnyx",
        call_id=call_control_id,
        stream_id=stream_id,
        meta={
            "stream_id": stream_id,
            "call_control_id": call_control_id,
            "outbound_encoding": outbound_encoding,
        },
    )

    serializer = TelnyxFrameSerializer(
        stream_id=stream_id,
        call_control_id=call_control_id,
        outbound_encoding=outbound_encoding,
        inbound_encoding="PCMU",
        api_key=os.getenv("TELNYX_API_KEY"),
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # Larger 160ms output frames (default 4 = 40ms). Choppiness scaled
            # monotonically with frame size on the Telnyx RTP leg: 20ms worse,
            # 40ms baseline, 80ms better — so we push further for jitter tolerance.
            # Buffering cost is negligible; barge-in still flushes the queue.
            audio_out_10ms_chunks=16,
            serializer=serializer,
        ),
    )

    _events = await asyncio.to_thread(get_events)
    _events_block = format_events_for_system_prompt(_events)
    system_instruction = SYSTEM_INSTRUCTION_GRC + "\n\n" + _events_block
    logger.info(f"[Telnyx CAG] Embedded {len(_events)} events into system instruction")
    logger.info(f"[Telnyx] LLM env provider={_env('LLM_PROVIDER')} model={_llm_model(_llm_provider())}")

    stt = create_stt("elevenlabs")
    llm = create_llm(_env("LLM_PROVIDER"), system_instruction=system_instruction)
    tts = create_tts("elevenlabs")
    filler_tts = FillerTTSProcessor()
    pending_bin_lookup = {}

    async def handle_get_bin_collection_day_telnyx(params: FunctionCallParams):
        await _handle_bin_collection_lookup(params, pending_bin_lookup, "[Telnyx]", llm)

    llm.register_function("get_bin_collection_day", handle_get_bin_collection_day_telnyx)

    telnyx_transfer_in_progress = False

    async def transfer_telnyx_call_to_human() -> str | None:
        nonlocal telnyx_transfer_in_progress
        if telnyx_transfer_in_progress:
            return None
        telnyx_transfer_in_progress = True
        transfer_number = os.getenv("TRANSFER_PHONE_NUMBER", "").strip()
        if not transfer_number:
            telnyx_transfer_in_progress = False
            return "I'm sorry, transfer is not available right now. Please call us on 9330 6400."
        try:
            import aiohttp
            logger.info(f"[Telnyx TRANSFER] Redirecting call_control_id={call_control_id} to {transfer_number}")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/transfer",
                    json={"to": transfer_number},
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {os.getenv('TELNYX_API_KEY')}",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status in (200, 202):
                        logger.info(f"[Telnyx TRANSFER] Success — status {resp.status}")
                        return None
                    else:
                        text = await resp.text()
                        logger.error(f"[Telnyx TRANSFER] Failed: {resp.status} {text}")
                        telnyx_transfer_in_progress = False
                        return "I wasn't able to transfer the call. Please call us directly on 9330 6400."
        except Exception as e:
            logger.error(f"[Telnyx TRANSFER] Exception: {e}")
            telnyx_transfer_in_progress = False
            return "I wasn't able to transfer the call. Please call us directly on 9330 6400."

    async def handle_transfer_to_human_telnyx(params: FunctionCallParams):
        result = await transfer_telnyx_call_to_human()
        await params.result_callback({"result": result or "Transferring you now. Please hold."})

    llm.register_function("transfer_to_human", handle_transfer_to_human_telnyx)

    async def handle_get_future_events_telnyx(params: FunctionCallParams):
        result = await asyncio.to_thread(get_future_events, 30)
        await params.result_callback({"result": result})

    llm.register_function("get_future_events", handle_get_future_events_telnyx)

    get_bin_collection_day_schema = FunctionSchema(
        name="get_bin_collection_day",
        description=(
            "Look up the bin collection day for a resident's address using the direct council bin lookup. "
            "Only call this tool once the resident has provided a specific street address. "
            "Do NOT call this tool if you only have a vague question — ask for the address first."
        ),
        properties={
            "address": {
                "type": "string",
                "description": (
                    "Full street address within the Georges River LGA for the direct council bin lookup. "
                    "When confirming a pending matched address, this can be the user's confirmation text."
                ),
            },
            "confirmed": {
                "type": "boolean",
                "description": (
                    "Set true only when the previous tool result asked the resident to confirm a matched address "
                    "and the resident has just confirmed it."
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
    get_future_events_schema = FunctionSchema(
        name="get_future_events",
        description=(
            "Fetch GRC events beyond the next 30 days. Call this when the user asks about "
            "events further in the future. Do NOT call this for events within the next 30 days."
        ),
        properties={},
        required=[],
    )
    context = LLMContext(
        tools=ToolsSchema(standard_tools=[
            get_bin_collection_day_schema,
            transfer_to_human_schema,
            get_future_events_schema,
        ])
    )

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=create_vad_analyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[
                    MinWordsUserTurnStartStrategy(min_words=1, use_interim=True),
                ],
            ),
            user_mute_strategies=[MuteUntilFirstBotCompleteUserMuteStrategy()],
        ),
    )
    lang_switch = LanguageSwitchProcessor(
        tts=tts,
        voice_id=_env("ELEVENLABS_VOICE_ID"),
        filler_tts=filler_tts,
        context=context,
    )
    transfer_processor = TransferRequestProcessor(
        on_transfer_request=transfer_telnyx_call_to_human,
        immediate_message="Transferring you now. Please hold.",
    )
    hesitation_gate = HesitationTurnGateProcessor()
    backchannel_suppressor = BackchannelSuppressorProcessor()

    pipeline = Pipeline([
        transport.input(),
        stt,
        transfer_processor,
        lang_switch,
        hesitation_gate,
        user_aggregator,
        llm,
        backchannel_suppressor,
        filler_tts,
        tts,
        transport.output(),
        assistant_aggregator,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[LatencyObserver(), TranscriptionObserver(monitor_id=monitor_id)],
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Telnyx client connected")
        context.add_message({
            "role": "user",
            "content": (
                "Greet the caller and ask for their language preference. Say exactly: "
                "'Hi, I'm Maya from Georges River Council — would you like to continue in English or Mandarin?'"
            ),
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Telnyx client disconnected")
        _end_live_call(monitor_id)
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


@app.post("/telnyx/voice")
async def telnyx_voice(request: Request):
    """Telnyx Call Control webhook.

    Call Control apps send JSON events and expect commands back via the REST API
    (they do NOT execute returned XML). On an inbound call.initiated we answer the
    call and start bidirectional media streaming to /telnyx/ws in one command.
    """
    try:
        body = await request.json()
    except Exception:
        return {"ok": True}

    data = body.get("data", {})
    event_type = data.get("event_type")
    payload = data.get("payload", {})
    call_control_id = payload.get("call_control_id")
    logger.info(f"[Telnyx] webhook event={event_type} call_control_id={call_control_id}")

    if event_type == "call.initiated" and payload.get("direction") == "incoming" and call_control_id:
        host = request.headers.get("host", "")
        ws_url = f"wss://{host}/telnyx/ws"
        api_key = os.getenv("TELNYX_API_KEY", "")
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/answer",
                    json={
                        "stream_url": ws_url,
                        "stream_track": "inbound_track",
                        "stream_bidirectional_mode": "rtp",
                        "stream_bidirectional_codec": "PCMU",
                    },
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status in (200, 202):
                        logger.info(f"[Telnyx] answered + streaming started → {ws_url}")
                    else:
                        text = await resp.text()
                        logger.error(f"[Telnyx] answer failed: {resp.status} {text}")
        except Exception as e:
            logger.error(f"[Telnyx] answer exception: {e}")

    return {"ok": True}


@app.websocket("/telnyx/ws")
async def telnyx_ws(websocket: WebSocket):
    """WebSocket endpoint for Telnyx Media Streams."""
    await run_telnyx_bot(websocket)


# ---------------------------------------------------------------------------
# Vobiz phone integration
# ---------------------------------------------------------------------------


def _vobiz_xml_for_host(host: str) -> str:
    ws_url = f"wss://{host}/vobiz/ws"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        '<Stream bidirectional="true" audioTrack="inbound" streamTimeout="7200" '
        'keepCallAlive="true" contentType="audio/x-l16;rate=16000">'
        f'{ws_url}'
        '</Stream>'
        '</Response>'
    )


async def run_vobiz_bot(websocket: WebSocket):
    """Run the GRC bot over a Vobiz bidirectional XML <Stream> websocket."""
    await websocket.accept()

    stream_id = None
    call_id = None
    vobiz_encoding = "audio/x-l16"
    vobiz_sample_rate = 16000

    async for raw in websocket.iter_text():
        msg = json.loads(raw)
        event = msg.get("event")
        if event == "start":
            start = msg.get("start", {})
            media_format = start.get("mediaFormat") or start.get("media_format") or {}
            vobiz_encoding = media_format.get("encoding") or vobiz_encoding
            try:
                vobiz_sample_rate = int(media_format.get("sampleRate") or media_format.get("sample_rate") or vobiz_sample_rate)
            except (TypeError, ValueError):
                vobiz_sample_rate = 16000
            call_id = (
                msg.get("callId")
                or msg.get("call_id")
                or start.get("callId")
                or start.get("call_id")
                or start.get("callUUID")
                or start.get("CallUUID")
                or start.get("call_uuid")
            )
            stream_id = (
                msg.get("streamId")
                or msg.get("stream_id")
                or start.get("streamId")
                or start.get("stream_id")
                or start.get("streamSid")
                or start.get("stream_sid")
                or call_id
                or "vobiz-stream"
            )
            logger.info(
                f"Vobiz call started — stream_id={stream_id} call_id={call_id} "
                f"encoding={vobiz_encoding} sample_rate={vobiz_sample_rate}"
            )
            break
        if event == "stop":
            logger.info("Vobiz call stopped before start event")
            return

    monitor_id = _start_live_call(
        "Vobiz",
        call_id=call_id,
        stream_id=stream_id,
        meta={
            "stream_id": stream_id,
            "call_id": call_id,
            "encoding": vobiz_encoding,
            "sample_rate": vobiz_sample_rate,
        },
    )

    serializer = VobizFrameSerializer(
        stream_id=stream_id,
        params=VobizFrameSerializer.InputParams(
            vobiz_encoding=vobiz_encoding,
            vobiz_sample_rate=vobiz_sample_rate,
        ),
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=16,
            serializer=serializer,
        ),
    )

    _events = await asyncio.to_thread(get_events)
    _events_block = format_events_for_system_prompt(_events)
    system_instruction = SYSTEM_INSTRUCTION_GRC + "\n\n" + _events_block
    logger.info(f"[Vobiz CAG] Embedded {len(_events)} events into system instruction")
    logger.info(f"[Vobiz] LLM env provider={_env('LLM_PROVIDER')} model={_llm_model(_llm_provider())}")

    stt = create_stt("elevenlabs")
    llm = create_llm(_env("LLM_PROVIDER"), system_instruction=system_instruction)
    tts = create_tts("elevenlabs")
    filler_tts = FillerTTSProcessor()
    pending_bin_lookup = {}

    async def handle_get_bin_collection_day_vobiz(params: FunctionCallParams):
        await _handle_bin_collection_lookup(params, pending_bin_lookup, "[Vobiz]", llm)

    llm.register_function("get_bin_collection_day", handle_get_bin_collection_day_vobiz)

    async def transfer_vobiz_call_to_human() -> str | None:
        transfer_number = os.getenv("TRANSFER_PHONE_NUMBER", "").strip()
        if transfer_number:
            logger.warning(
                f"[Vobiz TRANSFER] Transfer requested for call_id={call_id}, "
                "but Vobiz live transfer is not implemented in this bot yet"
            )
        return "I'm sorry, transfer is not available on this line right now. Please call us on 9330 6400."

    async def handle_transfer_to_human_vobiz(params: FunctionCallParams):
        result = await transfer_vobiz_call_to_human()
        await params.result_callback({"result": result})

    llm.register_function("transfer_to_human", handle_transfer_to_human_vobiz)

    async def handle_get_future_events_vobiz(params: FunctionCallParams):
        result = await asyncio.to_thread(get_future_events, 30)
        await params.result_callback({"result": result})

    llm.register_function("get_future_events", handle_get_future_events_vobiz)

    get_bin_collection_day_schema = FunctionSchema(
        name="get_bin_collection_day",
        description=(
            "Look up the bin collection day for a resident's address using the direct council bin lookup. "
            "Only call this tool once the resident has provided a specific street address. "
            "Do NOT call this tool if you only have a vague question — ask for the address first."
        ),
        properties={
            "address": {
                "type": "string",
                "description": (
                    "Full street address within the Georges River LGA for the direct council bin lookup. "
                    "When confirming a pending matched address, this can be the user's confirmation text."
                ),
            },
            "confirmed": {
                "type": "boolean",
                "description": (
                    "Set true only when the previous tool result asked the resident to confirm a matched address "
                    "and the resident has just confirmed it."
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
    get_future_events_schema = FunctionSchema(
        name="get_future_events",
        description=(
            "Fetch GRC events beyond the next 30 days. Call this when the user asks about "
            "events further in the future. Do NOT call this for events within the next 30 days."
        ),
        properties={},
        required=[],
    )
    context = LLMContext(
        tools=ToolsSchema(standard_tools=[
            get_bin_collection_day_schema,
            transfer_to_human_schema,
            get_future_events_schema,
        ])
    )

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=create_vad_analyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[
                    MinWordsUserTurnStartStrategy(min_words=1, use_interim=True),
                ],
            ),
            user_mute_strategies=[MuteUntilFirstBotCompleteUserMuteStrategy()],
        ),
    )
    lang_switch = LanguageSwitchProcessor(
        tts=tts,
        voice_id=_env("ELEVENLABS_VOICE_ID"),
        filler_tts=filler_tts,
        context=context,
    )
    transfer_processor = TransferRequestProcessor(
        on_transfer_request=transfer_vobiz_call_to_human,
        immediate_message="Transferring you now. Please hold.",
    )
    hesitation_gate = HesitationTurnGateProcessor()
    backchannel_suppressor = BackchannelSuppressorProcessor()

    pipeline = Pipeline([
        transport.input(),
        stt,
        transfer_processor,
        lang_switch,
        hesitation_gate,
        user_aggregator,
        llm,
        backchannel_suppressor,
        filler_tts,
        tts,
        transport.output(),
        assistant_aggregator,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[LatencyObserver(), TranscriptionObserver(monitor_id=monitor_id)],
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Vobiz client connected")
        context.add_message({
            "role": "user",
            "content": (
                "Greet the caller and ask for their language preference. Say exactly: "
                "'Hi, I'm Maya from Georges River Council — would you like to continue in English or Mandarin?'"
            ),
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Vobiz client disconnected")
        _end_live_call(monitor_id)
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


@app.api_route("/vobiz/answer", methods=["GET", "POST", "OPTIONS"])
async def vobiz_answer(request: Request):
    """Vobiz Answer URL — returns XML that streams call audio to this server."""
    if request.method == "OPTIONS":
        logger.info("[Vobiz] OPTIONS /vobiz/answer")
        return _options_ok()
    host = request.headers.get("host", "")
    logger.info(f"[Vobiz] answer webhook from host={host}")
    return Response(content=_vobiz_xml_for_host(host), media_type="application/xml")


def _options_ok(methods: str = "GET, POST, OPTIONS") -> Response:
    return Response(
        status_code=204,
        headers={
            "Allow": methods,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": methods,
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Auth-ID, X-Auth-Token",
            "Access-Control-Max-Age": "86400",
        },
    )


@app.options("/vobiz/answer")
async def vobiz_answer_options():
    """OPTIONS-compatible Vobiz Answer URL for dashboard checks."""
    logger.info("[Vobiz] OPTIONS /vobiz/answer")
    return _options_ok()


@app.post("/vobiz/stream-status")
async def vobiz_stream_status(request: Request):
    """Vobiz stream lifecycle callback for diagnosing websocket failures."""
    try:
        form = await request.form()
        payload = dict(form)
    except Exception:
        try:
            payload = await request.json()
        except Exception:
            payload = {"raw": (await request.body()).decode("utf-8", errors="replace")}
    logger.info(f"[Vobiz] stream status callback: {payload}")
    return {"ok": True}


@app.get("/vobiz/stream-status")
async def vobiz_stream_status_get(request: Request):
    """GET-compatible Vobiz stream lifecycle callback."""
    logger.info(f"[Vobiz] stream status GET callback: {dict(request.query_params)}")
    return {"ok": True}


@app.options("/vobiz/stream-status")
async def vobiz_stream_status_options():
    """OPTIONS-compatible Vobiz stream lifecycle callback."""
    logger.info("[Vobiz] OPTIONS /vobiz/stream-status")
    return _options_ok()


@app.get("/vobiz/answer")
async def vobiz_answer_get(request: Request):
    """GET-compatible Vobiz Answer URL for provider config checks."""
    return await vobiz_answer(request)


@app.api_route("/answer", methods=["GET", "POST", "OPTIONS"])
async def vobiz_answer_alias(request: Request):
    """Compatibility alias for Vobiz examples that use /answer."""
    if request.method == "OPTIONS":
        logger.info("[Vobiz] OPTIONS /answer")
        return _options_ok()
    return await vobiz_answer(request)


@app.get("/answer")
async def vobiz_answer_alias_get(request: Request):
    """GET-compatible compatibility alias for Vobiz examples that use /answer."""
    return await vobiz_answer(request)


@app.options("/answer")
async def vobiz_answer_alias_options():
    """OPTIONS-compatible compatibility alias for Vobiz examples that use /answer."""
    logger.info("[Vobiz] OPTIONS /answer")
    return _options_ok()


@app.api_route("/telnyx/answer", methods=["GET", "POST", "OPTIONS"])
async def legacy_telnyx_answer_alias(request: Request):
    """Legacy/provider-side alias that returns the Vobiz stream XML."""
    if request.method == "OPTIONS":
        logger.warning("[Vobiz] Received legacy OPTIONS /telnyx/answer")
        return _options_ok()
    logger.warning(f"[Vobiz] Received legacy {request.method} /telnyx/answer webhook; returning Vobiz XML")
    return await vobiz_answer(request)


@app.get("/telnyx/answer")
async def legacy_telnyx_answer_alias_get(request: Request):
    """GET-compatible legacy/provider-side alias that returns the Vobiz stream XML."""
    logger.warning("[Vobiz] Received legacy GET /telnyx/answer webhook; returning Vobiz XML")
    return await vobiz_answer(request)


@app.options("/telnyx/answer")
async def legacy_telnyx_answer_alias_options():
    """OPTIONS-compatible legacy/provider-side alias."""
    logger.warning("[Vobiz] Received legacy OPTIONS /telnyx/answer")
    return _options_ok()


@app.websocket("/vobiz/ws")
async def vobiz_ws(websocket: WebSocket):
    """WebSocket endpoint for Vobiz XML Stream audio."""
    await run_vobiz_bot(websocket)


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


def _is_filler_only(text: str) -> bool:
    """Return True when the transcript is nothing but filler/hesitation sounds."""
    return bool(_HESITATION_FILLER_RE.match(text.strip()))


class TranslationProcessor(FrameProcessor):
    """Translates STT transcripts and injects them into the other participant's pipeline."""

    def __init__(self, translation_llm: LLMService, session: TranslationSession, my_pc_id: str, **kwargs):
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
            logger.info(f"[Translation] Calling configured LLM for translation...")
            response = await _chat_completion_with_token_fallback(
                self._llm._client,
                model=self._llm._settings.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": text},
                ],
                max_tokens=500,
                stream=False,
            )
            translated = response.choices[0].message.content
            logger.info(f"[Translation] LLM result: {repr(translated)}")
            if not translated:
                logger.warning("[Translation] Empty result from LLM — skipping TTS injection")
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

    translation_llm = create_llm(_env("LLM_PROVIDER"))

    # TTS: voice only — no language/model override so ElevenLabs uses its
    # default multilingual model, which handles both English and Mandarin
    # without needing a language code that may be rejected by turbo v2.5.
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
    tts = ElevenLabsTTSService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        settings=ElevenLabsTTSService.Settings(
            voice=participant.voice_config["voice_id"],
            model=_env("ELEVENLABS_TTS_MODEL", "eleven_turbo_v2_5"),
            speed=_tts_float_env(participant.language.value, "speed", 1.0),
            stability=_tts_float_env(participant.language.value, "stability", 0.35),
            similarity_boost=_tts_float_env(participant.language.value, "similarity_boost", 0.75),
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
        translation_llm: LLMService,
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
            response = await _chat_completion_with_token_fallback(
                self._llm._client,
                model=self._llm._settings.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": text},
                ],
                max_tokens=500,
                stream=False,
            )
            translated = response.choices[0].message.content
            if translated:
                logger.info(f"[AutoTranslation] → '{translated.strip()[:60]}'")
                await self.push_frame(TTSSpeakFrame(text=translated.strip()))
            else:
                logger.warning("[AutoTranslation] Empty result from LLM")
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

            response = await _chat_completion_with_token_fallback(
                self._llm._client,
                model=self._llm._settings.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": text},
                ],
                max_tokens=500,
                stream=False,
            )
            translated = response.choices[0].message.content
            if translated:
                logger.info(f"[AutoTranslation] -> '{translated.strip()[:60]}'")
                await self.push_frame(TTSSpeakFrame(text=translated.strip()))
            else:
                logger.warning("[AutoTranslation] Empty result from LLM")
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

    async def _translate_text(self, text: str, source_lang: Language | None, *, max_tokens: int) -> tuple[str | None, Language | None]:
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

        response = await _chat_completion_with_token_fallback(
            self._llm._client,
            model=self._llm._settings.model,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=max_tokens,
            stream=False,
        )
        translated = response.choices[0].message.content
        return (translated.strip() if translated else None), (target_lang or self._target_lang(source_lang))

    async def _publish_live_preview(self, text: str, source_lang: Language | None):
        if not self._session or source_lang is None:
            return

        translated, target_lang = await self._translate_text(text, source_lang, max_tokens=80)
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
            translated, target_lang = await self._translate_text(text, source_lang, max_tokens=200)
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
                logger.warning("[AutoTranslation] Empty result from LLM")
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

    translation_llm = create_llm(_env("LLM_PROVIDER"))

    # Use a multilingual voice — handles both languages without voice-switching
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
    tts = ElevenLabsTTSService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        settings=ElevenLabsTTSService.Settings(
            voice=os.getenv(
                "ELEVENLABS_MULTILINGUAL_VOICE_ID",
                os.getenv("ELEVENLABS_VOICE_ID", ""),
            ),
            model=_env("ELEVENLABS_TTS_MODEL", "eleven_turbo_v2_5"),
            speed=_float_env("ELEVENLABS_TTS_SPEED", 1.0),
            stability=_float_env("ELEVENLABS_TTS_STABILITY", 0.35),
            similarity_boost=_float_env("ELEVENLABS_TTS_SIMILARITY_BOOST", 0.75),
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
