"""Vocare voice bot with configurable STT/LLM/TTS via WebRTC transport.

The frontend sends service selections (stt, llm, tts) as part of the
/api/offer request body. The bot dynamically creates the chosen services
for each session.
"""

import argparse
import array
import asyncio
import base64
import io
import json
import logging
import os
import re
import sys
import random
import secrets
import time
import wave
from datetime import datetime, timezone
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent / "GRC_pilot"))
from tools import _correct_address  # noqa: E402
from grc_events import get_events, format_events_for_system_prompt, get_future_events  # noqa: E402
from da_knowledge import DA_KNOWLEDGE  # noqa: E402
from bin_faq import BIN_FAQ  # noqa: E402

import uvicorn
from dotenv import load_dotenv
import aiohttp
from urllib.parse import quote
from fastapi import BackgroundTasks, FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

import vocare_llm  # noqa: E402

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
    if vocare_llm.wire_for(provider) == "bedrock":
        # Bedrock uses the AWS credential chain, not an API key in app config.
        return
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
    return SileroVADAnalyzer(
        params=VADParams(
            confidence=0.6,
            start_secs=0.15,
            stop_secs=0.6,
            min_volume=0.3,
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
        self._last_injected_language_text = ""
        self._last_injected_language_at = 0.0

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

    def _recently_injected_language_choice(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z\u4e00-\u9fff]+", " ", text).lower()).strip()
        return (
            bool(normalized)
            and normalized == self._last_injected_language_text
            and (time.monotonic() - self._last_injected_language_at) < 5.0
        )

    async def _emit_language_choice_turn(self, frame, is_english: bool, rewritten_text: str):
        original_text = getattr(frame, "text", "")
        self._record_language_preference(is_english, original_text)
        logger.info(
            f"Language preference interim finalized for LLM: "
            f"{original_text!r} -> {rewritten_text!r}"
        )

        self._last_injected_language_text = re.sub(
            r"\s+",
            " ",
            re.sub(r"[^a-zA-Z\u4e00-\u9fff]+", " ", original_text).lower(),
        ).strip()
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
                if not self._recently_injected_language_choice(frame.text):
                    await self._emit_language_choice_turn(
                        frame,
                        explicit_language_preference,
                        rewritten_language_turn,
                    )
                return

        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            switched = False
            explicit_language_preference = None
            rewritten_language_turn = None
            if self._recently_injected_language_choice(frame.text):
                logger.info(f"Suppressing delayed duplicate language transcript: {frame.text!r}")
                return

            # 1. Text-based keyword detection (catches "Mandarin" spoken in English)
            explicit_language_preference, rewritten_language_turn = self._detect_language_preference(frame.text)
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
                "Do not ask for the language again. Continue in English."
            )
        else:
            msg = (
                "CALLER_LANGUAGE_SELECTION: The caller explicitly selected Mandarin Chinese (普通话). "
                "Treat this as the complete answer to your language preference question. "
                "Do not ask for the language again. Acknowledge briefly in simplified Chinese "
                "and continue using Mandarin Chinese only."
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

    # Bedrock does not speak the OpenAI wire format, so it cannot be expressed
    # as a base_url swap like every provider below; it is built from the shared
    # registry instead.
    if vocare_llm.wire_for(provider) == "bedrock":
        spec = vocare_llm.resolve_spec("pipecat", provider=provider)
        return vocare_llm.make_pipecat_llm(spec, system_instruction)

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


def create_tts(name: str, voice_id: str = ""):
    """Create a TTS service by name, optionally pinned to a specific voice."""
    if name == "elevenlabs":
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

        # Prefer the multilingual voice so TTS can speak any language the LLM
        # generates without requiring a runtime switch.
        voice = (
            voice_id
            or _env("ELEVENLABS_MULTILINGUAL_VOICE_ID")
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

# The iOS and Android shells bundle the UI locally, so their API calls arrive
# cross-origin from the Capacitor WebView schemes rather than same-origin from
# /vocare. allow_credentials stays False: nothing here is authenticated, and
# enabling it would force origin echoing with cookie exposure for no benefit.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "capacitor://localhost",  # iOS WKWebView
        "https://localhost",      # Android WebView (androidScheme: https)
        "http://localhost",       # Android cleartext fallback
        "ionic://localhost",      # legacy Ionic scheme
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    max_age=600,
)

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

_CONFIRM_YES = {
    "yes", "yeah", "yep", "correct", "right", "that's right", "that is right",
    "thats right", "yes correct", "yes that's correct", "yes that is correct",
    "confirmed", "confirm", "that is correct", "that's correct", "thats correct",
}

_CONFIRM_NO = {
    "no", "nope", "nah", "incorrect", "not correct", "that's wrong", "thats wrong",
    "that is wrong", "wrong address", "not that address",
}


def _normalize_confirmation_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9']+", " ", text.lower())).strip()


def _is_confirmation_yes(text: str) -> bool:
    normalized = _normalize_confirmation_text(text)
    return normalized in _CONFIRM_YES


def _is_confirmation_no(text: str) -> bool:
    normalized = _normalize_confirmation_text(text)
    return normalized in _CONFIRM_NO


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
            llm._client.chat.completions.create(
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
    call_room_id: str = "grc-demo",
    caller: str = "Web caller",
    source: str = "grc_demo",
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
    provider = "Vocare Website" if source == "vocare_website" else "WebRTC"
    monitor_id = _start_live_call(
        provider,
        call_id=pc_id,
        caller=caller,
        meta={
            "room_id": call_room_id,
            "source": source,
        },
    )
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


def _debug_endpoints_enabled() -> bool:
    """Whether the internal debug tools are exposed.

    These belong to the waste-collection demo, not Vocare Translate, but they
    share this container — which is about to field public app-store traffic.
    /api/debug/address-lookup makes unauthenticated third-party address lookups,
    so left open it is a cost-abuse surface against someone else's API. Off
    unless ENABLE_DEBUG_ENDPOINTS is set truthy.
    """
    return _env("ENABLE_DEBUG_ENDPOINTS", "").strip().lower() in {"1", "true", "yes", "on"}


@app.get("/address-test", response_class=HTMLResponse)
async def address_test():
    if not _debug_endpoints_enabled():
        return Response(status_code=404, content="Not Found")
    html_path = os.path.join(os.path.dirname(__file__), "static", "address-test.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())


@app.post("/api/debug/address-lookup")
async def debug_address_lookup(request: Request):
    if not _debug_endpoints_enabled():
        return Response(status_code=404, content="Not Found")
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
    call_room_id = request.get("room_id") or "grc-demo"
    caller = request.get("caller") or "Web caller"
    source = request.get("source") or "grc_demo"

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
            run_bot, pipecat_connection, stt_name, llm_name, tts_name, call_room_id, caller, source
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


@app.post("/api/live-calls/ingest")
async def live_calls_ingest(request: Request):
    """Ingest live-call events from external voice agents (e.g. the LiveKit agent).

    Body: {"event": "start"|"line"|"end", "monitor_id": str, ...}
      start: provider, call_id, caller, meta
      line:  speaker ("caller"|"agent"), text
    """
    expected_token = _env("LIVE_CALL_INGEST_TOKEN")
    if expected_token and request.headers.get("x-ingest-token") != expected_token:
        return Response(status_code=403, content="Forbidden")
    try:
        data = await request.json()
    except Exception:
        return Response(status_code=400, content="Invalid JSON")

    event = (data.get("event") or "").strip().lower()
    monitor_id = (data.get("monitor_id") or "").strip()
    if not event or not monitor_id:
        return Response(status_code=400, content="event and monitor_id are required")

    if event == "start":
        provider = (data.get("provider") or monitor_id.split(":", 1)[0] or "external").strip()
        session = live_call_sessions.get(monitor_id)
        now = _now_iso()
        if not session:
            live_call_sessions[monitor_id] = {
                "id": monitor_id,
                "provider": provider,
                "call_id": data.get("call_id"),
                "stream_id": None,
                "caller": data.get("caller") or "Phone caller",
                "status": "active",
                "started_at": now,
                "ended_at": None,
                "last_update": now,
                "meta": data.get("meta") or {},
                "transcript": [],
            }
            _trim_live_call_sessions()
            logger.info(f"[LIVE_CALLS] Ingested start monitor_id={monitor_id}")
        else:
            session.update({
                "status": "active",
                "ended_at": None,
                "last_update": now,
                "caller": data.get("caller") or session.get("caller") or "Phone caller",
                "meta": {**session.get("meta", {}), **(data.get("meta") or {})},
            })
    elif event == "line":
        speaker = (data.get("speaker") or "caller").strip()
        text = (data.get("text") or "").strip()
        if text:
            _append_live_transcript(monitor_id, speaker, text)
    elif event == "end":
        _end_live_call(monitor_id)
    else:
        return Response(status_code=400, content=f"Unknown event: {event}")
    return {"ok": True}


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
        resp = await llm._client.chat.completions.create(
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

import phone_approval
import voice_control


async def _dial_report_call(task: dict) -> None:
    """Call the approver back to read out a finished voice task."""
    api_key = os.getenv("TELNYX_API_KEY", "").strip()
    app_id = os.getenv("TELNYX_CALL_CONTROL_APP_ID", "").strip()
    from_number = os.getenv("TELNYX_PHONE_NUMBER", "").strip()
    to_number = phone_approval.approver_number()
    if not all([api_key, app_id, from_number, to_number]):
        logger.error(f"[VoiceTask:{task['id']}] report call skipped — Telnyx env incomplete")
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.telnyx.com/v2/calls",
                json={
                    "connection_id": app_id,
                    "to": to_number,
                    "from": from_number,
                    "client_state": voice_control.encode_report_state(task["id"]),
                    "timeout_secs": 40,
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                body = await resp.text()
                if resp.status in (200, 201, 202):
                    call_control_id = json.loads(body)["data"]["call_control_id"]
                    voice_control.bind_report_call(call_control_id, task["id"])
                    logger.info(f"[VoiceTask:{task['id']}] report call dialing")
                else:
                    logger.error(f"[VoiceTask:{task['id']}] report dial failed: {resp.status} {body[:200]}")
    except Exception:
        logger.exception(f"[VoiceTask:{task['id']}] report dial exception")


async def run_telnyx_task_bot(websocket, stream_id, call_control_id, outbound_encoding,
                              system_instruction, connect_directive):
    """Shared voice-agent shell for the Claude control and report calls.

    Both agents can queue new tasks (submit_task) and read the latest task's
    state (get_status); only the system prompt differs.
    """
    serializer = TelnyxFrameSerializer(
        stream_id=stream_id,
        call_control_id=call_control_id,
        # The start event's media_format is authoritative for BOTH directions:
        # despite requesting PCMU in answer/streaming_start, Telnyx expects sent
        # audio in the negotiated leg codec (PCMA on many AU carriers). Pinning
        # outbound to PCMU produced distorted audio on PCMA calls.
        outbound_encoding=outbound_encoding,
        inbound_encoding=outbound_encoding,
        api_key=os.getenv("TELNYX_API_KEY"),
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

    stt = create_stt("elevenlabs")
    llm = create_llm(_env("LLM_PROVIDER"), system_instruction=system_instruction)
    tts = create_tts("elevenlabs", voice_id=os.getenv("APPROVAL_VOICE_ID", ""))

    async def handle_submit_task(params: FunctionCallParams):
        instruction = str(params.arguments.get("instruction", "")).strip()
        if len(instruction) < 8:
            await params.result_callback({"status": "error", "message": "instruction too short"})
            return
        task = voice_control.submit(instruction)
        await params.result_callback({"status": "queued", "task_id": task["id"]})

    async def handle_get_status(params: FunctionCallParams):
        task = voice_control.latest()
        if not task:
            await params.result_callback({"status": "empty", "message": "No tasks have been submitted yet."})
            return
        await params.result_callback({
            "task_id": task["id"],
            "instruction": task["instruction"][:200],
            "status": task["status"],
            "result": task["result"][:600],
        })

    async def handle_switch_session(params: FunctionCallParams):
        name = str(params.arguments.get("name", "")).strip()
        # Live interactive sessions on Von's machine win over named worker
        # sessions when the spoken name matches one.
        live = voice_control.resolve_live_session(name)
        if live is not None:
            voice_control.set_active_resume(live["id"], live["project"])
            await params.result_callback({
                "status": "targeting_live_session",
                "session": live["project"],
                "last_active_minutes_ago": live.get("minutes_ago"),
                "note": "Tasks now run inside this session's full context.",
            })
            return
        active = voice_control.switch_session(name)
        await params.result_callback({"status": "switched", "active_session": active})

    async def handle_list_sessions(params: FunctionCallParams):
        resume_id, resume_label = voice_control.active_resume()
        await params.result_callback({
            "active": resume_label or voice_control.active_session(),
            "named_sessions": voice_control.session_names(),
            "live_sessions_on_von_machine": [
                {"project": s.get("project"), "last_active_minutes_ago": s.get("minutes_ago")}
                for s in voice_control.live_sessions()
            ],
        })

    async def handle_call_contact(params: FunctionCallParams):
        name = str(params.arguments.get("name", "")).strip()
        topic = str(params.arguments.get("topic", "")).strip()
        resolved = voice_control.resolve_contact(name)
        if not resolved:
            await params.result_callback({
                "status": "unknown_contact",
                "message": "Only registered contacts can be called.",
                "registered": sorted(voice_control.contacts().keys()),
            })
            return
        contact_name, number = resolved
        if len(topic) < 5:
            await params.result_callback({
                "status": "error", "message": "A topic for the feedback call is required.",
            })
            return
        fb = voice_control.submit_feedback(contact_name, number, topic)
        asyncio.create_task(_dial_feedback_call(fb))
        await params.result_callback({
            "status": "calling", "contact": contact_name, "feedback_id": fb["id"],
        })

    def _safe(handler):
        async def wrapped(params: FunctionCallParams):
            name = getattr(handler, "__name__", "tool")
            logger.info(f"[VoiceControl] tool invoked: {name} args={dict(params.arguments)!r}")
            try:
                await handler(params)
            except Exception as error:
                logger.exception(f"[VoiceControl] tool {name} failed")
                try:
                    await params.result_callback({
                        "status": "error",
                        "message": f"The tool failed: {type(error).__name__}. Tell Von it did not work.",
                    })
                except Exception:
                    pass
        return wrapped

    llm.register_function("submit_task", _safe(handle_submit_task))
    llm.register_function("get_status", _safe(handle_get_status))
    llm.register_function("switch_session", _safe(handle_switch_session))
    llm.register_function("list_sessions", _safe(handle_list_sessions))
    llm.register_function("call_contact", _safe(handle_call_contact))

    switch_schema = FunctionSchema(
        name="switch_session",
        description=(
            "Switch which named Claude Code session future tasks go to. Sessions keep their "
            "own conversation context. A new name creates a fresh session."
        ),
        properties={"name": {"type": "string", "description": "Session name, e.g. 'watch app' or 'main'."}},
        required=["name"],
    )
    list_schema = FunctionSchema(
        name="list_sessions",
        description="List the named Claude Code sessions and which one is active.",
        properties={},
        required=[],
    )
    contact_schema = FunctionSchema(
        name="call_contact",
        description=(
            "Place a feedback call to a REGISTERED contact on Von's behalf. The agent on that "
            "call collects their feedback on the topic, and Von gets a call-back with the "
            "summary afterwards. Only registered contact names work — never digits dictated "
            "over the phone."
        ),
        properties={
            "name": {"type": "string", "description": "Contact name as Von said it, e.g. 'Nolan'."},
            "topic": {"type": "string", "description": "What to ask them for feedback on."},
        },
        required=["name", "topic"],
    )

    submit_schema = FunctionSchema(
        name="submit_task",
        description=(
            "Queue an instruction for Von's Claude Code session. Call this only after "
            "reading the instruction back and getting an explicit yes."
        ),
        properties={"instruction": {
            "type": "string",
            "description": "The complete instruction, in the caller's words, cleaned of filler.",
        }},
        required=["instruction"],
    )
    status_schema = FunctionSchema(
        name="get_status",
        description="Fetch the latest task's status and result. Call when asked for progress or status.",
        properties={},
        required=[],
    )
    context = LLMContext(tools=ToolsSchema(standard_tools=[
        submit_schema, status_schema, switch_schema, list_schema, contact_schema,
    ]))
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=create_vad_analyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[MinWordsUserTurnStartStrategy(min_words=1, use_interim=True)],
            ),
        ),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        llm,
        tts,
        transport.output(),
        assistant_aggregator,
    ])
    task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=True))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"[VoiceControl] task bot connected (enc={outbound_encoding}) — greeting")
        context.add_message({"role": "user", "content": connect_directive})
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("[VoiceControl] task bot disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.run(task)
    except Exception:
        logger.exception("[VoiceControl] task bot pipeline crashed")
        raise


async def _telnyx_dial(client_state: str, to_number: str, label: str) -> Optional[str]:
    """Place an outbound Call Control call; returns call_control_id or None."""
    api_key = os.getenv("TELNYX_API_KEY", "").strip()
    app_id = os.getenv("TELNYX_CALL_CONTROL_APP_ID", "").strip()
    from_number = os.getenv("TELNYX_PHONE_NUMBER", "").strip()
    if not all([api_key, app_id, from_number, to_number]):
        logger.error(f"[{label}] dial skipped — Telnyx env incomplete")
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.telnyx.com/v2/calls",
                json={
                    "connection_id": app_id,
                    "to": to_number,
                    "from": from_number,
                    "client_state": client_state,
                    "timeout_secs": 40,
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                body = await resp.text()
                if resp.status in (200, 201, 202):
                    return json.loads(body)["data"]["call_control_id"]
                logger.error(f"[{label}] dial failed: {resp.status} {body[:200]}")
    except Exception:
        logger.exception(f"[{label}] dial exception")
    return None


async def _dial_feedback_call(fb: dict) -> None:
    ccid = await _telnyx_dial(
        voice_control.encode_state("feedback", fb["id"]), fb["number"], f"Feedback:{fb['id']}"
    )
    if ccid:
        voice_control.bind_feedback_call(ccid, fb["id"])
    else:
        fb["status"] = "no_feedback"
        fb["summary"] = "The call to the contact could not be placed."
        await _dial_feedback_report(fb)


async def _dial_feedback_report(fb: dict) -> None:
    ccid = await _telnyx_dial(
        voice_control.encode_state("fbreport", fb["id"]),
        phone_approval.approver_number(),
        f"FeedbackReport:{fb['id']}",
    )
    if ccid:
        voice_control.bind_feedback_report(ccid, fb["id"])


async def run_telnyx_feedback_bot(websocket, stream_id, call_control_id, outbound_encoding, fb):
    """Agent for the contact leg: collects feedback on Von's behalf."""
    serializer = TelnyxFrameSerializer(
        stream_id=stream_id,
        call_control_id=call_control_id,
        # The start event's media_format is authoritative for BOTH directions:
        # despite requesting PCMU in answer/streaming_start, Telnyx expects sent
        # audio in the negotiated leg codec (PCMA on many AU carriers). Pinning
        # outbound to PCMU produced distorted audio on PCMA calls.
        outbound_encoding=outbound_encoding,
        inbound_encoding=outbound_encoding,
        api_key=os.getenv("TELNYX_API_KEY"),
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
    contact_display = fb["contact"].title()
    system_instruction = (
        f"You are an AI assistant calling {contact_display} on behalf of Von Viray. Von asked "
        f"you to collect {contact_display}'s feedback and suggestions on this topic:\n"
        f"{fb['topic']}\n\n"
        "Rules:\n"
        "- Open by identifying yourself as Von's AI assistant and say why you are calling. "
        "Ask if now is a quick okay moment.\n"
        "- Ask for their feedback and suggestions on the topic. Ask one short follow-up if "
        "an answer is vague. Keep the whole call under a few minutes.\n"
        "- When they are done (or decline), call record_feedback with a faithful two-to-four "
        "sentence summary of what they said, thank them, and say goodbye.\n"
        "- Do not commit Von to anything, and do not discuss other topics."
    )
    stt = create_stt("elevenlabs")
    llm = create_llm(_env("LLM_PROVIDER"), system_instruction=system_instruction)
    tts = create_tts("elevenlabs", voice_id=os.getenv("APPROVAL_VOICE_ID", ""))

    async def handle_record_feedback(params: FunctionCallParams):
        summary = str(params.arguments.get("summary", "")).strip()
        voice_control.record_feedback(fb["id"], summary)
        await params.result_callback({"status": "recorded"})

        async def _end_soon():
            await asyncio.sleep(6)
            await _telnyx_hangup(call_control_id)

        asyncio.create_task(_end_soon())

    llm.register_function("record_feedback", handle_record_feedback)
    feedback_schema = FunctionSchema(
        name="record_feedback",
        description="Record the collected feedback summary. Call exactly once, near the end of the call.",
        properties={"summary": {"type": "string"}},
        required=["summary"],
    )
    context = LLMContext(tools=ToolsSchema(standard_tools=[feedback_schema]))
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=create_vad_analyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[MinWordsUserTurnStartStrategy(min_words=1, use_interim=True)],
            ),
        ),
    )
    pipeline = Pipeline([
        transport.input(), stt, user_aggregator, llm, tts, transport.output(), assistant_aggregator,
    ])
    task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=True))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        context.add_message({
            "role": "user",
            "content": "The call has connected. Introduce yourself and why you are calling.",
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        voice_control.feedback_call_ended(fb["id"])
        await task.cancel()
        asyncio.create_task(_dial_feedback_report(fb))

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


CONTROL_AGENT_PROMPT = (
    "You are the voice control line for Von's Claude Code session — Von has called you "
    "to give the session work to do.\n\n"
    "Rules:\n"
    "- Be brief and operational. No small talk beyond a one-line greeting.\n"
    "- Listen for an instruction (a coding or ops task). Read it back in one sentence and "
    "ask for a yes before calling submit_task. Never submit without an explicit yes.\n"
    "- After submitting, say the task is queued and that you will call back when it finishes. "
    "Ask if there is anything else.\n"
    "- If asked about progress, call get_status and summarize it in one or two sentences.\n"
    "- If an instruction sounds destructive (deleting data, force-pushing, dropping "
    "infrastructure), read it back with a warning before asking for the yes.\n"
    "- Tasks go to the ACTIVE Claude session. list_sessions shows both named phone "
    "sessions and the LIVE Claude Code sessions currently open on Von's machine with "
    "how recently each was active. switch_session with a project name (e.g. 'the "
    "vocare demo session', 'the ai brain session') targets that live session — tasks "
    "then run with that session's full context. Mention the active session when "
    "confirming a task.\n"
    "- If Von asks you to call a friend or teammate for feedback (e.g. 'call Nolan for "
    "feedback on the new UI'), use call_contact with the name and topic. Only registered "
    "contacts can be called; tell Von the registered names if the lookup fails. Von will "
    "be called back with the summary after that call ends."
)


async def _telnyx_hangup(call_control_id: str):
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/hangup",
                json={},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {os.getenv('TELNYX_API_KEY', '')}",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            )
    except Exception:
        logger.exception(f"[Approval] hangup failed for {call_control_id}")


async def run_telnyx_approval_bot(websocket, stream_id, call_control_id, outbound_encoding, record):
    """Voice agent for a Claude Code approval call.

    Reads the pending action aloud, captures an explicit approve/deny, writes it
    into the approval store (which the Claude Code hook is polling), then ends
    the call.
    """
    serializer = TelnyxFrameSerializer(
        stream_id=stream_id,
        call_control_id=call_control_id,
        # The start event's media_format is authoritative for BOTH directions:
        # despite requesting PCMU in answer/streaming_start, Telnyx expects sent
        # audio in the negotiated leg codec (PCMA on many AU carriers). Pinning
        # outbound to PCMU produced distorted audio on PCMA calls.
        outbound_encoding=outbound_encoding,
        inbound_encoding=outbound_encoding,
        api_key=os.getenv("TELNYX_API_KEY"),
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

    system_instruction = (
        "You are the approval line for Von's Claude Code session. You have called Von "
        "because the session needs a yes-or-no decision before it may continue.\n\n"
        f"The pending action is:\nTitle: {record['title']}\nDetails: {record['detail']}\n\n"
        "Rules:\n"
        "- PLAIN ENGLISH ONLY. Never read out command syntax, flags, file paths, tool "
        "names, or technical jargon (no 'grep', 'git push', 'dash dash' anything). "
        "Describe what the action DOES in everyday terms, as if to a non-programmer.\n"
        "- Keep the entire explanation under fifteen seconds of speech — one or two short "
        "sentences — then ask: 'Approve or deny?'\n"
        "- Only if Von explicitly asks for the exact command may you read the technical text.\n"
        "- Accept only an explicit decision. Approve, yes, go ahead mean approve; deny, "
        "reject, no, stop mean deny. If ambiguous, ask again briefly.\n"
        "- The moment you have a clear decision, call the record_decision function. "
        "Never call it before Von has clearly decided.\n"
        "- After recording, confirm in a few words and say goodbye."
    )

    stt = create_stt("elevenlabs")
    llm = create_llm(_env("LLM_PROVIDER"), system_instruction=system_instruction)
    tts = create_tts("elevenlabs", voice_id=os.getenv("APPROVAL_VOICE_ID", ""))

    async def handle_record_decision(params: FunctionCallParams):
        decision = str(params.arguments.get("decision", "")).strip().lower()
        spoken = str(params.arguments.get("caller_words", ""))
        if decision not in ("approve", "deny"):
            await params.result_callback({"status": "error", "message": "decision must be approve or deny"})
            return
        ok = phone_approval.record_decision(record["id"], decision, spoken)
        await params.result_callback({
            "status": "recorded" if ok else "already_closed",
            "decision": decision,
        })

        async def _end_call_soon():
            await asyncio.sleep(6)  # let the confirmation sentence play out
            await _telnyx_hangup(call_control_id)

        asyncio.create_task(_end_call_soon())

    llm.register_function("record_decision", handle_record_decision)

    record_decision_schema = FunctionSchema(
        name="record_decision",
        description=(
            "Record the caller's explicit approval decision for the pending Claude Code action. "
            "Call this exactly once, only after the caller has clearly said approve or deny."
        ),
        properties={
            "decision": {"type": "string", "enum": ["approve", "deny"]},
            "caller_words": {
                "type": "string",
                "description": "The caller's own words that expressed the decision.",
            },
        },
        required=["decision"],
    )
    context = LLMContext(tools=ToolsSchema(standard_tools=[record_decision_schema]))
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=create_vad_analyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[MinWordsUserTurnStartStrategy(min_words=1, use_interim=True)],
            ),
        ),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        llm,
        tts,
        transport.output(),
        assistant_aggregator,
    ])
    task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=True))

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"[Approval:{record['id']}] call connected — reading request")
        context.add_message({
            "role": "user",
            "content": (
                "The call has connected. Greet Von, say this is the Claude Code approval line, "
                "state the pending action, and ask for approve or deny."
            ),
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"[Approval:{record['id']}] call disconnected")
        phone_approval.mark_call_ended(record["id"])
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


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

    # Routing bindings are in-memory, so a deploy rollover between the webhook
    # and this WS start loses them — which used to dump Von's control calls
    # into the public GRC bot. If nothing matches locally, ask Telnyx for the
    # call's client_state and caller and rebuild the binding statelessly.
    if (
        phone_approval.get_by_call_control(call_control_id) is None
        and voice_control.report_task_for_call(call_control_id) is None
        and voice_control.feedback_for_call(call_control_id) is None
        and voice_control.feedback_report_for_call(call_control_id) is None
        and not voice_control.is_control_call(call_control_id)
    ):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.telnyx.com/v2/calls/{call_control_id}",
                    headers={"Authorization": f"Bearer {os.getenv('TELNYX_API_KEY', '')}"},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status == 200:
                        info = (await resp.json()).get("data", {})
                        state = info.get("client_state")
                        caller = info.get("from")
                        approval_id = phone_approval.decode_client_state(state)
                        if approval_id and phone_approval.get(approval_id):
                            phone_approval.bind_call(approval_id, call_control_id)
                        elif (task_id := voice_control.decode_report_state(state)):
                            voice_control.bind_report_call(call_control_id, task_id)
                        elif (fb_id := voice_control.decode_state(state, "feedback")):
                            voice_control.bind_feedback_call(call_control_id, fb_id)
                        elif (fb_id := voice_control.decode_state(state, "fbreport")):
                            voice_control.bind_feedback_report(call_control_id, fb_id)
                        elif voice_control.is_controller(caller):
                            voice_control.bind_control_call(call_control_id)
                            logger.info("[VoiceControl] rebound control call after state loss")
        except Exception:
            logger.exception("[Telnyx] stateless call-info lookup failed")

    approval_record = phone_approval.get_by_call_control(call_control_id)
    if approval_record is not None:
        await run_telnyx_approval_bot(
            websocket, stream_id, call_control_id, outbound_encoding, approval_record
        )
        return

    report_task = voice_control.report_task_for_call(call_control_id)
    if report_task is not None:
        outcome = "finished" if report_task["status"] == "done" else "failed"
        await run_telnyx_task_bot(
            websocket, stream_id, call_control_id, outbound_encoding,
            system_instruction=(
                "You are the voice control line for Von's Claude Code session, calling Von "
                "back with the result of a task he dictated earlier.\n\n"
                f"The task was: {report_task['instruction']}\n"
                f"It has {outcome}. Result summary: {report_task['result'] or 'no summary was produced'}\n\n"
                "Rules:\n"
                "- Open by saying the task " + outcome + " and give the summary in one or two sentences.\n"
                "- Ask if Von wants any follow-up changes. If he dictates one, read it back, "
                "get an explicit yes, then call submit_task.\n"
                "- If no follow-up, say goodbye briefly.\n"
                "- get_status is available if he asks for details again."
            ),
            connect_directive=(
                "The call has connected. Greet Von briefly and report the task outcome."
            ),
        )
        return

    fb = voice_control.feedback_for_call(call_control_id)
    if fb is not None:
        await run_telnyx_feedback_bot(websocket, stream_id, call_control_id, outbound_encoding, fb)
        return

    fb_report = voice_control.feedback_report_for_call(call_control_id)
    if fb_report is not None:
        await run_telnyx_task_bot(
            websocket, stream_id, call_control_id, outbound_encoding,
            system_instruction=(
                "You are the voice control line for Von's Claude Code session, calling Von "
                f"back after a feedback call to {fb_report['contact'].title()}.\n\n"
                f"The topic was: {fb_report['topic']}\n"
                f"Outcome: {fb_report['status']}\n"
                f"Feedback collected: {fb_report['summary'] or 'none'}\n\n"
                "Rules:\n"
                "- Relay the feedback faithfully in a few sentences.\n"
                "- If Von wants changes made based on it, capture the instruction, read it "
                "back, get a yes, then call submit_task.\n"
                "- switch_session/list_sessions are available if he wants a different session.\n"
                "- Otherwise say goodbye briefly."
            ),
            connect_directive=(
                "The call has connected. Greet Von and relay the feedback from the call."
            ),
        )
        return

    if voice_control.is_control_call(call_control_id):
        await run_telnyx_task_bot(
            websocket, stream_id, call_control_id, outbound_encoding,
            system_instruction=CONTROL_AGENT_PROMPT,
            connect_directive=(
                "The call has connected. Say: 'Claude Code control line — what would you "
                "like the session to do?'"
            ),
        )
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
        # The start event's media_format is authoritative for BOTH directions:
        # despite requesting PCMU in answer/streaming_start, Telnyx expects sent
        # audio in the negotiated leg codec (PCMA on many AU carriers). Pinning
        # outbound to PCMU produced distorted audio on PCMA calls.
        outbound_encoding=outbound_encoding,
        inbound_encoding=outbound_encoding,
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

    async def _start_streaming(label: str) -> None:
        host = request.headers.get("host", "")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/streaming_start",
                    json={
                        "stream_url": f"wss://{host}/telnyx/ws",
                        "stream_track": "inbound_track",
                        "stream_bidirectional_mode": "rtp",
                        "stream_bidirectional_codec": "PCMU",
                    },
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {os.getenv('TELNYX_API_KEY', '')}",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status in (200, 202):
                        logger.info(f"[{label}] answered — streaming started")
                    else:
                        logger.error(f"[{label}] streaming_start failed: {resp.status}")
                        await _telnyx_hangup(call_control_id)
        except Exception as error:
            logger.error(f"[{label}] streaming_start exception: {error!r}")
            await _telnyx_hangup(call_control_id)

    # Outbound legs: approval, task report, feedback contact, feedback report.
    approval_id = phone_approval.decode_client_state(payload.get("client_state"))
    report_task_id = voice_control.decode_report_state(payload.get("client_state"))
    feedback_id = voice_control.decode_state(payload.get("client_state"), "feedback")
    fbreport_id = voice_control.decode_state(payload.get("client_state"), "fbreport")
    if feedback_id and call_control_id:
        if event_type == "call.answered":
            voice_control.bind_feedback_call(call_control_id, feedback_id)
            await _start_streaming(f"Feedback:{feedback_id}")
        elif event_type == "call.hangup":
            fb = voice_control.get_feedback(feedback_id)
            if fb and fb["status"] == "calling":
                # Never answered: report back that the contact was unreachable.
                voice_control.feedback_call_ended(feedback_id)
                fb["summary"] = "The contact did not answer the call."
                asyncio.create_task(_dial_feedback_report(fb))
        return {"ok": True}
    if fbreport_id and call_control_id:
        if event_type == "call.answered":
            voice_control.bind_feedback_report(call_control_id, fbreport_id)
            await _start_streaming(f"FeedbackReport:{fbreport_id}")
        return {"ok": True}
    if report_task_id and call_control_id:
        if event_type == "call.answered":
            voice_control.bind_report_call(call_control_id, report_task_id)
            await _start_streaming(f"VoiceTask:{report_task_id}")
        return {"ok": True}
    if approval_id and call_control_id:
        if event_type == "call.answered":
            phone_approval.bind_call(approval_id, call_control_id)
            host = request.headers.get("host", "")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/streaming_start",
                        json={
                            "stream_url": f"wss://{host}/telnyx/ws",
                            "stream_track": "inbound_track",
                            "stream_bidirectional_mode": "rtp",
                            "stream_bidirectional_codec": "PCMU",
                        },
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {os.getenv('TELNYX_API_KEY', '')}",
                        },
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status in (200, 202):
                            logger.info(f"[Approval:{approval_id}] answered — streaming started")
                        else:
                            body_text = (await resp.text())[:200]
                            logger.error(
                                f"[Approval:{approval_id}] streaming_start failed: {resp.status} {body_text}"
                            )
                            record = phone_approval.get(approval_id)
                            if record:
                                record["status"] = "error"
                                record["detail_status"] = f"streaming_start {resp.status}: {body_text}"
                            await _telnyx_hangup(call_control_id)
            except Exception as error:
                logger.error(f"[Approval:{approval_id}] streaming_start exception: {error!r}")
                record = phone_approval.get(approval_id)
                if record:
                    record["status"] = "error"
                    record["detail_status"] = f"streaming_start exception: {error!r}"[:300]
                await _telnyx_hangup(call_control_id)
        elif event_type == "call.hangup":
            phone_approval.mark_call_ended(approval_id)
        return {"ok": True}

    if event_type == "call.initiated" and payload.get("direction") == "incoming" and call_control_id:
        # The approver's own number gets the Claude control agent; everyone else
        # gets the public GRC bot.
        if voice_control.is_controller(payload.get("from")):
            voice_control.bind_control_call(call_control_id)
            logger.info(f"[VoiceControl] inbound control call from approver — {call_control_id}")
        # Answer WITHOUT stream params. Bundling them into the answer command
        # produced PCMA media negotiation and badly distorted agent audio on
        # inbound calls, while the outbound legs — which attach media with a
        # separate streaming_start after answer — negotiate PCMU and sound
        # clean. The call.answered branch below runs the same streaming_start
        # for inbound, making both paths identical.
        api_key = os.getenv("TELNYX_API_KEY", "")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/answer",
                    json={},
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status in (200, 202):
                        logger.info(f"[Telnyx] inbound answered — streaming attaches on call.answered")
                    else:
                        text = await resp.text()
                        logger.error(f"[Telnyx] answer failed: {resp.status} {text}")
        except Exception as e:
            logger.error(f"[Telnyx] answer exception: {e}")
        return {"ok": True}

    if event_type == "call.answered" and call_control_id:
        # No direction check: every outbound leg (approval, task report, feedback,
        # feedback report) has already returned above via its client_state branch,
        # so any call.answered reaching here is the inbound leg. Telnyx does not
        # reliably report direction="incoming" on call.answered, and gating on it
        # silently skipped streaming_start — the caller heard dead air.
        logger.info(f"[Inbound] call.answered direction={payload.get('direction')!r}")
        await _start_streaming("Inbound")
        return {"ok": True}

    return {"ok": True}


