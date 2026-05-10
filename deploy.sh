#!/bin/bash
set -e

REGISTRY="vocaregrcregistry.azurecr.io"
BASE_IMAGE="$REGISTRY/vocare-grc-base:latest"
APP_IMAGE="$REGISTRY/vocare-grc-bot"
TAG="latest"
for arg in "$@"; do
  [[ "$arg" != "--base" ]] && TAG="$arg"
done

az acr login --name vocaregrcregistry

# Only rebuild base if --base flag is passed
if [[ "$*" == *"--base"* ]]; then
  echo "==> Building base image (deps only)"
  TMPDIR=$(mktemp -d)
  cp Dockerfile.base "$TMPDIR/Dockerfile"
  cp requirements.docker.txt "$TMPDIR/"
  cp -r wheelhouse-linux/ "$TMPDIR/wheelhouse-linux/"
  docker build -t "$BASE_IMAGE" "$TMPDIR"
  rm -rf "$TMPDIR"
  docker push "$BASE_IMAGE"
  echo "==> Base image pushed"
fi

echo "==> Building app image $APP_IMAGE:$TAG"
docker build -t "$APP_IMAGE:$TAG" .

echo "==> Pushing $APP_IMAGE:$TAG"
docker push "$APP_IMAGE:$TAG"

echo "==> Deploying to Azure Container Apps"
az containerapp update \
  --name vocare-grc-bot \
  --resource-group vocare-grc \
  --image "$APP_IMAGE:$TAG"

echo "==> Done. Live at https://vocare-grc-bot.yellowtree-d62e92d2.australiaeast.azurecontainerapps.io"
