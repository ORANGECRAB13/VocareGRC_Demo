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

import array
import asyncio
import os
import time
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

from translation_agent_prompt import language_name, language_note, translation_prompt

# The SDK's AudioInterface contract: 16-bit PCM mono @ 16kHz, ~4000 samples (250ms).
AGENT_SAMPLE_RATE = 16000
AGENT_CHUNK_SAMPLES = 4000
AGENT_CHUNK_BYTES = AGENT_CHUNK_SAMPLES * 2  # 16-bit
PTT_MAX_SECONDS = 90
PTT_MAX_BYTES = AGENT_SAMPLE_RATE * 2 * PTT_MAX_SECONDS
# Ceiling for a turn with no response. Healthy turns measure 1.6-2.1s
# release-to-event (live QA), so anything at or below ~5s would fail good
# translations mid-flight; the ElevenLabs agents are configured to
# retranscribe a missed turn at 7s server-side, so 10s covers that rescue
# plus margin before the client gives up and frees the speaker.
PTT_TURN_TIMEOUT_SECONDS = 10
# 2.5 s of PCM silence. One second was not reliably enough for the EN agent's
# end-of-turn detection: QA on 2026-08-26 saw ~50% of ZH→EN turns hang until the
# 30 s response timeout, and the identical audio passed 3/3 once padded to 2.5 s.
# The padding streams over the websocket far faster than real time, so the added
# latency is negligible next to a 30 s timeout retry.
PTT_COMMIT_SILENCE_BYTES = AGENT_SAMPLE_RATE * 5  # 2.5 s of 16-bit PCM silence
PTT_RTP_DRAIN_SECONDS = 0.18


class BridgeDisconnected(RuntimeError):
    """Raised when a PTT action arrives after the media session has gone away."""

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
        on_stopped: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self._label = label
        self._on_output = on_output
        self._on_interrupt = on_interrupt
        # The SDK swallows websocket send errors internally and tears the
        # session down via end_session() -> audio_interface.stop(); this hook
        # is therefore the ONLY reliable place to learn the socket died.
        self._on_stopped = on_stopped
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
        if self._on_stopped is not None:
            try:
                await self._on_stopped()
            except Exception:
                logger.exception(f"[AgentTranslate:{self._label}] on_stopped hook failed")

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


class BufferedWatchAudioInterface(AsyncAudioInterface):
    """One-shot Agent audio interface used by the watch release-to-translate API."""

    def __init__(self):
        self._input_callback: Optional[Callable[[bytes], Awaitable[None]]] = None
        self.started = asyncio.Event()
        self.output_audio = bytearray()
        self.first_output = asyncio.Event()
        self.last_output_at = 0.0

    async def start(self, input_callback: Callable[[bytes], Awaitable[None]]):
        self._input_callback = input_callback
        self.started.set()

    async def stop(self):
        self._input_callback = None

    async def output(self, audio: bytes):
        self.output_audio.extend(audio)
        self.last_output_at = time.monotonic()
        self.first_output.set()

    async def interrupt(self):
        return

    async def send(self, audio: bytes):
        if self._input_callback is None:
            raise RuntimeError("ElevenLabs watch audio interface is not ready")
        await self._input_callback(audio)