@app.websocket("/telnyx/ws")
async def telnyx_ws(websocket: WebSocket):
    """WebSocket endpoint for Telnyx Media Streams."""
    await run_telnyx_bot(websocket)


@app.post("/api/approval/request")
async def approval_request(request: Request):
    """Create a phone approval and dial the approver. Auth: X-Approval-Secret."""
    if not phone_approval.check_secret(request.headers.get("X-Approval-Secret")):
        return JSONResponse(status_code=403, content={"error": "bad or missing approval secret"})
    data = await request.json()
    title = str(data.get("title", "")).strip()
    detail = str(data.get("detail", "")).strip()
    if not title:
        return JSONResponse(status_code=400, content={"error": "title is required"})
    try:
        record = await phone_approval.create_and_dial(title, detail)
    except RuntimeError as error:
        return JSONResponse(status_code=503, content={"error": str(error)})
    return {"id": record["id"], "status": record["status"]}


@app.post("/api/voicetask")
async def voicetask_submit(request: Request):
    """Queue a task as if dictated (testing / non-voice submissions)."""
    if not phone_approval.check_secret(request.headers.get("X-Approval-Secret")):
        return JSONResponse(status_code=403, content={"error": "bad or missing approval secret"})
    data = await request.json()
    instruction = str(data.get("instruction", "")).strip()
    if len(instruction) < 8:
        return JSONResponse(status_code=400, content={"error": "instruction is required"})
    task = voice_control.submit(instruction, source=str(data.get("source", "api")))
    return {"id": task["id"], "status": task["status"]}


