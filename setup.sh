#!/usr/bin/env bash
# setup.sh — one-time setup for poke-mail
# Automates token creation via the Poke login SDK so you don't have to
# copy/paste API keys manually.
set -euo pipefail

cd "$(dirname "$0")"

echo ""
echo "  poke-mail setup"
echo "  ────────────────────────────────────────"
echo ""

# ── 1. Python virtualenv ────────────────────────────────────────────────────
if [ ! -d .venv ]; then
  echo "Creating Python virtualenv (.venv)..."
  python3 -m venv .venv
fi
source .venv/bin/activate
echo "Installing Python dependencies..."
pip install -q -r requirements.txt
echo "  ✓ Python dependencies installed"
echo ""

# ── 2. Poke API key (poke_api_key for config.yml) ────────────────────────────
# The 'poke' npm package stores credentials at:
#   ~/.config/poke/credentials.json  →  { "token": "..." }
# when the user runs: npx poke login

POKE_CREDENTIALS_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/poke/credentials.json"
POKE_API_KEY_VALUE=""

if [ -f "$POKE_CREDENTIALS_FILE" ]; then
  # Try to extract .token with python (avoids jq dependency)
  POKE_API_KEY_VALUE=$(python3 -c "
import json, sys
try:
    data = json.load(open('$POKE_CREDENTIALS_FILE'))
    print(data.get('token', ''))
except Exception:
    print('')
" 2>/dev/null || true)
fi

if [ -n "$POKE_API_KEY_VALUE" ]; then
  echo "  ✓ Found Poke credentials from 'poke login'"
else
  echo "  Poke API key not found via 'poke login'."
  echo ""
  echo "  Option 1 (recommended): Run the following, then re-run this script:"
  echo "    npx poke login"
  echo ""
  echo "  Option 2: Paste your API key from https://poke.com/settings/advanced"
  echo ""
  printf "  Poke API key (leave blank to set later): "
  read -r POKE_API_KEY_VALUE
  POKE_API_KEY_VALUE=$(echo "$POKE_API_KEY_VALUE" | tr -d '[:space:]')
fi
echo ""

# ── 3. config.yml ────────────────────────────────────────────────────────────
if [ ! -f config.yml ]; then
  echo "Copying config.example.yml → config.yml..."
  cp config.example.yml config.yml
  echo "  ✓ config.yml created"
else
  echo "  ✓ config.yml already exists — skipping copy"
fi

# Inject poke_api_key into config.yml if we have one
if [ -n "$POKE_API_KEY_VALUE" ]; then
  # Replace the placeholder value in-place (handles both quoted forms)
  python3 - <<EOF
import re

with open('config.yml', 'r') as f:
    content = f.read()

# Replace poke_api_key: "your-api-key-here" or poke_api_key: your-api-key-here
pattern = r'(poke_api_key:\s*)[^\n]+'
replacement = r'\g<1>"${POKE_API_KEY_VALUE}"'
new_content = re.sub(pattern, replacement, content)

with open('config.yml', 'w') as f:
    f.write(new_content)

print('  ✓ poke_api_key written to config.yml')
EOF
else
  echo "  ⚠  poke_api_key not set — edit config.yml manually before running start.sh"
fi
echo ""

# ── 4. MCP_API_KEY (.env) ────────────────────────────────────────────────────
if [ -f .env ] && grep -q 'MCP_API_KEY=' .env && ! grep -q 'MCP_API_KEY=your-secret-key-here' .env; then
  echo "  ✓ .env already has MCP_API_KEY — skipping"
else
  echo "Generating MCP_API_KEY..."
  RANDOM_KEY=$(python3 -c "
import secrets, string
alphabet = string.ascii_letters + string.digits
print(''.join(secrets.choice(alphabet) for _ in range(48)))
")
  if [ -f .env ]; then
    # Update existing file
    python3 - <<PYEOF
import re
with open('.env', 'r') as f:
    content = f.read()
new_content = re.sub(r'MCP_API_KEY=.*', 'MCP_API_KEY=${RANDOM_KEY}', content)
if 'MCP_API_KEY=' not in new_content:
    new_content += '\nMCP_API_KEY=${RANDOM_KEY}\n'
with open('.env', 'w') as f:
    f.write(new_content)
PYEOF
  else
    echo "MCP_API_KEY=${RANDOM_KEY}" > .env
  fi
  echo "  ✓ MCP_API_KEY generated and saved to .env"
fi
echo ""

# ── 5. Poke npm package ───────────────────────────────────────────────────────
if ! command -v poke &>/dev/null; then
  echo "Installing the 'poke' npm package globally..."
  npm install -g poke
  echo "  ✓ poke installed"
else
  echo "  ✓ poke CLI already installed ($(poke --version 2>/dev/null || echo 'version unknown'))"
fi
echo ""

# ── Done ─────────────────────────────────────────────────────────────────────
echo "  ────────────────────────────────────────"
echo "  Setup complete!"
echo ""
if [ -z "$POKE_API_KEY_VALUE" ]; then
  echo "  Next steps:"
  echo "    1. Run 'npx poke login' or add your Poke API key to config.yml"
  echo "    2. Fill in your email credentials in config.yml"
  echo "    3. Run ./start.sh"
else
  echo "  Next steps:"
  echo "    1. Fill in your email credentials in config.yml"
  echo "    2. Run ./start.sh"
fi
echo ""
