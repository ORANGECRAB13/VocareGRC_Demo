#!/usr/bin/env python3
"""Concurrency stress test for the Vocare GRC bot's Telnyx WebSocket path.

Emulates the Telnyx media-streaming protocol directly against /telnyx/ws,
bypassing the /telnyx/voice webhook and real Telnyx. Each synthetic call:
  1. opens the WS, sends `connected` + `start` (fake call_control_id, PCMU 8k)
  2. streams a real μ-law speech clip in real time (20ms frames)
  3. receives the bot's audio back, recording time-to-first-bot-audio,
     bot frame count, and inter-frame gap stats (a choppiness proxy)
  4. sends `stop` and closes

The orchestrator ramps concurrency and AUTO-STOPS at the failure wall (error
rate / TTFA blow-up), so you don't need exact ElevenLabs/OpenAI quotas — the
ramp discovers them. 429s show up as WS close/handshake errors here and in the
container logs.

Usage:
  python telnyx_stress.py --url wss://<app>/telnyx/ws --ramp 1,2,5,10,20,40
"""
import argparse
import asyncio
import base64
import json
import os
import statistics
import time
import uuid

import websockets

FRAME_BYTES = 160       # 20ms of μ-law @ 8kHz (1 byte/sample)
FRAME_SECS = 0.02


def load_frames(path):
    with open(path, "rb") as f:
        data = f.read()
    return [data[i:i + FRAME_BYTES] for i in range(0, len(data) - FRAME_BYTES + 1, FRAME_BYTES)]


class CallResult:
    __slots__ = ("ok", "ttfa", "bot_frames", "max_gap", "error")

    def __init__(self):
        self.ok = False
        self.ttfa = None        # seconds to first bot audio frame
        self.bot_frames = 0
        self.max_gap = 0.0      # largest gap between consecutive bot frames
        self.error = None


async def run_call(url, frames, listen_secs, idx):
    res = CallResult()
    cid = f"stress-{idx}-{uuid.uuid4().hex[:8]}"
    start_t = time.monotonic()
    last_bot_t = [None]

    try:
        async with websockets.connect(url, open_timeout=15, close_timeout=5, max_size=None) as ws:
            await ws.send(json.dumps({"event": "connected"}))
            await ws.send(json.dumps({
                "event": "start",
                "stream_id": cid,
                "start": {
                    "call_control_id": cid,
                    "media_format": {"encoding": "PCMU", "sample_rate": 8000, "channels": 1},
                },
            }))

            async def receiver():
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("event") == "media" and msg.get("media", {}).get("payload"):
                        now = time.monotonic()
                        if res.ttfa is None:
                            res.ttfa = now - start_t
                        if last_bot_t[0] is not None:
                            res.max_gap = max(res.max_gap, now - last_bot_t[0])
                        last_bot_t[0] = now
                        res.bot_frames += 1

            rx = asyncio.create_task(receiver())

            # Stream the speech clip paced at real time.
            send_clock = time.monotonic()
            for i, fr in enumerate(frames):
                await ws.send(json.dumps({"event": "media", "media": {"payload": base64.b64encode(fr).decode()}}))
                send_clock += FRAME_SECS
                await asyncio.sleep(max(0, send_clock - time.monotonic()))

            # Linger to capture the bot's reply, then hang up.
            await asyncio.sleep(listen_secs)
            await ws.send(json.dumps({"event": "stop"}))
            rx.cancel()
            try:
                await rx
            except asyncio.CancelledError:
                pass

        res.ok = True
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
    return res


async def run_step(url, frames, n, listen_secs, base_idx):
    tasks = [run_call(url, frames, listen_secs, base_idx + i) for i in range(n)]
    return await asyncio.gather(*tasks)


def summarize(level, results):
    ok = [r for r in results if r.ok and r.error is None]
    errs = [r for r in results if r.error]
    ttfas = [r.ttfa for r in ok if r.ttfa is not None]
    no_audio = [r for r in ok if r.ttfa is None]
    gaps = [r.max_gap for r in ok if r.bot_frames > 1]

    def p(xs, f):
        return f"{f(xs):.2f}" if xs else "—"

    err_rate = len(errs) / len(results) if results else 0
    print(f"\n── concurrency {level} ({len(results)} calls) ──")
    print(f"  ok={len(ok)}  errors={len(errs)}  no-bot-audio={len(no_audio)}  err_rate={err_rate:.0%}")
    print(f"  TTFA  mean={p(ttfas, statistics.mean)}s  p95={p(sorted(ttfas), lambda x: x[int(len(x)*0.95)-1]) if ttfas else '—'}s  max={p(ttfas, max)}s")
    print(f"  bot max-gap  mean={p(gaps, statistics.mean)}s  worst={p(gaps, max)}s  (choppiness proxy)")
    if errs:
        from collections import Counter
        for msg, c in Counter(e.error.split(':')[0] for e in errs).most_common(4):
            print(f"    error: {msg} ×{c}")
    return {
        "level": level, "calls": len(results), "ok": len(ok), "errors": len(errs),
        "no_audio": len(no_audio), "err_rate": round(err_rate, 3),
        "ttfa_mean": round(statistics.mean(ttfas), 3) if ttfas else "",
        "ttfa_max": round(max(ttfas), 3) if ttfas else "",
        "gap_worst": round(max(gaps), 3) if gaps else "",
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="wss://<app>/telnyx/ws")
    ap.add_argument("--audio", default=os.path.join(os.path.dirname(__file__), "sample_ulaw_8k.raw"))
    ap.add_argument("--ramp", default="1,2,5,10,20,40", help="comma-separated concurrency levels")
    ap.add_argument("--listen-secs", type=float, default=6.0, help="seconds to wait for bot reply after sending audio")
    ap.add_argument("--max-concurrency", type=int, default=60, help="hard safety cap")
    ap.add_argument("--fail-err-rate", type=float, default=0.25, help="auto-stop when a step's error rate exceeds this")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "report.csv"))
    args = ap.parse_args()

    if not os.path.exists(args.audio):
        raise SystemExit(f"Audio clip not found: {args.audio}\nRun: python loadtest/gen_sample_audio.py")
    frames = load_frames(args.audio)
    levels = [int(x) for x in args.ramp.split(",") if x.strip()]
    print(f"Loaded {len(frames)} frames ({len(frames)*FRAME_SECS:.1f}s). Ramp: {levels}  Target: {args.url}")

    rows, base = [], 0
    for lvl in levels:
        if lvl > args.max_concurrency:
            print(f"\n⚠ stopping: level {lvl} exceeds --max-concurrency {args.max_concurrency}")
            break
        results = await run_step(args.url, frames, lvl, args.listen_secs, base)
        base += lvl
        row = summarize(lvl, results)
        rows.append(row)
        if row["err_rate"] > args.fail_err_rate:
            print(f"\n🛑 wall detected at concurrency {lvl}: err_rate {row['err_rate']:.0%} > {args.fail_err_rate:.0%}. Stopping ramp.")
            break

    if rows:
        import csv
        with open(args.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n✓ report written: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