@app.get("/api/voicetask/next")
async def voicetask_next(request: Request):
    """Worker poll: hand out the oldest queued task, marking it running."""
    if not phone_approval.check_secret(request.headers.get("X-Approval-Secret")):
        return JSONResponse(status_code=403, content={"error": "bad or missing approval secret"})
    task = voice_control.next_pending()
    if not task:
        return {"task": None}
    return {"task": {
        "id": task["id"],
        "instruction": task["instruction"],
        "session": task.get("session", "main"),
        "resume_id": task.get("resume_id"),
    }}


@app.post("/api/voicetask/sessions")
async def voicetask_sessions(request: Request):
    """Worker heartbeat: the live Claude sessions on the dev machine."""
    if not phone_approval.check_secret(request.headers.get("X-Approval-Secret")):
        return JSONResponse(status_code=403, content={"error": "bad or missing approval secret"})
    data = await request.json()
    sessions = data.get("sessions")
    if not isinstance(sessions, list):
        return JSONResponse(status_code=400, content={"error": "sessions list required"})
    voice_control.update_live_sessions(sessions)
    return {"ok": True, "count": len(sessions)}


@app.post("/api/voicetask/{task_id}/result")
async def voicetask_result(task_id: str, request: Request):
    """Worker reports completion; the approver gets a call-back with the summary."""
    if not phone_approval.check_secret(request.headers.get("X-Approval-Secret")):
        return JSONResponse(status_code=403, content={"error": "bad or missing approval secret"})
    data = await request.json()
    task = voice_control.finish(task_id, bool(data.get("ok")), str(data.get("result", "")))
    if not task:
        return JSONResponse(status_code=404, content={"error": "unknown task id"})
    if data.get("call_back", True):
        asyncio.create_task(_dial_report_call(task))
    return {"id": task["id"], "status": task["status"]}


