"""Cached LLM end-of-turn detection with an optional VAD-bypass fast lane.

Interim transcripts are classified in the background. LiveKit's normal turn
detector consumes the cached result at VAD silence, while a high-confidence,
stable COMPLETE result can explicitly commit the user turn before local VAD
fires. If classification fails or remains uncertain, normal VAD endpointing is
left untouched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import aiohttp
from livekit import rtc
from livekit.agents import llm as agent_llm

logger = logging.getLogger("vocare-eot")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    try:
        return int(float(value)) if value else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    try:
        return float(value) if value else default
    except ValueError:
        return default


@dataclass
class EOTConfig:
    enabled: bool
    model: str
    api_key: str
    chat_url: str
    token_param: str
    reasoning_effort: str
    timeout_s: float
    debounce_ms: int
    complete_threshold: float
    fast_lane_enabled: bool
    fast_lane_stability_ms: int
    fast_lane_min_words: int
    fast_lane_transcript_timeout_s: float
    context_filler_enabled: bool
    late_filler_window_s: float

    @classmethod
    def from_env(
        cls,
        *,
        default_model: str,
        default_api_key: str,
        default_base_url: str | None,
    ) -> "EOTConfig":
        explicit_key = os.getenv("EOT_API_KEY", "").strip()
        explicit_url = os.getenv("EOT_CHAT_URL", "").strip()
        explicit_base = os.getenv("EOT_BASE_URL", "").strip().rstrip("/")
        cerebras_key = os.getenv("CEREBRAS_API_KEY", "").strip()
        use_cerebras = bool(cerebras_key) and not explicit_key and not explicit_url and not explicit_base

        if use_cerebras:
            api_key = cerebras_key
            chat_url = "https://api.cerebras.ai/v1/chat/completions"
            model = os.getenv("EOT_MODEL", "").strip() or "gpt-oss-120b"
            token_param = os.getenv("EOT_TOKEN_PARAM", "").strip() or "max_tokens"
            reasoning_effort = os.getenv("EOT_REASONING_EFFORT", "").strip() or "low"
        else:
            api_key = explicit_key or default_api_key
            base_url = explicit_base or (default_base_url or "").rstrip("/")
            chat_url = explicit_url or (f"{base_url}/chat/completions" if base_url else "")
            model = os.getenv("EOT_MODEL", "").strip() or default_model
            token_param = (
                os.getenv("EOT_TOKEN_PARAM", "").strip() or "max_completion_tokens"
            )
            reasoning_effort = os.getenv("EOT_REASONING_EFFORT", "").strip()

        return cls(
            enabled=_env_bool("EOT_ENABLED", False),
            model=model,
            api_key=api_key,
            chat_url=chat_url,
            token_param=token_param,
            reasoning_effort=reasoning_effort,
            timeout_s=_env_float("EOT_TIMEOUT_SECONDS", 4.0),
            debounce_ms=_env_int("EOT_CLASSIFY_DEBOUNCE_MS", 150),
            complete_threshold=_env_float("EOT_COMPLETE_THRESHOLD", 0.9),
            fast_lane_enabled=_env_bool("EOT_FAST_LANE_ENABLED", True),
            fast_lane_stability_ms=_env_int("EOT_FAST_LANE_STABILITY_MS", 650),
            fast_lane_min_words=_env_int("EOT_FAST_LANE_MIN_WORDS", 2),
            fast_lane_transcript_timeout_s=_env_float(
                "EOT_FAST_LANE_TRANSCRIPT_TIMEOUT_SECONDS", 0.8
            ),
            context_filler_enabled=_env_bool("EOT_CONTEXT_FILLER_ENABLED", True),
            late_filler_window_s=_env_float("EOT_LATE_FILLER_WINDOW_SECONDS", 1.5),
        )


@dataclass
class EOTResult:
    text: str
    complete: bool
    confidence: float
    intent: str | None
    is_question: bool


_CLASSIFY_SYSTEM = (
    "You are an end-of-turn classifier for a live council phone assistant. "
    "Decide whether the caller has finished their current turn. The transcript may "
    "be an unstable partial transcript and may be in English or Mandarin Chinese. "
    "complete=true only when it is a self-contained request, question, answer, "
    "greeting, confirmation, rejection, or farewell. complete=false when it ends "
    "mid-clause, on a conjunction, with a hesitation, or appears likely to continue. "
    "Be conservative because a COMPLETE verdict may interrupt a caller who is still "
    "speaking. is_question=true only when the caller is asking a direct question; "
    "requests, statements, confirmations, answers, greetings, and farewells are not "
    "questions. intent is a short topic of at most four words, or an empty string if "
    "the topic is not clear yet. Reply with strict minified JSON only: "
    '{"complete":<true|false>,"confidence":<0..1>,"intent":"<topic>",'
    '"is_question":<true|false>}.'
)

_NEUTRAL_FILLERS_EN = (
    "Okay... got it... so, um...",
    "Okay... gotcha... so, um...",
    "Right... understood... so, um...",
    "Got it... alright... so, um...",
    "Alright... I follow... let's go from there...",
    "Got you... that makes sense... now...",
    "Right... I'm with you... let's continue...",
    "Understood... thanks for that... now...",
    "Okay... I follow you... let's see...",
    "Got it... that helps... now...",
    "Right... I see what you mean... now...",
    "Alright... understood... let's work through that...",
)

_NEUTRAL_FILLERS_ZH = (
    "好的... 明白了... 那么...",
    "好... 知道了... 那么...",
    "明白... 好的... 那么...",
    "好的... 我明白你的意思... 接下来...",
    "知道了... 这样就清楚了... 那么...",
    "明白... 谢谢你说明... 接下来...",
)

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_NO_FILLER_INTENTS = {
    "acknowledgment",
    "acknowledgement",
    "farewell",
    "greeting",
    "thank you",
    "thanks",
}


def _question_filler(text: str, intent: str, index: int) -> str:
    if _CJK_RE.search(text):
        options = (
            "嗯... 这个问题问得很好... 那么...",
            "好的... 这是个好问题... 我们来看一下...",
            "明白... 这个问题很重要... 那么...",
        )
        return options[index % len(options)]
    topic = re.sub(r"[^a-zA-Z0-9 '&/-]+", "", intent).strip()
    topic_suffix = f" about {topic}" if topic else ""
    options = (
        f"Yeah... great question{topic_suffix}... so, um...",
        f"That's a good question{topic_suffix}... let's look at that...",
        f"Right... good question{topic_suffix}... here's the key part...",
        f"Yeah... that's worth asking{topic_suffix}... so...",
        f"Good question{topic_suffix}... let's work through it...",
    )
    return options[index % len(options)]


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


async def classify_turn(
    session: aiohttp.ClientSession,
    cfg: EOTConfig,
    text: str,
) -> EOTResult:
    body: dict = {
        "model": cfg.model,
        "temperature": 0,
        cfg.token_param: 300,
        "messages": [
            {"role": "system", "content": _CLASSIFY_SYSTEM},
            {"role": "user", "content": text},
        ],
        "response_format": {"type": "json_object"},
    }
    if cfg.reasoning_effort:
        body["reasoning_effort"] = cfg.reasoning_effort

    try:
        async with session.post(
            cfg.chat_url,
            json=body,
            headers={"Authorization": f"Bearer {cfg.api_key}"},
            timeout=aiohttp.ClientTimeout(total=cfg.timeout_s),
        ) as response:
            payload = await response.json()
            if response.status >= 400:
                error = payload.get("error") if isinstance(payload, dict) else None
                message = error.get("message") if isinstance(error, dict) else None
                raise RuntimeError(message or f"EOT HTTP {response.status}")
        raw = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = _extract_json(raw)
        return EOTResult(
            text=text,
            complete=bool(parsed.get("complete")),
            confidence=max(0.0, min(1.0, float(parsed.get("confidence", 0.0)))),
            intent=(parsed.get("intent") or "").strip() or None,
            is_question=bool(parsed.get("is_question")),
        )
    except Exception as exc:
        # A classifier outage must never force an early commit.
        logger.warning("EOT classification failed; leaving turn to VAD: %s", exc)
        return EOTResult(
            text=text,
            complete=False,
            confidence=0.0,
            intent=None,
            is_question=False,
        )


class _PreRender:
    def __init__(self, phrase: str) -> None:
        self.phrase = phrase
        self.frames: list[rtc.AudioFrame] = []
        self.claimed = False
        self._done = False
        self._new = asyncio.Event()

    def append(self, frame: rtc.AudioFrame) -> None:
        self.frames.append(frame)
        self._new.set()

    def finish(self) -> None:
        self._done = True
        self._new.set()

    async def stream(self) -> AsyncIterator[rtc.AudioFrame]:
        index = 0
        while True:
            while index < len(self.frames):
                yield self.frames[index]
                index += 1
            if self._done:
                return
            self._new.clear()
            if index < len(self.frames):
                continue
            await self._new.wait()


def _last_user_text(chat_ctx: agent_llm.ChatContext) -> str:
    for item in reversed(chat_ctx.items):
        if getattr(item, "role", None) == "user":
            return (item.text_content or "").strip()
    return ""


class LLMTurnDetector:
    def __init__(self, controller: "EOTController") -> None:
        self._controller = controller

    @property
    def model(self) -> str:
        return self._controller.cfg.model

    @property
    def provider(self) -> str:
        return "eot-llm"

    async def unlikely_threshold(self, language: str | None) -> float | None:
        return self._controller.cfg.complete_threshold

    async def supports_language(self, language: str | None) -> bool:
        return True

    async def predict_end_of_turn(
        self,
        chat_ctx: agent_llm.ChatContext,
        *,
        timeout: float | None = None,
    ) -> float:
        text = _last_user_text(chat_ctx)
        if not text:
            return 1.0
        result, cached = await self._controller.classify_for_turn(text)
        logger.info(
            "EOT VAD path %s conf=%.2f cache=%s text=%r",
            "COMPLETE" if result.complete else "INCOMPLETE",
            result.confidence,
            "hit" if cached else "miss",
            text,
        )
        if result.complete:
            self._controller.on_complete(user_text=text)
        return result.confidence if result.complete else 0.0


class EOTController:
    def __init__(self, *, cfg: EOTConfig) -> None:
        self.cfg = cfg
        self.turn_detector = LLMTurnDetector(self)
        self._session = None
        self._filler_tts = None
        self._http: aiohttp.ClientSession | None = None
        self._worker: asyncio.Task | None = None
        self._fast_lane_task: asyncio.Task | None = None
        self._dirty = asyncio.Event()
        self._closed = False
        self._bg: set[asyncio.Task] = set()
        self._final_text = ""
        self._interim = ""
        self._revision = 0
        self._committed = False
        self._suppress_until_silence = False
        self._user_speaking = False
        self._last_transcript_was_final = False
        self._last_result: EOTResult | None = None
        self._last_classified_text = ""
        self._context_filler: _PreRender | None = None
        self._filler_inflight = False
        self._filler_armed = False
        self._filler_claimed = False
        self._fire_pending_at = 0.0
        self._turn_complete = asyncio.Event()
        self._neutral_filler_index = 0
        self._question_filler_index = 0
        self._filler_epoch = 0

    def bind(self, session, filler_tts) -> None:
        self._session = session
        self._filler_tts = filler_tts

    def start(self) -> None:
        self._http = aiohttp.ClientSession()
        self._worker = asyncio.create_task(self._classify_worker(), name="eot-classify")

    async def aclose(self) -> None:
        self._closed = True
        self._dirty.set()
        for task in (self._worker, self._fast_lane_task):
            if task is not None:
                task.cancel()
        for task in list(self._bg):
            task.cancel()
        if self._http is not None:
            await self._http.close()

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    def on_user_input_transcribed(self, event) -> None:
        if self._suppress_until_silence:
            logger.debug("Ignoring transcript after fast-lane commit until speech ends")
            return
        transcript = getattr(event, "transcript", "").strip()
        if not transcript and not event.is_final:
            return
        if event.is_final:
            self._final_text = f"{self._final_text} {transcript}".strip()
            self._interim = ""
        else:
            self._interim = transcript
        self._last_transcript_was_final = bool(event.is_final)
        self._revision += 1
        self._dirty.set()

    def on_user_state_changed(self, event) -> None:
        self._user_speaking = event.new_state == "speaking"
        if self._suppress_until_silence and not self._user_speaking:
            logger.info("EOT fast-lane speech boundary reached; accepting next turn")
            self._suppress_until_silence = False
            self._reset_turn(
                preserve_filler=self._filler_armed and not self._filler_claimed
            )

    def on_conversation_item_added(self, event) -> None:
        item = getattr(event, "item", None)
        if getattr(item, "role", None) != "user":
            return
        if not self._suppress_until_silence:
            self._reset_turn(
                preserve_filler=self._filler_armed and not self._filler_claimed
            )

    def current_text(self) -> str:
        return f"{self._final_text} {self._interim}".strip()

    def _reset_turn(self, *, preserve_filler: bool = False) -> None:
        self._final_text = ""
        self._interim = ""
        self._revision += 1
        self._committed = False
        self._suppress_until_silence = False
        self._last_transcript_was_final = False
        self._last_result = None
        self._last_classified_text = ""
        if not preserve_filler:
            self._filler_epoch += 1
            self._context_filler = None
            self._filler_inflight = False
            self._filler_armed = False
            self._filler_claimed = False
            self._fire_pending_at = 0.0
            self._turn_complete = asyncio.Event()
        if self._fast_lane_task is not None:
            self._fast_lane_task.cancel()
            self._fast_lane_task = None

    async def classify_for_turn(self, text: str) -> tuple[EOTResult, bool]:
        if self._last_result is not None and self._last_classified_text == text:
            return self._last_result, True
        assert self._http is not None
        result = await classify_turn(self._http, self.cfg, text)
        self._last_classified_text = text
        self._last_result = result
        return result, False

    async def _classify_worker(self) -> None:
        debounce = self.cfg.debounce_ms / 1000.0
        while not self._closed:
            await self._dirty.wait()
            self._dirty.clear()
            await asyncio.sleep(debounce)
            if self._closed:
                return
            text = self.current_text()
            revision = self._revision
            if not text or text == self._last_classified_text or self._committed:
                continue
            result, _ = await self.classify_for_turn(text)
            if revision != self._revision or text != self.current_text():
                logger.debug("Discarding stale EOT result for %r", text)
                self._dirty.set()
                continue
            logger.info(
                "EOT %s conf=%.2f question=%s intent=%s text=%r",
                "COMPLETE" if result.complete else "INCOMPLETE",
                result.confidence,
                result.is_question,
                result.intent,
                text,
            )
            self._consider_filler(result)
            if (
                self.cfg.fast_lane_enabled
                and self._user_speaking
                and not self._last_transcript_was_final
                and result.complete
                and result.confidence >= self.cfg.complete_threshold
                and len(text.split()) >= self.cfg.fast_lane_min_words
            ):
                if self._fast_lane_task is not None:
                    self._fast_lane_task.cancel()
                self._fast_lane_task = asyncio.create_task(
                    self._commit_if_stable(text, revision),
                    name="eot-fast-lane",
                )

    async def _commit_if_stable(self, text: str, revision: int) -> None:
        await asyncio.sleep(self.cfg.fast_lane_stability_ms / 1000.0)
        result = self._last_result
        if (
            self._closed
            or self._committed
            or self._session is None
            or not self._user_speaking
            or self._last_transcript_was_final
            or revision != self._revision
            or text != self.current_text()
            or self._last_classified_text != text
            or result is None
            or not result.complete
            or result.confidence < self.cfg.complete_threshold
        ):
            return
        self._committed = True
        self._suppress_until_silence = self._user_speaking
        self.on_complete(user_text=text)
        logger.info(
            "EOT FAST LANE commit before VAD conf=%.2f stable=%dms text=%r",
            result.confidence,
            self.cfg.fast_lane_stability_ms,
            text,
        )
        try:
            self._session.commit_user_turn(
                transcript_timeout=self.cfg.fast_lane_transcript_timeout_s,
                stt_flush_duration=0.0,
            )
            self._spawn(self._authorize_fast_lane_reply())
        except Exception:
            self._committed = False
            self._suppress_until_silence = False
            logger.exception("EOT fast-lane commit failed; leaving turn to VAD")

    async def _authorize_fast_lane_reply(self) -> None:
        """Release the reply silence gate after the EOT model completes the turn."""
        deadline = (
            time.monotonic()
            + self.cfg.fast_lane_transcript_timeout_s
            + 0.75
        )
        released = False
        while not self._closed and time.monotonic() < deadline:
            activity = getattr(self._session, "_activity", None)
            silence_event = getattr(activity, "_user_silence_event", None)
            if silence_event is not None:
                silence_event.set()
                if not released:
                    released = True
                    logger.info("EOT FAST LANE released reply before VAD silence")
            await asyncio.sleep(0.01)

    def _consider_filler(self, result: EOTResult) -> None:
        normalized_intent = (result.intent or "").strip().lower()
        if (
            self.cfg.context_filler_enabled
            and normalized_intent not in _NO_FILLER_INTENTS
            and (result.intent or not result.is_question)
            and self._context_filler is None
            and not self._filler_inflight
            and not self._filler_claimed
        ):
            self._spawn(
                self._prepare_context_filler(
                    result.text,
                    result.intent or "caller request",
                    is_question=result.is_question,
                    filler_epoch=self._filler_epoch,
                )
            )

    async def _prepare_context_filler(
        self,
        text: str,
        intent: str,
        *,
        is_question: bool,
        filler_epoch: int,
    ) -> None:
        if self._http is None or self._filler_tts is None:
            return
        self._filler_inflight = True
        try:
            if is_question:
                phrase = _question_filler(
                    text,
                    intent,
                    self._question_filler_index,
                )
                self._question_filler_index += 1
                filler_kind = "question"
            else:
                options = _NEUTRAL_FILLERS_ZH if _CJK_RE.search(text) else _NEUTRAL_FILLERS_EN
                phrase = options[self._neutral_filler_index % len(options)]
                self._neutral_filler_index += 1
                filler_kind = "neutral"
            if (
                filler_epoch != self._filler_epoch
                or not phrase
                or self._filler_claimed
                or self._context_filler is not None
            ):
                return
            pre_render = _PreRender(phrase)
            self._context_filler = pre_render
            logger.info(
                "EOT rendering predictive %s filler: %r",
                filler_kind,
                phrase,
            )
            try:
                async for event in self._filler_tts.synthesize(phrase):
                    if filler_epoch != self._filler_epoch and not pre_render.claimed:
                        logger.info("EOT discarded stale predictive filler: %r", phrase)
                        return
                    pre_render.append(event.frame)
                logger.info(
                    "EOT predictive filler ready: %r (%d frames)",
                    phrase,
                    len(pre_render.frames),
                )
            finally:
                pre_render.finish()
        except Exception:
            logger.exception("EOT predictive filler render failed")
        finally:
            if filler_epoch == self._filler_epoch:
                self._filler_inflight = False

    def on_complete(self, *, user_text: str = "") -> None:
        if self._filler_claimed or not self.cfg.context_filler_enabled:
            return
        self._filler_armed = True
        self._fire_pending_at = time.monotonic()
        self._turn_complete.set()
        logger.info("EOT predictive filler armed for main reply")

    async def claim_context_filler_for_reply(self) -> _PreRender | None:
        """Wait for EOT, then attach cached audio to a preemptively-built reply."""
        if not self.cfg.context_filler_enabled:
            return None

        # Initial greetings and tool-only follow-up generations do not belong to
        # the currently transcribed user turn and must never wait on EOT.
        if (
            not self.current_text()
            and not self._filler_armed
            and not self._filler_inflight
        ):
            return None

        if not self._filler_armed:
            try:
                await asyncio.wait_for(self._turn_complete.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                logger.warning("EOT filler gate timed out; continuing without filler")
                self._filler_claimed = True
                self._filler_epoch += 1
                self._context_filler = None
                self._filler_inflight = False
                return None

        if (
            not self._filler_armed
            or self._filler_claimed
        ):
            return None
        if self._context_filler is None:
            self._filler_claimed = True
            self._filler_epoch += 1
            self._filler_inflight = False
            logger.info("EOT completed before predictive filler was available")
            return None
        if (
            self._fire_pending_at
            and time.monotonic() - self._fire_pending_at
            > self.cfg.late_filler_window_s
        ):
            logger.info("EOT predictive filler expired before reply TTS claimed it")
            self._filler_claimed = True
            self._filler_epoch += 1
            self._context_filler = None
            self._filler_inflight = False
            return None
        self._filler_claimed = True
        pre_render = self._context_filler
        pre_render.claimed = True
        self._context_filler = None
        logger.info(
            "EOT predictive filler claimed by main reply: %r",
            pre_render.phrase,
        )
        return pre_render
