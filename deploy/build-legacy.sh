#!/bin/sh
# Legacy Compose cannot pass BuildKit secrets. Build via the container's Docker CLI.
set -eu
. /workspace/deploy/compose-common.sh
control_file="/workspace/$1"
shift
pull=false
no_cache=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --pull) pull=true ;;
    --no-cache) no_cache=true ;;
    web|tools|migrate) ;;
    *) printf 'Unsupported legacy build option. Use compose build [--pull] [--no-cache] [web|tools|migrate].\n' >&2; exit 2 ;;
  esac
  shift
done
image=$(deployment_value "$control_file" GEV_INTRANET_IMAGE)
fingerprint=$(deployment_value "$control_file" GEV_CA_FINGERPRINT)
network=$(deployment_value "$control_file" GEV_BUILD_NETWORK)
ca=$(deployment_value "$control_file" GEV_BUILD_CA)
if [ -z "$image" ] || [ -z "$fingerprint" ] || [ -z "$network" ] || [ ! -f "/workspace/$network" ] || [ -z "$ca" ] || [ ! -f "/workspace/$ca" ]; then
  printf 'Missing build configuration; run bash deploy/deploy.sh --init for this environment.\n' >&2
  exit 1
fi
set -- build --file /workspace/deploy/Dockerfile --tag "$image" \
  --build-arg "GEV_CA_FINGERPRINT=$fingerprint" \
  --secret "id=build_network,src=/workspace/$network" \
  --secret "id=corporate_ca,src=/workspace/$ca" \
  --add-host host.docker.internal:host-gateway
if [ "$pull" = true ]; then set -- "$@" --pull; fi
if [ "$no_cache" = true ]; then set -- "$@" --no-cache; fi
DOCKER_BUILDKIT=1 docker "$@" /workspace
