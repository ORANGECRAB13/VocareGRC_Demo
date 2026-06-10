#!/usr/bin/env python3
"""Synthetic voice-call regression tests for the GRC bot.

The runner generates caller audio with ElevenLabs, streams it into the bot's
Telnyx websocket endpoint, polls /api/live-calls for the live transcript, and
optionally asks an LLM to evaluate the conversation against scenario
expectations.
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import base64
import hashlib
import json
import math
import os
import random
import struct
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
DEFAULT_SCENARIOS = ROOT / "scenarios" / "grc_smoke.json"
DEFAULT_REPORT_DIR = ROOT / "reports"
DEFAULT_AUDIO_CACHE = ROOT / "audio_cache"
PCM_RATE = 16000
PCM_WIDTH = 2
TELNYX_RATE = 8000
FRAME_SECS = 0.02
FRAME_BYTES = int(PCM_RATE * FRAME_SECS * PCM_WIDTH)
TELNYX_FRAME_BYTES = int(TELNYX_RATE * FRAME_SECS)


@dataclass
class ScenarioResult:
    id: str
    description: str
    status: str
    call_id: str
    elapsed_secs: float
    transcript: list[dict[str, Any]]
    metrics: dict[str, Any]
    evaluation: dict[str, Any]
    errors: list[str]


def utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")


def normalize_target(target: str) -> tuple[str, str]:
    """Return (websocket_url, http_base_url) from app base URL or ws URL."""
    target = target.rstrip("/")
    if target.startswith("ws://") or target.startswith("wss://"):
        parsed = urlparse(target)
        ws_url = target if parsed.path else f"{target}/telnyx/ws"
        scheme = "https" if parsed.scheme == "wss" else "http"
        http_base = f"{scheme}://{parsed.netloc}"
        return ws_url, http_base

    if target.startswith("http://") or target.startswith("https://"):
        parsed = urlparse(target)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{ws_scheme}://{parsed.netloc}/telnyx/ws", f"{parsed.scheme}://{parsed.netloc}"

    raise ValueError("target must be http(s) app URL or ws(s) websocket URL")


def scenario_fingerprint(text: str, voice_id: str, model: str) -> str:
    payload = json.dumps({"text": text, "voice_id": voice_id, "model": model}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


async def generate_elevenlabs_pcm(
    text: str,
    cache_dir: Path,
    voice_id: str,
    api_key: str,
    model: str,
    speed: float,
    stability: float,
    similarity_boost: float,
    force: bool = False,
) -> bytes:
    import httpx

    cache_dir.mkdir(parents=True, exist_ok=True)
    key = scenario_fingerprint(f"{text}|speed={speed}|stability={stability}|similarity={similarity_boost}", voice_id, model)
    path = cache_dir / f"{key}.pcm"
    if path.exists() and not force:
        return path.read_bytes()

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    params = {"output_format": "pcm_16000"}
    payload = {
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity_boost,
            "speed": speed,
        },
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/pcm",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, params=params, headers=headers, json=payload)
        resp.raise_for_status()
        audio = resp.content

    path.write_bytes(audio)
    return audio


def silence(duration_secs: float) -> bytes:
    samples = max(0, int(duration_secs * PCM_RATE))
    return b"\x00\x00" * samples


def scale_pcm(pcm: bytes, gain: float) -> bytes:
    if abs(gain - 1.0) < 0.001:
        return pcm
    out = bytearray()
    for (sample,) in struct.iter_unpack("<h", pcm[: len(pcm) - (len(pcm) % 2)]):
        value = max(-32768, min(32767, int(sample * gain)))
        out.extend(struct.pack("<h", value))
    return bytes(out)


def add_white_noise(pcm: bytes, level: float, seed: int = 7) -> bytes:
    if level <= 0:
        return pcm
    rng = random.Random(seed)
    out = bytearray()
    amp = int(32767 * min(level, 1.0))
    for (sample,) in struct.iter_unpack("<h", pcm[: len(pcm) - (len(pcm) % 2)]):
        noise = rng.randint(-amp, amp)
        value = max(-32768, min(32767, sample + noise))
        out.extend(struct.pack("<h", value))
    return bytes(out)


def mix_pcm(a: bytes, b: bytes, b_gain: float = 0.35) -> bytes:
    frames = min(len(a), len(b)) // 2
    out = bytearray()
    a_samples = struct.iter_unpack("<h", a[: frames * 2])
    b_samples = struct.iter_unpack("<h", b[: frames * 2])
    for (sa,), (sb,) in zip(a_samples, b_samples):
        value = max(-32768, min(32767, int(sa + sb * b_gain)))
        out.extend(struct.pack("<h", value))
    if len(a) > frames * 2:
        out.extend(a[frames * 2 :])
    return bytes(out)


def chunk_pcm(pcm: bytes) -> list[bytes]:
    return [pcm[i : i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES) if len(pcm[i : i + FRAME_BYTES]) == FRAME_BYTES]


def pcm16_to_telnyx_ulaw(pcm: bytes) -> bytes:
    """Convert ElevenLabs 16 kHz signed PCM into Telnyx 8 kHz PCMU."""
    clean = pcm[: len(pcm) - (len(pcm) % PCM_WIDTH)]
    resampled, _ = audioop.ratecv(clean, PCM_WIDTH, 1, PCM_RATE, TELNYX_RATE, None)
    return audioop.lin2ulaw(resampled, PCM_WIDTH)


def telnyx_ulaw_to_pcm16(ulaw: bytes) -> bytes:
    """Convert Telnyx 8 kHz PCMU into 16 kHz signed PCM for WAV playback."""
    pcm8 = audioop.ulaw2lin(ulaw, PCM_WIDTH)
    pcm16, _ = audioop.ratecv(pcm8, PCM_WIDTH, 1, TELNYX_RATE, PCM_RATE, None)
    return pcm16


def wav_from_pcm16(pcm: bytes, sample_rate: int = PCM_RATE) -> bytes:
    data_size = len(pcm)
    return b"".join([
        b"RIFF",
        struct.pack("<I", 36 + data_size),
        b"WAVE",
        b"fmt ",
        struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * PCM_WIDTH, PCM_WIDTH, 16),
        b"data",
        struct.pack("<I", data_size),
        pcm,
    ])


async def transcribe_agent_audio(pcm: bytes, args) -> str:
    if not pcm or len(pcm) < int(0.25 * PCM_RATE * PCM_WIDTH):
        return ""
    api_key = args.elevenlabs_api_key or os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return ""
    import httpx

    files = {"file": ("agent.wav", wav_from_pcm16(pcm), "audio/wav")}
    data = {
        "model_id": getattr(args, "agent_stt_model", "scribe_v2"),
        "tag_audio_events": "false",
        "num_speakers": "1",
    }
    headers = {"xi-api-key": api_key}
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post("https://api.elevenlabs.io/v1/speech-to-text", headers=headers, data=data, files=files)
            resp.raise_for_status()
            payload = resp.json()
            return (payload.get("text") or "").strip()
    except Exception:
        return ""


def chunk_telnyx_ulaw(ulaw: bytes) -> list[bytes]:
    return [
        ulaw[i : i + TELNYX_FRAME_BYTES]
        for i in range(0, len(ulaw), TELNYX_FRAME_BYTES)
        if len(ulaw[i : i + TELNYX_FRAME_BYTES]) == TELNYX_FRAME_BYTES
    ]


async def build_utterance_audio(utterance: str, args) -> bytes:
    audio = await generate_elevenlabs_pcm(
        utterance,
        cache_dir=Path(args.audio_cache),
        voice_id=args.voice_id,
        api_key=args.elevenlabs_api_key,
        model=args.elevenlabs_model,
        speed=args.speed,
        stability=args.stability,
        similarity_boost=args.similarity_boost,
        force=args.force_audio,
    )
    audio = scale_pcm(audio, args.gain)
    audio = add_white_noise(audio, args.noise)
    if args.background_voice:
        bg = await generate_elevenlabs_pcm(
            args.background_voice,
            cache_dir=Path(args.audio_cache),
            voice_id=args.voice_id,
            api_key=args.elevenlabs_api_key,
            model=args.elevenlabs_model,
            speed=args.speed,
            stability=args.stability,
            similarity_boost=args.similarity_boost,
            force=args.force_audio,
        )
        if len(bg) < len(audio):
            repeats = math.ceil(len(audio) / max(1, len(bg)))
            bg = (bg * repeats)[: len(audio)]
        audio = mix_pcm(audio, bg, b_gain=args.background_gain)
    return silence(args.pre_silence) + audio + silence(args.post_silence)


async def poll_call(http_base: str, monitor_id: str, timeout_secs: float) -> dict[str, Any] | None:
    import httpx

    deadline = time.monotonic() + timeout_secs
    async with httpx.AsyncClient(timeout=10) as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"{http_base}/api/live-calls")
                resp.raise_for_status()
                data = resp.json()
                for call in data.get("calls", []):
                    if call.get("id") == monitor_id:
                        return call
            except Exception:
                pass
            await asyncio.sleep(0.5)
    return None


async def wait_for_transcript_quiet(http_base: str, monitor_id: str, timeout_secs: float, quiet_secs: float) -> dict[str, Any] | None:
    import httpx

    deadline = time.monotonic() + timeout_secs
    last_count = -1
    last_change = time.monotonic()
    last_call = None
    async with httpx.AsyncClient(timeout=10) as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"{http_base}/api/live-calls")
                resp.raise_for_status()
                data = resp.json()
                call = next((c for c in data.get("calls", []) if c.get("id") == monitor_id), None)
                if call:
                    last_call = call
                    count = len(call.get("transcript") or [])
                    if count != last_count:
                        last_count = count
                        last_change = time.monotonic()
                    elif count > 0 and (time.monotonic() - last_change) >= quiet_secs:
                        return call
            except Exception:
                pass
            await asyncio.sleep(0.5)
    return last_call


def parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def transcript_turn_latencies(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latencies = []
    for index, line in enumerate(transcript):
        if line.get("speaker") != "caller":
            continue
        caller_ts = parse_iso_ts(line.get("timestamp"))
        if caller_ts is None:
            continue
        next_agent = next((item for item in transcript[index + 1 :] if item.get("speaker") == "agent"), None)
        if not next_agent:
            continue
        agent_ts = parse_iso_ts(next_agent.get("timestamp"))
        if agent_ts is None:
            continue
        latencies.append({
            "caller_text": line.get("text", ""),
            "agent_text": next_agent.get("text", ""),
            "latency_secs": round(max(0.0, agent_ts - caller_ts), 3),
            "caller_timestamp": line.get("timestamp"),
            "agent_timestamp": next_agent.get("timestamp"),
        })
    return latencies


async def wait_for_agent_turn(http_base: str, monitor_id: str, previous_agent_count: int, timeout_secs: float) -> dict[str, Any] | None:
    import httpx

    deadline = time.monotonic() + timeout_secs
    last_call = None
    async with httpx.AsyncClient(timeout=10) as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"{http_base}/api/live-calls")
                resp.raise_for_status()
                data = resp.json()
                call = next((c for c in data.get("calls", []) if c.get("id") == monitor_id), None)
                if call:
                    last_call = call
                    agent_count = sum(1 for line in call.get("transcript") or [] if line.get("speaker") == "agent")
                    if agent_count > previous_agent_count:
                        return call
            except Exception:
                pass
            await asyncio.sleep(0.35)
    return last_call


def emit_audio_segment(args, speaker: str, pcm: bytes):
    callback = getattr(args, "on_audio_segment", None)
    if callable(callback) and pcm:
        callback(speaker, pcm)


async def send_telnyx_audio(ws, pcm: bytes, args=None, speaker: str = "caller"):
    emit_audio_segment(args, speaker, pcm)
    ulaw = pcm16_to_telnyx_ulaw(pcm)
    send_clock = time.monotonic()
    for frame in chunk_telnyx_ulaw(ulaw):
        await ws.send(json.dumps({
            "event": "media",
            "media": {
                "payload": base64.b64encode(frame).decode("ascii"),
            },
        }))
        send_clock += FRAME_SECS
        await asyncio.sleep(max(0, send_clock - time.monotonic()))


async def create_json_chat_completion(client, *, model: str, messages: list[dict[str, str]], max_tokens: int | None = None):
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    try:
        return await client.chat.completions.create(**kwargs)
    except Exception as exc:
        message = str(exc)
        if max_tokens and "max_tokens" in message and "max_completion_tokens" in message:
            kwargs.pop("max_tokens", None)
            kwargs["max_completion_tokens"] = max_tokens
            return await client.chat.completions.create(**kwargs)
        raise


async def decide_resident_utterance(scenario: dict[str, Any], transcript: list[dict[str, Any]], turn_index: int, args) -> dict[str, Any]:
    api_key = args.openai_api_key or os.getenv("OPENAI_API") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        fallback = ["English please", "I need help with my bins.", "My address is fifty Warraba Street Hurstville.", "Yes, that is correct."]
        return {"utterance": fallback[min(turn_index, len(fallback) - 1)], "done": turn_index >= len(fallback), "rationale": "fallback_no_llm_key"}

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key, base_url=args.openai_base_url or None)
    persona = scenario.get("persona") or args.resident_persona
    goal = scenario.get("goal") or args.resident_goal
    max_turns = int(scenario.get("max_turns") or args.autonomous_turns)
    transcript_text = "\n".join(f"{line.get('speaker')}: {line.get('text')}" for line in transcript[-14:])
    system = (
        "You are a synthetic test caller for a Georges River Council phone voice agent. "
        "Your job is to roleplay a realistic GRC resident, not to help the assistant. "
        "Stay in persona, respond naturally and briefly, and pursue the test goal. "
        "You may ask about bin collections, development applications, or council events. "
        "You should sometimes use realistic short answers, corrections, or mild confusion if the persona calls for it. "
        "Do not mention that you are an AI, a tester, a scenario, or evaluating the agent. "
        "Output only JSON with keys: utterance string, done boolean, rationale string. "
        "Set done true only when the goal has been satisfied, the agent failed irrecoverably, or max turns is reached."
    )
    user = {
        "persona": persona,
        "goal": goal,
        "turn_index": turn_index,
        "max_turns": max_turns,
        "conversation_so_far": transcript_text,
        "policy": (
            "If the agent asks for language, choose English unless the persona explicitly says otherwise. "
            "If the agent asks to confirm an address and the address matches your intended one, say yes. "
            "If it mishears the address, correct only the wrong part."
        ),
    }
    resp = await create_json_chat_completion(
        client,
        model=args.resident_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        max_tokens=220,
    )
    content = resp.choices[0].message.content or "{}"
    data = json.loads(content)
    utterance = (data.get("utterance") or "").strip()
    if not utterance and not data.get("done"):
        utterance = "Could you repeat that please?"
    if turn_index + 1 >= max_turns:
        data["done"] = True
    data["utterance"] = utterance
    return data


async def stream_telnyx_call(ws_url: str, http_base: str, scenario: dict[str, Any], args) -> dict[str, Any]:
    import websockets

    call_id = f"synthetic-{scenario['id']}-{uuid.uuid4().hex[:8]}"
    stream_id = f"stream-{call_id}"
    monitor_id = f"telnyx:{call_id}"
    on_call_started = getattr(args, "on_call_started", None)
    if callable(on_call_started):
        on_call_started(call_id, monitor_id, scenario)
    received_bot_audio = 0
    first_bot_audio_at = None
    last_bot_audio_at = None
    started_at = time.monotonic()
    errors: list[str] = []

    utterance_audio = []
    for utterance in scenario.get("utterances", []):
        utterance_audio.append(await build_utterance_audio(utterance, args))

    async with websockets.connect(ws_url, open_timeout=20, close_timeout=5, max_size=None) as ws:
        await ws.send(json.dumps({"event": "connected"}))
        await ws.send(json.dumps({
            "event": "start",
            "stream_id": stream_id,
            "start": {
                "call_control_id": call_id,
                "media_format": {"encoding": "PCMU", "sample_rate": TELNYX_RATE, "channels": 1},
            },
        }))

        await poll_call(http_base, monitor_id, timeout_secs=8)

        async def receiver():
            nonlocal received_bot_audio, first_bot_audio_at, last_bot_audio_at
            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("event") == "media" and (msg.get("media") or {}).get("payload"):
                        try:
                            payload = base64.b64decode((msg.get("media") or {}).get("payload"))
                            emit_audio_segment(args, "agent", telnyx_ulaw_to_pcm16(payload))
                        except Exception:
                            pass
                        received_bot_audio += 1
                        last_bot_audio_at = time.monotonic()
                        if first_bot_audio_at is None:
                            first_bot_audio_at = last_bot_audio_at
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                errors.append(f"receiver: {type(exc).__name__}: {exc}")

        rx = asyncio.create_task(receiver())

        if args.wait_for_greeting:
            deadline = time.monotonic() + args.greeting_timeout
            while time.monotonic() < deadline:
                if first_bot_audio_at and last_bot_audio_at and (time.monotonic() - last_bot_audio_at) >= args.greeting_quiet_secs:
                    break
                await asyncio.sleep(0.05)

        for audio in utterance_audio:
            await send_telnyx_audio(ws, audio, args=args, speaker="caller")
            await asyncio.sleep(args.between_utterances)

        call = await wait_for_transcript_quiet(http_base, monitor_id, args.listen_secs, args.quiet_secs)
        try:
            await ws.send(json.dumps({"event": "stop"}))
        except Exception:
            pass
        rx.cancel()
        try:
            await rx
        except asyncio.CancelledError:
            pass

    elapsed = time.monotonic() - started_at
    return {
        "call_id": call_id,
        "monitor_id": monitor_id,
        "call": call or {},
        "metrics": {
            "elapsed_secs": round(elapsed, 3),
            "bot_audio_frames": received_bot_audio,
            "time_to_first_bot_audio_secs": round(first_bot_audio_at - started_at, 3) if first_bot_audio_at else None,
            "turn_latencies": transcript_turn_latencies((call or {}).get("transcript") or []),
        },
        "errors": errors,
    }


async def stream_autonomous_telnyx_call(ws_url: str, http_base: str, scenario: dict[str, Any], args) -> dict[str, Any]:
    import websockets

    call_id = f"autonomous-{scenario['id']}-{uuid.uuid4().hex[:8]}"
    stream_id = f"stream-{call_id}"
    monitor_id = f"telnyx:{call_id}"
    on_call_started = getattr(args, "on_call_started", None)
    if callable(on_call_started):
        on_call_started(call_id, monitor_id, scenario)
    received_bot_audio = 0
    first_bot_audio_at = None
    last_bot_audio_at = None
    started_at = time.monotonic()
    errors: list[str] = []
    resident_turns: list[dict[str, Any]] = []
    agent_audio_transcripts: list[dict[str, Any]] = []
    agent_audio_buffer = bytearray()
    call = None

    async with websockets.connect(ws_url, open_timeout=20, close_timeout=5, max_size=None) as ws:
        await ws.send(json.dumps({"event": "connected"}))
        await ws.send(json.dumps({
            "event": "start",
            "stream_id": stream_id,
            "start": {
                "call_control_id": call_id,
                "media_format": {"encoding": "PCMU", "sample_rate": TELNYX_RATE, "channels": 1},
            },
        }))

        await poll_call(http_base, monitor_id, timeout_secs=8)

        async def receiver():
            nonlocal received_bot_audio, first_bot_audio_at, last_bot_audio_at, agent_audio_buffer
            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("event") == "media" and (msg.get("media") or {}).get("payload"):
                        try:
                            payload = base64.b64decode((msg.get("media") or {}).get("payload"))
                            agent_pcm = telnyx_ulaw_to_pcm16(payload)
                            agent_audio_buffer.extend(agent_pcm)
                            emit_audio_segment(args, "agent", agent_pcm)
                        except Exception:
                            pass
                        received_bot_audio += 1
                        last_bot_audio_at = time.monotonic()
                        if first_bot_audio_at is None:
                            first_bot_audio_at = last_bot_audio_at
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                errors.append(f"receiver: {type(exc).__name__}: {exc}")

        rx = asyncio.create_task(receiver())

        async def wait_for_bot_audio_quiet(timeout_secs: float, min_bot_frames: int = 0) -> bytes:
            nonlocal agent_audio_buffer
            deadline = time.monotonic() + timeout_secs
            saw_audio = False
            while time.monotonic() < deadline:
                if last_bot_audio_at and received_bot_audio > min_bot_frames:
                    saw_audio = True
                    if (time.monotonic() - last_bot_audio_at) >= args.greeting_quiet_secs:
                        break
                await asyncio.sleep(0.05)
            if not saw_audio:
                return b""
            pcm = bytes(agent_audio_buffer)
            agent_audio_buffer = bytearray()
            return pcm

        async def capture_heard_agent_audio(timeout_secs: float, label: str, min_bot_frames: int = 0) -> str:
            pcm = await wait_for_bot_audio_quiet(timeout_secs, min_bot_frames=min_bot_frames)
            text = await transcribe_agent_audio(pcm, args)
            if text:
                agent_audio_transcripts.append({
                    "speaker": "agent_audio",
                    "text": text,
                    "timestamp": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
                    "label": label,
                })
            return text

        await capture_heard_agent_audio(args.greeting_timeout, "greeting")

        call = await wait_for_agent_turn(http_base, monitor_id, previous_agent_count=0, timeout_secs=2.0)
        max_turns = int(scenario.get("max_turns") or args.autonomous_turns)
        for turn_index in range(max_turns):
            transcript = (call or {}).get("transcript") or []
            heard_transcript = transcript + agent_audio_transcripts[-4:]
            agent_count_before = sum(1 for line in transcript if line.get("speaker") == "agent")
            decision_started = time.monotonic()
            try:
                decision = await decide_resident_utterance(scenario, heard_transcript, turn_index, args)
            except Exception as exc:
                errors.append(f"resident_llm: {type(exc).__name__}: {exc}")
                fallback = "English please" if turn_index == 0 else "Could you help me with my bins please?"
                decision = {"utterance": fallback, "done": turn_index >= 2, "rationale": "resident_llm_failed"}

            utterance = (decision.get("utterance") or "").strip()
            if decision.get("done") and not utterance:
                break
            if not utterance:
                utterance = "Could you repeat that please?"

            audio = await build_utterance_audio(utterance, args)
            bot_frames_before_reply = received_bot_audio
            await send_telnyx_audio(ws, audio, args=args, speaker="caller")
            resident_turns.append({
                "turn": turn_index + 1,
                "utterance": utterance,
                "rationale": decision.get("rationale", ""),
                "decision_latency_secs": round(time.monotonic() - decision_started, 3),
            })

            await asyncio.sleep(args.between_utterances)
            call = await wait_for_agent_turn(
                http_base,
                monitor_id,
                previous_agent_count=agent_count_before,
                timeout_secs=args.autonomous_agent_timeout,
            )
            await capture_heard_agent_audio(
                args.autonomous_agent_timeout,
                f"turn_{turn_index + 1}",
                min_bot_frames=bot_frames_before_reply,
            )
            if decision.get("done"):
                break

        call = await wait_for_transcript_quiet(http_base, monitor_id, args.listen_secs, args.quiet_secs)
        try:
            await ws.send(json.dumps({"event": "stop"}))
        except Exception:
            pass
        rx.cancel()
        try:
            await rx
        except asyncio.CancelledError:
            pass

    elapsed = time.monotonic() - started_at
    transcript = (call or {}).get("transcript") or []
    return {
        "call_id": call_id,
        "monitor_id": monitor_id,
        "call": call or {},
        "metrics": {
            "elapsed_secs": round(elapsed, 3),
            "bot_audio_frames": received_bot_audio,
            "time_to_first_bot_audio_secs": round(first_bot_audio_at - started_at, 3) if first_bot_audio_at else None,
            "turn_latencies": transcript_turn_latencies(transcript),
            "resident_turns": resident_turns,
            "agent_audio_transcripts": agent_audio_transcripts,
            "autonomous": True,
        },
        "errors": errors,
    }


def heuristic_evaluation(scenario: dict[str, Any], transcript: list[dict[str, Any]], metrics: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    joined = "\n".join(f"{line.get('speaker')}: {line.get('text')}" for line in transcript).lower()
    issues = []
    if errors:
        issues.append("The websocket runner saw errors.")
    if not transcript:
        issues.append("No live transcript appeared for this synthetic call.")
    if not any(line.get("speaker") == "caller" for line in transcript):
        issues.append("No caller transcript lines were captured.")
    if not any(line.get("speaker") == "agent" for line in transcript):
        issues.append("No agent transcript lines were captured.")
    if "warraba" in " ".join(scenario.get("utterances", [])).lower() or "warboss" in " ".join(scenario.get("utterances", [])).lower():
        if "warraba" not in joined:
            issues.append("Warraba did not appear in the transcript/agent response.")
    if scenario["id"].startswith("language") and "language" in joined and "english" not in joined:
        issues.append("Language-selection flow may still be unclear.")

    return {
        "pass": not issues,
        "score": 1.0 if not issues else max(0.0, 1.0 - 0.2 * len(issues)),
        "issues": issues,
        "summary": "Heuristic evaluation only. Configure evaluator LLM for deeper analysis.",
    }


async def llm_evaluate(scenario: dict[str, Any], transcript: list[dict[str, Any]], metrics: dict[str, Any], errors: list[str], args) -> dict[str, Any]:
    if not args.evaluate:
        return heuristic_evaluation(scenario, transcript, metrics, errors)

    api_key = args.openai_api_key or os.getenv("OPENAI_API") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        result = heuristic_evaluation(scenario, transcript, metrics, errors)
        result["summary"] += " OPENAI_API/OPENAI_API_KEY was not set."
        return result

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=args.openai_base_url or None)
        prompt = {
            "scenario": scenario,
            "transcript": transcript,
            "metrics": metrics,
            "runner_errors": errors,
            "instructions": (
                "Evaluate whether the voice agent handled this synthetic phone call correctly. "
                "Focus on hearing/turn-taking, delayed transcripts, language choice handling, "
                "address correction, tool confirmation behavior, interruptions, and latency. "
                "Important grading policy: the agent is allowed to ask the language preference at the start of the call. "
                "For language-choice scenarios, do not count that initial prompt as a repeated question. "
                "After the caller chooses English or Mandarin, the agent does not need to explicitly say 'English confirmed'; "
                "continuing naturally in the chosen language is a pass. "
                "Only fail language choice if the agent stays silent, asks the language preference again after the caller answered, "
                "uses the wrong language, or ignores the caller's selected language. "
                "Return strict JSON with keys: pass boolean, score 0-1, issues array, strengths array, "
                "likely_root_cause string, recommended_fix string, summary string."
            ),
        }
        resp = await create_json_chat_completion(
            client,
            model=args.evaluator_model,
            messages=[
                {"role": "system", "content": "You are a strict QA evaluator for a phone voice agent. Output only JSON."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        return json.loads(content)
    except Exception as exc:
        result = heuristic_evaluation(scenario, transcript, metrics, errors)
        result["summary"] += f" LLM evaluation failed: {type(exc).__name__}: {exc}"
        return result


async def run_scenario(ws_url: str, http_base: str, scenario: dict[str, Any], args) -> ScenarioResult:
    started = time.monotonic()
    errors: list[str] = []
    call_payload: dict[str, Any] = {}
    metrics: dict[str, Any] = {}

    try:
        if scenario.get("autonomous"):
            call_payload = await stream_autonomous_telnyx_call(ws_url, http_base, scenario, args)
        else:
            call_payload = await stream_telnyx_call(ws_url, http_base, scenario, args)
        errors.extend(call_payload.get("errors") or [])
        metrics = call_payload.get("metrics") or {}
        call = call_payload.get("call") or {}
        transcript = call.get("transcript") or []
    except Exception as exc:
        transcript = []
        errors.append(f"{type(exc).__name__}: {exc}")

    evaluation = await llm_evaluate(scenario, transcript, metrics, errors, args)
    status = "PASS" if evaluation.get("pass") else "FAIL"
    return ScenarioResult(
        id=scenario["id"],
        description=scenario.get("description", ""),
        status=status,
        call_id=call_payload.get("call_id", ""),
        elapsed_secs=round(time.monotonic() - started, 3),
        transcript=transcript,
        metrics=metrics,
        evaluation=evaluation,
        errors=errors,
    )


def write_reports(results: list[ScenarioResult], report_dir: Path, suite_name: str) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{utc_stamp()}-{suite_name}"
    json_path = report_dir / f"{stem}.json"
    md_path = report_dir / f"{stem}.md"

    json_path.write_text(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [f"# Voice Agent Synthetic Test Report", "", f"Suite: `{suite_name}`", ""]
    passed = sum(1 for r in results if r.status == "PASS")
    lines.append(f"Summary: **{passed}/{len(results)} passed**")
    lines.append("")
    for result in results:
        lines.extend([
            f"## {result.status}: {result.id}",
            "",
            result.description,
            "",
            f"- Call ID: `{result.call_id or 'n/a'}`",
            f"- Elapsed: `{result.elapsed_secs}s`",
            f"- Bot audio frames: `{result.metrics.get('bot_audio_frames', 0)}`",
            f"- Time to first bot audio: `{result.metrics.get('time_to_first_bot_audio_secs')}`",
            f"- Score: `{result.evaluation.get('score', '')}`",
            "",
            f"Evaluator summary: {result.evaluation.get('summary', '')}",
            "",
        ])
        issues = result.evaluation.get("issues") or []
        if issues:
            lines.append("Issues:")
            for issue in issues:
                lines.append(f"- {issue}")
            lines.append("")
        if result.evaluation.get("recommended_fix"):
            lines.extend(["Recommended fix:", result.evaluation["recommended_fix"], ""])
        if result.transcript:
            lines.append("Transcript:")
            for line in result.transcript:
                lines.append(f"- **{line.get('speaker', 'unknown')}**: {line.get('text', '')}")
            lines.append("")
        if result.errors:
            lines.append("Runner errors:")
            for error in result.errors:
                lines.append(f"- `{error}`")
            lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def load_scenarios(path: Path, only: str | None) -> tuple[str, list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    scenarios = data.get("scenarios") or []
    if only:
        wanted = {item.strip() for item in only.split(",") if item.strip()}
        scenarios = [scenario for scenario in scenarios if scenario.get("id") in wanted]
    if not scenarios:
        raise SystemExit("No scenarios selected.")
    return data.get("name") or path.stem, scenarios


def parse_args():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Run synthetic voice-call scenarios against the GRC bot.")
    ap.add_argument("--target", default=os.getenv("VOICE_TEST_TARGET", "https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io"))
    ap.add_argument("--scenarios", default=str(DEFAULT_SCENARIOS))
    ap.add_argument("--only", help="comma-separated scenario IDs to run")
    ap.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    ap.add_argument("--audio-cache", default=str(DEFAULT_AUDIO_CACHE))
    ap.add_argument("--elevenlabs-api-key", default=os.getenv("ELEVENLABS_API_KEY"))
    ap.add_argument("--voice-id", default=os.getenv("VOICE_TEST_ELEVENLABS_VOICE_ID") or os.getenv("ELEVENLABS_VOICE_ID"))
    ap.add_argument("--elevenlabs-model", default=os.getenv("VOICE_TEST_ELEVENLABS_MODEL", "eleven_turbo_v2_5"))
    ap.add_argument("--force-audio", action="store_true", help="regenerate audio even when cached")
    ap.add_argument("--speed", type=float, default=float(os.getenv("VOICE_TEST_SPEED", "1.0")))
    ap.add_argument("--stability", type=float, default=float(os.getenv("VOICE_TEST_STABILITY", "0.45")))
    ap.add_argument("--similarity-boost", type=float, default=float(os.getenv("VOICE_TEST_SIMILARITY_BOOST", "0.75")))
    ap.add_argument("--gain", type=float, default=float(os.getenv("VOICE_TEST_GAIN", "1.0")))
    ap.add_argument("--noise", type=float, default=float(os.getenv("VOICE_TEST_NOISE", "0.0")), help="white-noise amplitude 0.0-1.0")
    ap.add_argument("--background-voice", default=os.getenv("VOICE_TEST_BACKGROUND_VOICE", ""), help="optional overlapping background speech text")
    ap.add_argument("--background-gain", type=float, default=float(os.getenv("VOICE_TEST_BACKGROUND_GAIN", "0.25")))
    ap.add_argument("--pre-silence", type=float, default=float(os.getenv("VOICE_TEST_PRE_SILENCE", "0.25")))
    ap.add_argument("--post-silence", type=float, default=float(os.getenv("VOICE_TEST_POST_SILENCE", "0.45")))
    ap.add_argument("--between-utterances", type=float, default=float(os.getenv("VOICE_TEST_BETWEEN_UTTERANCES", "1.2")))
    ap.add_argument("--wait-for-greeting", action=argparse.BooleanOptionalAction, default=os.getenv("VOICE_TEST_WAIT_FOR_GREETING", "true").lower() not in {"0", "false", "no"})
    ap.add_argument("--greeting-timeout", type=float, default=float(os.getenv("VOICE_TEST_GREETING_TIMEOUT", "8")))
    ap.add_argument("--greeting-quiet-secs", type=float, default=float(os.getenv("VOICE_TEST_GREETING_QUIET_SECS", "0.9")))
    ap.add_argument("--listen-secs", type=float, default=float(os.getenv("VOICE_TEST_LISTEN_SECS", "14")))
    ap.add_argument("--quiet-secs", type=float, default=float(os.getenv("VOICE_TEST_QUIET_SECS", "2.0")))
    ap.add_argument("--evaluate", action="store_true", help="use an LLM evaluator; otherwise use heuristics")
    ap.add_argument("--openai-api-key", default=os.getenv("OPENAI_API") or os.getenv("OPENAI_API_KEY"))
    ap.add_argument("--openai-base-url", default=os.getenv("OPENAI_BASE_URL") or os.getenv("LLM_BASE_URL"))
    ap.add_argument("--evaluator-model", default=os.getenv("VOICE_TEST_EVALUATOR_MODEL", "gpt-4o-mini"))
    ap.add_argument("--resident-model", default=os.getenv("VOICE_TEST_RESIDENT_MODEL", os.getenv("VOICE_TEST_EVALUATOR_MODEL", "gpt-4o-mini")))
    ap.add_argument("--resident-persona", default=os.getenv("VOICE_TEST_RESIDENT_PERSONA", "A realistic Georges River Council resident who wants clear help and gives concise answers."))
    ap.add_argument("--resident-goal", default=os.getenv("VOICE_TEST_RESIDENT_GOAL", "Choose English, ask for bin collection help, provide 50 Warraba Street Hurstville, confirm the address, and check the answer."))
    ap.add_argument("--autonomous-turns", type=int, default=int(os.getenv("VOICE_TEST_AUTONOMOUS_TURNS", "6")))
    ap.add_argument("--autonomous-agent-timeout", type=float, default=float(os.getenv("VOICE_TEST_AUTONOMOUS_AGENT_TIMEOUT", "18")))
    ap.add_argument("--agent-stt-model", default=os.getenv("VOICE_TEST_AGENT_STT_MODEL", "scribe_v2"))
    args = ap.parse_args()

    if not args.elevenlabs_api_key:
        raise SystemExit("ELEVENLABS_API_KEY is required to generate caller audio.")
    if not args.voice_id:
        raise SystemExit("ELEVENLABS_VOICE_ID or VOICE_TEST_ELEVENLABS_VOICE_ID is required.")
    return args


async def async_main():
    args = parse_args()
    ws_url, http_base = normalize_target(args.target)
    suite_name, scenarios = load_scenarios(Path(args.scenarios), args.only)
    print(f"Target WS: {ws_url}")
    print(f"Target API: {http_base}")
    print(f"Suite: {suite_name} ({len(scenarios)} scenario(s))")

    results = []
    for index, scenario in enumerate(scenarios, start=1):
        print(f"\n[{index}/{len(scenarios)}] {scenario['id']}: {scenario.get('description', '')}")
        result = await run_scenario(ws_url, http_base, scenario, args)
        results.append(result)
        score = result.evaluation.get("score", "")
        print(f"  {result.status} score={score} transcript_lines={len(result.transcript)} call_id={result.call_id}")
        if result.evaluation.get("summary"):
            print(f"  {result.evaluation['summary']}")

    json_path, md_path = write_reports(results, Path(args.report_dir), suite_name)
    print(f"\nReports written:\n  {json_path}\n  {md_path}")


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
