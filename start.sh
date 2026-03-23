#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Check for updates
REPO="kacperkwapisz/poke-mail"
LOCAL_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
REMOTE_SHA=$(curl -sf "https://api.github.com/repos/${REPO}/commits/main" \
  | grep -m1 '"sha"' | cut -d'"' -f4 || echo "")

if [ -n "$REMOTE_SHA" ] && [ "$REMOTE_SHA" != "$LOCAL_SHA" ]; then
  echo "⚡ A newer version of poke-mail is available."
  echo "   Local:  ${LOCAL_SHA:0:7}"
  echo "   Remote: ${REMOTE_SHA:0:7}"
  echo "   Run 'git pull' to update."
  echo ""
fi

# Load .env if present
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

# Activate virtualenv
source .venv/bin/activate

: "${MCP_API_KEY:?MCP_API_KEY is not set — add it to .env or export it}"

# Start poke-mail server in background
echo "Starting poke-mail server..."
python src/server.py &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null" EXIT

# Wait for server to be ready
sleep 2

# Tunnel to Poke
echo "Starting tunnel to Poke..."
poke tunnel http://localhost:3000/mcp --name "poke-mail"