@app.get("/api/voicetask/{task_id}")
async def voicetask_status(task_id: str, request: Request):
    if not phone_approval.check_secret(request.headers.get("X-Approval-Secret")):
        return JSONResponse(status_code=403, content={"error": "bad or missing approval secret"})
    task = voice_control.get(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"error": "unknown task id"})
    return {k: task[k] for k in ("id", "instruction", "status", "result")}


@app.get("/api/approval/{approval_id}")
async def approval_status(approval_id: str, request: Request):
    if not phone_approval.check_secret(request.headers.get("X-Approval-Secret")):
        return JSONResponse(status_code=403, content={"error": "bad or missing approval secret"})
    record = phone_approval.get(approval_id)
    if not record:
        return JSONResponse(status_code=404, content={"error": "unknown approval id"})
    return {
        "id": record["id"],
        "status": record["status"],
        "detail_status": record.get("detail_status", ""),
        "spoken": record.get("spoken", ""),
    }


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
    bridge: object | None = None    # AgentTranslationBridge, agent engine only

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
        # Opaque id supplied by the creating device, used to scope the session
        # list back to its owner. Not an identity or an authenticator - it only
        # stops one device listing another's conversations.
        self.client_id: str | None = None

# Module-level registries
translation_sessions: Dict[str, TranslationSession] = {}  # session_id -> session
pc_to_translation: Dict[str, str] = {}                     # pc_id -> session_id

