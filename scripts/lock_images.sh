#!/bin/sh
# Host script: records the exact image IDs used by this compose project.
mkdir -p data/environment
docker compose config --images | sort -u | while read img; do
  printf '%s %s\n' "$img" "$(docker image inspect -f '{{.Id}}' "$img" 2>/dev/null || echo not-built)"
done | tee data/environment/images.lock
