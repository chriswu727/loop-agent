#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
api_port="${API_PORT:-8000}"
web_port="${WEB_PORT:-3000}"
next_env="$root/apps/web/next-env.d.ts"
next_env_backup="$root/apps/web/.next/next-env.d.ts.loop-backup"
api_pid=""

cleanup() {
  [[ -n "$api_pid" ]] && kill "$api_pid" 2>/dev/null || true
  [[ -n "$api_pid" ]] && wait "$api_pid" 2>/dev/null || true
  if [[ -f "$next_env_backup" ]]; then
    cp "$next_env_backup" "$next_env"
    rm -f "$next_env_backup"
  fi
}
trap cleanup EXIT INT TERM

[[ -x "$root/apps/api/.venv/bin/python" ]] || {
  echo "dev: Python environment missing; run 'make setup' first." >&2
  exit 1
}
if command -v pnpm >/dev/null 2>&1; then
  pnpm_cmd=(pnpm)
elif command -v corepack >/dev/null 2>&1; then
  pnpm_cmd=(corepack pnpm)
else
  echo "dev: pnpm 11+ is required." >&2
  exit 1
fi

token="${LOOP_DEV_TOKEN:-$("$root/apps/api/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')}"
mkdir -p "$(dirname "$next_env_backup")"
cp "$next_env" "$next_env_backup"

(
  cd "$root/apps/api"
  API_TOKEN="$token" .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port "$api_port"
) &
api_pid=$!

cd "$root"
LOOP_API_TOKEN="$token" \
API_INTERNAL_URL="http://127.0.0.1:$api_port" \
"${pnpm_cmd[@]}" --filter web exec next dev --port "$web_port"
