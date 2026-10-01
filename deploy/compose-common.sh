#!/bin/sh
# Shared by container-side deployment helpers; never sources configuration files.

compose_detect() {
  if compose_version=$(docker compose version --short 2>/dev/null); then
    compose_command=plugin
  elif compose_version=$(docker-compose version --short 2>/dev/null); then
    compose_command=standalone
  else
    printf 'Compose unavailable in the Docker CLI image: install docker compose or docker-compose >= 1.29.2.\n' >&2
    return 1
  fi
  compose_mode=$(printf '%s\n' "$compose_version" | awk '
    { sub(/^v/, ""); split($0, v, ".") }
    /^[0-9]+\.[0-9]+\.[0-9]+/ {
      if (v[1]+0 > 2 || (v[1]+0 == 2 && v[2]+0 >= 30)) print "modern";
      else if (v[1]+0 == 2 || (v[1]+0 == 1 && (v[2]+0 > 29 || (v[2]+0 == 29 && v[3]+0 >= 2)))) print "legacy";
    }')
  if [ -z "$compose_mode" ]; then
    printf 'Unsupported Compose version; use docker-compose >= 1.29.2 or Docker Compose v2.\n' >&2
    return 1
  fi
}

compose_cli() {
  if [ "$compose_command" = plugin ]; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

# Caller supplies compose(), including its manifest, environment and project.
compose_start() {
  health_timeout=$1
  health_service=$2
  shift 2
  if [ "$compose_mode" = modern ]; then
    compose up "$@" --wait --wait-timeout "$health_timeout" "$health_service"
    return
  fi
  # V1 recreation reads obsolete ContainerConfig image metadata (which can
  # also be null with modern image stores). Replace only this container;
  # named volumes are retained. Deployment has already stopped writers and
  # completed migration before reaching web startup. Never pass -v here.
  case "$compose_version" in
    1.*|v1.*) compose rm -f -s "$health_service" || return $? ;;
  esac
  compose up "$@" "$health_service" || return $?
  health_deadline=$(( $(date +%s) + health_timeout ))
  while :; do
    health_ids=$(compose ps -q "$health_service") || return $?
    if [ -z "$health_ids" ]; then
      printf 'No container found while waiting for %s health.\n' "$health_service" >&2
      return 1
    fi
    all_healthy=true
    for health_id in $health_ids; do
      health_state=$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$health_id") || return $?
      case "$health_state" in
        'running healthy') ;;
        'running starting'|'created '*|'restarting '*) all_healthy=false ;;
        *) printf 'Service %s failed its health check (%s).\n' "$health_service" "$health_state" >&2; return 1 ;;
      esac
    done
    if [ "$all_healthy" = true ]; then return 0; fi
    if [ "$(date +%s)" -ge "$health_deadline" ]; then
      printf 'Timed out waiting for %s to become healthy (%s seconds).\n' "$health_service" "$health_timeout" >&2
      return 1
    fi
    sleep 1
  done
}

# Control files contain literal KEY=value data produced by setup.py, not shell.
deployment_value() {
  awk -v wanted="$2" 'index($0, wanted "=") == 1 { print substr($0, length(wanted) + 2); exit }' "$1"
}
