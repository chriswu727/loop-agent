#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
api_port="${LOOP_DEMO_API_PORT:-8000}"
web_port="${LOOP_DEMO_WEB_PORT:-3000}"
demo_dir="$root/.demo"
api_log="$demo_dir/api.log"
web_log="$demo_dir/web.log"
demo_db=""
next_env="$root/apps/web/next-env.d.ts"
next_env_backup="$demo_dir/next-env.d.ts"
api_pid=""
web_pid=""

fail() {
  echo "demo: $*" >&2
  exit 1
}

# Invoked indirectly by the signal trap below.
# shellcheck disable=SC2329
cleanup() {
  [[ -n "$web_pid" ]] && kill "$web_pid" 2>/dev/null || true
  [[ -n "$api_pid" ]] && kill "$api_pid" 2>/dev/null || true
  [[ -n "$web_pid" ]] && wait "$web_pid" 2>/dev/null || true
  [[ -n "$api_pid" ]] && wait "$api_pid" 2>/dev/null || true
  [[ -n "$demo_db" && -f "$demo_db" ]] && rm -f "$demo_db"
  if [[ -f "$next_env_backup" ]]; then
    cp "$next_env_backup" "$next_env"
    rm -f "$next_env_backup"
  fi
}
trap cleanup EXIT INT TERM

port_available() {
  "$root/apps/api/.venv/bin/python" - "$1" <<'PY'
import socket
import sys

with socket.socket() as sock:
    sock.settimeout(0.2)
    raise SystemExit(1 if sock.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 0)
PY
}

wait_for_url() {
  local url="$1"
  local pid="$2"
  local log="$3"
  local attempts=0
  until "$root/apps/api/.venv/bin/python" - "$url" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

with urllib.request.urlopen(sys.argv[1], timeout=1) as response:
    raise SystemExit(0 if response.status < 500 else 1)
PY
  do
    attempts=$((attempts + 1))
    if ! kill -0 "$pid" 2>/dev/null; then
      tail -80 "$log" >&2 || true
      fail "a demo process exited before becoming ready"
    fi
    if (( attempts >= 120 )); then
      tail -80 "$log" >&2 || true
      fail "timed out waiting for $url"
    fi
    sleep 0.25
  done
}

find_pnpm() {
  if command -v pnpm >/dev/null 2>&1; then
    printf '%s\n' "pnpm"
  elif command -v corepack >/dev/null 2>&1; then
    printf '%s\n' "corepack pnpm"
  else
    fail "pnpm 11+ is required. Run 'make setup' after installing pnpm."
  fi
}

cd "$root"
if [[ "${LOOP_DEMO_SKIP_INSTALL:-0}" != "1" ]]; then
  bash scripts/setup.sh
fi

[[ -x apps/api/.venv/bin/python ]] || fail "Python environment missing; run 'make setup'."
read -r -a pnpm_cmd <<<"$(find_pnpm)"
port_available "$api_port" || fail "API port $api_port is already in use (set LOOP_DEMO_API_PORT)."
port_available "$web_port" || fail "Web port $web_port is already in use (set LOOP_DEMO_WEB_PORT)."

mkdir -p "$demo_dir/workspaces" "$demo_dir/memory"
demo_db="$(mktemp "$demo_dir/loop.XXXXXX.db")"
cp "$next_env" "$next_env_backup"
: >"$api_log"
: >"$web_log"
demo_token="$(apps/api/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '%s' "$demo_token" >"$demo_dir/token"
chmod 600 "$demo_dir/token"

(
  cd apps/api
  DATABASE_URL="sqlite+aiosqlite:///$demo_db" .venv/bin/alembic upgrade head
) >>"$api_log" 2>&1

(
  cd apps/api
  API_TOKEN="$demo_token" \
  CORS_ORIGINS="http://localhost:$web_port" \
  DEMO_MODE=1 \
  LLM_DEFAULT_PROVIDER=mock \
  EXECUTION_MODE=inline \
  CACHE_BACKEND=memory \
  AGENT_SANDBOX=inline \
  DATABASE_URL="sqlite+aiosqlite:///$demo_db" \
  AGENT_WORKSPACES_ROOT="$demo_dir/workspaces" \
  AGENT_MEMORY_ROOT="$demo_dir/memory" \
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$api_port"
) >"$api_log" 2>&1 &
api_pid=$!

LOOP_API_TOKEN="$demo_token" \
API_INTERNAL_URL="http://127.0.0.1:$api_port" \
"${pnpm_cmd[@]}" --filter web exec next dev --port "$web_port" >"$web_log" 2>&1 &
web_pid=$!

wait_for_url "http://127.0.0.1:$api_port/healthz" "$api_pid" "$api_log"
wait_for_url "http://localhost:$web_port/api/health" "$web_pid" "$web_log"

url="http://localhost:$web_port"
echo
echo "Loop demo is ready: $url"
echo "Choose the Fibonacci example, run it, then replay its Receipt."
echo "Logs: $api_log and $web_log"
echo "Press Ctrl+C to stop."

if [[ "${LOOP_DEMO_OPEN:-1}" == "1" && -z "${CI:-}" ]]; then
  if command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 || true
  fi
fi

while kill -0 "$api_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do
  sleep 1
done

tail -80 "$api_log" >&2 || true
tail -80 "$web_log" >&2 || true
fail "a demo process exited unexpectedly"
