"""ElevenLabs Agents-powered live translation (Option B: agent owns the whole leg).

The cascaded path in bot.py runs ElevenLabs STT → external LLM → ElevenLabs TTS as three
separate services. This module replaces all three with a single ElevenLabs Agent per
participant, so the only AI vendor in the path is ElevenLabs.

The routing trick: an Agent streams its audio back on the same session it received audio
on, but a translator needs A's translated speech to come out of B's speaker. So the
Conversation is held server-side and its AudioInterface.output() is pointed at the *other*
participant's pipeline — structurally the same cross-injection TranslationProcessor does.

    A's mic ──► A's bridge ──► Agent(A) ──► output() ──► B's transport.output()
    B's mic ──► B's bridge ──► Agent(B) ──► output() ──► A's transport.output()

Agent audio is 16-bit PCM mono @ 16kHz in both directions (elevenlabs SDK AudioInterface
contract), which is why the transports are pinned to 16kHz — it avoids resampling on the
hot path entirely.
"""

from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, Optional

from elevenlabs.client import ElevenLabs
from elevenlabs.conversational_ai.conversation import (
    AsyncAudioInterface,
    AsyncConversation,
    ConversationInitiationData,
)
from loguru import logger
from pipecat.frames.frames import (
    InputAudioRawFrame,
    OutputAudioRawFrame,
    InterruptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from translation_agent_prompt import language_name, translation_prompt

# The SDK's AudioInterface contract: 16-bit PCM mono @ 16kHz, ~4000 samples (250ms).
AGENT_SAMPLE_RATE = 16000
AGENT_CHUNK_SAMPLES = 4000
AGENT_CHUNK_BYTES = AGENT_CHUNK_SAMPLES * 2  # 16-bit

class RelayAudioInterface(AsyncAudioInterface):
    """AudioInterface that relays agent audio to the *other* participant.

    `start()` is awaited inside the SDK's websocket loop, so it only stores the callback —
    audio is fed in later by the bridge as WebRTC frames arrive.
    """

    def __init__(
        self,
        label: str,
        on_output: Callable[[bytes], Awaitable[None]],
        on_interrupt: Callable[[], Awaitable[None]],
    ):
        self._label = label
        self._on_output = on_output
        self._on_interrupt = on_interrupt
        self._input_callback: Optional[Callable[[bytes], Awaitable[None]]] = None
        self._started = asyncio.Event()
        self._logged_first_output = False

    async def start(self, input_callback: Callable[[bytes], Awaitable[None]]):
        self._input_callback = input_callback
        self._started.set()
        logger.info(f"[AgentTranslate:{self._label}] audio interface started")

    async def stop(self):
        self._input_callback = None
        self._started.clear()
        logger.info(f"[AgentTranslate:{self._label}] audio interface stopped")

    async def output(self, audio: bytes):
        if not self._logged_first_output:
            self._logged_first_output = True
            logger.info(
                f"[AgentTranslate:{self._label}] first agent audio out — {len(audio)} bytes"
            )
        await self._on_output(audio)

    async def interrupt(self):
        logger.debug(f"[AgentTranslate:{self._label}] interrupt")
        await self._on_interrupt()

    async def send(self, pcm: bytes):
        """Feed caller audio to the agent. No-op until the SDK has handed us a callback."""
        if self._input_callback is None:
            return
        await self._input_callback(pcm)

    @property
    def ready(self) -> bool:
        return self._input_callback is not None


class AgentTranslationBridge(FrameProcessor):
    """Bridges one participant's WebRTC audio into a per-participant ElevenLabs Agent.

    Sits where STT → LLM → TTS used to sit. Inbound mic audio is swallowed here (the agent
    consumes it over its own websocket); everything else — including the OutputAudioRawFrames
    queued in by the *other* participant's bridge — passes straight through to the transport.
    """

    def __init__(self, session, participant, get_other, api_key: str, **kwargs):
        super().__init__(**kwargs)
        self._session = session
        self._participant = participant
        self._get_other = get_other
        self._api_key = api_key

        self._conversation: Optional[AsyncConversation] = None
        self._audio: Optional[RelayAudioInterface] = None
        self._buffer = bytearray()
        self._pending_original: Optional[str] = None
        self._started = False

    # -- lifecycle ---------------------------------------------------------

    async def start_agent(self):
        """Open the Agent session. Safe to call more than once."""
        if self._started:
            return
        self._started = True

        me = self._participant
        other = self._get_other()
        if other is None:
            logger.warning(
                f"[AgentTranslate:{me.name}] no counterpart yet — agent will start without "
                "an output target; audio is dropped until they join"
            )

        source = language_name(me.language.value)
        # Output language is the *counterpart's* language. Falls back to English so a
        # single-participant session still produces something rather than erroring.
        target_code = other.language.value if other else "en"
        target = language_name(target_code)

        self._audio = RelayAudioInterface(
            label=me.name,
            on_output=self._relay_output,
            on_interrupt=self._relay_interrupt,
        )

        # The agent is chosen by output language rather than reconfigured per session:
        # the TTS model is validated against the agent's baked-in language and model_id
        # cannot be overridden, so English and Mandarin need separate agents.
        agent_id = agent_id_for_language(target_code)

        voice_id = (other.voice_config if other else me.voice_config).get("voice_id", "")
        overrides = {
            "agent": {
                "prompt": {"prompt": translation_prompt(source, target)},
                "first_message": "",
            },
            "tts": {
                "voice_id": voice_id,
                "speed": _tts_float(target_code, "speed", 1.0),
                "stability": _tts_float(target_code, "stability", 0.35),
                "similarity_boost": _tts_float(target_code, "similarity_boost", 0.75),
            },
        }

        # A *sync* client on purpose: AsyncConversation calls
        # client.conversational_ai.conversations.get_signed_url() without awaiting it
        # (SDK 2.63.0), so an AsyncElevenLabs here raises
        # "'coroutine' object has no attribute 'signed_url'". The call is already wrapped
        # in run_in_executor, so the sync client does not block the event loop.
        client = ElevenLabs(api_key=self._api_key)
        self._conversation = AsyncConversation(
            client,
            agent_id,
            requires_auth=True,
            audio_interface=self._audio,
            config=ConversationInitiationData(conversation_config_override=overrides),
            callback_user_transcript=self._on_user_transcript,
            callback_agent_response=self._on_agent_response,
        )
        await self._conversation.start_session()
        logger.info(
            f"[AgentTranslate:{me.name}] agent session started — {source} → {target} "
            f"(voice={voice_id[:8]}…, agent={agent_id})"
        )

    async def stop_agent(self):
        if self._conversation is not None:
            try:
                await self._conversation.end_session()
            except Exception:
                logger.exception(f"[AgentTranslate:{self._participant.name}] end_session failed")
            self._conversation = None
        self._started = False

    # -- agent → other participant ----------------------------------------

    async def _relay_output(self, audio: bytes):
        other = self._get_other()
        if other is None or other.pipeline_task is None:
            return
        await other.pipeline_task.queue_frames(
            [
                OutputAudioRawFrame(
                    audio=audio,
                    sample_rate=AGENT_SAMPLE_RATE,
                    num_channels=1,
                )
            ]
        )

    async def _relay_interrupt(self):
        other = self._get_other()
        if other is None or other.pipeline_task is None:
            return
        # Drops whatever this speaker's translation had already buffered in the
        # counterpart's transport, so a barge-in doesn't leave stale audio playing.
        await other.pipeline_task.queue_frames([InterruptionFrame()])

    # -- transcript events (same shape the dashboard already consumes) ------

    async def _on_user_transcript(self, transcript: str):
        text = (transcript or "").strip()
        if not text:
            return
        self._pending_original = text
        me = self._participant
        logger.info(f"[AgentTranslate:{me.name}] heard: {text[:60]!r}")
        self._session.live_transcripts[me.pc_id] = {
            "speaker": me.pc_id,
            "speaker_name": me.name,
            "text": text,
        }
        await self._session.event_queue.put(
            {
                "type": "live",
                "speaker": me.pc_id,
                "speaker_name": me.name,
                "text": text,
            }
        )

    async def _on_agent_response(self, response: str):
        translated = (response or "").strip()
        if not translated:
            return
        me = self._participant
        other = self._get_other()
        original = self._pending_original or ""
        self._pending_original = None

        event = {
            "type": "turn",
            "speaker": me.pc_id,
            "speaker_name": me.name,
            "original": original,
            "original_lang": me.language.value,
            "translated": translated,
            "translated_lang": other.language.value if other else me.language.value,
        }
        logger.info(f"[AgentTranslate:{me.name}] translated: {translated[:60]!r}")
        self._session.live_transcripts.pop(me.pc_id, None)
        self._session.transcript.append(event)
        await self._session.event_queue.put(event)

    # -- frame plumbing ----------------------------------------------------

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
            # Mic audio belongs to the agent websocket, not the rest of the pipeline.
            await self._feed(frame.audio)
            return

        await self.push_frame(frame, direction)

    async def _feed(self, pcm: bytes):
        if self._audio is None or not self._audio.ready:
            return
        self._buffer.extend(pcm)
        while len(self._buffer) >= AGENT_CHUNK_BYTES:
            chunk = bytes(self._buffer[:AGENT_CHUNK_BYTES])
            del self._buffer[:AGENT_CHUNK_BYTES]
            try:
                await self._audio.send(chunk)
            except Exception:
                logger.exception(f"[AgentTranslate:{self._participant.name}] send failed")
                return


def _tts_float(lang_code: str, key: str, default: float) -> float:
    """Reuse the per-language TTS env knobs the cascaded path already honours."""
    lang = lang_code.split("-")[0].upper()
    for name in (f"ELEVENLABS_TTS_{lang}_{key.upper()}", f"ELEVENLABS_TTS_{key.upper()}"):
        raw = os.getenv(name)
        if raw:
            try:
                return float(raw)
            except ValueError:
                logger.warning(f"[AgentTranslate] {name}={raw!r} is not a float — ignoring")
    return default


def agent_engine_enabled() -> bool:
    return os.getenv("TRANSLATION_ENGINE", "cascaded").strip().lower() == "agent"


def agent_id_for_language(lang_code: str) -> str:
    """Agent id for the agent that *speaks* `lang_code`."""
    lang = lang_code.split("-")[0].lower()
    env_name = f"ELEVENLABS_TRANSLATE_AGENT_{lang.upper()}"
    agent_id = os.getenv(env_name, "").strip()
    if not agent_id:
        raise RuntimeError(
            f"{env_name} is not set — run `python create_translation_agent.py --lang {lang}` "
            "to provision the agent that speaks this language"
        )
    return agent_id


def require_agent_config() -> str:
    """Return the ElevenLabs API key, raising with an actionable message if unset."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    return api_key