async def translate_buffered_utterance(
    pcm: bytes,
    source_language: str,
    target_language: str,
    voice_id: str,
) -> tuple[str, str, bytes]:
    """Translate one fully recorded watch utterance through the existing Agent stack."""
    if not pcm:
        raise ValueError("The watch recording contained no audio")

    source = language_name(source_language)
    target = language_name(target_language)
    target_note = language_note(target_language)
    audio = BufferedWatchAudioInterface()
    transcripts: list[str] = []
    translations: list[str] = []
    response_received = asyncio.Event()

    async def on_transcript(value: str):
        text = (value or "").strip()
        if text:
            transcripts.append(text)

    async def on_response(value: str):
        text = (value or "").strip()
        if text:
            translations.append(text)
            response_received.set()

    client = ElevenLabs(api_key=require_agent_config())
    conversation = AsyncConversation(
        client,
        agent_id_for_language(target_language),
        requires_auth=True,
        audio_interface=audio,
        config=ConversationInitiationData(
            conversation_config_override={
                "agent": {
                    "prompt": {"prompt": translation_prompt(source, target, target_note)},
                    "first_message": "",
                },
                "tts": {
                    "voice_id": voice_id,
                    "speed": _tts_float(target_language, "speed", 1.0),
                    "stability": _tts_float(target_language, "stability", 0.35),
                    "similarity_boost": _tts_float(target_language, "similarity_boost", 0.75),
                },
            }
        ),
        callback_user_transcript=on_transcript,
        callback_agent_response=on_response,
    )

    try:
        await conversation.start_session()
        await asyncio.wait_for(audio.started.wait(), timeout=5)
        payload = pcm + bytes(PTT_COMMIT_SILENCE_BYTES)
        remainder = len(payload) % AGENT_CHUNK_BYTES
        if remainder:
            payload += bytes(AGENT_CHUNK_BYTES - remainder)
        for offset in range(0, len(payload), AGENT_CHUNK_BYTES):
            await audio.send(payload[offset:offset + AGENT_CHUNK_BYTES])

        # Keep the "mic" open with real-time-paced silence until the agent
        # answers. A fixed silence tail is not enough: the agent's end-of-turn
        # detection is unreliable once the audio stream stops (QA 2026-08-26 saw
        # ZH→EN turns hang ~50% of the time), while live sessions — whose mic
        # streams silence continuously — never exhibit this.
        async def hold_mic_open() -> None:
            silence = bytes(AGENT_CHUNK_BYTES)  # 250 ms at 16 kHz PCM16
            while not response_received.is_set():
                await audio.send(silence)
                await asyncio.sleep(0.25)

        keepalive = asyncio.create_task(hold_mic_open())
        try:
            await asyncio.wait_for(response_received.wait(), timeout=30)
        finally:
            keepalive.cancel()
        await asyncio.wait_for(audio.first_output.wait(), timeout=15)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            await asyncio.sleep(0.15)
            if audio.last_output_at and time.monotonic() - audio.last_output_at >= 1.2:
                break
        return (
            transcripts[-1] if transcripts else "",
            translations[-1] if translations else "",
            bytes(audio.output_audio),
        )
    finally:
        try:
            await asyncio.wait_for(conversation.end_session(), timeout=5)
        except Exception:
            logger.exception("[WatchTranslate] failed to close one-shot Agent session")


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
        self._ptt_buffer = bytearray()
        self._ptt_recording = False
        self._ptt_lock = asyncio.Lock()
        self._ptt_overflow_logged = False
        self._turn_in_flight = False
        self._turn_started_at = 0.0
        self._turn_keepalive: Optional[asyncio.Task] = None
        self._turn_payload: bytes = b""
        self._expected_stop = False
        self._pending_original: Optional[str] = None
        self._started = False
        # True once the session has been torn down; distinguishes "gone" from
        # "still starting up" so early holds are not rejected as disconnected.
        self._stopped = False

    # -- lifecycle ---------------------------------------------------------

    async def start_agent(self):
        """Open the Agent session. Safe to call more than once."""
        if self._started:
            return
        self._started = True
        self._stopped = False

        me = self._participant
        other = self._get_other()
        if other is None:
            # This used to fall back to English and to this participant's OWN
            # voice, on the theory that a one-sided session should still produce
            # something. In practice it produced a session translating into the
            # wrong language in the wrong voice, with only a warning in the log —
            # and the restart-after-a-dead-turn path could hit it mid-session,
            # which is how a Cantonese session could suddenly change voice.
            # There is nothing useful to translate into without a counterpart, so
            # fail loudly instead; the caller rejects the next hold as
            # disconnected, which is honest and recoverable.
            self._started = False
            raise RuntimeError(
                f"cannot start agent for {me.name}: no counterpart in the session"
            )

        source = language_name(me.language.value)
        # Output language is always the counterpart's.
        target_code = other.language.value
        target = language_name(target_code)
        target_note = language_note(target_code)

        self._expected_stop = False
        self._audio = RelayAudioInterface(
            label=me.name,
            on_output=self._relay_output,
            on_interrupt=self._relay_interrupt,
            on_stopped=self._on_agent_leg_stopped,
        )

        # The agent is chosen by output language rather than reconfigured per session:
        # the TTS model is validated against the agent's baked-in language and model_id
        # cannot be overridden, so English and Mandarin need separate agents.
        agent_id = agent_id_for_language(target_code)

        # Always the counterpart's voice — it is their language being spoken.
        voice_id = other.voice_config.get("voice_id", "")
        if not voice_id:
            # An empty voice_id makes ElevenLabs fall back to whatever voice the
            # agent was provisioned with, which is how a session can silently
            # come out in an unexpected voice. Better to know.
            logger.error(
                f"[AgentTranslate:{me.name}] no voice configured for target "
                f"language {target_code!r} — the agent's default voice will be used"
            )
        overrides = {
            "agent": {
                "prompt": {"prompt": translation_prompt(source, target, target_note)},
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

    async def _on_agent_leg_stopped(self):
        """Fires whenever the SDK tears the session down — including a dead
        websocket, which the SDK otherwise swallows silently. If a turn is in
        flight and this was not our own shutdown, fail it immediately instead
        of letting the speaker sit on "Translating" until the 15s ceiling."""
        if self._expected_stop:
            return
        async with self._ptt_lock:
            turn_active = self._turn_in_flight
        if turn_active:
            logger.warning(
                f"[AgentTranslate:{self._participant.name}] agent websocket died "
                "mid-turn — failing the turn now"
            )
            asyncio.create_task(
                self._fail_turn_and_restart("connection to the agent was lost")
            )

    async def stop_agent(self):
        self._expected_stop = True
        if self._conversation is not None:
            try:
                await self._conversation.end_session()
            except Exception:
                logger.exception(f"[AgentTranslate:{self._participant.name}] end_session failed")
            self._conversation = None
        self._started = False
        self._stopped = True
        if self._turn_keepalive is not None:
            self._turn_keepalive.cancel()
            self._turn_keepalive = None
        async with self._ptt_lock:
            self._ptt_recording = False
            self._ptt_buffer.clear()
            self._turn_in_flight = False
            self._turn_started_at = 0.0
            self._pending_original = None

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
        try:
            if not translated:
                return
            me = self._participant
            other = self._get_other()
            original = self._pending_original or ""

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
        finally:
            # A response completes only this participant's turn. The same speaker
            # can immediately open another independent PTT turn; the counterpart
            # does not have to speak in between.
            async with self._ptt_lock:
                self._turn_in_flight = False
                self._turn_started_at = 0.0
                self._pending_original = None

    # -- frame plumbing ----------------------------------------------------

    async def hold_to_speak_started(self):
        """Start a fresh server-side utterance without exposing audio to Agent VAD.

        Returns "ok", "busy" (previous translation still in flight), or
        "disconnected" (the media session or agent leg is gone).
        """
        if self._stopped:
            logger.warning(
                f"[AgentTranslate:{self._participant.name}] PTT hold rejected — "
                "session is no longer connected"
            )
            return "disconnected"
        async with self._ptt_lock:
            if self._ptt_recording:
                return "ok"
            if self._turn_in_flight:
                age = time.monotonic() - self._turn_started_at
                if age < PTT_TURN_TIMEOUT_SECONDS:
                    return "busy"
                logger.warning(
                    f"[AgentTranslate:{self._participant.name}] recovering stale "
                    f"translation turn after {age:.1f}s"
                )
                self._turn_in_flight = False
                self._turn_started_at = 0.0
                self._pending_original = None
            self._ptt_buffer.clear()
            self._ptt_recording = True
            self._ptt_overflow_logged = False
        logger.debug(f"[AgentTranslate:{self._participant.name}] PTT gate opened")
        return "ok"

    async def hold_to_speak_released(self):
        """Atomically close and flush one complete utterance to the ElevenLabs Agent."""
        # The HTTP release signal and final RTP packet travel independently. Leave the
        # gate open briefly after the browser disables its track so in-flight tail audio
        # reaches the buffer before we take the atomic snapshot.
        await asyncio.sleep(PTT_RTP_DRAIN_SECONDS)
        # An agent leg that is still starting can become ready during the drain;
        # give it a moment before deciding the session is gone.
        if not self._stopped and (self._audio is None or not self._audio.ready):
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if self._stopped or (self._audio is not None and self._audio.ready):
                    break
                await asyncio.sleep(0.1)
        if self._stopped or self._audio is None or not self._audio.ready:
            # The media session died (or the agent never came up) while the
            # button was held. Accepting the audio anyway starts a turn no agent
            # will ever answer, leaving the UI on "Translating" — reject so the
            # client resets immediately.
            async with self._ptt_lock:
                self._ptt_recording = False
                self._ptt_buffer.clear()
            logger.warning(
                f"[AgentTranslate:{self._participant.name}] PTT release rejected — "
                "session is not connected"
            )
            raise BridgeDisconnected("The live session is no longer connected")
        async with self._ptt_lock:
            if not self._ptt_recording:
                return 0
            self._ptt_recording = False
            utterance = bytes(self._ptt_buffer)
            self._ptt_buffer.clear()
            # WebRTC delivers packets even when nobody speaks, so "released
            # without saying anything" arrives as a buffer full of silence.
            # Gate on energy BEFORE opening a turn: handing silence to the
            # agent parks the speaker on "Translating" through the whole
            # resend/timeout ladder for nothing. Below the gate the release
            # returns instantly and the mic is immediately ready again.
            audible = False
            if utterance:
                samples = array.array("h", utterance[: len(utterance) - (len(utterance) % 2)])
                peak = max(abs(s) for s in samples) if samples else 0
                audible = peak >= 200
                if not audible:
                    logger.info(
                        f"[AgentTranslate:{self._participant.name}] silent release "
                        f"({len(utterance)}B, peak={peak}) — no turn opened"
                    )
            if audible:
                self._turn_in_flight = True
                self._turn_started_at = time.monotonic()

        if not utterance or not audible:
            return 0

        # The agent sees no samples while the button is held. A final second of
        # silence gives its built-in VAD one unambiguous end-of-turn marker only
        # after the user releases the button.
        payload = utterance + bytes(PTT_COMMIT_SILENCE_BYTES)
        remainder = len(payload) % AGENT_CHUNK_BYTES
        if remainder:
            payload += bytes(AGENT_CHUNK_BYTES - remainder)
        try:
            await self._feed_agent(payload)
        except Exception:
            async with self._ptt_lock:
                self._turn_in_flight = False
                self._turn_started_at = 0.0
                self._pending_original = None
            raise
        logger.info(
            f"[AgentTranslate:{self._participant.name}] PTT gate released — "
            f"flushed {len(utterance)} bytes"
        )
        # In PTT mode nothing reaches the agent after this flush, and QA showed
        # the agent's end-of-turn detection stalls intermittently once its audio
        # stream stops (the same failure the buffered watch path had). Keep the
        # stream alive with real-time-paced silence until the response lands —
        # otherwise a stalled turn leaves _turn_in_flight set and the speaker
        # locked out with 409s until the stale-turn recovery.
        self._turn_payload = payload
        if self._turn_keepalive is not None:
            self._turn_keepalive.cancel()
        self._turn_keepalive = asyncio.create_task(self._hold_turn_open())
        return len(utterance)

    async def _hold_turn_open(self):
        silence = bytes(AGENT_CHUNK_BYTES)  # 250 ms of 16-bit PCM at 16 kHz
        timed_out = False
        resent = False
        try:
            while True:
                async with self._ptt_lock:
                    if not self._turn_in_flight or self._ptt_recording:
                        return
                    elapsed = time.monotonic() - self._turn_started_at
                    if elapsed >= PTT_TURN_TIMEOUT_SECONDS:
                        # The agent never answered. Stopping the silence feed
                        # alone would leave _turn_in_flight set and the speaker
                        # stuck on "Translating" — clear the turn, tell the UI,
                        # and cycle the agent leg so the next press works.
                        self._turn_in_flight = False
                        self._turn_started_at = 0.0
                        self._pending_original = None
                        timed_out = True
                if timed_out:
                    break
                # The agent's turn detection intermittently misses a flush
                # entirely (QA: ~1 in 10 turns). One in-turn resend at 6 s
                # recovers those in seconds instead of failing at the ceiling.
                if not resent and elapsed >= 6.0 and self._turn_payload:
                    resent = True
                    logger.warning(
                        f"[AgentTranslate:{self._participant.name}] no response after "
                        f"{elapsed:.1f}s — resending the utterance"
                    )
                    await self._feed_agent(self._turn_payload)
                else:
                    await self._feed_agent(silence)
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            return
        me = self._participant
        logger.warning(
            f"[AgentTranslate:{me.name}] no agent response within "
            f"{PTT_TURN_TIMEOUT_SECONDS}s — failing the turn and restarting the agent"
        )
        try:
            await self._session.event_queue.put({
                "type": "turn_failed",
                "speaker": me.pc_id,
                "speaker_name": me.name,
                "reason": "translation timed out",
            })
        except Exception:
            logger.exception(f"[AgentTranslate:{me.name}] failed to emit turn_failed event")
        if self._started:
            await self._restart_agent()

    async def _restart_agent(self):
        """Tear down and reopen the agent leg after a dead turn."""
        self._expected_stop = True
        conversation = self._conversation
        self._conversation = None
        self._started = False
        if conversation is not None:
            try:
                await asyncio.wait_for(conversation.end_session(), timeout=5)
            except Exception:
                logger.exception(
                    f"[AgentTranslate:{self._participant.name}] end_session failed during restart"
                )
        try:
            await self.start_agent()
            logger.info(f"[AgentTranslate:{self._participant.name}] agent session restarted")
        except Exception:
            logger.exception(
                f"[AgentTranslate:{self._participant.name}] agent restart failed — "
                "next PTT hold will be rejected as disconnected"
            )

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
            # Mic audio belongs to the agent websocket, not the rest of the pipeline.
            await self._buffer_ptt_audio(frame.audio)
            return

        await self.push_frame(frame, direction)

    async def _buffer_ptt_audio(self, pcm: bytes):
        async with self._ptt_lock:
            if not self._ptt_recording:
                return
            remaining = PTT_MAX_BYTES - len(self._ptt_buffer)
            if remaining > 0:
                self._ptt_buffer.extend(pcm[:remaining])
            if len(pcm) > remaining and not self._ptt_overflow_logged:
                self._ptt_overflow_logged = True
                logger.warning(
                    f"[AgentTranslate:{self._participant.name}] PTT recording reached "
                    f"the {PTT_MAX_SECONDS}s safety limit; extra audio is being dropped"
                )

    async def _feed_agent(self, pcm: bytes):
        if self._audio is None or not self._audio.ready:
            return
        self._buffer.extend(pcm)
        while len(self._buffer) >= AGENT_CHUNK_BYTES:
            chunk = bytes(self._buffer[:AGENT_CHUNK_BYTES])
            del self._buffer[:AGENT_CHUNK_BYTES]
            try:
                await self._audio.send(chunk)
            except Exception:
                # A raising send means the agent websocket is dead (a zombie
                # socket still reports ready). Swallowing this used to strand
                # the turn until the 15s ceiling — instead fail the turn NOW,
                # tell the UI, and cycle the leg so the next press works.
                logger.exception(
                    f"[AgentTranslate:{self._participant.name}] websocket send failed — "
                    "failing the turn and restarting the agent leg"
                )
                self._buffer.clear()
                asyncio.create_task(self._fail_turn_and_restart("connection to the agent was lost"))
                return

    async def _fail_turn_and_restart(self, reason: str):
        """Immediately terminate the in-flight turn: clear state, notify, restart."""
        async with self._ptt_lock:
            had_turn = self._turn_in_flight
            self._turn_in_flight = False
            self._turn_started_at = 0.0
            self._pending_original = None
        if self._turn_keepalive is not None:
            self._turn_keepalive.cancel()
            self._turn_keepalive = None
        if had_turn:
            try:
                await self._session.event_queue.put({
                    "type": "turn_failed",
                    "speaker": self._participant.pc_id,
                    "speaker_name": self._participant.name,
                    "reason": reason,
                })
            except Exception:
                logger.exception(
                    f"[AgentTranslate:{self._participant.name}] failed to emit turn_failed"
                )
        if self._started:
            await self._restart_agent()


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


# NOT CURRENTLY USED — kept for the record and for the cascaded path.
#
# `agent.language` looked like the fix for "Filipino input is not transcribed":
# each agent is provisioned per OUTPUT language, so its ASR listens in the
# language it SPEAKS, and Filipino into a Cantonese session was silently
# discarded. Overriding it to the SOURCE language did fix the transcription.
#
# But that field drives the ASR, the LLM's language context AND the TTS
# pronunciation together. Setting it to the source told the Cantonese voice it
# was speaking English, so correct Cantonese text came out with English
# phonetics — the accent was lost and translation quality dropped. Measured on
# live sessions 2026-08-29.
#
# One agent cannot listen in one language and speak in another. Fixing input for
# distant source languages needs the cascaded engine (separate STT language and
# TTS voice) or an agent per source/target pair, not this override.
_AGENT_ASR_LANGUAGE_ALIASES = {
    "yue": "zh",
}


def agent_asr_language(lang_code: str) -> str:
    """Agent language to use so ASR listens in the speaker's language."""
    code = (lang_code or "en").split("-")[0].lower()
    return _AGENT_ASR_LANGUAGE_ALIASES.get(code, code)


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


def has_agent_for_language(lang_code: str) -> bool:
    """Whether a translation agent that speaks `lang_code` is provisioned.

    Callers that gate a request on language support should use this rather than
    catching the RuntimeError from agent_id_for_language, so an unsupported
    language is a clean 4xx instead of a 502.
    """
    lang = lang_code.split("-")[0].lower()
    return bool(os.getenv(f"ELEVENLABS_TRANSLATE_AGENT_{lang.upper()}", "").strip())


def require_agent_config() -> str:
    """Return the ElevenLabs API key, raising with an actionable message if unset."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    return api_key
