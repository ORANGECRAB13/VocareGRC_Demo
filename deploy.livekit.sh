#!/bin/bash
# Deploy the VocareGRC LiveKit agent (livekit_agent.py / Dockerfile.livekit) to
# Azure Container Apps, connecting to LiveKit Cloud.
#
# The agent is a worker with NO ingress: it dials out to LiveKit Cloud and waits
# for explicit dispatch (agent_name=vocare-grc-agent). Because .dockerignore
# excludes .env, all runtime keys are injected here as Container App secrets/env.
#
# Usage: ./deploy.livekit.sh [tag]   (tag defaults to a UTC timestamp)
set -euo pipefail

cd "$(dirname "$0")"

SUBSCRIPTION="${SUBSCRIPTION:-45b12582-372e-4102-8a44-c3640e08b714}"
RG="${RESOURCE_GROUP:-vocare-grc}"
ACR="${ACR_NAME:-vocaregrcregistry}"
ENV_NAME="${CONTAINERAPP_ENV:-vocare-grc-env}"
APP="${CONTAINER_APP:-utilities10x-livekit-demo-agent}"
PLATFORM="${PLATFORM:-linux/amd64}"
TAG="${1:-$(date -u +%Y%m%d%H%M%S)}"
IMAGE="$ACR.azurecr.io/$APP:$TAG"

# Read a value from .env (everything after the first '='), trimming CR.
envval() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d '\r'; }

ELEVENLABS_API_KEY="$(envval ELEVENLABS_API_KEY)"
CEREBRAS_API_KEY="$(envval CEREBRAS_API_KEY)"
ELEVENLABS_VOICE_ID="$(envval ELEVENLABS_VOICE_ID)"
ELEVENLABS_CHINESE_VOICE="$(envval ELEVENLABS_CHINESE_VOICE)"
LIVEKIT_URL="$(envval LIVEKIT_URL)"
LIVEKIT_API_KEY="$(envval LIVEKIT_API_KEY)"
LIVEKIT_API_SECRET="$(envval LIVEKIT_API_SECRET)"

for required in ELEVENLABS_API_KEY CEREBRAS_API_KEY LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET; do
  if [[ -z "${!required}" ]]; then
    echo "ERROR: $required is missing from .env" >&2
    exit 1
  fi
done

az account set --subscription "$SUBSCRIPTION"

echo "==> Building agent image in ACR: $APP:$TAG"
az acr build \
  --registry "$ACR" \
  --image "$APP:$TAG" \
  --file Dockerfile.livekit \
  --platform "$PLATFORM" \
  .

# Non-secret runtime config. Secrets (ELEVENLABS/CEREBRAS keys, LiveKit secret)
# are passed separately and referenced via secretref:.
ENV_VARS=(
  "LIVEKIT_URL=$LIVEKIT_URL"
  "LIVEKIT_API_KEY=$LIVEKIT_API_KEY"
  "LIVEKIT_API_SECRET=secretref:livekit-api-secret"
  "AGENT_NAME=utilities10x-agent"
  "LLM_PROVIDER=cerebras"
  "LLM_MODEL=zai-glm-4.7"
  "CEREBRAS_MODEL=zai-glm-4.7"
  "LLM_REASONING_EFFORT=none"
  "LIVEKIT_LLM_MAX_TOKENS=220"
  "ELEVENLABS_API_KEY=secretref:elevenlabs-api-key"
  "ELEVENLABS_VOICE_ID=$ELEVENLABS_VOICE_ID"
  "ELEVENLABS_CHINESE_VOICE=$ELEVENLABS_CHINESE_VOICE"
  "LIVEKIT_STT_MODEL=scribe_v2_realtime"
  "LIVEKIT_STT_VAD_SILENCE_SECS=0.3"
  "LIVEKIT_TTS_MODEL=eleven_flash_v2_5"
  "ELEVENLABS_TTS_OUTPUT_FORMAT=pcm_8000"
  "ELEVENLABS_TTS_AUTO_MODE=true"
  "ELEVENLABS_TTS_SYNC_ALIGNMENT=false"
  "ELEVENLABS_TTS_APPLY_TEXT_NORMALIZATION=off"
  "ELEVENLABS_TTS_ENABLE_LOGGING=true"
  "ELEVENLABS_TTS_USE_SPEAKER_BOOST=false"
  "ELEVENLABS_TTS_INACTIVITY_TIMEOUT=60"
  "LIVEKIT_BACKGROUND_AUDIO=true"
  "LIVEKIT_BACKGROUND_AUDIO_VOLUME=1.0"
  "LIVEKIT_MIN_ENDPOINTING_DELAY=0.05"
  "LIVEKIT_MAX_ENDPOINTING_DELAY=0.6"
  "LIVEKIT_PREEMPTIVE_GENERATION=true"
  "LIVEKIT_ALLOW_INTERRUPTION=true"
  "LIVEKIT_NUM_IDLE_PROCESSES=1"
  "LIVEKIT_GREETING_DELAY_SECS=0.3"
  "CEREBRAS_API_KEY=secretref:cerebras-api-key"
  "EOT_ENABLED=true"
  "EOT_MODEL=gpt-oss-120b"
  "EOT_REASONING_EFFORT=low"
  "EOT_CLASSIFY_DEBOUNCE_MS=150"
  "EOT_COMPLETE_THRESHOLD=0.98"
  "EOT_FAST_LANE_ENABLED=true"
  "EOT_FAST_LANE_STABILITY_MS=800"
  "EOT_FAST_LANE_MIN_WORDS=3"
  "EOT_FAST_LANE_TRANSCRIPT_TIMEOUT_SECONDS=0.8"
  "EOT_CONTEXT_FILLER_ENABLED=true"
  "EOT_LATE_FILLER_WINDOW_SECONDS=1.5"
)

SECRETS=(
  "elevenlabs-api-key=$ELEVENLABS_API_KEY"
  "cerebras-api-key=$CEREBRAS_API_KEY"
  "livekit-api-secret=$LIVEKIT_API_SECRET"
)

if az containerapp show -n "$APP" -g "$RG" >/dev/null 2>&1; then
  echo "==> Updating existing Container App: $APP"
  az containerapp secret set -n "$APP" -g "$RG" --secrets "${SECRETS[@]}"
  az containerapp update -n "$APP" -g "$RG" \
    --image "$IMAGE" \
    --set-env-vars "${ENV_VARS[@]}"
else
  echo "==> Creating Container App: $APP"
  ACR_PW="$(az acr credential show -n "$ACR" --query 'passwords[0].value' -o tsv)"
  az containerapp create \
    --name "$APP" \
    --resource-group "$RG" \
    --environment "$ENV_NAME" \
    --image "$IMAGE" \
    --registry-server "$ACR.azurecr.io" \
    --registry-username "$ACR" \
    --registry-password "$ACR_PW" \
    --min-replicas 1 --max-replicas 1 \
    --cpu 1.0 --memory 2.0Gi \
    --secrets "${SECRETS[@]}" \
    --env-vars "${ENV_VARS[@]}"
fi

echo "==> Done. Tail logs with:"
echo "    az containerapp logs show -n $APP -g $RG --tail 50 --follow"
