#!/bin/sh
# Docker-only helper. Keep the same project directory for existing named volumes.
set -eu
: "${GEV_PROJECT_ROOT:?Use bash deploy/deploy.sh compose}"
. /workspace/deploy/compose-common.sh
env_file=runtime/local.env
if [ "${1:-}" = "--env-file" ]; then
  env_file=$2
  shift 2
fi
control_file=${env_file%.env}.deploy.env
if [ ! -f "/workspace/$control_file" ]; then
  printf 'Run bash deploy/deploy.sh --init for this environment first.\n' >&2
  exit 1
fi
compose_detect
if [ "$compose_mode" = legacy ]; then
  merged_file="${env_file%.env}.deploy/compose.env"
  if [ ! -f "/workspace/$merged_file" ]; then
    printf 'Run bash deploy/deploy.sh --init for this environment to prepare legacy Compose configuration.\n' >&2
    exit 1
  fi
  if [ "/workspace/$env_file" -nt "/workspace/$merged_file" ] || [ "/workspace/$control_file" -nt "/workspace/$merged_file" ]; then
    printf 'Deployment settings changed; run bash deploy/deploy.sh --init for this environment before using legacy Compose management.\n' >&2
    exit 1
  fi
  ca_dir=$(deployment_value "/workspace/$control_file" GEV_CA_DIR)
  network_file=$(deployment_value "/workspace/$control_file" GEV_RUNTIME_NETWORK)
  if [ -z "$ca_dir" ] || [ ! -d "/workspace/$ca_dir" ] || [ -z "$network_file" ] || [ ! -f "/workspace/$network_file" ]; then
    printf 'Missing CA directory or runtime network file; run bash deploy/deploy.sh --init for this environment.\n' >&2
    exit 1
  fi
  if [ "${1:-}" = build ]; then
    shift
    exec sh /workspace/deploy/build-legacy.sh "$control_file" "$@"
  fi
  # No build section is present: prevent an explicit rebuild request being ignored.
  if [ "${1:-}" = up ] || [ "${1:-}" = run ]; then
    for argument in "$@"; do
      if [ "$argument" = --build ]; then
        printf 'For a legacy Compose rebuild, use bash deploy/deploy.sh (or its compose build command) first.\n' >&2
        exit 2
      fi
    done
  fi
  compose_cli --project-directory "$GEV_PROJECT_ROOT" \
    -f "$GEV_PROJECT_ROOT/deploy/compose.legacy.yaml" --env-file "/workspace/$merged_file" "$@"
else
  compose_cli --project-directory "$GEV_PROJECT_ROOT" \
    -f "$GEV_PROJECT_ROOT/deploy/compose.yaml" \
    --env-file "/workspace/$env_file" --env-file "/workspace/$control_file" "$@"
fi