def _translation_voice(code: str, *fallback_env_names: str) -> str:
    """Voice id for a language.

    Checks ELEVENLABS_VOICE_<CODE> first — the convention already used in the
    deployed environment (ELEVENLABS_VOICE_YUE is configured there) — then any
    legacy names for that language, then the shared multilingual voice.
    """
    for name in (f"ELEVENLABS_VOICE_{code.upper()}", *fallback_env_names):
        value = os.getenv(name, "")
        if value:
            return value
    return os.getenv("ELEVENLABS_MULTILINGUAL_VOICE_ID", os.getenv("ELEVENLABS_VOICE_ID", ""))


# Voice + TTS language for each language the UI offers. This map previously held
# only "en" and "zh", which caused two failures: the Apple Watch endpoint
# rejected every other language outright, and the session path fell back to
# TRANSLATION_VOICES["en"] — so a Cantonese or Japanese translation was handed
# to ElevenLabs tagged as English.
#
# NOTE: "language" is NOT currently sent to ElevenLabs — the TTS service is
# constructed with a voice and model only, and lets ElevenLabs infer the language
# from the text. The value is kept here because it is the correct code to send if
# that is ever wired up, and because it documents the Cantonese caveat below.
# Only "voice_id" is read today.
#
# Cantonese caveat, checked against the live /v1/models API: no ElevenLabs model
# (turbo_v2_5, flash_v2_5, multilingual_v2, v3) supports Cantonese — "zh" is the
# only Chinese option. So Cantonese text is synthesised with the Chinese voice and
# read with Mandarin pronunciation. The translation and transcript are correct
# Cantonese; only the spoken audio is approximate. Fixing that needs a provider
# with a yue-HK voice (Azure and Google both have one) or on-device synthesis.
TRANSLATION_VOICES = {
    "en":  {"voice_id": _translation_voice("en", "ELEVENLABS_VOICE_ID"),          "language": Language.EN},
    "zh":  {"voice_id": _translation_voice("zh", "ELEVENLABS_CHINESE_VOICE"),     "language": Language.ZH},
    "yue": {"voice_id": _translation_voice("yue", "ELEVENLABS_CANTONESE_VOICE",
                                           "ELEVENLABS_CHINESE_VOICE"),           "language": Language.ZH},  # see note above
    "ja":  {"voice_id": _translation_voice("ja"),                                  "language": Language.JA},
    "ko":  {"voice_id": _translation_voice("ko"),                                  "language": Language.KO},
    "es":  {"voice_id": _translation_voice("es"),                                  "language": Language.ES},
    "fr":  {"voice_id": _translation_voice("fr"),                                  "language": Language.FR},
    "de":  {"voice_id": _translation_voice("de"),                                  "language": Language.DE},
    "ar":  {"voice_id": _translation_voice("ar"),                                  "language": Language.AR},
    "hi":  {"voice_id": _translation_voice("hi"),                                  "language": Language.HI},
    "fil": {"voice_id": _translation_voice("fil"),                                 "language": Language.FIL},
}


WATCH_TRANSLATION_MAX_BYTES = 6 * 1024 * 1024
watch_translation_slots = asyncio.Semaphore(4)

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
            # Raw codes: these go into the session events as original_lang /
            # translated_lang, and the web UI compares them against its own
            # language codes to decide which half of the screen a turn belongs
            # to. They must stay as codes.
            source_name = source_lang.value
            target_name = target_lang.value
            # Human names for the LLM prompt only. Passing the bare code here
            # ("Translate from en to yue.") is what made Cantonese come back as
            # Mandarin: an ISO code for a Chinese variant carries no instruction
            # about which variety or script is wanted.
            source_prompt_name = translation_prompt_name(source_lang)
            target_prompt_name = translation_prompt_name(target_lang)
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
                f"Translate from {source_prompt_name} to {target_prompt_name}. "
                "Output ONLY the translation, nothing else. "
                "Translate into exactly the target language described, including its "
                "specified script and regional variety. Never substitute a more common "
                "related language or dialect. "
                "If the input consists entirely of filler sounds (e.g. 'um', 'uh', 'ahh', 'hmm') with no meaningful content, output nothing."
            )
            logger.info(f"[Translation] Calling configured LLM for translation...")
            response = await self._llm._client.chat.completions.create(
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

async def run_agent_translation_participant(
    webrtc_connection: SmallWebRTCConnection,
    session: TranslationSession,
    participant: TranslationParticipant,
):
    """Agent engine: one ElevenLabs Agent per participant does STT + LLM + TTS.

    The pipeline is transport-only — the bridge swallows mic audio into the agent websocket
    and the counterpart's bridge queues translated audio back in. See
    elevenlabs_agent_translation for the routing.
    """
    from elevenlabs_agent_translation import (
        AGENT_SAMPLE_RATE,
        AgentTranslationBridge,
        require_agent_config,
    )

    api_key = require_agent_config()

    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # Pinned to the agent's PCM rate so the relay never resamples.
            audio_in_sample_rate=AGENT_SAMPLE_RATE,
            audio_out_sample_rate=AGENT_SAMPLE_RATE,
            # The agent runs its own turn detection server-side, so local VAD is only
            # here to keep the transport's speaking events flowing for the dashboard.
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    def get_other():
        """The counterpart whose language this participant's agent speaks.

        This used to return the first entry with a different pc_id. A stale
        participant left behind by a reconnect therefore won a race against the
        real counterpart, and the agent was configured with that stale
        participant's language — which is how a session could start speaking in
        the wrong voice partway through. Prefer a live participant, and only
        fall back to a disconnected one if there is nothing better.
        """
        stale = None
        for p in session.participants.values():
            if p.pc_id == participant.pc_id:
                continue
            if p.pipeline_task is not None:
                return p
            stale = stale or p
        return stale

    bridge = AgentTranslationBridge(
        session=session,
        participant=participant,
        get_other=get_other,
        api_key=api_key,
    )
    participant.bridge = bridge

    pipeline = Pipeline([
        transport.input(),
        bridge,
        AudioProbeProcessor(label=f"agent:{participant.name}"),
        transport.output(),
    ])

    task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=True))
    participant.pipeline_task = task

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(
            f"[AgentTranslate] participant connected: {participant.name} "
            f"({participant.language.value})"
        )
        connected = [p for p in session.participants.values() if p.pipeline_task is not None]
        if len(connected) < 2:
            logger.info("[AgentTranslate] waiting for counterpart before starting agents")
            return
        # Both languages are known only now, and each agent's output language is the
        # *other* participant's — so neither agent can start before this point.
        session.status = "live"
        await session.event_queue.put({"type": "status", "status": "live"})
        for p in connected:
            if p.bridge is not None:
                await p.bridge.start_agent()

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"[AgentTranslate] participant disconnected: {participant.name}")
        session.status = "ended"
        session.ended_at = datetime.now()
        await session.event_queue.put({"type": "status", "status": "ended"})
        pc_to_translation.pop(participant.pc_id, None)
        # Drop the participant from the session too. Leaving it behind meant a
        # reconnect produced a session holding both the dead and the live entry,
        # and get_other() could then pick the dead one — configuring the agent
        # for the wrong language and voice.
        participant.pipeline_task = None
        session.participants.pop(participant.pc_id, None)
        # Both agents are billed per minute — tear the counterpart's down too rather than
        # leaving it open on a session that can no longer relay anywhere.
        for p in session.participants.values():
            if p.bridge is not None:
                await p.bridge.stop_agent()
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


