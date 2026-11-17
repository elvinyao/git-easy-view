#!/usr/bin/env bash
# Run one command in a clean Python container with the source mounted.
# Deployment and daily operation live in ./gev.
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="python:3.13-bookworm"
TTY_FLAG=""
if [[ -t 0 && -t 1 ]]; then
  TTY_FLAG="-t"
fi

# Former entry points, kept so existing habits and scripts continue to work.
case "${1:-}" in
  deploy) shift; exec bash "${PROJECT_ROOT}/gev" up "$@" ;;
  compose) shift; exec bash "${PROJECT_ROOT}/gev" compose "$@" ;;
esac

exec docker run --rm -i ${TTY_FLAG:+-t} --init \
  -v "${PROJECT_ROOT}:/workspace" \
  -w /workspace \
  "${IMAGE}" \
  "$@"
