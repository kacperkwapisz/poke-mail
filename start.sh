#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# ── Check for updates ─────────────────────────────────────────────────────────
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

# ── One-time setup (skipped on subsequent runs) ───────────────────────────────

# 1. Python virtualenv + dependencies
if [ ! -d .venv ]; then
  echo "First run — setting up poke-mail..."
  echo ""
  echo "Creating Python virtualenv (.venv)..."
  python3 -m venv .venv
fi
source .venv/bin/activate

if ! python3 -c "import fastmcp" &>/dev/null 2>&1; then
  echo "Installing Python dependencies..."
  pip install -q -r requirements.txt
  echo "  ✓ Dependencies installed"
  echo ""
fi

# 2. config.yml — copy example if missing
if [ ! -f config.yml ]; then
  echo "Copying config.example.yml → config.yml..."
  cp config.example.yml config.yml
  echo "  ✓ config.yml created"
  echo ""
fi

# 3. Poke API key — read from 'poke login' credentials, inject into config.yml
#    The 'poke' npm package writes ~/.config/poke/credentials.json { "token": "..." }
#    after 'npx poke login'. We auto-read it so the user never has to copy/paste.
if grep -q 'your-api-key-here' config.yml 2>/dev/null; then
  POKE_CREDENTIALS_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/poke/credentials.json"
  POKE_TOKEN=""

  if [ -f "$POKE_CREDENTIALS_FILE" ]; then
    POKE_TOKEN=$(python3 -c "
import json
try:
    data = json.load(open('$POKE_CREDENTIALS_FILE'))
    print(data.get('token', ''))
except Exception:
    print('')
" 2>/dev/null || true)
  fi

  if [ -n "$POKE_TOKEN" ]; then
    echo "  ✓ Poke API key detected from 'poke login'"
    python3 - <<PYEOF
import re
with open('config.yml', 'r') as f:
    content = f.read()
new_content = re.sub(r'(poke_api_key:\s*)[^\n]+', r'\g<1>"${POKE_TOKEN}"', content)
with open('config.yml', 'w') as f:
    f.write(new_content)
PYEOF
    echo "  ✓ poke_api_key written to config.yml"
    echo ""
  else
    echo "  ⚠  poke_api_key not set in config.yml."
    echo "     Run 'npx poke login' then restart, or paste your key from poke.com/settings/advanced"
    echo ""
    printf "  Poke API key (leave blank to set manually later): "
    read -r POKE_TOKEN_INPUT
    POKE_TOKEN_INPUT=$(echo "$POKE_TOKEN_INPUT" | tr -d '[:space:]')
    if [ -n "$POKE_TOKEN_INPUT" ]; then
      python3 - <<PYEOF
import re
with open('config.yml', 'r') as f:
    content = f.read()
new_content = re.sub(r'(poke_api_key:\s*)[^\n]+', r'\g<1>"${POKE_TOKEN_INPUT}"', content)
with open('config.yml', 'w') as f:
    f.write(new_content)
PYEOF
      echo "  ✓ poke_api_key saved to config.yml"
    fi
    echo ""
  fi
fi

# 4. MCP_API_KEY — generate once and persist to .env
if [ ! -f .env ] || grep -q 'your-secret-key-here' .env 2>/dev/null || ! grep -q 'MCP_API_KEY=' .env 2>/dev/null; then
  RANDOM_KEY=$(python3 -c "
import secrets, string
alphabet = string.ascii_letters + string.digits
print(''.join(secrets.choice(alphabet) for _ in range(48)))
")
  if [ -f .env ]; then
    python3 - <<PYEOF
import re
with open('.env', 'r') as f:
    content = f.read()
new_content = re.sub(r'MCP_API_KEY=.*', 'MCP_API_KEY=${RANDOM_KEY}', content)
if 'MCP_API_KEY=' not in new_content:
    new_content += 'MCP_API_KEY=${RANDOM_KEY}\n'
with open('.env', 'w') as f:
    f.write(new_content)
PYEOF
  else
    echo "MCP_API_KEY=${RANDOM_KEY}" > .env
  fi
  echo "  ✓ MCP_API_KEY generated and saved to .env"
  echo ""
fi

# ── Load .env ─────────────────────────────────────────────────────────────────
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

: "${MCP_API_KEY:?MCP_API_KEY is not set — add it to .env or export it}"

# ── Start server + tunnel ─────────────────────────────────────────────────────
echo "Starting poke-mail server..."
python src/server.py &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null" EXIT

# Wait for server to be ready
sleep 2

# Tunnel to Poke
echo "Starting tunnel to Poke..."
poke tunnel http://localhost:3000/mcp --name "poke-mail"
