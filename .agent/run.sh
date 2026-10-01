#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="python:3.13-bookworm"
CLI_IMAGE="${GEV_DOCKER_CLI_IMAGE:-docker:28-cli}"
TTY_FLAG=""
if [[ -t 0 && -t 1 ]]; then
  TTY_FLAG="-t"
fi

# Bootstrap and deployment orchestration both execute inside containers.
if [[ "${1:-}" == "deploy" ]]; then
  shift
  docker run --rm -i ${TTY_FLAG:+-t} --init \
    -v "${PROJECT_ROOT}:/workspace" \
    -w /workspace "${IMAGE}" \
    python .agent/setup-local.py "$@"
  exec docker run --rm -i ${TTY_FLAG:+-t} --init \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "${PROJECT_ROOT}:/workspace" \
    -v "${PROJECT_ROOT}:${PROJECT_ROOT}" \
    -e "GEV_PROJECT_ROOT=${PROJECT_ROOT}" \
    --entrypoint '' -w /workspace "${CLI_IMAGE}" \
    sh .agent/deploy.sh "$@"
fi

# Compose itself runs in a CLI container. The absolute host path is also
# mounted so bind mounts and build contexts resolve correctly in Docker Desktop.
if [[ "${1:-}" == "compose" ]]; then
  shift
  exec docker run --rm -i ${TTY_FLAG:+-t} --init \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "${PROJECT_ROOT}:/workspace" \
    -v "${PROJECT_ROOT}:${PROJECT_ROOT}" \
    -e "GEV_PROJECT_ROOT=${PROJECT_ROOT}" \
    --entrypoint '' -w /workspace "${CLI_IMAGE}" \
    sh .agent/compose.sh "$@"
fi

exec docker run --rm -i ${TTY_FLAG:+-t} --init \
  -v "${PROJECT_ROOT}:/workspace" \
  -w /workspace \
  "${IMAGE}" \
  "$@"
