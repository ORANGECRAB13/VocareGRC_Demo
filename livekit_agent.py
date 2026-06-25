"""Georges River Council voice agent using the production LiveKit pipeline."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
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
    function_tool,
    JobContext,
    JobProcess,
    cli,
)
from livekit.agents import metrics as agent_metrics
from livekit.plugins import elevenlabs, openai, silero
from loguru import logger

import vocare_eot as eot

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)

GRC_PILOT_DIR = Path(__file__).parent / "GRC_pilot"
if str(GRC_PILOT_DIR) not in sys.path:
    sys.path.insert(0, str(GRC_PILOT_DIR))

from bin_faq import BIN_FAQ  # noqa: E402
from da_knowledge import DA_KNOWLEDGE  # noqa: E402
from grc_events import format_events_for_system_prompt, get_events, get_future_events  # noqa: E402
from tools import _correct_address  # noqa: E402


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

    def __init__(
        self,
        *,
        eot_controller: eot.EOTController | None,
        apply_main_voice=None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._eot_controller = eot_controller
        self._apply_main_voice = apply_main_voice

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

        # Pick the voice from the reply text itself, before the synth connection
        # opens, so the spoken voice always matches the language being said. We
        # buffer just the opening (until a CJK char, sentence end, or 16 chars).
        text_iter = text.__aiter__()
        opening = ""
        async for chunk in text_iter:
            opening += chunk
            if _CJK_RE.search(opening) or len(opening) >= 16 or re.search(r"[.!?…\n]", opening):
                break
        if self._apply_main_voice is not None:
            self._apply_main_voice(bool(_CJK_RE.search(opening)))

        async def _reassembled() -> AsyncIterator[str]:
            if opening:
                yield opening
            async for rest in text_iter:
                yield rest

        main_started = False
        sanitized_text = _sanitize_main_tts_text(_reassembled())
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


def _load_events_prompt_block() -> str:
    try:
        return format_events_for_system_prompt(get_events())
    except Exception as exc:
        logger.warning("Could not preload GRC events for prompt: {}", exc)
        return "GRC EVENTS: Could not preload the events list. Use the event tools for what's-on questions."


SYSTEM_INSTRUCTION_GRC = (
    "You are a voice agent for Georges River Council. Your name is Maya and you have an Australian accent. "
    "You help residents with three services: bin collection day lookups, "
    "development application inquiries, and upcoming council events. "
    "This is a phone conversation. Speak naturally, warmly, and concisely — one or two sentences at a time. "
    "Never use lists, bullet points, markdown, emojis, URLs, or long menus. "
    "Do not use filler phrases like 'Certainly!' or 'Of course!'. "
    "Never backchannel while the caller is thinking or speaking. "
    "If the caller only says a filler sound, hesitation, or asks you to wait, stay completely silent. "
    "Never say internal status phrases such as 'silence', 'no response', 'no output', 'None', 'null', or 'N/A'. "
    "\n\n"
    "CRITICAL OVERRIDE — if the caller asks to speak to a human, person, agent, operator, "
    "or asks to be transferred or escalated, immediately call transfer_to_human. "
    "\n\n"
    "For bin collection day lookups: ask for the resident's full street address if they haven't provided one. "
    "Only call get_bin_collection_day once you have a specific street address. "
    "Never call the tool with a vague phrase, question, or incomplete input. "
    "Never guess or invent a collection day. If the lookup cannot find the address, ask for the full address again. "
    "For bin service FAQ questions, answer directly from the BIN SERVICES KNOWLEDGE BASE below without a tool call. "
    "\n\n"
    "For development application inquiries: answer from the DEVELOPMENT APPLICATIONS knowledge base below. "
    "Direct residents to lodge via the NSW Planning Portal only. "
    "For specific planning advice, refer them to Council's Duty Planner on 9330 6400. "
    "\n\n"
    "For questions about upcoming events, activities, or what's on: "
    "Use the embedded next-30-days event list below when it contains the answer. "
    "If the caller asks generally, ask one short friendly narrowing question, such as whether they want free events, kids activities, or a particular type of activity. "
    "If the caller names an interest, answer immediately with 2 or 3 matching events where possible. "
    "If the caller asks about later dates, call get_future_council_events. Do not read out URLs; say they can register on the Georges River Council website. "
    "\n\n"
    "You ONLY handle bin collection day lookups, development application inquiries, and Georges River Council events. "
    "If the resident asks about anything else, politely say you can only help with those three topics, then stop. "
    "Your only sources of truth are tool results and the knowledge bases in this prompt. If the answer is not there, say you don't have that information. "
    "\n\n"
    "A separate predictive voice bridge may already have spoken an acknowledgement immediately before your response. "
    "Treat your first words as its direct continuation. Begin with substance, not another filler or acknowledgement. "
    "Do not open with 'amazing', 'awesome', 'okay', 'sure', 'of course', 'great question', 'so', 'well', 'um', or 'let me'. "
    "Never output the literal word 'none'. "
    "\n\n"
    "LANGUAGE — STRICT, TOP PRIORITY: Your first message asks the caller whether they want to continue in English or Mandarin Chinese. "
    "As soon as the caller indicates a choice, lock to that language and speak ONLY that language for the entire rest of the call — every single word. "
    "If they choose English (for example they say 'English', 'in English', or 'English please'), respond only in English from then on. "
    "If they choose Mandarin or Chinese (for example 'Mandarin', 'Chinese', '中文', '普通话', or they speak in Chinese), respond only in Simplified Chinese characters from then on — do not use English words except unavoidable proper nouns or street names. "
    "Never mix the two languages within a reply, and never switch the conversation language again unless the caller explicitly asks to change it. "
    "If the caller's choice is unclear, ask once: 'Would you prefer English or Mandarin?' and wait. "
    "\n\n"
    + DA_KNOWLEDGE
    + "\n\n"
    + BIN_FAQ
    + "\n\n"
    + _load_events_prompt_block()
)

INITIAL_GREETING = (
    "Hello, you're speaking with Maya, a virtual assistant from Georges River Council. "
    "Would you like to continue in English, or in Mandarin Chinese?"
)


@function_tool(
    description=(
        "Look up the bin collection day for a resident's full street address in the Georges River Council area. "
        "Only call this after the resident provides a specific street address."
    )
)
async def get_bin_collection_day(address: str) -> str:
    address = (address or "").strip()
    if not address:
        return "Please ask the resident for their full street address."

    corrected = _correct_address(address)
    try:
        from grc_wastetrack import get_bin_collection_details as _wt, format_voice_response

        result = await asyncio.to_thread(_wt, corrected)
        voice = format_voice_response(result)
        if voice:
            logger.info(
                "[GRC TOOL] bin lookup success raw={!r} corrected={!r} matched={!r}",
                address,
                corrected,
                result.get("address"),
            )
            return voice
        logger.warning("[GRC TOOL] bin lookup empty for {!r}: {}", corrected, result.get("error"))
    except Exception as exc:
        logger.warning("[GRC TOOL] bin lookup failed for {!r}: {}", corrected, exc)

    return (
        "I couldn't find a bin collection record for that address in the council bin lookup. "
        "Please ask the resident to repeat the full street address."
    )


@function_tool(
    description=(
        "Fetch Georges River Council events beyond the embedded next-30-days list. "
        "Use this when the caller asks about later dates, such as next month or a future month."
    )
)
async def get_future_council_events(after_days: int = 30) -> str:
    return await asyncio.to_thread(get_future_events, after_days)


@function_tool(
    description=(
        "Use when the caller asks to speak to a human, person, live agent, operator, "
        "or asks to be transferred or escalated."
    )
)
async def transfer_to_human() -> str:
    logger.info("[GRC TOOL] transfer_to_human requested")
    return "I can't transfer the call directly from this demo line, but you can reach Georges River Council on 9330 6400."


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


async def _run_s2s_session(ctx: JobContext) -> None:
    """Speech-to-speech agent on a hosted Azure realtime model (gpt-realtime-2).

    Same Maya persona and tools as the cascaded pipeline, but the realtime model
    handles speech-in / speech-out and turn-taking directly — no ElevenLabs TTS,
    no EOT classifier, no cascaded STT. Connects to the Azure OpenAI v1 GA
    realtime surface: wss://<resource>/openai/v1/realtime?model=<model> (Bearer).
    """
    from livekit.plugins.openai import realtime

    api_key = _env("S2S_TARGET_API_KEY")
    base = _env("S2S_TARGET_URI").rstrip("/")
    if not api_key or not base:
        raise RuntimeError("S2S_ENABLED but S2S_TARGET_URI / S2S_TARGET_API_KEY are not set")
    # The realtime plugin uses base_url verbatim as the websocket endpoint (it does
    # NOT append /realtime), so point it at the full Azure v1 GA realtime path:
    #   wss://<resource>/openai/v1/realtime?model=<model>
    if not base.endswith("/realtime"):
        if not base.endswith("/openai/v1"):
            base = base + "/openai/v1"
        base = base + "/realtime"
    model_name = _env("S2S_MODEL", "gpt-realtime-2")
    voice = _env("S2S_VOICE", "marin")

    # Authenticate via the api-key QUERY param, not just the Bearer header. The
    # Azure realtime endpoint answers a Bearer-only handshake with a redirect to
    # the same URL carrying api-key, and the plugin's aiohttp client refuses to
    # follow a wss:// redirect (NonHttpUrlRedirectClientError). Putting api-key in
    # the query authenticates on the first request, so no redirect is issued.
    sep = "&" if "?" in base else "?"
    base_url = f"{base}{sep}api-key={api_key}"

    # Server-side turn detection for the realtime model (its own VAD, separate from
    # the cascaded Silero VAD). Tunable via env:
    #   S2S_TURN_TYPE=server_vad|semantic_vad
    #   server_vad:  S2S_VAD_THRESHOLD (0-1), S2S_VAD_SILENCE_MS, S2S_VAD_PREFIX_MS
    #   semantic_vad: S2S_VAD_EAGERNESS=low|medium|high|auto
    from openai.types.beta.realtime.session import TurnDetection

    td_type = _env("S2S_TURN_TYPE", "server_vad").lower()
    if td_type == "semantic_vad":
        turn_detection = TurnDetection(
            type="semantic_vad",
            eagerness=_env("S2S_VAD_EAGERNESS", "auto"),
            create_response=True,
            interrupt_response=True,
        )
        td_desc = f"semantic_vad eagerness={_env('S2S_VAD_EAGERNESS', 'auto')}"
    else:
        turn_detection = TurnDetection(
            type="server_vad",
            threshold=_float_env("S2S_VAD_THRESHOLD", 0.5),
            prefix_padding_ms=_int_env("S2S_VAD_PREFIX_MS", 300),
            silence_duration_ms=_int_env("S2S_VAD_SILENCE_MS", 500),
            create_response=True,
            interrupt_response=True,
        )
        td_desc = (
            f"server_vad threshold={_float_env('S2S_VAD_THRESHOLD', 0.5)} "
            f"silence_ms={_int_env('S2S_VAD_SILENCE_MS', 500)} "
            f"prefix_ms={_int_env('S2S_VAD_PREFIX_MS', 300)}"
        )

    realtime_model = realtime.RealtimeModel(
        model=model_name,
        voice=voice,
        base_url=base_url,
        api_key=api_key,
        # Lower temperature improves adherence to the accent instruction (0.6 is the
        # gpt-realtime floor). Tunable via S2S_TEMPERATURE.
        temperature=_float_env("S2S_TEMPERATURE", 0.6),
        turn_detection=turn_detection,
    )
    session = AgentSession(llm=realtime_model)

    @session.on("error")
    def _on_error(event) -> None:
        logger.error("S2S pipeline error from {}: {}", type(event.source).__name__, event.error)

    @session.on("user_input_transcribed")
    def _on_transcript(event) -> None:
        if getattr(event, "is_final", False):
            logger.info("S2S user [{}]: {!r}", getattr(event, "language", "?"), event.transcript)

    # Realtime voices are accent-neutral/American and DRIFT back to American over a call,
    # so steer the accent hard via instructions — at the very top (priority) and bottom
    # (recency) of the prompt, and demand it on every turn.
    accent = _env("S2S_ACCENT", "Australian")
    accent_prefix = (
        f"You are a local Sydney council officer who speaks English with a broad, natural "
        f"{accent} accent at ALL times and NEVER drifts into an American or neutral accent.\n\n"
    )
    accent_note = (
        "\n\nACCENT — MANDATORY, HIGHEST PRIORITY, APPLIES TO EVERY SINGLE TURN: Speak "
        f"English only in a broad, natural {accent} English accent — Australian vowels, "
        "non-rhotic Rs, and Australian intonation, like someone born and raised in "
        "Australia. This applies to EVERY response for the entire call: the greeting, "
        "answers, follow-ups, and ESPECIALLY when reading out addresses, dates, bin days, "
        "or event details (do not switch to a flat 'reading' voice). Do NOT drift into an "
        "American or neutral accent at any point; if you catch yourself, correct straight "
        "back to Australian. When speaking Mandarin, use standard Mandarin. Never mention "
        "or announce your accent."
    )
    logger.info(
        "Starting GRC S2S agent | model={} voice={} accent={} turn=[{}] endpoint={}",
        model_name, voice, accent, td_desc, base,
    )
    await session.start(
        room=ctx.room,
        agent=Agent(
            instructions=accent_prefix + SYSTEM_INSTRUCTION_GRC + accent_note,
            tools=[
                get_bin_collection_day,
                get_future_council_events,
                transfer_to_human,
            ],
        ),
    )
    await _wait_for_remote_participant(
        ctx, timeout=_float_env("LIVEKIT_GREETING_PARTICIPANT_WAIT_SECS", 2.0)
    )
    await asyncio.sleep(_float_env("LIVEKIT_GREETING_DELAY_SECS", 0.3))
    logger.info("Sending initial greeting (S2S)")
    await session.generate_reply(
        instructions=(
            f"In a broad {accent} English accent, say this greeting word-for-word and then "
            f"wait for the caller to choose a language: {INITIAL_GREETING}"
        )
    )
    logger.info("Initial greeting sent (S2S)")


@server.rtc_session(agent_name=_env("AGENT_NAME", "utilities10x-agent"))
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect(rtc_config=_agent_rtc_config())

    if _bool_env("S2S_ENABLED", False):
        await _run_s2s_session(ctx)
        return

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

    # Maya uses a dedicated Mandarin voice when speaking Chinese and the default
    # English voice otherwise. ElevenLabs flash v2.5 is multilingual, so we swap
    # voice_id + per-language settings. The main voice and the predictive filler
    # voice are each chosen from the exact text about to be spoken (in tts_node and
    # in the filler renderer), not from the user's transcript — that removes the
    # race/mis-detection that flipped the voice inconsistently.
    main_voice_is_english = True
    filler_voice_is_english = True

    def _apply_main_voice(is_chinese: bool) -> None:
        nonlocal main_voice_is_english
        if (not is_chinese) == main_voice_is_english:
            return
        main_voice_is_english = not is_chinese
        if is_chinese:
            tts_service.update_options(
                voice_id=chinese_voice_id, language="zh", voice_settings=chinese_voice_settings
            )
            logger.info("Main TTS voice -> Mandarin ({})", chinese_voice_id)
        else:
            tts_service.update_options(
                voice_id=voice_id, language="en", voice_settings=english_voice_settings
            )
            logger.info("Main TTS voice -> English ({})", voice_id)

    def _apply_filler_voice(is_chinese: bool) -> None:
        nonlocal filler_voice_is_english
        if (not is_chinese) == filler_voice_is_english:
            return
        filler_voice_is_english = not is_chinese
        if is_chinese:
            filler_tts_service.update_options(
                voice_id=chinese_voice_id, language="zh", voice_settings=chinese_voice_settings
            )
            logger.info("Filler TTS voice -> Mandarin ({})", chinese_voice_id)
        else:
            filler_tts_service.update_options(
                voice_id=voice_id, language="en", voice_settings=english_voice_settings
            )
            logger.info("Filler TTS voice -> English ({})", voice_id)

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
        eot_controller.filler_apply_voice = _apply_filler_voice
        ctx.add_shutdown_callback(eot_controller.aclose)
        eot_controller.start()

    logger.info(
        "Starting GRC LiveKit agent | model={} STT={} silence={}s TTS={} tools={}",
        llm_kwargs["model"],
        stt_kwargs["model_id"],
        stt_kwargs["server_vad"]["vad_silence_threshold_secs"],
        tts_service.model,
        3,
    )

    await session.start(
        room=ctx.room,
        agent=VocareAgent(
            eot_controller=eot_controller,
            apply_main_voice=_apply_main_voice,
            instructions=SYSTEM_INSTRUCTION_GRC,
            tools=[
                get_bin_collection_day,
                get_future_council_events,
                transfer_to_human,
            ],
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