async def run_translation_participant(
    webrtc_connection: SmallWebRTCConnection,
    session: TranslationSession,
    participant: TranslationParticipant,
):
    from elevenlabs_agent_translation import agent_engine_enabled

    if agent_engine_enabled():
        return await run_agent_translation_participant(
            webrtc_connection, session, participant
        )

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
# Language naming
#
# Three separate copies of a code->name map used to live inside the translation
# processors, and they disagreed: one covered seven languages, another only two.
# Any code missing from a copy was interpolated into the LLM prompt as a bare
# ISO code ("Target language: yue"), and a bare code for a Chinese variant
# reliably came back as Mandarin. That is what made Cantonese answer in
# Mandarin. These two maps are now the single source of truth.
#
# PROMPT_NAMES are deliberately verbose: for languages that a model is prone to
# collapse into a more common relative, the entry spells out the script and the
# variety so the instruction cannot be read as "some kind of Chinese".
# ---------------------------------------------------------------------------

TRANSLATION_PROMPT_NAMES = {
    "en": "English",
    "zh": "Mandarin Chinese, written in Simplified Chinese characters",
    "yue": (
        "Cantonese as spoken in Hong Kong, written in Traditional Chinese characters, "
        "using Cantonese vocabulary and grammar (e.g. 係, 唔, 嘅, 咗, 佢) — "
        "this must NOT be Mandarin/Putonghua"
    ),
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ar": "Arabic",
    "hi": "Hindi",
    "fil": "Filipino (Tagalog)",
    "vi": "Vietnamese",
    "el": "Greek",
}

# Short labels for transcripts and the UI.
TRANSLATION_DISPLAY_NAMES = {
    "en": "English",
    "zh": "Mandarin",
    "yue": "Cantonese",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ar": "Arabic",
    "hi": "Hindi",
    "fil": "Filipino",
    "vi": "Vietnamese",
    "el": "Greek",
}


def _lang_code(lang) -> str:
    """Accept a pipecat Language enum, a raw code, or None."""
    if lang is None:
        return "unknown"
    return getattr(lang, "value", lang)


def translation_prompt_name(lang) -> str:
    """Name to interpolate into an LLM translation prompt."""
    code = _lang_code(lang)
    return TRANSLATION_PROMPT_NAMES.get(code, code)


def translation_display_name(lang) -> str:
    """Short human label for transcripts and session events."""
    code = _lang_code(lang)
    return TRANSLATION_DISPLAY_NAMES.get(code, code)


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
            a_name = translation_prompt_name(self._lang_a)
            b_name = translation_prompt_name(self._lang_b)

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
            a_name = translation_prompt_name(self._lang_a)
            b_name = translation_prompt_name(self._lang_b)

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
        # These labels used to be the literals "Mandarin" and "English" whatever
        # pair was actually configured, so a Cantonese/Japanese session still
        # labelled its transcript rows Mandarin and English.
        if source_lang == self._lang_b:
            return self._lang_b.value, translation_display_name(self._lang_b)
        if source_lang == self._lang_a:
            return self._lang_a.value, translation_display_name(self._lang_a)
        return "unknown", "Speaker"

    def _target_lang(self, source_lang: Language | None) -> Language | None:
        if source_lang == self._lang_b:
            return self._lang_a
        if source_lang == self._lang_a:
            return self._lang_b
        return None

    async def _translate_text(self, text: str, source_lang: Language | None, *, max_tokens: int) -> tuple[str | None, Language | None]:
        a_name = translation_prompt_name(self._lang_a)
        b_name = translation_prompt_name(self._lang_b)

        target_lang = self._target_lang(source_lang)
        target_language = translation_prompt_name(target_lang) if target_lang else None

        system_instruction = (
            "You are a translation engine.\n"
            "Translate only.\n"
            "Never explain, define, annotate, answer questions, or add notes.\n"
            "Return only the translated text.\n"
            "Translate into exactly the target language described, including its "
            "specified script and regional variety. Never substitute a more common "
            "related language or dialect.\n"
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


# ─────────────────────────────────────────────
# Subscription entitlement and translation-minute credits
# ─────────────────────────────────────────────
#
# Two tiers ship in the mobile apps:
#
#   free  — on-device translation only (ML Kit / Apple Translation) plus ads.
#           It never reaches this server, so it consumes no credits and costs
#           nothing per minute. FREE_TIER_SECONDS is 0 by design, not an
#           oversight: a free user who somehow calls the cloud path is refused.
#   pro   — AUD 29.99/month for PAID_TIER_SECONDS of cloud translation.
#
# Balances live in Redis when LIVE_CALL_REDIS_URL is configured. That matters:
# an in-process dict hands every subscriber a fresh 60 minutes on each container
# restart, which on a rolling deploy is unbounded free usage. The in-memory
# fallback exists so local development works without Redis, and it says so in
# the balance payload rather than pretending to be durable.

FREE_TIER_SECONDS = 0
PAID_TIER_SECONDS = int(os.getenv("PAID_TIER_SECONDS", str(60 * 60)))

# Metering is OFF until the store products actually exist. There is no honest way
# to charge someone minutes before they have any way to buy them: with
# enforcement on and no configured store, every install is "free" with zero
# seconds, so the browser build at /vocare and any TestFlight build would be
# refused a session outright. While this is false the server still tracks usage
# — so the numbers are real when you switch it on — it simply does not refuse
# anyone. Set ENTITLEMENT_ENFORCED=true once App Store Connect and Play Console
# are live.
ENTITLEMENT_ENFORCED = os.getenv("ENTITLEMENT_ENFORCED", "false").strip().lower() == "true"

# Whether a sandbox / test purchase counts as a real subscription.
#
# It must, while testing: StoreKit sandbox, TestFlight and Play's licence
# testers are the only ways to exercise a purchase before release, and they all
# produce test receipts. It must NOT in production — a sandbox Apple Account is
# free to create, so accepting sandbox receipts on a live build hands anyone a
# subscription for nothing. Default true because today this app is pre-launch;
# set STORE_ALLOW_SANDBOX=false in the same change that sets
# ENTITLEMENT_ENFORCED=true.
STORE_ALLOW_SANDBOX = os.getenv("STORE_ALLOW_SANDBOX", "true").strip().lower() == "true"

_ENTITLEMENT_PREFIX = os.getenv("LIVE_CALL_REDIS_PREFIX", "vocare:livecalls").rsplit(":", 1)[0] + ":entitlement"
_entitlement_memory: dict[str, dict] = {}
# session_id -> seconds already deducted, so a re-reported total is idempotent.
_session_charges: dict[str, int] = {}
_entitlement_redis = None
_entitlement_redis_tried = False


def _billing_period(now: datetime | None = None) -> str:
    """Credits reset on the calendar month, in UTC so the boundary is unambiguous."""
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def _entitlement_client():
    """Redis client, or None when unconfigured or unreachable. Connects once."""
    global _entitlement_redis, _entitlement_redis_tried
    if _entitlement_redis_tried:
        return _entitlement_redis
    _entitlement_redis_tried = True
    url = os.getenv("LIVE_CALL_REDIS_URL", "").strip()
    if not url:
        logger.info("[entitlement] LIVE_CALL_REDIS_URL unset - balances are in-process only")
        return None
    if url.startswith("redis://") and os.getenv("LIVE_CALL_REDIS_ALLOW_INSECURE", "false").lower() != "true":
        logger.error("[entitlement] refusing plaintext redis:// URL; use rediss:// or set LIVE_CALL_REDIS_ALLOW_INSECURE=true")
        return None
    try:
        import redis.asyncio as aioredis

        _entitlement_redis = aioredis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=float(os.getenv("LIVE_CALL_REDIS_CONNECT_TIMEOUT_SECS", "2")),
            socket_timeout=float(os.getenv("LIVE_CALL_REDIS_TIMEOUT_SECS", "2")),
        )
        logger.info("[entitlement] using Redis for credit balances")
    except Exception:
        logger.exception("[entitlement] could not build a Redis client; falling back to memory")
        _entitlement_redis = None
    return _entitlement_redis


def _entitlement_key(subject: str) -> str:
    return f"{_ENTITLEMENT_PREFIX}:{subject}"


def _tier_seconds(tier: str) -> int:
    return PAID_TIER_SECONDS if tier == "pro" else FREE_TIER_SECONDS


def _blank_record(tier: str = "free") -> dict:
    return {"tier": tier, "period": _billing_period(), "used": 0}


async def _read_entitlement(subject: str) -> tuple[dict, bool]:
    """Return (record, durable). Rolls the record over when the month changes."""
    client = _entitlement_client()
    record = None
    durable = client is not None
    if client is not None:
        try:
            raw = await client.hgetall(_entitlement_key(subject))
            if raw:
                record = {"tier": raw.get("tier", "free"), "period": raw.get("period", ""), "used": int(raw.get("used", 0) or 0)}
        except Exception:
            logger.exception("[entitlement] Redis read failed for %s; serving from memory", subject)
            durable = False
    if record is None:
        record = _entitlement_memory.get(subject)
    if record is None:
        record = _blank_record()
    if record["period"] != _billing_period():
        # New month: the allowance refills, the tier carries over.
        record = _blank_record(record["tier"])
        await _write_entitlement(subject, record)
    return record, durable


async def _write_entitlement(subject: str, record: dict) -> None:
    _entitlement_memory[subject] = dict(record)
    client = _entitlement_client()
    if client is None:
        return
    try:
        await client.hset(_entitlement_key(subject), mapping={
            "tier": record["tier"], "period": record["period"], "used": str(record["used"]),
        })
        # Two full billing periods is enough history to survive a late renewal
        # without keeping a row for every install that ever opened the app.
        await client.expire(_entitlement_key(subject), 70 * 24 * 3600)
    except Exception:
        logger.exception("[entitlement] Redis write failed for %s", subject)


def _balance_payload(record: dict, durable: bool) -> dict:
    total = _tier_seconds(record["tier"])
    # Usage is recorded against the tier's allowance even when unenforced, but it
    # is not clamped to it — the raw figure is what tells you, before launch,
    # what real usage per install looks like.
    used = record["used"]
    return {
        "tier": record["tier"],
        "period": record["period"],
        "seconds_total": total,
        # Reported unclamped on purpose. With enforcement off the free tier's
        # total is 0, so clamping would report every install as having used
        # nothing — hiding exactly the pre-launch usage this is here to collect.
        "seconds_used": used,
        "seconds_remaining": max(0, total - used),
        "durable": durable,
        "enforced": ENTITLEMENT_ENFORCED,
        "sandbox_ok": STORE_ALLOW_SANDBOX,
    }


