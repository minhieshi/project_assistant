#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
  if [[ -n "${API_PID:-}" ]]; then kill "$API_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

cd "$ROOT"
project-assistant-api &
API_PID=$!

# The Next.js server only needs the local backend URL/token. Explicitly remove
# Portkey credentials from the frontend process even though non-NEXT_PUBLIC vars
# would not normally be bundled into browser JavaScript.
cd "$ROOT/web"
env \
  -u PORTKEY_API_KEY \
  -u PORTKEY_CHAT_VIRTUAL_KEY \
  -u PORTKEY_EMBEDDING_VIRTUAL_KEY \
  -u PORTKEY_CHAT_CONFIG_ID \
  -u PORTKEY_EMBEDDING_CONFIG_ID \
  -u PORTKEY_EXTRA_HEADERS_JSON \
  npm run dev
