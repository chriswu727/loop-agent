#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
container="loop-enforcement-acceptance-${GITHUB_RUN_ID:-local}-$$"
volume="${container}-data"
namespace="${LOOP_ACCEPTANCE_NAMESPACE:-loop:acceptance:${GITHUB_RUN_ID:-local}:$$}"

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}

wait_for_redis() {
  for _ in {1..30}; do
    if [[ "$(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null || true)" == "healthy" ]]; then
      return 0
    fi
    sleep 1
  done
  docker logs "$container"
  return 1
}

run_acceptance() {
  (
    cd "$root/apps/api"
    uv run --frozen --extra dev python scripts/enforcement_acceptance.py "$@"
  )
}

trap cleanup EXIT
docker volume create "$volume" >/dev/null
docker run -d \
  --name "$container" \
  --mount "source=$volume,target=/data" \
  --publish 127.0.0.1::6379 \
  --health-cmd 'redis-cli ping' \
  --health-interval 1s \
  --health-timeout 1s \
  --health-retries 20 \
  redis:7-alpine \
  redis-server --appendonly yes --appendfsync always >/dev/null
wait_for_redis

endpoint="$(docker port "$container" 6379/tcp)"
redis_port="${endpoint##*:}"
redis_url="redis://127.0.0.1:${redis_port}/15"

run_acceptance exercise --redis-url "$redis_url" --namespace "$namespace"
docker stop "$container" >/dev/null
run_acceptance verify-unavailable --redis-url "$redis_url" --namespace "$namespace"
docker start "$container" >/dev/null
wait_for_redis
run_acceptance verify-recovery --redis-url "$redis_url" --namespace "$namespace"
