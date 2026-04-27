#!/usr/bin/env bash
# ai-assistant-app — start the dev server and open the app in a browser.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$PROJECT_ROOT/apps/ai-assistant-app"
PORT=5177

if [ ! -d "$APP_DIR/node_modules" ]; then
  echo "→ Installing npm dependencies (first run)..."
  npm install --prefix "$APP_DIR" --silent
fi

echo "→ Starting ai-assistant-app dev server on :$PORT..."
npm --prefix "$APP_DIR" run dev &
DEV_PID=$!

echo "→ Waiting for server to be ready..."
for _ in $(seq 1 60); do
  if curl -s "http://localhost:$PORT" > /dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

echo "✓ ai-assistant-app running at http://localhost:$PORT"
if command -v open &>/dev/null; then open "http://localhost:$PORT"
elif command -v xdg-open &>/dev/null; then xdg-open "http://localhost:$PORT"
fi

echo "Press Ctrl+C to stop."
cleanup() { kill "$DEV_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
wait "$DEV_PID"
