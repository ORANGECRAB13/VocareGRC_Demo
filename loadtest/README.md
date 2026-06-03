# Telnyx Concurrency Stress Test

Synthetic load test for the GRC voice bot's Telnyx WebSocket path. Emulates the
Telnyx media-streaming protocol directly against `/telnyx/ws` (bypassing the
`/telnyx/voice` webhook and real Telnyx), replaying a real μ-law speech clip so
the **full STT → LLM → TTS pipeline** runs per synthetic call.

## Setup

```bash
python3 -m venv .venv-loadtest
./.venv-loadtest/bin/pip install -r loadtest/requirements.txt
./.venv-loadtest/bin/python loadtest/gen_sample_audio.py   # makes sample_ulaw_8k.raw (macOS `say`)
```

## Run

```bash
APP=wss://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/telnyx/ws

# Ramp (burst) — finds graceful-degradation / error wall, auto-stops on errors
./.venv-loadtest/bin/python loadtest/telnyx_stress.py --url "$APP" --ramp 1,2,5,10,20,40

# Sustained — hold N concurrent long enough for the autoscaler (30s poll) to react
./.venv-loadtest/bin/python loadtest/telnyx_stress.py --url "$APP" --ramp 20 --listen-secs 90
```

Key flags: `--ramp` (levels), `--listen-secs` (hold each call open after audio),
`--fail-err-rate` (auto-stop threshold), `--max-concurrency` (hard safety cap).

## Watch scaling during a run

```bash
watch -n 10 'az containerapp replica list -n vocare-grc-bot -g vocare-grc -o table'
az containerapp logs show -n vocare-grc-bot -g vocare-grc --tail 200 | grep -iE "429|rate.?limit|too many"
```

## Metrics reported

- **TTFA** — time-to-first-bot-audio (latency under load; the bot greets on connect)
- **bot max-gap** — largest gap between consecutive bot audio frames (choppiness proxy)
- **err_rate / no-bot-audio** — failed or silent calls (429s show up as WS errors)

## Notes

- Burst steps are short; the HTTP-concurrency autoscaler (30s poll, 300s cooldown)
  only reacts under **sustained** load — use `--listen-secs 90` to see replicas climb.
- Each synthetic call uses a fake `call_control_id`; the serializer's auto-hangup
  REST call fails harmlessly at end-of-call (just a log line).
- Runs against the LIVE app and spends ElevenLabs/Cerebras credits — cap concurrency
  and watch the provider dashboards.
