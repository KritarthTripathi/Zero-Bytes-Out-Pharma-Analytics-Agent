#!/bin/sh
# Host script: records the exact Python package versions inside the app image.
mkdir -p data/environment
docker compose run --rm --no-deps app pip freeze 2>/dev/null > data/environment/requirements.lock
[ -s data/environment/requirements.lock ] && echo "wrote data/environment/requirements.lock ($(wc -l < data/environment/requirements.lock) packages)" || echo "FAILED: requirements.lock is empty"
