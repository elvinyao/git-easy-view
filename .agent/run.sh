#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="python:3.13-bookworm"
TTY_FLAG=""
if [[ -t 0 && -t 1 ]]; then
  TTY_FLAG="-t"
fi

# Compose itself runs in a CLI container. The absolute host path is also
# mounted so bind mounts and build contexts resolve correctly in Docker Desktop.
if [[ "${1:-}" == "compose" ]]; then
  shift
  exec docker run --rm -i ${TTY_FLAG:+-t} --init \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "${PROJECT_ROOT}:/workspace" \
    -v "${PROJECT_ROOT}:${PROJECT_ROOT}" \
    -w /workspace docker:28-cli \
    docker compose --project-directory "${PROJECT_ROOT}" \
      -f "${PROJECT_ROOT}/compose.yaml" "$@"
fi

exec docker run --rm -i ${TTY_FLAG:+-t} --init \
  -v "${PROJECT_ROOT}:/workspace" \
  -w /workspace \
  "${IMAGE}" \
  "$@"