def _entitlement_subject(value: str | None) -> str:
    subject = (value or "").strip()
    # The subject is an opaque per-install id, not a credential. It scopes a
    # balance; it does not prove a purchase. Only /api/entitlement/activate,
    # which checks the store receipt, can move an install onto the paid tier.
    if not subject or len(subject) > 128:
        return ""
    return subject


# ── Store receipt verification ──
#
# Both stores are verified server-side because the client cannot be trusted with
# entitlement: a rooted or jailbroken device can make the app claim anything.
# Each verifier returns:
#
#   "pro"  — a valid, unexpired, unrevoked subscription
#   "free" — verified, and the user does not hold one
#   None   — we could not tell (unconfigured, or the store/network failed)
#
# None is deliberately distinct from "free". Failing closed on a network blip
# would strip paying subscribers of minutes they already bought, so callers keep
# the last known tier when verification is merely unavailable.

APPLE_ROOT_CA_G3_URL = "https://www.apple.com/certificateauthority/AppleRootCA-G3.cer"
_apple_root_cert: bytes | None = None


async def _apple_root_ca() -> bytes | None:
    """Apple's root certificate, fetched once and cached in the process.

    Bundling it in the image would be sturdier, but it rotates on Apple's
    schedule rather than ours; fetching once per container start keeps it
    current without a request per verification.
    """
    global _apple_root_cert
    if _apple_root_cert is not None:
        return _apple_root_cert
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(APPLE_ROOT_CA_G3_URL, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    logger.error("[entitlement] Apple root CA fetch returned %s", resp.status)
                    return None
                _apple_root_cert = await resp.read()
    except Exception:
        logger.exception("[entitlement] could not fetch Apple root CA")
        return None
    return _apple_root_cert


async def _verify_apple_jws(jws: str) -> str | None:
    """Verify a StoreKit 2 signed transaction.

    The JWS header carries an x5c chain: leaf -> intermediate -> Apple root. We
    check the chain terminates at Apple's real root, verify the ES256 signature
    with the leaf's public key, then check the payload actually describes an
    active subscription to OUR product in OUR app. Skipping the chain check and
    trusting the embedded certificate is the classic mistake here: anyone can
    sign a payload and attach their own certificate.
    """
    try:
        import jwt
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError:
        logger.error("[entitlement] pyjwt/cryptography missing; cannot verify Apple receipts")
        return None

    try:
        header = jwt.get_unverified_header(jws)
        chain = header.get("x5c") or []
        if len(chain) < 2:
            logger.warning("[entitlement] Apple JWS has no certificate chain")
            return "free"

        certs = [x509.load_der_x509_certificate(base64.b64decode(c)) for c in chain]

        root_der = await _apple_root_ca()
        if root_der is None:
            return None
        apple_root = x509.load_der_x509_certificate(root_der)
        if certs[-1].fingerprint(hashes.SHA256()) != apple_root.fingerprint(hashes.SHA256()):
            logger.warning("[entitlement] Apple JWS chain does not terminate at Apple's root")
            return "free"

        # Each certificate must actually be signed by the next one up.
        now = datetime.now(timezone.utc)
        for child, parent in zip(certs, certs[1:]):
            parent.public_key().verify(
                child.signature,
                child.tbs_certificate_bytes,
                ec.ECDSA(child.signature_hash_algorithm),
            )
            if not (child.not_valid_before_utc <= now <= child.not_valid_after_utc):
                logger.warning("[entitlement] Apple JWS certificate outside validity window")
                return "free"

        payload = jwt.decode(
            jws,
            certs[0].public_key(),
            algorithms=["ES256"],
            options={"verify_aud": False, "verify_exp": False},
        )
    except Exception:
        logger.exception("[entitlement] Apple JWS verification failed")
        return "free"

    bundle_id = os.getenv("APPLE_BUNDLE_ID", "com.vocare.translate").strip()
    if payload.get("bundleId") != bundle_id:
        logger.warning("[entitlement] Apple JWS is for bundle %r, not %r", payload.get("bundleId"), bundle_id)
        return "free"
    if payload.get("productId") != os.getenv("STORE_PRODUCT_ID", "vocare_pro_monthly"):
        return "free"
    if payload.get("revocationDate"):
        return "free"

    # StoreKit stamps every transaction with the environment that produced it.
    # A Sandbox receipt is cryptographically valid — it is signed by Apple — so
    # the signature check above passes and only this rejects it.
    environment = (payload.get("environment") or "Production").strip()
    if environment != "Production" and not STORE_ALLOW_SANDBOX:
        logger.warning("[entitlement] refusing %s receipt; STORE_ALLOW_SANDBOX is false", environment)
        return "free"
    if environment != "Production":
        logger.info("[entitlement] accepting %s receipt (STORE_ALLOW_SANDBOX=true)", environment)

    # StoreKit timestamps are milliseconds since the epoch.
    expires_ms = payload.get("expiresDate")
    if expires_ms and datetime.fromtimestamp(expires_ms / 1000, timezone.utc) <= datetime.now(timezone.utc):
        return "free"
    return "pro"


async def _verify_google_token(purchase_token: str) -> str | None:
    """Verify a Play purchase token against the Play Developer API.

    Needs a service account with the "View financial data" permission, linked to
    the Play Console. GOOGLE_PLAY_SERVICE_ACCOUNT_JSON holds its key material.
    """
    raw = os.getenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    package = os.getenv("ANDROID_PACKAGE_NAME", "com.vocare.translate").strip()

    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import service_account
    except ImportError:
        logger.error("[entitlement] google-auth missing; cannot verify Play receipts")
        return None

    try:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=["https://www.googleapis.com/auth/androidpublisher"]
        )
        # Blocking refresh; short and cached inside the credentials object, so it
        # is kept off the event loop rather than made async.
        await asyncio.to_thread(creds.refresh, GoogleRequest())

        url = (
            f"https://androidpublisher.googleapis.com/androidpublisher/v3/applications/"
            f"{quote(package, safe='')}/purchases/subscriptionsv2/tokens/{quote(purchase_token, safe='')}"
        )
        async with aiohttp.ClientSession() as http:
            async with http.get(
                url,
                headers={"Authorization": f"Bearer {creds.token}"},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status == 404:
                    # Play does not know this token: not a transient failure.
                    return "free"
                if resp.status != 200:
                    logger.warning("[entitlement] Play API returned %s", resp.status)
                    return None
                body = await resp.json()
    except Exception:
        logger.exception("[entitlement] Play verification failed")
        return None

    # Play marks a licence-tester purchase with a testPurchase object rather than
    # an environment string. Same rule as Apple's Sandbox.
    if body.get("testPurchase") is not None:
        if not STORE_ALLOW_SANDBOX:
            logger.warning("[entitlement] refusing Play test purchase; STORE_ALLOW_SANDBOX is false")
            return "free"
        logger.info("[entitlement] accepting Play test purchase (STORE_ALLOW_SANDBOX=true)")

    state = body.get("subscriptionState")
    if state in ("SUBSCRIPTION_STATE_ACTIVE", "SUBSCRIPTION_STATE_IN_GRACE_PERIOD"):
        return "pro"
    return "free"


async def _verify_purchase(platform: str, receipt: str) -> str | None:
    """Dispatch to the right store verifier. Unknown platform verifies nothing."""
    if not receipt:
        return None
    if platform == "ios":
        return await _verify_apple_jws(receipt)
    if platform == "android":
        return await _verify_google_token(receipt)
    logger.warning("[entitlement] unknown purchase platform %r", platform)
    return None


async def _entitlement_gate(subject: str | None):
    """None when this install may open a cloud leg, else a 402 JSONResponse.

    Left deliberately permissive when no subject is supplied: the browser build
    at /vocare has no store identity, and locking it out would break the demo
    and the load tests. Native builds always send one.
    """
    if not ENTITLEMENT_ENFORCED:
        return None
    subject = _entitlement_subject(subject)
    if not subject:
        return None
    record, durable = await _read_entitlement(subject)
    payload = _balance_payload(record, durable)
    if payload["seconds_remaining"] > 0:
        return None
    return JSONResponse(
        {"error": "no_translation_credit", "balance": payload},
        status_code=402,
    )


@app.get("/api/entitlement")
async def get_entitlement(request: Request):
    """Current tier and remaining translation seconds for one install."""
    subject = _entitlement_subject(request.query_params.get("subject"))
    if not subject:
        return JSONResponse({"error": "subject required"}, status_code=400)
    record, durable = await _read_entitlement(subject)
    return _balance_payload(record, durable)


@app.post("/api/entitlement/activate")
async def activate_entitlement(request: Request):
    """Move an install onto the tier its store receipt actually supports.

    The client sends the receipt it got from StoreKit or Play; the server
    verifies it with Apple or Google before believing it, so a patched client
    cannot mint minutes. When verification is unavailable the existing tier is
    left alone and the response says `verified: false`, which is the honest
    state for a build with no store credentials configured.
    """
    data = await request.json()
    subject = _entitlement_subject(data.get("subject"))
    if not subject:
        return JSONResponse({"error": "subject required"}, status_code=400)

    platform = (data.get("platform") or "").strip().lower()
    receipt = (data.get("receipt") or "").strip()

    record, durable = await _read_entitlement(subject)
    verified_tier = await _verify_purchase(platform, receipt) if receipt else None

    if verified_tier is not None:
        record["tier"] = verified_tier
        await _write_entitlement(subject, record)
    elif not receipt:
        # No receipt at all means the app found no purchase on this device.
        # That is a verified "free" only when the store was actually reachable,
        # which the client signals explicitly.
        if data.get("store_reachable") is True:
            record["tier"] = "free"
            await _write_entitlement(subject, record)

    payload = _balance_payload(record, durable)
    payload["verified"] = verified_tier is not None
    return payload


@app.post("/api/entitlement/consume")
async def consume_entitlement(request: Request):
    """Deduct elapsed cloud translation seconds from an install's balance.

    Called when a session ends and periodically while one runs, so that a call
    that dies without a clean teardown still bills for the part that happened.
    Consumption is monotonic per session: the client reports total elapsed
    seconds for that session, never a delta, so a retry cannot double-charge.
    """
    data = await request.json()
    subject = _entitlement_subject(data.get("subject"))
    if not subject:
        return JSONResponse({"error": "subject required"}, status_code=400)
    try:
        elapsed = max(0, int(data.get("session_seconds", 0)))
    except (TypeError, ValueError):
        return JSONResponse({"error": "session_seconds must be an integer"}, status_code=400)

    session_id = (data.get("session_id") or "").strip()
    record, durable = await _read_entitlement(subject)

    # Track what this session has already been charged so repeated reports
    # settle to the highest watermark rather than accumulating.
    charged = _session_charges.get(session_id, 0) if session_id else 0
    delta = max(0, elapsed - charged)
    if session_id:
        _session_charges[session_id] = max(charged, elapsed)

    if delta:
        record["used"] = record["used"] + delta
        await _write_entitlement(subject, record)

    return _balance_payload(record, durable)



@app.post("/api/translation/session")
async def create_translation_session(request: Request):
    """Create a new translation session. Returns session_id + join link."""
    data = await request.json()
    # A 4-digit code gave 9,000 possibilities, and the session-detail endpoint is
    # unauthenticated - so every transcript on the container could be enumerated
    # in seconds. The id is the capability that guards a transcript, so it has to
    # be unguessable.
    session_id = f"GRC-{secrets.token_urlsafe(12)}"
    session = TranslationSession(
        session_id=session_id,
        caller_name=data.get("caller_name", "Unknown"),
        caller_lang=data.get("caller_language", "zh"),
        topic=data.get("topic", "General"),
    )
    client_id = (data.get("client_id") or "").strip()
    session.client_id = client_id or None
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

    # Cloud translation is the metered path. Refusing here rather than at
    # session creation means a session that runs out mid-call still ends
    # cleanly; only a *new* leg is turned away.
    gate = await _entitlement_gate(session.client_id)
    if gate is not None:
        return gate

    language = data.get("language", "en")
    name = data.get("name", "Participant")
    voice_config = TRANSLATION_VOICES.get(language)
    if voice_config is None:
        # Falling back silently used to mean a Cantonese or Japanese session was
        # synthesised as English with no trace in the logs. Still fall back so the
        # session starts, but say so.
        logger.warning(
            f"[translation] no voice configured for language={language!r}; "
            f"falling back to English. Supported: {sorted(TRANSLATION_VOICES)}"
        )
        voice_config = TRANSLATION_VOICES["en"]

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

@app.post("/api/translation/ptt")
async def translation_ptt(request: Request):
    """Control the server-side PTT audio gate for an ElevenLabs Agent participant."""
    data = await request.json()
    pc_id = data.get("pc_id")
    action = data.get("action")
    session_id = pc_to_translation.get(pc_id)
    session = translation_sessions.get(session_id) if session_id else None
    participant = session.participants.get(pc_id) if session else None
    bridge = participant.bridge if participant else None
    # The offer response can reach the browser a few milliseconds before FastAPI's
    # participant background task assigns its bridge. Absorb that startup race so the
    # user's first press is not rejected.
    for _ in range(20):
        if bridge is not None:
            break
        await asyncio.sleep(0.05)
        bridge = participant.bridge if participant else None

    if bridge is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": "Translation participant is not ready"},
        )
    from elevenlabs_agent_translation import BridgeDisconnected

    if action == "hold":
        opened = await bridge.hold_to_speak_started()
        if opened == "disconnected":
            return JSONResponse(
                status_code=410,
                content={
                    "ok": False,
                    "state": "disconnected",
                    "error": "The live session is no longer connected. Rejoin and try again.",
                },
            )
        if opened == "busy" or opened is False:
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "state": "translating",
                    "error": "This speaker's previous translation is still in progress",
                },
            )
        return {"ok": True, "state": "recording"}
    if action == "release":
        try:
            flushed_bytes = await bridge.hold_to_speak_released()
        except BridgeDisconnected:
            return JSONResponse(
                status_code=410,
                content={
                    "ok": False,
                    "state": "disconnected",
                    "error": "The live session is no longer connected. Rejoin and try again.",
                },
            )
        return {"ok": True, "state": "released", "flushed_bytes": flushed_bytes}
    return JSONResponse(
        status_code=400,
        content={"ok": False, "error": "action must be 'hold' or 'release'"},
    )

