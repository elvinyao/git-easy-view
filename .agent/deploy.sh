#!/bin/sh
# Executed in the Docker CLI container by .agent/run.sh, never on the host.
set -eu

: "${GEV_PROJECT_ROOT:?Use bash .agent/run.sh deploy; this script runs inside Docker}"
. /workspace/deploy/compose-common.sh

production=false
env_file=
build=true
pull=false
wait_timeout=180
migrate_sqlite=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --production) production=true; shift ;;
    --env-file) env_file=$2; shift 2 ;;
    --port|--project-name) shift 2 ;; # The Python configuration phase validated these.
    --no-build) build=false; shift ;;
    --migrate-sqlite) migrate_sqlite=true; shift ;;
    --pull) pull=true; shift ;;
    --wait-timeout) wait_timeout=$2; shift 2 ;;
    -h|--help) exit 0 ;; # Help was already displayed by the configuration phase.
    *) printf 'Unknown deployment option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
if [ -z "$env_file" ]; then
  if [ "$production" = true ]; then env_file=runtime/production.env; else env_file=runtime/local.env; fi
fi

compose() {
  compose_cli --project-directory "$GEV_PROJECT_ROOT" \
    -f "$GEV_PROJECT_ROOT/compose.yaml" --env-file "/workspace/$env_file" "$@"
}

phase="Docker availability"
report_failure() {
  status=$?
  if [ "$status" -ne 0 ]; then
    printf '\nDeployment failed during: %s (exit %s).\n' "$phase" "$status" >&2
    printf 'Configuration and persistent volumes were retained. Inspect with:\n' >&2
    printf 'bash .agent/run.sh compose --env-file %s ps -a\n' "$env_file" >&2
    printf 'bash .agent/run.sh compose --env-file %s logs --tail=100 web\n' "$env_file" >&2
    if [ "$phase" = "database migration" ]; then
      printf 'Application services remain stopped. Fix the migration or restore a verified backup before restarting.\n' >&2
    fi
  fi
}
trap report_failure EXIT
docker info --format '{{.ServerVersion}}'
phase="Compose availability"
compose_detect
phase="Compose configuration validation"
compose config --quiet
if [ "$build" = true ]; then
  phase="application image build"
  if [ "$pull" = true ]; then compose build --pull web; else compose build web; fi
fi
phase="application settings validation"
compose run --rm --no-deps -T tools python -c 'from app.config import Settings; Settings(); print("Application settings valid")'
phase="application shutdown before migration"
compose stop web worker dispatcher
if [ "$migrate_sqlite" = true ]; then
  phase="PostgreSQL to SQLite transfer"
  compose_start "$wait_timeout" postgres -d --no-build
  compose run --rm --no-deps -T tools python .agent/migrate-sqlite.py --env-file "$env_file"
fi
phase="database migration"
compose run --rm --no-deps -T migrate
phase="legacy service shutdown"
compose stop postgres redis
phase="application startup and health check"
compose_start "$wait_timeout" web -d --no-deps --no-build
phase="deployment status"
compose ps
printf '\nDeployment complete. Web loopback address: '
compose port web 8000
if [ "$production" = true ]; then
  printf 'Use your configured HTTPS reverse proxy for browser access.\n'
else
  printf 'Open that address with http:// in your browser.\n'
fi
printf 'No accounts or example data are created automatically. See docs/README.md.\n'
