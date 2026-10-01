#!/bin/sh
# Docker-only helper. Keep the same project directory for existing named volumes.
set -eu
: "${GEV_PROJECT_ROOT:?Use bash .agent/run.sh intranet-compose}"
env_file=runtime/local.env
if [ "${1:-}" = "--env-file" ]; then
  env_file=$2
  shift 2
fi
control_file=${env_file%.env}.deploy.env
if [ ! -f "/workspace/$control_file" ]; then
  printf 'Run deploy-intranet --init for this environment first.\n' >&2
  exit 1
fi
exec docker compose --project-directory "$GEV_PROJECT_ROOT" \
  -f "$GEV_PROJECT_ROOT/deploy/compose.yaml" \
  --env-file "/workspace/$env_file" --env-file "/workspace/$control_file" "$@"
