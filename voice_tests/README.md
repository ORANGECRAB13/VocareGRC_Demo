# Synthetic Voice Agent Tests

This harness lets us test the live GRC phone agent without asking people to call
the number. It generates caller speech with ElevenLabs, streams the audio into
the bot's Vobiz websocket, watches `/api/live-calls`, and writes a report.

It is useful for testing:

- short language choices like `English please`
- one-word turns
- delayed or missing transcripts
- noisy address variants like `warboss street`
- filler words and pauses
- background speech and low-volume audio
- whether the agent called the right tool or asked the right confirmation

## Setup

From the repo root:

```bash
cd /Users/vonners/DevProjects/VocareGRC_Demo
python3 -m venv .venv-voice-tests
./.venv-voice-tests/bin/pip install -r voice_tests/requirements.txt
```

Make sure `.env` contains:

```bash
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
OPENAI_API=... # optional, only needed for --evaluate
```

## Run One Scenario

```bash
./.venv-voice-tests/bin/python voice_tests/run_voice_tests.py \
  --only language_english_please
```

## Run The Smoke Suite

```bash
./.venv-voice-tests/bin/python voice_tests/run_voice_tests.py
```

## Run With LLM Evaluation

```bash
./.venv-voice-tests/bin/python voice_tests/run_voice_tests.py --evaluate
```

The evaluator writes deeper notes about likely root cause and recommended fixes.
Without `--evaluate`, the runner still writes a heuristic report.

## Test Noisy Phone Conditions

Lower volume:

```bash
./.venv-voice-tests/bin/python voice_tests/run_voice_tests.py \
  --only language_english_please \
  --gain 0.45
```

Add white noise:

```bash
./.venv-voice-tests/bin/python voice_tests/run_voice_tests.py \
  --only warraba_warboss \
  --noise 0.04
```

Add an overlapping background voice:

```bash
./.venv-voice-tests/bin/python voice_tests/run_voice_tests.py \
  --only warraba_warboss \
  --background-voice "I am talking in the background about something else" \
  --background-gain 0.35
```

## Target Localhost

If running the bot locally:

```bash
./.venv-voice-tests/bin/python voice_tests/run_voice_tests.py \
  --target http://localhost:8080 \
  --only language_english_please
```

The runner converts the target into:

```text
ws(s)://<host>/vobiz/ws
http(s)://<host>/api/live-calls
```

## Outputs

Reports are written to:

```text
voice_tests/reports/
```

Audio clips are cached in:

```text
voice_tests/audio_cache/
```

Use `--force-audio` to regenerate cached ElevenLabs audio.

## Adding Scenarios

Edit:

```text
voice_tests/scenarios/grc_smoke.json
```

Each scenario has:

```json
{
  "id": "short_name",
  "description": "What this checks",
  "utterances": ["What the synthetic caller says"],
  "expectations": ["What should happen"]
}
```

Keep each scenario focused. If one fails, the report should make the broken
layer obvious: audio ingress, VAD, STT finalization, turn aggregation, LLM,
tool call, or TTS.
