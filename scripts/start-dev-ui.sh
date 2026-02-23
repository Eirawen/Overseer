#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="${OVERSEER_REPO_ROOT:-$ROOT_DIR}"

API_HOST="${OVERSEER_API_HOST:-127.0.0.1}"
API_PORT="${OVERSEER_API_PORT:-8765}"
UI_HOST="${OVERSEER_UI_HOST:-127.0.0.1}"
UI_PORT="${OVERSEER_UI_PORT:-5173}"
EXEC_BACKEND="${OVERSEER_EXECUTION_BACKEND:-local}"

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to run the UI" >&2
  exit 1
fi

if command -v overseer >/dev/null 2>&1; then
  OVERSEER_CMD=(overseer)
else
  OVERSEER_CMD=(python -m overseer)
fi

cleanup() {
  if [[ -n "${API_PID:-}" ]] && kill -0 "$API_PID" >/dev/null 2>&1; then
    kill "$API_PID" >/dev/null 2>&1 || true
    wait "$API_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "Starting Overseer local dev stack"
echo "repo_root=$REPO_ROOT"
echo "api=http://$API_HOST:$API_PORT"
echo "ui=http://$UI_HOST:$UI_PORT"
echo "execution_backend=$EXEC_BACKEND"

mkdir -p "$REPO_ROOT/codex"

(
  cd "$ROOT_DIR"
  export OVERSEER_EXECUTION_BACKEND="$EXEC_BACKEND"
  "${OVERSEER_CMD[@]}" --repo-root "$REPO_ROOT" init >/dev/null
  "${OVERSEER_CMD[@]}" --repo-root "$REPO_ROOT" serve --host "$API_HOST" --port "$API_PORT"
) &
API_PID=$!

# Give the daemon a brief head start so the UI can connect cleanly.
sleep 1
if ! kill -0 "$API_PID" >/dev/null 2>&1; then
  echo "Overseer API failed to start" >&2
  exit 1
fi

cd "$ROOT_DIR/ui"
if [[ ! -d node_modules ]]; then
  npm install
fi

export VITE_API_ROOT="http://$API_HOST:$API_PORT"
echo "UI started. Open http://$UI_HOST:$UI_PORT (API Root defaults to $VITE_API_ROOT)."
npm run dev -- --host "$UI_HOST" --port "$UI_PORT"
