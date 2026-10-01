#!/bin/sh
# Executed only in the Docker CLI container by .agent/run.sh.
set -eu
: "${GEV_PROJECT_ROOT:?Use bash .agent/run.sh compose}"
. /workspace/deploy/compose-common.sh
compose_detect
compose_cli --project-directory "$GEV_PROJECT_ROOT" -f "$GEV_PROJECT_ROOT/compose.yaml" "$@"
