#!/bin/sh
# Records the exact Python package versions inside the app image.
# Writes from INSIDE the container, so the ownership of data/environment on the host does not matter.
docker compose run --rm --no-deps app sh -c "mkdir -p data/environment && python -m pip freeze > data/environment/requirements.lock && wc -l data/environment/requirements.lock"
