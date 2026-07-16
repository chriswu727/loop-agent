#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
  echo "setup: $*" >&2
  exit 1
}

version_at_least() {
  "$1" - "$2" <<'PY'
import sys

required = tuple(int(part) for part in sys.argv[1].split("."))
raise SystemExit(0 if sys.version_info[:len(required)] >= required else 1)
PY
}

find_python() {
  local candidate
  for candidate in "${PYTHON:-}" python3.13 python3.12 python3; do
    [[ -n "$candidate" ]] || continue
    if command -v "$candidate" >/dev/null 2>&1 && version_at_least "$candidate" 3.12; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  fail "Python 3.12+ is required. Set PYTHON=/path/to/python3.12 and retry."
}

find_pnpm() {
  if command -v pnpm >/dev/null 2>&1; then
    printf '%s\n' "pnpm"
    return
  fi
  if command -v corepack >/dev/null 2>&1; then
    printf '%s\n' "corepack pnpm"
    return
  fi
  fail "pnpm 11+ is required. Install pnpm, or use a Node distribution with Corepack."
}

command -v node >/dev/null 2>&1 || fail "Node 22.13+ is required."
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 13) ? 0 : 1)' \
  || fail "Node 22.13+ is required; found $(node --version)."

python_bin="$(find_python)"
read -r -a pnpm_cmd <<<"$(find_pnpm)"
pnpm_version="$("${pnpm_cmd[@]}" --version)"
[[ "${pnpm_version%%.*}" -ge 11 ]] \
  || fail "pnpm 11+ is required; found $pnpm_version."

cd "$root"
echo "Installing JavaScript dependencies with ${pnpm_cmd[*]}..."
"${pnpm_cmd[@]}" install --frozen-lockfile

venv="$root/apps/api/.venv"
if [[ -x "$venv/bin/python" ]] && ! version_at_least "$venv/bin/python" 3.12; then
  rm -rf "$venv"
fi
if [[ ! -x "$venv/bin/python" ]]; then
  "$python_bin" -m venv "$venv"
fi

echo "Installing locked Python dependencies with $("$venv/bin/python" --version)..."
"$venv/bin/python" -m pip install --quiet uv==0.11.15
(
  cd "$root/apps/api"
  .venv/bin/uv sync --frozen --extra dev --extra office
)
