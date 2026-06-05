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
az containerapp update \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --image "$APP_IMAGE:$TAG"

echo "==> Done. Live at https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io"
