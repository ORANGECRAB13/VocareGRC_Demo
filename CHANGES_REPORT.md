# Change Report — Telnyx Integration, Voice Fixes, Scaling & Stress Test

Branch: `Telnyx-Phone` · Azure app: `vocare-grc-bot` (RG `vocare-grc`, Australia East)

This report summarizes all changes made in this work session: a local Docker build
path, a Telnyx phone integration, voice-quality and prompt bug fixes, Azure
horizontal-scaling config, and a concurrency stress-test harness with results.

---

## 1. Local Docker build (no Azure base image required)

The committed `Dockerfile` builds `FROM` a private Azure Container Registry base
image. To build/run locally without ACR access:

- **`Dockerfile.local`** — self-contained build from `python:3.11-slim` + PyPI,
  with the system libs needed by `aiortc`/`av`/`pylibsrtp`.
- **`docker-compose.local.yml`** — uses `Dockerfile.local`, adds `depends_on: neo4j`.
- **`.env.example`** — template for all required secrets.

Run: `docker compose -f docker-compose.local.yml up --build`

## 2. Cerebras model read from env (no hardcoded models)

`bot.py` — replaced all three hardcoded model names (`qwen-3-235b-a22b-instruct-2507`,
`llama3.1-8b`, `gpt-oss-120b`) with `os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")`.
Fixes a 404 where a decommissioned model was hardcoded. Set `CEREBRAS_MODEL` in env.

## 3. Agents-tab live transcript UI

- **`static/grc-agents.jsx`** — the call panel now polls `/api/graph/poll` and renders
  **bot-only** transcripts. Chinese replies render as **bold Chinese** with the English
  translation beneath (via a new translate endpoint); user speech is not shown.
- **`bot.py`** — new `POST /api/translate` (Cerebras) for Chinese→English.

## 4. Telnyx phone integration (Call Control)

The number is on a Telnyx **Call Control** Application (not TeXML), so the app must
answer the call and start streaming via the REST API — returning TeXML does nothing.

`bot.py`:
- Import `TelnyxFrameSerializer`.
- `_configure_telnyx_webhook()` — on startup, PATCHes the Call Control Application's
  webhook to `PUBLIC_URL_TELNYX/telnyx/voice`.
- `POST /telnyx/voice` — on `call.initiated` (incoming), calls
  `/v2/calls/{id}/actions/answer` with `stream_url`, `stream_bidirectional_mode=rtp`,
  `stream_bidirectional_codec=PCMU` → answers + starts bidirectional streaming.
- `run_telnyx_bot()` + `/telnyx/ws` — runs the GRC agent (ElevenLabs STT/TTS, Cerebras
  LLM, language switching, bin lookup, transfer) over the native Telnyx WS protocol.
  Self-contained per connection (no shared in-memory state) → replica-independent.
- Transfer uses `/v2/calls/{call_control_id}/actions/transfer`.

`.env.example` — added `TELNYX_API_KEY`, `TELNYX_CALL_CONTROL_APP_ID`,
`TELNYX_PHONE_NUMBER`, `PUBLIC_URL_TELNYX`.

**Diagnosis history:** TURN servers are NOT in the phone path (WebSocket, not WebRTC);
the original "call rings forever" was a Call-Control-vs-TeXML mismatch.

## 5. Voice-quality fix (choppy bot audio)

`bot.py` — set `audio_out_10ms_chunks=16` (160 ms frames) on the Telnyx transport.
Choppiness scaled monotonically with frame size on the Telnyx RTP leg (20 ms worse,
40 ms baseline, 80 ms better, 160 ms smooth). Twilio keeps its default. Root cause was
per-chunk resample/encode seams + RTP-playout jitter, not CPU or geography.

## 6. Bin FAQ bug (agent claimed the finder was offline)

`GRC_pilot/bin_faq.py` — removed an "IMPORTANT STATUS … currently unavailable" block
that was embedded in the system prompt. The LLM parroted it and refused to call the
working `get_bin_collection_day` tool. Reworded to direct the model to use the tool and
never claim it's offline. The tool itself was never broken.

## 7. Azure horizontal scaling

Applied via `az` (infra, not code) on the live app:
- **Session affinity = `sticky`** — required because browser flows (`/api/offer` →
  `/api/graph/poll` → `/api/hangup`) share per-replica in-memory state
  (`graph_event_queues`). Without affinity, multi-replica breaks the Agents tab.
  (Phone calls are replica-independent and don't need it.)
- **Scale rule** — HTTP concurrency, `min 1 / max 25`, threshold **5** concurrent
  requests per replica.

## 8. Stress-test harness (`loadtest/`)

- **`gen_sample_audio.py`** — macOS `say` → 8 kHz mono μ-law clip.
- **`telnyx_stress.py`** — async harness emulating the Telnyx WS protocol against
  `/telnyx/ws`; ramps concurrency, auto-stops at the error wall, reports TTFA, bot
  frame-gap (choppiness proxy), and error rate; writes CSV.
- **`requirements.txt`, `README.md`.** Local venv + generated audio/CSVs are gitignored.

---

## Stress-test results (live app)

**Ramp (short bursts):** all steps **0% errors**; latency degraded gracefully.

| Concurrency | err rate | TTFA mean | TTFA max |
|------------:|:--------:|:---------:|:--------:|
| 1  | 0% | 2.7s | 2.7s |
| 2  | 0% | 1.4s | 1.4s |
| 5  | 0% | 1.7s | 2.0s |
| 10 | 0% | 2.1s | 2.2s |
| 20 | 0% | 3.0s | 3.8s |
| 40 | 0% | 4.2s | 4.6s |

**Sustained (20 concurrent for ~95 s):** 0% errors, TTFA mean 2.4s; replicas scaled
**2 → 5** (matches 20 ÷ threshold 5). No upstream `429`s observed.

### Findings
- **No hard failure wall up to 40 concurrent.** The system absorbs burst load as
  latency (TTFA 1.4s → 4.2s), not errors.
- **Horizontal scaling works**, but the HTTP-concurrency autoscaler (30 s poll, 300 s
  cooldown) only reacts under **sustained** load — short bursts ran on 1–2 replicas.
- **Upstream APIs were not the bottleneck** at this level (no 429s) — ElevenLabs/
  Cerebras tiers are sufficient for ~40 concurrent in these tests.

### Recommendations
- For spiky telephony traffic, consider `min-replicas 2–3` to cut cold-start latency,
  and/or lower the concurrency threshold (3–4) for snappier TTFA — at higher idle cost.
- Per-replica (1 vCPU/2 GiB) handles ~5–10 concurrent calls before TTFA noticeably
  climbs; bump to 2 vCPU/4 GiB if you want more headroom per replica.
- Re-run sustained tests at higher concurrency (e.g. 40–60 held for several minutes)
  to find the true ElevenLabs/Cerebras concurrency ceiling.

## Deployment

Built amd64 images via `az acr build --platform linux/amd64` and deployed by digest
with `az containerapp update`. Latest functional revision: `vocare-grc-bot--0000042`
(bin FAQ fix), with scaling config + sticky sessions applied on top.

Set `PUBLIC_URL` and `PUBLIC_URL_TELNYX` to the app's own Azure URL in production.
