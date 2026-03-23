#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

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
