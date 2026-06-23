"""Prompt-only Utilities10x outage demo using the production LiveKit pipeline."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import urllib.request
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AudioConfig,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    JobContext,
    JobProcess,
    cli,
)
from livekit.agents import metrics as agent_metrics
from livekit.plugins import elevenlabs, openai, silero
from loguru import logger

import vocare_eot as eot

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)


_MAIN_OPENING_FILLER_RE = re.compile(
    r"^\s*(?:(?:amazing|awesome|great|okay|ok|alright|sure|certainly|"
    r"of course|absolutely|yeah|yes|well|got it|gotcha)"
    r"(?:\s+then)?\s*[\.,!…:;\-]+\s*)+",
    flags=re.IGNORECASE,
)


async def _sanitize_main_tts_text(text: AsyncIterable[str]) -> AsyncIterator[str]:
    """Prevent stray pre-tool tokens and duplicate acknowledgements reaching TTS."""
    opening = ""
    decided = False

    async for chunk in text:
        if decided:
            yield chunk
            continue

        opening += chunk
        # Hold only the opening clause. The predictive filler hides this tiny
        # buffer, while it gives us enough context to safely remove "Amazing..."
        # without damaging substantive openings such as "Right now...".
        if (
            len(opening) < 96
            and not re.search(r"[.!?…]\s+\S", opening)
        ):
            continue

        cleaned = _MAIN_OPENING_FILLER_RE.sub("", opening, count=1)
        if cleaned != opening:
            logger.info("Suppressed duplicate main-LLM opener: {!r}", opening[:80])
        opening = cleaned
        decided = True
        if opening.strip().lower() != "none" and opening:
            yield opening

    if not decided and opening:
        cleaned = _MAIN_OPENING_FILLER_RE.sub("", opening, count=1)
        if cleaned != opening:
            logger.info("Suppressed duplicate main-LLM opener: {!r}", opening[:80])
        if cleaned.strip().lower().strip(".!…") == "none":
            logger.info("Suppressed stray pre-tool main-LLM token: 'none'")
            return
        if cleaned:
            yield cleaned


class VocareAgent(Agent):
    """Keep cached filler and the substantive answer in one speech pipeline."""

    def __init__(self, *, eot_controller: eot.EOTController | None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._eot_controller = eot_controller

    async def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings,
    ) -> AsyncIterator[rtc.AudioFrame]:
        pre_render = (
            await self._eot_controller.claim_context_filler_for_reply()
            if self._eot_controller is not None
            else None
        )
        if pre_render is not None:
            first_frame = True
            filler_frames = 0
            filler_duration = 0.0
            async for frame in pre_render.stream():
                if first_frame:
                    first_frame = False
                    logger.info(
                        "EOT cached filler audio start: {!r}",
                        pre_render.phrase,
                    )
                filler_frames += 1
                filler_duration += frame.duration
                yield frame
            logger.info(
                "EOT cached filler audio end: frames={} duration={:.0f}ms",
                filler_frames,
                filler_duration * 1000,
            )

        main_started = False
        sanitized_text = _sanitize_main_tts_text(text)
        async for frame in Agent.default.tts_node(
            self,
            sanitized_text,
            model_settings,
        ):
            if not main_started:
                main_started = True
                logger.info("EOT substantive TTS audio start")
            yield frame
        logger.info("EOT substantive TTS audio end started={}", main_started)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int_env(name: str, default: int) -> int:
    try:
        return int(_env(name)) if _env(name) else default
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(_env(name)) if _env(name) else default
    except ValueError:
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = _env(name)
    return raw.lower() in {"1", "true", "yes", "on"} if raw else default


def _llm_provider() -> str:
    return _env("LLM_PROVIDER", "openai").lower()


def _llm_model() -> str:
    provider = _llm_provider()
    if provider == "cerebras":
        return _env("CEREBRAS_MODEL") or _env("LLM_MODEL") or "gpt-oss-120b"
    return (
        _env("LLM_MODEL")
        or _env("OPENAI_MODEL")
        or _env("AZURE_MODEL")
        or _env("AZURE_OPENAI_DEPLOYMENT")
        or "gpt-4o-mini"
    )


def _llm_api_key() -> str:
    if _llm_provider() == "cerebras":
        return _env("CEREBRAS_API_KEY")
    return (
        _env("AZURE_OPENAI_API_KEY")
        or _env("AZURE_OPENAI_KEY")
        or _env("OPENAI_API_KEY")
        or _env("OPENAI_API")
    )


def _llm_base_url() -> str | None:
    if _llm_provider() == "cerebras":
        return (_env("CEREBRAS_BASE_URL") or "https://api.cerebras.ai/v1").rstrip("/")

    base_url = (
        _env("LLM_BASE_URL")
        or _env("OPENAI_BASE_URL")
        or _env("AZURE_PROJECT_ENDPOINT")
        or _env("AZURE_OPENAI_ENDPOINT")
    ).rstrip("/")
    for suffix in ("/responses", "/chat/completions"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
    return base_url or None


# Spoken language-preference keywords. "Mandarin"/"Chinese" are English words, so
# the STT often tags them as en; we catch them in text so the switch still fires.
_MANDARIN_KEYWORDS = ("mandarin", "chinese", "中文", "普通话", "国语")
_ENGLISH_KEYWORDS = ("english", "英文", "英语")
_CJK_RE = re.compile(r"[一-鿿]")


def _wants_english_voice(language, text: str) -> bool | None:
    """Decide the TTS language from a transcript.

    Returns True for English, False for Mandarin, or None when there is no signal.
    Explicit spoken requests ("I'd like Mandarin") and any Chinese characters
    override the STT language tag, which can mis-detect short utterances.
    """
    lowered = (text or "").lower()
    if any(k in lowered for k in _MANDARIN_KEYWORDS):
        return False
    if any(k in lowered for k in _ENGLISH_KEYWORDS):
        return True
    if _CJK_RE.search(text or ""):
        return False
    if language is not None:
        lang_str = language.value if hasattr(language, "value") else str(language)
        return not lang_str.lower().startswith(("zh", "cmn", "zho"))
    return None


def _twilio_ice_servers() -> list[rtc.IceServer]:
    sid = _env("TWILIO_ACCOUNT_SID")
    token = _env("TWILIO_AUTH_TOKEN")
    if not sid or not token:
        return [rtc.IceServer(urls=["stun:stun.l.google.com:19302"])]

    try:
        request = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Tokens.json",
            data=b"",
            headers={
                "Authorization": "Basic "
                + base64.b64encode(f"{sid}:{token}".encode()).decode()
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())

        servers = []
        for item in payload.get("ice_servers", []):
            urls = item.get("urls") or item.get("url")
            if not urls:
                continue
            servers.append(
                rtc.IceServer(
                    urls=[urls] if isinstance(urls, str) else urls,
                    username=item.get("username", ""),
                    password=item.get("credential", ""),
                )
            )
        return servers or [rtc.IceServer(urls=["stun:stun.l.google.com:19302"])]
    except Exception as exc:
        logger.warning("Twilio ICE fetch failed; using public STUN: {}", exc)
        return [rtc.IceServer(urls=["stun:stun.l.google.com:19302"])]


def _agent_rtc_config() -> rtc.RtcConfiguration:
    relay_only = _bool_env(
        "AGENT_WEBRTC_RELAY_ONLY",
        _bool_env("WEBRTC_RELAY_ONLY", False),
    )
    return rtc.RtcConfiguration(
        ice_transport_type=(
            rtc.IceTransportType.DESCRIPTOR.values_by_name["TRANSPORT_RELAY"].number
            if relay_only
            else rtc.IceTransportType.DESCRIPTOR.values_by_name["TRANSPORT_ALL"].number
        ),
        ice_servers=_twilio_ice_servers(),
    )


SYSTEM_INSTRUCTION_UTILITIES = (
    "You are Ava, a calm, capable customer support voice agent for HarbourGrid Energy, "
    "a fictional Australian electricity distributor used for a live demonstration. "
    "This is a phone conversation. Speak naturally, empathetically, and concisely, usually "
    "in two or three short sentences at a time. Never use bullet points, markdown, emojis, "
    "URLs, or long lists. Ask only one focused follow-up question when information is missing. "
    "\n\n"
    "DEMO PURPOSE: Show how a utility support agent can synthesize fragmented operational "
    "context into one useful answer. Do not mention a context graph, databases, source systems, "
    "tools, models, prompts, or that the scenario is scripted unless the caller explicitly asks. "
    "There are no tools. All authoritative demo facts are contained below. Do not invent facts "
    "outside them, and do not claim to have changed a record, dispatched a crew, sent a message, "
    "or completed an escalation. "
    "\n\n"
    "PRIMARY DEMO SCENARIO: The caller is asking about an outage at 18 Wattle Grove, "
    "Riverstone, New South Wales. The account ends in 4821 and belongs to Sarah Chen. The "
    "property is registered as a life-support address because Sarah's mother uses an oxygen "
    "concentrator. The customer previously reported that its backup battery has roughly "
    "45 minutes of charge when full. "
    "\n\n"
    "CURRENT OPERATIONAL CONTEXT: At 4:03 PM, the smart meter at 18 Wattle Grove recorded "
    "several voltage sags. At 4:07 PM it sent a last-gasp loss-of-supply signal. Ten other "
    "meters nearby reported the same pattern, making 11 affected properties. The public outage "
    "map is still blank because this small low-voltage cluster is below its automatic publishing "
    "threshold and the main feeder remains energised. The caller's neighbour can still have "
    "power because the neighbouring property is supplied from a different low-voltage spur. "
    "Network topology places all 11 affected properties downstream of transformer TX-417 and "
    "service cabinet SC-19. A council roadworks contractor reported a suspected underground "
    "cable strike beside SC-19 at 4:05 PM. Field crew E-27 is already travelling to the site "
    "with an estimated arrival in about 12 minutes. "
    "\n\n"
    "RESTORATION OUTLOOK: If the crew confirms the suspected cable damage, remote switching "
    "should restore nine of the 11 properties in approximately 25 to 35 minutes. Two properties, "
    "including 18 Wattle Grove, are on the damaged section and currently have an estimated "
    "restoration time of two to three hours. Make clear that this is an estimate pending the "
    "crew's on-site inspection, not a guarantee. "
    "\n\n"
    "SAFETY RESPONSE: Because this is a registered life-support address, lead with practical "
    "safety when the oxygen concentrator is mentioned. Advise the caller to switch to the backup "
    "battery now and conserve it. If the battery may not last, if there is no safe alternative "
    "power source, or if the patient has any breathing difficulty or immediate medical risk, "
    "tell them to call Triple Zero immediately. Never suggest connecting a generator indoors "
    "or back-feeding a home circuit. "
    "\n\n"
    "IDEAL SYNTHESIS: Explain naturally that this appears to be a small local cable fault rather "
    "than a wider feeder outage; that is why the map can be blank and a neighbour can still have "
    "power. Then give the crew status, the two-to-three-hour estimate for this property, and the "
    "life-support safety action. Do not recite asset IDs unless the caller asks for technical detail. "
    "\n\n"
    "If the caller gives no address, ask for it. If they give an address other than 18 Wattle "
    "Grove, explain that this demonstration only has verified operational context for that address "
    "and offer general outage safety guidance without inventing a status. "
    "\n\n"
    "A separate predictive voice bridge may already have spoken an acknowledgement immediately "
    "before your response. Treat your first words as its direct continuation. Begin with substance, "
    "not another filler or acknowledgement. Do not open with 'amazing', 'awesome', 'okay', 'sure', "
    "'of course', 'great question', 'so', 'well', 'um', or 'let me'. Never output the literal "
    "word 'none'. Support English and Mandarin, replying in the language used by the caller."
)

INITIAL_GREETING = (
    "Hi, you're speaking with Ava at HarbourGrid Energy. How can I help with your electricity service today?"
)


async def _wait_for_remote_participant(ctx: JobContext, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        participants = getattr(ctx.room, "remote_participants", None) or {}
        if participants:
            logger.info("Remote participant ready before greeting | count={}", len(participants))
            return
        await asyncio.sleep(0.05)
    logger.info("Remote participant wait timed out before greeting")


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server = AgentServer(
    ws_url=_env("LIVEKIT_URL", "ws://localhost:7880"),
    api_key=_env("LIVEKIT_API_KEY", "devkey"),
    api_secret=_env("LIVEKIT_API_SECRET", "secret"),
    setup_fnc=prewarm,
    num_idle_processes=_int_env("LIVEKIT_NUM_IDLE_PROCESSES", 1),
)


@server.rtc_session(agent_name=_env("AGENT_NAME", "utilities10x-agent"))
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect(rtc_config=_agent_rtc_config())

    elevenlabs_api_key = _env("ELEVENLABS_API_KEY")
    voice_id = _env("ELEVENLABS_VOICE_ID")
    chinese_voice_id = _env("ELEVENLABS_CHINESE_VOICE") or voice_id
    llm_api_key = _llm_api_key()
    if not elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    if not voice_id:
        raise RuntimeError("ELEVENLABS_VOICE_ID is not set")
    if not llm_api_key:
        raise RuntimeError("No LLM API key is configured")

    stt_kwargs: dict = {
        "api_key": elevenlabs_api_key,
        "model_id": _env("LIVEKIT_STT_MODEL", "scribe_v2_realtime"),
        "server_vad": {
            "vad_silence_threshold_secs": _float_env(
                "LIVEKIT_STT_VAD_SILENCE_SECS",
                0.3,
            ),
        },
    }
    stt_language = _env("LIVEKIT_STT_LANGUAGE_CODE")
    if stt_language:
        stt_kwargs["language_code"] = stt_language

    llm_kwargs: dict = {
        "model": _llm_model(),
        "api_key": llm_api_key,
        "max_completion_tokens": _int_env("LIVEKIT_LLM_MAX_TOKENS", 220),
    }
    if base_url := _llm_base_url():
        llm_kwargs["base_url"] = base_url
    if _env("LLM_REASONING_EFFORT"):
        llm_kwargs["reasoning_effort"] = _env("LLM_REASONING_EFFORT")
    elif _llm_provider() == "cerebras" and _llm_model().startswith("gpt-oss"):
        llm_kwargs["reasoning_effort"] = "low"
    elif _llm_provider() == "cerebras":
        # GLM otherwise spends the small voice-response token budget on hidden
        # reasoning and can return no speakable text to TTS.
        llm_kwargs["reasoning_effort"] = "none"

    english_voice_settings = elevenlabs.VoiceSettings(
        stability=_float_env("ELEVENLABS_TTS_STABILITY", 0.35),
        similarity_boost=_float_env("ELEVENLABS_TTS_SIMILARITY_BOOST", 0.75),
        speed=_float_env("ELEVENLABS_TTS_SPEED", 1.0),
        use_speaker_boost=_bool_env("ELEVENLABS_TTS_USE_SPEAKER_BOOST", False),
    )
    # Mandarin reads better with a steadier voice; tunable via ELEVENLABS_TTS_ZH_*.
    chinese_voice_settings = elevenlabs.VoiceSettings(
        stability=_float_env("ELEVENLABS_TTS_ZH_STABILITY", 0.55),
        similarity_boost=_float_env("ELEVENLABS_TTS_ZH_SIMILARITY_BOOST", 0.85),
        speed=_float_env("ELEVENLABS_TTS_ZH_SPEED", 0.9),
        use_speaker_boost=_bool_env("ELEVENLABS_TTS_USE_SPEAKER_BOOST", False),
    )

    tts_service = elevenlabs.TTS(
        api_key=elevenlabs_api_key,
        voice_id=voice_id,
        model=_env("LIVEKIT_TTS_MODEL", "eleven_flash_v2_5"),
        encoding=_env("ELEVENLABS_TTS_OUTPUT_FORMAT", "pcm_16000"),
        voice_settings=english_voice_settings,
        auto_mode=_bool_env("ELEVENLABS_TTS_AUTO_MODE", True),
        apply_text_normalization=_env(
            "ELEVENLABS_TTS_APPLY_TEXT_NORMALIZATION",
            "off",
        ),
        sync_alignment=_bool_env("ELEVENLABS_TTS_SYNC_ALIGNMENT", False),
        inactivity_timeout=_int_env("ELEVENLABS_TTS_INACTIVITY_TIMEOUT", 60),
        enable_logging=_bool_env("ELEVENLABS_TTS_ENABLE_LOGGING", True),
    )
    # Predictive rendering uses a separate connection so it cannot consume or
    # disturb the substantive answer's streaming TTS connection.
    filler_tts_service = elevenlabs.TTS(
        api_key=elevenlabs_api_key,
        voice_id=voice_id,
        model=_env("LIVEKIT_TTS_MODEL", "eleven_flash_v2_5"),
        encoding=_env("ELEVENLABS_TTS_OUTPUT_FORMAT", "pcm_16000"),
        voice_settings=english_voice_settings,
        auto_mode=_bool_env("ELEVENLABS_TTS_AUTO_MODE", True),
        apply_text_normalization=_env(
            "ELEVENLABS_TTS_APPLY_TEXT_NORMALIZATION",
            "off",
        ),
        sync_alignment=False,
        inactivity_timeout=_int_env("ELEVENLABS_TTS_INACTIVITY_TIMEOUT", 60),
        enable_logging=_bool_env("ELEVENLABS_TTS_ENABLE_LOGGING", True),
    )

    # Ava uses a dedicated Mandarin voice when the caller speaks Chinese and the
    # default English voice otherwise. ElevenLabs flash v2.5 is multilingual, so we
    # swap voice_id + per-language settings on the live TTS connection.
    tts_voice_is_english = True

    def _maybe_switch_tts_voice(language, text: str = "") -> None:
        nonlocal tts_voice_is_english
        wants_english = _wants_english_voice(language, text)
        if wants_english is None or wants_english == tts_voice_is_english:
            return
        tts_voice_is_english = wants_english
        if wants_english:
            tts_service.update_options(
                voice_id=voice_id, language="en", voice_settings=english_voice_settings
            )
            filler_tts_service.update_options(
                voice_id=voice_id,
                language="en",
                voice_settings=english_voice_settings,
            )
            logger.info("TTS voice -> English ({})", voice_id)
        else:
            tts_service.update_options(
                voice_id=chinese_voice_id, language="zh", voice_settings=chinese_voice_settings
            )
            filler_tts_service.update_options(
                voice_id=chinese_voice_id,
                language="zh",
                voice_settings=chinese_voice_settings,
            )
            logger.info("TTS voice -> Mandarin ({})", chinese_voice_id)

    eot_cfg = eot.EOTConfig.from_env(
        default_model=_llm_model(),
        default_api_key=llm_api_key,
        default_base_url=_llm_base_url(),
    )
    eot_controller: eot.EOTController | None = None
    turn_detection = None
    if eot_cfg.enabled:
        if not eot_cfg.api_key or not eot_cfg.chat_url:
            logger.warning("EOT disabled because its API key or chat URL is missing")
        else:
            eot_controller = eot.EOTController(cfg=eot_cfg)
            turn_detection = eot_controller.turn_detector
            logger.info(
                "EOT enabled | model={} fast_lane={} threshold={} stability={}ms",
                eot_cfg.model,
                eot_cfg.fast_lane_enabled,
                eot_cfg.complete_threshold,
                eot_cfg.fast_lane_stability_ms,
            )

    session = AgentSession(
        vad=ctx.proc.userdata.get("vad") or silero.VAD.load(),
        turn_detection=turn_detection,
        stt=elevenlabs.STT(**stt_kwargs),
        llm=openai.LLM(**llm_kwargs),
        tts=tts_service,
        min_endpointing_delay=_float_env("LIVEKIT_MIN_ENDPOINTING_DELAY", 0.05),
        max_endpointing_delay=_float_env("LIVEKIT_MAX_ENDPOINTING_DELAY", 0.6),
        preemptive_generation=_bool_env("LIVEKIT_PREEMPTIVE_GENERATION", True),
        allow_interruptions=_bool_env("LIVEKIT_ALLOW_INTERRUPTION", True),
    )

    @session.on("user_input_transcribed")
    def _on_transcript(event) -> None:
        kind = "FINAL" if event.is_final else "interim"
        logger.info("STT {} [{}]: {!r}", kind, event.language, event.transcript)
        _maybe_switch_tts_voice(event.language, event.transcript)
        if eot_controller is not None:
            eot_controller.on_user_input_transcribed(event)

    @session.on("error")
    def _on_error(event) -> None:
        logger.error("Pipeline error from {}: {}", type(event.source).__name__, event.error)

    @session.on("speech_created")
    def _on_speech_created(event) -> None:
        logger.info("Speech created via {} user_initiated={}", event.source, event.user_initiated)

    @session.on("metrics_collected")
    def _on_metrics(event) -> None:
        metric = event.metrics
        if isinstance(metric, agent_metrics.EOUMetrics):
            logger.info(
                "LATENCY EOU endpoint={:.0f}ms transcription={:.0f}ms",
                metric.end_of_utterance_delay * 1000,
                metric.transcription_delay * 1000,
            )
        elif isinstance(metric, agent_metrics.LLMMetrics) and not metric.cancelled:
            logger.info("LATENCY LLM ttft={:.0f}ms", metric.ttft * 1000)
        elif isinstance(metric, agent_metrics.TTSMetrics) and not metric.cancelled:
            logger.info("LATENCY TTS ttfb={:.0f}ms", metric.ttfb * 1000)

    if eot_controller is not None:
        @session.on("user_state_changed")
        def _on_user_state(event) -> None:
            eot_controller.on_user_state_changed(event)

        @session.on("conversation_item_added")
        def _on_conversation_item(event) -> None:
            eot_controller.on_conversation_item_added(event)

        eot_controller.bind(session, filler_tts_service)
        ctx.add_shutdown_callback(eot_controller.aclose)
        eot_controller.start()

    logger.info(
        "Starting Utilities10x LiveKit demo | model={} STT={} silence={}s TTS={}",
        llm_kwargs["model"],
        stt_kwargs["model_id"],
        stt_kwargs["server_vad"]["vad_silence_threshold_secs"],
        tts_service.model,
    )

    await session.start(
        room=ctx.room,
        agent=VocareAgent(
            eot_controller=eot_controller,
            instructions=SYSTEM_INSTRUCTION_UTILITIES,
            tools=[],
        ),
    )

    if _bool_env("LIVEKIT_BACKGROUND_AUDIO", False):
        background_audio = BackgroundAudioPlayer(
            ambient_sound=AudioConfig(
                BuiltinAudioClip.OFFICE_AMBIENCE,
                volume=_float_env("LIVEKIT_BACKGROUND_AUDIO_VOLUME", 0.3),
            ),
        )
        await background_audio.start(room=ctx.room, agent_session=session)
        ctx.add_shutdown_callback(background_audio.aclose)
        logger.info(
            "Background office ambience started | volume={}",
            _float_env("LIVEKIT_BACKGROUND_AUDIO_VOLUME", 0.3),
        )

    await _wait_for_remote_participant(
        ctx,
        timeout=_float_env("LIVEKIT_GREETING_PARTICIPANT_WAIT_SECS", 2.0),
    )
    await asyncio.sleep(_float_env("LIVEKIT_GREETING_DELAY_SECS", 0.3))
    logger.info("Sending initial greeting")
    await session.say(INITIAL_GREETING, allow_interruptions=True)
    logger.info("Initial greeting sent")


if __name__ == "__main__":
    cli.run_app(server)
