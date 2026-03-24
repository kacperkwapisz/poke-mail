#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# ── OTA update ────────────────────────────────────────────────────────────────
# Ensure git is available. If not, attempt a quiet install via brew (macOS) or
# apt-get (Linux). If the install fails or the OS is unsupported, skip OTA
# silently — never abort the script over a missing update tool.
if ! command -v git &>/dev/null; then
  echo "  ℹ  git not found — attempting to install..."
  _git_installed=0
  if command -v brew &>/dev/null; then
    brew install git --quiet &>/dev/null && _git_installed=1 || true
  elif command -v apt-get &>/dev/null; then
    sudo apt-get install -y -qq git &>/dev/null && _git_installed=1 || true
  fi
  if [ "$_git_installed" -eq 1 ] && command -v git &>/dev/null; then
    echo "  ✓ git installed"
  else
    echo "  ⚠  Could not install git — skipping update check."
  fi
fi

# Pull latest changes from remote with a short timeout so we don't hang offline.
# If requirements.txt changed, reinstall dependencies afterwards.
if command -v git &>/dev/null && git rev-parse --is-inside-work-tree &>/dev/null 2>&1; then
  echo "Checking for updates..."
  REQS_BEFORE=$(git rev-parse HEAD:requirements.txt 2>/dev/null || echo "")

  # git fetch with a 5-second timeout; silently skip if offline or unreachable
  if git fetch --depth=1 origin --quiet --no-tags \
       -c core.sshCommand="ssh -o ConnectTimeout=5" \
       -c http.lowSpeedLimit=1 -c http.lowSpeedTime=5 \
       2>/dev/null; then
    LOCAL=$(git rev-parse HEAD)
    REMOTE=$(git rev-parse FETCH_HEAD 2>/dev/null || echo "")

    if [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
      echo "  ↳ Update found (${LOCAL:0:7} → ${REMOTE:0:7}), applying..."
      git merge --ff-only FETCH_HEAD --quiet
      echo "  ✓ Updated to $(git rev-parse --short HEAD)"

      # Re-check requirements.txt after update
      REQS_AFTER=$(git rev-parse HEAD:requirements.txt 2>/dev/null || echo "")
      if [ "$REQS_BEFORE" != "$REQS_AFTER" ]; then
        echo "  ↳ requirements.txt changed — reinstalling dependencies..."
        # Activate venv if it already exists so pip targets the right env
        [ -d .venv ] && source .venv/bin/activate
        pip install -q -r requirements.txt
        echo "  ✓ Dependencies updated"
      fi
      echo ""
    else
      echo "  ✓ Already up to date"
      echo ""
    fi
  else
    echo "  ℹ  Could not reach remote — continuing with local version."
    echo ""
  fi
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
import json, sys
try:
    data = json.load(open('$POKE_CREDENTIALS_FILE'))
    print(data.get('token', ''))
except Exception:
    print('')
" 2>/dev/null || true)
  fi

  if [ -n "$POKE_TOKEN" ]; then
    echo "  ✓ Poke API key detected from 'poke login'"
    # Pass token via env var to avoid shell-interpolation injection in Python source.
    # json.dumps handles quoting/escaping so the result is valid YAML.
    POKE_TOKEN="$POKE_TOKEN" python3 - <<'PYEOF'
import os, re, json
token = os.environ['POKE_TOKEN']
with open('config.yml', 'r') as f:
    content = f.read()
pattern = r'(?m)^([ \t]*poke_api_key:[ \t*])[^\n]+'
new_content, n = re.subn(pattern, lambda m: m.group(1) + json.dumps(token), content)
if n == 0:
    print('  ⚠  Warning: poke_api_key key not found in config.yml — update it manually.')
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
      POKE_TOKEN="$POKE_TOKEN_INPUT" python3 - <<'PYEOF'
import os, re, json
token = os.environ['POKE_TOKEN']
with open('config.yml', 'r') as f:
    content = f.read()
pattern = r'(?m)^([ \t]*poke_api_key:[ \t*])[^\n]+'
new_content, n = re.subn(pattern, lambda m: m.group(1) + json.dumps(token), content)
if n == 0:
    print('  ⚠  Warning: poke_api_key key not found in config.yml — update it manually.')
with open('config.yml', 'w') as f:
    f.write(new_content)
PYEOF
      echo "  ✓ poke_api_key saved to config.yml"
    fi
    echo ""
  fi
fi

# 4. MCP_API_KEY — generate once and persist to .env
#    Regenerate when: .env is missing, contains the placeholder, or has an
#    empty assignment (MCP_API_KEY=) which would still fail at the :? check.
#    Anchored to non-commented, line-start assignments only.
if [ ! -f .env ] \
   || grep -Eq '^[[:space:]]*MCP_API_KEY=your-secret-key-here' .env 2>/dev/null \
   || ! grep -Eq '^[[:space:]]*MCP_API_KEY=.+' .env 2>/dev/null; then
  RANDOM_KEY=$(python3 -c "
import secrets, string
alphabet = string.ascii_letters + string.digits
print(''.join(secrets.choice(alphabet) for _ in range(48)))
")
  if [ -f .env ]; then
    # Anchor to line-start with MULTILINE so only the actual assignment is updated.
    RANDOM_KEY="$RANDOM_KEY" python3 - <<'PYEOF'
import os, re
new_key = os.environ['RANDOM_KEY']
with open('.env', 'r') as f:
    content = f.read()
new_content, n = re.subn(r'(?m)^[[:space:]]*MCP_API_KEY=.*', f'MCP_API_KEY={new_key}', content)
if n == 0:
    new_content += f'MCP_API_KEY={new_key}\n'
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

# ── Tunnel-mode detection ─────────────────────────────────────────────────────
# When POKE_TUNNEL=1 the poke tunnel handles auth — MCP_API_KEY is optional.
# In all other modes (direct HTTP, Docker, etc.) it is required.
POKE_TUNNEL="${POKE_TUNNEL:-1}"  # default to tunnel mode since start.sh always tunnels

if [ "${POKE_TUNNEL}" != "1" ]; then
  : "${MCP_API_KEY:?MCP_API_KEY is not set — add it to .env or export it}"
else
  # In tunnel mode warn when the key is absent but don't abort.
  if [ -z "${MCP_API_KEY:-}" ]; then
    echo "  ℹ  MCP_API_KEY not set — server runs unauthenticated (safe: poke tunnel handles auth)."
  fi
export POKE_TUNNEL
fi

# ── Start server + tunnel ─────────────────────────────────────────────────────
echo "Starting poke-mail server..."
python3 src/server.py &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null" EXIT

# Wait for server to be ready
sleep 2

# Tunnel to Poke — prefer the globally-installed poke binary; fall back to npx.
echo "Starting tunnel to Poke..."
if command -v poke &>/dev/null; then
  poke tunnel http://localhost:3000/mcp --name "poke-mail"
else
  echo "  ℹ  'poke' binary not found in PATH — using npx poke (requires Node.js)."
  if ! command -v npx &>/dev/null; then
    echo "  ✗ Neither 'poke' nor 'npx' found. Install Node.js (nodejs.org) and run:"
    echo "      npm install -g poke   OR   npx poke tunnel ..."
    exit 1
  fi
  npx --yes poke tunnel http://localhost:3000/mcp --name "poke-mail"
fi
