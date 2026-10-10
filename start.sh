#!/usr/bin/env bash
# Start Brand Guard, plus the iMessage agent when Spectrum credentials are set.
# Used by Replit's Run button and deployment (see .replit); works on any Linux/macOS box with Python 3.10+ and Node 20+.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8000}"

# On Replit the app has a public https address: messages link to it, and reverse image search can fetch the original
if [ -z "${PUBLIC_BASE_URL:-}" ] && [ -n "${REPLIT_DOMAINS:-}" ]; then
  export PUBLIC_BASE_URL="https://${REPLIT_DOMAINS%%,*}"
fi

if ! python -c "import fastapi, cv2, pillow_heif" 2>/dev/null; then
  pip install -q -r requirements.txt
fi

if [ -n "${SPECTRUM_PROJECT_ID:-}" ] && [ -n "${SPECTRUM_PROJECT_SECRET:-}" ]; then
  [ -d imessage-agent/node_modules ] || (cd imessage-agent && npm ci --no-audit --no-fund)
  # restart the agent if it ever exits; it only calls the API when a message arrives, so start order does not matter
  (cd imessage-agent && while true; do BRAND_GUARD_URL="http://127.0.0.1:$PORT" npm start || true; sleep 5; done) &
else
  echo "iMessage agent off: set SPECTRUM_PROJECT_ID and SPECTRUM_PROJECT_SECRET (Replit Secrets) to turn it on."
fi

exec python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
