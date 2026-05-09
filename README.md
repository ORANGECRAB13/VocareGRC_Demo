# Georges River Council — AI Voice Agent Demo

A real-time, multilingual voice AI agent for Georges River Council built on [Pipecat](https://github.com/pipecat-ai/pipecat). It handles resident enquiries about bin collection days, development applications, and upcoming council events — entirely over WebRTC, with sub-second response latency.

---

## Capabilities

- **Bin Collection Days** — Live lookup via the GRC Wastetrack API with polygon-based fallback. Proactive prefetch begins the moment intent is detected, before the LLM finishes responding.
- **Development Applications** — Answers policy and process questions from a comprehensive GRC DA knowledge base embedded directly in the system prompt.
- **What's On Events** — Fetches the GRC RSS events feed at session start and embeds it into context (Cache Augmented Generation) — no tool call latency during conversation.
- **Multilingual** — Automatically switches LLM model and TTS voice mid-call when the caller speaks a language other than English.

---

## Project Structure

```
GRC PRESENTATION DEMO/
├── bot.py                    # Main Pipecat bot — WebRTC pipeline entry point
├── requirements.txt          # Python dependencies
├── Dockerfile                # Container build
├── docker-compose.yml        # Compose config
├── .env.example              # Environment variable template
│
├── GRC_pilot/                # Core agent logic
│   ├── tools.py              # Bin collection tool (Wastetrack + polygon fallback)
│   ├── grc_wastetrack.py     # GRC Wastetrack API scraper (persistent session)
│   ├── grc_events.py         # GRC What's On RSS feed parser + 30-min cache
│   ├── bin_zones.py          # Polygon zone definitions + point-in-polygon lookup
│   ├── da_knowledge.py       # DA policy knowledge base (embedded in system prompt)
│   ├── street_corrector.py   # STT street-name error correction
│   ├── benchmark_correction.py
│   ├── main.py               # ElevenLabs SDK version (standalone alternative)
│   ├── schedules/            # Bin schedule data
│   └── api/                  # Node.js geographic microservice (polygon fallback)
│       ├── bin-zone-api.js
│       ├── package.json
│       └── zones/            # GeoJSON polygon files per collection day
│
└── static/                   # Frontend dashboard (served by bot.py)
    ├── index.html            # Main shell
    ├── grc-shell.jsx         # App layout and navigation
    ├── grc-dashboard.jsx     # Live metrics dashboard
    ├── grc-agents.jsx        # Agent call panel (WebRTC PTT + Natural mic modes)
    ├── grc-livecalls.jsx     # Live call monitor
    ├── grc-heatmap.jsx       # Call volume heatmap
    ├── grc-history.jsx       # Session history
    ├── grc-translation.jsx   # Live translation mode
    └── vocare-app.jsx        # Translation session UI
```

---

## Architecture

### Pipeline (bot.py)

```
Deepgram STT → ThinkerProcessor → ContextEnricher → LLM (Cerebras) → ElevenLabs TTS
                     ↓
              Wastetrack prefetch
              (async, fires on intent detection)
```

- **ThinkerProcessor** — Runs a fast background LLM (Cerebras `llama3.1-8b`) to classify intent and extract entities. If bin collection intent + address is detected, it immediately starts the Wastetrack HTTP call in the background, so the result is often ready before the main LLM even calls the tool.
- **ContextEnricherProcessor** — Injects thinker state into the LLM context. Skips injection for generic events intent so the LLM naturally asks a clarifying question instead of dumping the full list.
- **LanguageSwitchProcessor** — Detects language changes and hot-swaps both the TTS voice and the LLM model without reconnecting.
- **FillerTTSProcessor** — Plays short filler phrases ("One moment…") while tool calls are in flight to keep conversation natural.
- **CAG (Cache Augmented Generation)** — Events are fetched from the GRC RSS feed at session start and embedded directly into the system prompt. No tool call needed for events questions.

### LLM Models (Cerebras)

| Context | Model |
|---|---|
| English conversations | `gpt-oss-120b` |
| Non-English conversations | `qwen-3-235b-a22b-instruct-2507` |
| Thinker (intent classifier) | `llama3.1-8b` |

### Bin Collection Lookup

1. **Primary** — GRC Wastetrack API (`v2.wastetrack.net`): returns exact next collection date per service type.
2. **Fallback** — Google Geocoding + local GeoJSON polygon lookup, returning the collection zone and weekly schedule.

---

## Setup

### Requirements

- Python 3.11+
- Node.js 20+ (for the polygon fallback API)
- API keys: Cerebras, ElevenLabs, Deepgram, Google Maps

### 1. Clone and install dependencies

```bash
pip install -r requirements.txt

cd GRC_pilot/api
npm install
cd ../..
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your keys:

```env
CEREBRAS_API_KEY=your_cerebras_key
ELEVENLABS_API_KEY=your_elevenlabs_key
DEEPGRAM_API_KEY=your_deepgram_key
GOOGLE_MAPS_API_KEY=your_google_maps_key

# ElevenLabs voice IDs
ELEVENLABS_VOICE_ID_EN=...
ELEVENLABS_VOICE_ID_ML=...
```

### 3. Run

```bash
python bot.py
```

The server starts on `http://localhost:8080`. Open the dashboard in a browser, navigate to **AI Agents**, and click **Connect to GRC Agent**.

#### Docker

```bash
docker-compose up --build
```

---

## Mic Modes

The agent call panel supports two mic modes, toggleable during an active call:

- **PTT (Push-to-Talk)** — Hold the button to speak. Default mode.
- **Natural** — Mic stays open; VAD on the server side handles turn detection.

---

## ElevenLabs SDK Version

`GRC_pilot/main.py` is a standalone alternative that runs the agent entirely through the ElevenLabs Conversational AI SDK (no Pipecat). It supports bin collection and DA enquiries but lacks the thinker pipeline, CAG events, and multilingual LLM switching.

```bash
cd GRC_pilot
python main.py
```

---

## Azure CI/CD Deployment

This project includes a GitHub Actions workflow (`.github/workflows/deploy-azure.yml`) to automatically deploy the application to **Azure Container Apps**.

> **Note**: Azure Container Apps routes traffic through a Layer 7 load balancer (TCP 80/443). WebRTC's native UDP connections won't pass through. Therefore, the **Twilio TURN server configuration in your `.env` file is strictly required** for audio to work in production!

### One-Time Setup Instructions

Before the CI pipeline can run, you must provision the Azure infrastructure and set up GitHub Secrets.

#### 1. Provision Azure Resources
Run the following Azure CLI commands to create a Container Registry and a Container App:

```bash
az login

RESOURCE_GROUP="vocare-grc-rg"
LOCATION="australiaeast"
ACR_NAME="vocaregrcregistry$RANDOM"
ENV_NAME="vocare-grc-env"
APP_NAME="vocare-grc-app"

# Create Resource Group & Registry
az group create --name $RESOURCE_GROUP --location $LOCATION
az acr create --resource-group $RESOURCE_GROUP --name $ACR_NAME --sku Basic --admin-enabled true

# Create Container App Environment & App
az containerapp env create --name $ENV_NAME --resource-group $RESOURCE_GROUP --location $LOCATION
az containerapp create \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --environment $ENV_NAME \
  --image mcr.microsoft.com/k8se/quickstart:latest \
  --target-port 8080 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 5
```

#### 2. Set Container App Environment Variables
Copy the keys from your `.env` file into Azure so the bot can authenticate:
```bash
az containerapp update --name $APP_NAME --resource-group $RESOURCE_GROUP \
  --set-env-vars "ELEVENLABS_API_KEY=sk_..." "GROQ_API_KEY=gsk_..." "TWILIO_ACCOUNT_SID=AC..." "TWILIO_AUTH_TOKEN=..."
```

#### 3. Add GitHub Secrets
Navigate to your GitHub repository's **Settings > Secrets and variables > Actions** and add the following repository secrets:

1. `AZURE_CREDENTIALS`: Run the command below and paste the entire JSON output as the secret value.
   ```bash
   az ad sp create-for-rbac --name "vocare-deployer" --role contributor \
     --scopes /subscriptions/<YOUR_SUBSCRIPTION_ID>/resourceGroups/vocare-grc-rg \
     --sdk-auth
   ```
2. `REGISTRY_LOGIN_SERVER`: Output of `az acr show --name $ACR_NAME --query loginServer -o tsv`
3. `REGISTRY_USERNAME`: Output of `az acr credential show --name $ACR_NAME --query username -o tsv`
4. `REGISTRY_PASSWORD`: Output of `az acr credential show --name $ACR_NAME --query passwords[0].value -o tsv`

### Continuous Deployment
Once the setup is complete, every time you push code to the `master` branch, GitHub Actions will automatically:
1. Build the new Docker image.
2. Push the image to Azure Container Registry.
3. Update the Azure Container App to serve the latest version.
