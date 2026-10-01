#!/bin/sh
# Executed by the Docker runner, never directly on the host.
set -eu
: "${GEV_PROJECT_ROOT:?Use bash .agent/run.sh deploy-intranet}"
env_file=
production=false
build=true
pull=false
wait_timeout=180
while [ "$#" -gt 0 ]; do
  case "$1" in
    --init|-h|--help) exit 0 ;;
    --production) production=true; shift ;;
    --env-file) env_file=$2; shift 2 ;;
    --port|--project-name|--network-file|--ca-dir|--bind-address) shift 2 ;;
    --no-build) build=false; shift ;;
    --pull) pull=true; shift ;;
    --wait-timeout) wait_timeout=$2; shift 2 ;;
    *) printf 'Unsupported deployment option\n' >&2; exit 2 ;;
  esac
done
if [ -z "$env_file" ]; then
  if [ "$production" = true ]; then env_file=runtime/production.env; else env_file=runtime/local.env; fi
fi
compose() { sh deploy/compose.sh --env-file "$env_file" "$@"; }
phase="Docker availability"
failure() {
  status=$?
  if [ "$status" -ne 0 ]; then
    printf 'Deployment failed during %s. Configuration and volumes retained.\n' "$phase" >&2
    printf 'Inspect: bash .agent/run.sh intranet-compose --env-file %s logs --tail=100 web\n' "$env_file" >&2
  fi
}
trap failure EXIT
docker info --format '{{.ServerVersion}}'
phase="Compose validation"
compose config --quiet
if [ "$build" = true ]; then
  phase="proxy/CA-aware build"
  if [ "$pull" = true ]; then compose build --pull web; else compose build web; fi
fi
phase="CA and application configuration validation"
compose run --rm --no-deps -T tools python -c 'import os; from pathlib import Path; from app.config import Settings; Settings(); assert Path("/opt/gev-deploy/entrypoint.py").is_file() and Path(os.environ.get("SSL_CERT_FILE", "/missing")).is_file(), "Rebuild with deploy-intranet before using --no-build"; print("Settings and CA valid")'
phase="stop application writers"
compose stop web
phase="database migration"
compose run --rm --no-deps -T migrate
phase="application startup"
compose up -d --no-deps --no-build --wait --wait-timeout "$wait_timeout" web
compose ps
printf 'Deployment complete. Published address: '
compose port web 8000
if [ "$production" = true ]; then printf 'Use your HTTPS reverse proxy for browser login.\n'; fi
