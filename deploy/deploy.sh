#!/usr/bin/env bash
# Standalone Docker-only launcher; no Python or application code runs on the host.
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CLI_IMAGE="${GEV_DOCKER_CLI_IMAGE:-docker:28-cli}"
TTY_FLAG=""
if [[ -t 0 && -t 1 ]]; then
  TTY_FLAG="-t"
fi

run_compose_container() {
  docker run --rm -i ${TTY_FLAG:+-t} --init \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "${PROJECT_ROOT}:/workspace" \
    -v "${PROJECT_ROOT}:${PROJECT_ROOT}" \
    -e "GEV_PROJECT_ROOT=${PROJECT_ROOT}" \
    --entrypoint '' -w /workspace "${CLI_IMAGE}" "$@"
}

if [[ "${1:-}" == "compose" ]]; then
  shift
  run_compose_container sh deploy/compose.sh "$@"
  exit $?
fi

docker run --rm -i ${TTY_FLAG:+-t} --init \
  -v "${PROJECT_ROOT}:/workspace" \
  -w /workspace python:3.13-bookworm \
  python deploy/setup.py "$@"

# Init/help need only the Python preparation container, not the Docker CLI image.
for argument in "$@"; do
  case "$argument" in --init|-h|--help) exit 0 ;; esac
done

run_compose_container sh deploy/internal-deploy.sh "$@"
