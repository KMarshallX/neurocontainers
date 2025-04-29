#!/bin/bash
set -e

echo "[DEBUG] build/$APPLICATION"
cd build/$APPLICATION

IMAGENAME=$1

if [ -n "$GH_REGISTRY" ]; then
  DOCKER_REGISTRY=$GH_REGISTRY
else
  DOCKER_REGISTRY=$(echo "$GITHUB_REPOSITORY" | awk -F / '{print $1}')
fi
REGISTRY=$(echo ghcr.io/$DOCKER_REGISTRY | tr '[A-Z]' '[a-z]')
IMAGEID="$REGISTRY/$IMAGENAME"
echo "[DEBUG] IMAGENAME: $IMAGENAME"
echo "[DEBUG] REGISTRY: $REGISTRY"
echo "[DEBUG] IMAGEID: $IMAGEID"

echo "[DEBUG] Pulling $IMAGEID"
{
  docker pull $IMAGEID &&
    ROOTFS_CACHE=$(docker inspect --format='{{.RootFS}}' $IMAGEID)
} || echo "$IMAGEID not found. Resuming build..."

echo "[DEBUG] Docker build ..."
time docker build . --file ${IMAGENAME}.Dockerfile --tag $IMAGEID:$SHORT_SHA --cache-from $IMAGEID --label "GITHUB_REPOSITORY=$GITHUB_REPOSITORY" --label "GITHUB_SHA=$GITHUB_SHA"

echo "[DEBUG] # Get image RootFS to check for changes ..."
ROOTFS_NEW=$(docker inspect --format='{{.RootFS}}' $IMAGEID:$SHORT_SHA)

# Tag and Push if new image RootFS differs from cached image
if [ "$ROOTFS_NEW" = "$ROOTFS_CACHE" ]; then
  echo "[DEBUG] Skipping push to registry. No changes found"
else
  echo "[DEBUG] Changes found"
fi

if [ "$GITHUB_REF" == "refs/heads/main" ]; then
  if [ -n "$GH_REGISTRY" ]; then
    echo "[DEBUG] Pushing to GitHub Registry $GH_REGISTRY"
    # Push to GH Packages
    docker tag $IMAGEID:$SHORT_SHA $IMAGEID:$BUILDDATE
    docker tag $IMAGEID:$SHORT_SHA $IMAGEID:latest
    time docker push $IMAGEID:$BUILDDATE
    echo "[DEBUG] done Pushing to GitHub Registry!"
    docker push $IMAGEID:latest
  else
    echo "[DEBUG] Skipping push to GitHub Registry. secrets.GH_REGISTRY not found"
  fi
  # Push to Dockerhub
  if [ -n "$DOCKERHUB_ORG" ]; then
    echo "[DEBUG] Pushing to Dockerhub Registry $DOCKERHUB_ORG"
    docker tag $IMAGEID:$SHORT_SHA $DOCKERHUB_ORG/$IMAGENAME:$BUILDDATE
    docker tag $IMAGEID:$SHORT_SHA $DOCKERHUB_ORG/$IMAGENAME:latest
    time docker push $DOCKERHUB_ORG/${IMAGENAME}:${BUILDDATE}
    echo "[DEBUG] done Pushing to Dockerhub Registry!"
    docker push $DOCKERHUB_ORG/$IMAGENAME:latest
  else
    echo "[DEBUG] Skipping push to Dockerhub Registry. secrets.DOCKERHUB_ORG not found"
  fi
else
  echo "[DEBUG] Skipping push to registry. Not on main branch"
fi







