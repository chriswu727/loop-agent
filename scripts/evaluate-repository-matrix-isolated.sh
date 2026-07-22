#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="$root/apps/api/.venv/bin/python"
sandbox_image="${AGENT_SANDBOX_IMAGE:-loop-sandbox:latest}"
api_pid=""
state_dir=""
api_log=""

fail() {
  echo "isolated repository eval: $*" >&2
  exit 1
}

cleanup() {
  local status=$?
  if [[ -n "$api_pid" ]]; then
    kill "$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
  fi
  if (( status != 0 )) && [[ -f "$api_log" ]]; then
    tail -80 "$api_log" >&2 || true
  fi
  if [[ "$state_dir" == "$root/.loop-isolated-eval."* ]]; then
    rm -rf "$state_dir"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

has_flag() {
  local wanted="$1"
  shift
  local argument
  for argument in "$@"; do
    [[ "$argument" == "$wanted" || "$argument" == "$wanted="* ]] && return 0
  done
  return 1
}

wait_for_api() {
  local attempts=0
  until "$python" - "$1" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

with urllib.request.urlopen(sys.argv[1], timeout=1) as response:
    raise SystemExit(0 if response.status < 500 else 1)
PY
  do
    attempts=$((attempts + 1))
    if ! kill -0 "$api_pid" 2>/dev/null; then
      fail "API exited before becoming ready"
    fi
    (( attempts < 120 )) || fail "timed out waiting for the disposable API"
    sleep 0.25
  done
}

[[ -x "$python" ]] || fail "Python environment missing; run 'make setup' first"
has_flag --allow-model-spend "$@" || fail "pass --allow-model-spend to acknowledge provider cost"
has_flag --output "$@" || fail "pass --output so paid evaluation evidence is checkpointed"
docker info >/dev/null 2>&1 || fail "Docker is unavailable"

if ! docker image inspect "$sandbox_image" >/dev/null 2>&1; then
  docker build -f "$root/apps/api/sandbox.Dockerfile" -t "$sandbox_image" "$root"
fi

providers="$({
  cd "$root/apps/api"
  DEMO_MODE=false "$python" - <<'PY'
from app.core.config import settings
from app.core.llm.registry import configured_providers

print(",".join(configured_providers(settings.llm_default_provider)))
PY
})"
[[ -n "$providers" ]] || fail "no real or local model provider is configured in the environment"

state_dir="$(mktemp -d "$root/.loop-isolated-eval.XXXXXX")"
api_log="$state_dir/api.log"
mkdir -p "$state_dir/projects" "$state_dir/workspaces" "$state_dir/memory"
api_port="${LOOP_EVAL_API_PORT:-$("$python" - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)}"
token="$("$python" -c 'import secrets; print(secrets.token_urlsafe(32))')"

(
  cd "$root/apps/api"
  API_TOKEN="$token" \
  DATABASE_URL="sqlite+aiosqlite:///$state_dir/loop.db" \
  CACHE_BACKEND=memory \
  EXECUTION_MODE=inline \
  SCHEDULER_ENABLED=false \
  PROMETHEUS_ENABLED=false \
  AGENT_SANDBOX=required \
  AGENT_SANDBOX_BACKEND=docker \
  AGENT_SANDBOX_IMAGE="$sandbox_image" \
  LOOP_LOCAL_PROJECTS_ROOT="$state_dir/projects" \
  AGENT_WORKSPACES_ROOT="$state_dir/workspaces" \
  AGENT_MEMORY_ROOT="$state_dir/memory" \
  DEMO_MODE=false \
  "$python" -m uvicorn app.main:app --host 127.0.0.1 --port "$api_port"
) >"$api_log" 2>&1 &
api_pid=$!

wait_for_api "http://127.0.0.1:$api_port/healthz"
echo "isolated repository eval: providers=$providers sandbox=container"

cd "$root"
DEMO_MODE=false PYTHONPATH="$root/apps/api" \
  LOOP_API_TOKEN="$token" \
  "$python" "$root/apps/api/scripts/evaluate_repository_matrix.py" \
  "$@" \
  --base-url "http://127.0.0.1:$api_port" \
  --project-root "$state_dir/projects" \
  --modes full_loop \
  --require-isolation container
