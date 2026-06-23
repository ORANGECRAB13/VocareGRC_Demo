# Utilities10x LiveKit outage demo

This branch contains a prompt-only utilities customer-support demonstration. It
uses the production LiveKit voice pipeline with no tool calling and simulates a
complex local power-outage scenario from prompt context.

Pipeline:

```text
LiveKit room audio
  -> ElevenLabs Scribe v2 Realtime
  -> Silero VAD and LiveKit endpointing
  -> OpenAI-compatible LLM with the GRC prompt
  -> ElevenLabs streaming TTS
  -> LiveKit room audio
```

The defaults match the Hemaas LiveKit branch:

- ElevenLabs server VAD silence: `0.3` seconds
- LiveKit minimum endpointing delay: `0.05` seconds
- LiveKit maximum endpointing delay: `0.6` seconds
- Preemptive generation: enabled
- Interruptions: enabled
- One prewarmed idle worker process

## Run with Docker

Add the required API keys and voice ID to `.env`, then run:

```bash
docker compose -f docker-compose.livekit.yml up --build
```

Open <http://localhost:8190>, join the default room, and allow microphone access.

The recommended caller script and scenario facts are documented in
`docs/utilities10x-demo-scenario.md`.

This stack uses host ports `7900-7902` for LiveKit and `8190` for the browser
client so it can run alongside the Hemaas stack on `7880-7882` and `8090`.
It is fully self-hosted and does not connect to LiveKit Cloud.

Docker Desktop must advertise an address reachable by both the browser and the
containers. If your local network address changes, set this in `.env`:

```bash
LIVEKIT_NODE_IP=$(ipconfig getifaddr en0)
```

## Run locally

Use Python 3.11 or newer. The macOS Command Line Tools Python 3.9 is too old for
the pinned LiveKit Agents SDK.

Start a LiveKit dev server:

```bash
docker run --rm \
  -v "$PWD/livekit-dev.yaml:/etc/livekit.yaml:ro" \
  -p 7900:7900 -p 7901:7901 -p 7902:7902/udp \
  livekit/livekit-server:latest \
  --dev --config /etc/livekit.yaml --bind 0.0.0.0
```

Then:

```bash
python3.11 -m venv .venv-livekit
source .venv-livekit/bin/activate
pip install -r requirements.livekit.txt
python livekit_agent.py download-files
python livekit_agent.py dev
```

In another terminal:

```bash
source .venv-livekit/bin/activate
LIVEKIT_URL=ws://localhost:7900 \
LIVEKIT_PUBLIC_URL=ws://localhost:7900 \
uvicorn livekit_token_server:app --host 0.0.0.0 --port 8190
```