@app.post("/api/watch/translate")
async def watch_translate(request: Request):
    """Translate a complete watchOS PTT recording only after the button is released."""
    source = request.headers.get("X-Vocare-Source-Language", "en").lower().split("-")[0]
    target = request.headers.get("X-Vocare-Target-Language", "zh").lower().split("-")[0]
    # The watch runs through the ElevenLabs Agent stack, which needs a agent
    # provisioned per spoken language. Check that here so an unprovisioned
    # language is a clean 400 rather than a RuntimeError surfacing as a 502.
    from elevenlabs_agent_translation import has_agent_for_language

    watch_supported = sorted(c for c in TRANSLATION_VOICES if has_agent_for_language(c))
    if source == target or source not in watch_supported or target not in watch_supported:
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    "Unsupported language pair. Languages available on the watch: "
                    + (", ".join(watch_supported) or "none provisioned")
                    + "; source and target must differ."
                )
            },
        )

    if request.headers.get("content-type", "").split(";")[0].lower() not in {"audio/wav", "audio/x-wav"}:
        return JSONResponse(status_code=415, content={"error": "Expected an audio/wav recording."})

    wav_data = await request.body()
    if not wav_data or len(wav_data) > WATCH_TRANSLATION_MAX_BYTES:
        return JSONResponse(status_code=413, content={"error": "Recording is empty or too large."})

    try:
        with wave.open(io.BytesIO(wav_data), "rb") as recording:
            if (
                recording.getnchannels() != 1
                or recording.getsampwidth() != 2
                or recording.getframerate() != 16000
                or recording.getcomptype() != "NONE"
            ):
                raise ValueError("Expected mono 16-bit PCM WAV at 16 kHz")
            frame_count = recording.getnframes()
            if frame_count > 16000 * 90:
                raise ValueError("Watch recordings are limited to 90 seconds")
            pcm = recording.readframes(frame_count)
    except (wave.Error, EOFError, ValueError) as error:
        message = str(error) or "The recording was not a readable WAV file."
        return JSONResponse(status_code=400, content={"error": message})

    if len(pcm) < 3200:  # less than 100ms at PCM16/16kHz
        return JSONResponse(status_code=400, content={"error": "Recording was too short."})

    # Reject inaudible recordings before opening an agent session. A dead or
    # disconnected microphone (e.g. a simulator with no audio input) produces a
    # near-zero waveform; the agent hears nothing, never responds, and the
    # request burns the full 30 s response timeout. The threshold must stay far
    # below quiet-but-real speech: the watch records in measurement mode (no
    # AGC), so genuine wrist-distance speech can peak surprisingly low.
    samples = array.array("h", pcm)
    peak = max(abs(sample) for sample in samples)
    rms = int((sum(sample * sample for sample in samples) / len(samples)) ** 0.5)
    logger.info(f"[WatchTranslate] {source}->{target} {len(pcm)}B peak={peak} rms={rms}")
    if peak < 150:
        return JSONResponse(
            status_code=422,
            content={"error": "No speech detected — check that the microphone is working."},
        )

    from elevenlabs_agent_translation import translate_buffered_utterance

    if not TRANSLATION_VOICES[target]["voice_id"]:
        return JSONResponse(status_code=503, content={"error": "The target-language voice is not configured."})

    try:
        async with watch_translation_slots:
            original, translated, output_pcm = await asyncio.wait_for(
                translate_buffered_utterance(
                    pcm=pcm,
                    source_language=source,
                    target_language=target,
                    voice_id=TRANSLATION_VOICES[target]["voice_id"],
                ),
                timeout=55,
            )
    except asyncio.TimeoutError:
        return JSONResponse(status_code=504, content={"error": "Translation timed out. Please try again."})
    except Exception:
        logger.exception("[WatchTranslate] translation failed")
        return JSONResponse(status_code=502, content={"error": "Translation service failed."})

    if not translated or not output_pcm:
        return JSONResponse(status_code=422, content={"error": "No translatable speech was detected."})

    output = io.BytesIO()
    with wave.open(output, "wb") as wav_output:
        wav_output.setnchannels(1)
        wav_output.setsampwidth(2)
        wav_output.setframerate(16000)
        wav_output.writeframes(output_pcm)

    return {
        "original": original,
        "translated": translated,
        "source_language": source,
        "target_language": target,
        "audio_wav_base64": base64.b64encode(output.getvalue()).decode("ascii"),
    }

@app.get("/api/translation/sessions")
async def list_translation_sessions(request: Request):
    """Sessions belonging to the calling device.

    This used to return every session on the container to any anonymous caller,
    which exposed other people's conversation metadata — caller names, topics and
    session codes — and, combined with guessable codes, their transcripts too.
    A caller now sees only sessions created with its own client_id; a request
    without one gets an empty list rather than everyone else's.
    """
    client_id = (request.query_params.get("client_id") or "").strip()
    if not client_id:
        return {"sessions": []}

    sessions = []
    for s in translation_sessions.values():
        if s.client_id != client_id:
            continue
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

@app.get("/healthz")
async def healthz():
    """Liveness probe for the mobile shells, uptime monitoring and the container
    health check. Deliberately touches no LLM, database or session state - it
    answers one question: is this container awake?"""
    return {"status": "ok", "service": "vocare-translate"}

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy():
    """Public privacy policy. Both app stores require a reachable URL, and Play
    requires one unconditionally because the app declares RECORD_AUDIO."""
    html_path = os.path.join(os.path.dirname(__file__), "static", "privacy.html")
    with open(html_path) as f:
        return HTMLResponse(content=f.read())

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
