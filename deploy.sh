#!/bin/bash
set -e

ACR_NAME="${ACR_NAME:-vocaregrcregistry}"
REGISTRY="${REGISTRY:-$ACR_NAME.azurecr.io}"
BASE_IMAGE_NAME="${BASE_IMAGE_NAME:-vocare-grc-base}"
APP_IMAGE_NAME="${APP_IMAGE_NAME:-vocare-grc-bot}"
BASE_IMAGE="$REGISTRY/$BASE_IMAGE_NAME:latest"
APP_IMAGE="$REGISTRY/$APP_IMAGE_NAME"
CONTAINER_APP="${CONTAINER_APP:-vocare-grc-bot}"
RESOURCE_GROUP="${RESOURCE_GROUP:-vocare-grc}"
PLATFORM="${PLATFORM:-linux/amd64}"
TAG="latest"
BUILD_BASE=false
USE_ACR_BUILD=false

usage() {
  cat <<EOF
Usage: ./deploy.sh [--base] [--acr-build] [tag]

Options:
  --base       Rebuild the dependency base image before the app image.
  --acr-build  Build images in Azure Container Registry instead of local Docker.
  tag          Optional app image tag. Defaults to "latest".

Environment overrides:
  RESOURCE_GROUP=$RESOURCE_GROUP
  CONTAINER_APP=$CONTAINER_APP
  ACR_NAME=$ACR_NAME
  PLATFORM=$PLATFORM
  MIN_REPLICAS   Set to 1 to keep a warm replica (use during app-store review).
EOF
}

for arg in "$@"; do
  case "$arg" in
    --base)
      BUILD_BASE=true
      ;;
    --acr-build)
      USE_ACR_BUILD=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      TAG="$arg"
      ;;
  esac
done

if [[ "$USE_ACR_BUILD" == true ]]; then
  if [[ "$BUILD_BASE" == true ]]; then
    echo "==> Building base image in ACR: $BASE_IMAGE_NAME:latest"
    az acr build \
      --registry "$ACR_NAME" \
      --image "$BASE_IMAGE_NAME:latest" \
      --file Dockerfile.base \
      --platform "$PLATFORM" \
      .
  fi

  echo "==> Building app image in ACR: $APP_IMAGE_NAME:$TAG"
  az acr build \
    --registry "$ACR_NAME" \
    --image "$APP_IMAGE_NAME:$TAG" \
    --file Dockerfile \
    --platform "$PLATFORM" \
    .
else
  az acr login --name "$ACR_NAME"

  if [[ "$BUILD_BASE" == true ]]; then
    echo "==> Building base image locally: $BASE_IMAGE"
    docker build --platform "$PLATFORM" -f Dockerfile.base -t "$BASE_IMAGE" .
    docker push "$BASE_IMAGE"
    echo "==> Base image pushed"
  fi

  echo "==> Building app image locally: $APP_IMAGE:$TAG"
  docker build --platform "$PLATFORM" -t "$APP_IMAGE:$TAG" .

  echo "==> Pushing $APP_IMAGE:$TAG"
  docker push "$APP_IMAGE:$TAG"
fi

echo "==> Deploying to Azure Container Apps"
# Deploy by digest, not by tag. Container Apps only rolls a new revision when the
# image *reference string* changes — updating with a reused tag like ":latest"
# leaves the old revision running the old image even though ACR has the new build.
# Pinning the just-pushed digest forces a fresh revision that pulls the new image.
DIGEST="$(az acr repository show \
  --name "$ACR_NAME" \
  --image "$APP_IMAGE_NAME:$TAG" \
  --query digest -o tsv)"

if [[ -z "$DIGEST" ]]; then
  echo "ERROR: could not resolve digest for $APP_IMAGE_NAME:$TAG" >&2
  exit 1
fi

echo "==> Deploying $APP_IMAGE@$DIGEST"
az containerapp update \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --image "$APP_IMAGE@$DIGEST"

# Scaling was previously only ever set imperatively, so nothing in the repo
# restored it after the app was scaled to zero for cost. MIN_REPLICAS makes it
# reproducible: set MIN_REPLICAS=1 while an app-store review is in flight so a
# cold start can never greet a reviewer, and put it back to 0 afterwards.
if [[ -n "${MIN_REPLICAS:-}" ]]; then
  echo "==> Pinning min-replicas=$MIN_REPLICAS (costs money above 0 - unset when review is done)"
  az containerapp update \
    --name "$CONTAINER_APP" \
    --resource-group "$RESOURCE_GROUP" \
    --min-replicas "$MIN_REPLICAS"
fi

echo "==> Done. Live at https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io"
echo "==> Health: curl -fsS https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io/healthz"
