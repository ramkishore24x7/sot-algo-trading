#!/usr/bin/env bash
# onboarding.sh — first-time setup for the SOT Algo Trading System
# Run from the repo root: bash onboarding.sh

set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$HOME/Documents/algo/venv"
PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"
CREDS_FILE="$REPO_DIR/utils/credentials.py"
ALIASES_FILE="$REPO_DIR/.aliases"
OLLAMA_MODEL="llama3.2"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✔ $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $*${NC}"; }
err()  { echo -e "${RED}  ✘ $*${NC}"; }
hdr()  { echo -e "\n${BLUE}▶ $*${NC}"; }

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║       SOT Algo Trading — Onboarding Setup            ║"
echo "╚══════════════════════════════════════════════════════╝"

# ── 1. System checks ──────────────────────────────────────────────────────────
hdr "System checks"

if [[ "$(uname)" != "Darwin" ]]; then
  err "This script is macOS-only. Adjust Homebrew steps for your OS."
  exit 1
fi
ok "macOS confirmed"

if ! command -v brew &>/dev/null; then
  err "Homebrew not found. Install from https://brew.sh then re-run."
  exit 1
fi
ok "Homebrew: $(brew --version | head -1)"

# ── 2. Python virtual environment ─────────────────────────────────────────────
hdr "Python virtual environment"

if [[ ! -f "$PYTHON" ]]; then
  warn "venv not found at $VENV_DIR — creating one with system python3"
  python3 -m venv "$VENV_DIR"
  ok "venv created at $VENV_DIR"
else
  ok "venv found at $VENV_DIR"
fi

PYVER=$("$PYTHON" --version 2>&1)
ok "Python: $PYVER"

# ── 3. pip dependencies ───────────────────────────────────────────────────────
hdr "Installing Python dependencies"

"$PIP" install --upgrade pip --quiet
"$PIP" install -r "$REPO_DIR/requirements.txt" --quiet
ok "requirements.txt installed"

# Ensure openai package (used for Ollama fallback)
"$PIP" install openai --quiet
ok "openai package (Ollama client) installed"

# ── 4. Ollama (local LLM fallback) ───────────────────────────────────────────
hdr "Ollama — local LLM fallback"

if ! command -v ollama &>/dev/null; then
  echo "  Installing Ollama via Homebrew..."
  brew install ollama
  ok "Ollama installed"
else
  ok "Ollama already installed: $(ollama --version 2>/dev/null || echo 'version unknown')"
fi

# Start Ollama service
if ! brew services list | grep -q "ollama.*started"; then
  echo "  Starting Ollama service..."
  brew services start ollama
  sleep 3
fi
ok "Ollama service running"

# Pull llama3.2 if not already present
if ollama list 2>/dev/null | grep -q "llama3.2"; then
  ok "llama3.2 model already pulled"
else
  echo "  Pulling llama3.2 (~2 GB, this takes a few minutes)..."
  ollama pull "$OLLAMA_MODEL"
  ok "llama3.2 pulled"
fi

# Quick smoke test
echo "  Testing Ollama API..."
OLLAMA_RESP=$("$PYTHON" - <<'EOF'
import openai, sys
try:
    c = openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
    r = c.chat.completions.create(
        model="llama3.2", max_tokens=10,
        messages=[{"role":"user","content":'Reply only: {"intent":"NOISE"}'}]
    )
    print(r.choices[0].message.content.strip()[:40])
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
EOF
)
ok "Ollama smoke test passed: $OLLAMA_RESP"

# ── 5. credentials.py ─────────────────────────────────────────────────────────
hdr "Credentials"

if [[ -f "$CREDS_FILE" ]]; then
  ok "utils/credentials.py already exists — skipping template creation"
else
  warn "utils/credentials.py not found — creating template"
  cat > "$CREDS_FILE" <<'CREDS_EOF'
# utils/credentials.py — git-ignored, fill in your real values
# ── Anthropic ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = "sk-ant-api03-REPLACE_ME"   # console.anthropic.com/settings/keys

# ── Fyers — RAM account ──────────────────────────────────────────────────────
RAM_DEMAT = {
    "account_name": "RAM_DEMAT",
    "client_id":    "XXXXXXXX-100",
    "secret_key":   "XXXXXXXXXX",
    "FY_ID":        "XXXXXXXX",
    "TOTP_KEY":     "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
    "PIN":          "XXXX",
    "redirect_uri": "https://myapi.fyers.in/",
}

# ── Fyers — SAI account ──────────────────────────────────────────────────────
SAI_DEMAT = {
    "account_name": "SAI_DEMAT",
    "client_id":    "XXXXXXXX-100",
    "secret_key":   "XXXXXXXXXX",
    "FY_ID":        "XXXXXXXX",
    "TOTP_KEY":     "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
    "PIN":          "XXXX",
    "redirect_uri": "https://myapi.fyers.in/",
}

DEMATS_FOR_LOGIN = [SAI_DEMAT, RAM_DEMAT]

# ── Google Sheets ────────────────────────────────────────────────────────────
GSHEET_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
GSHEET_CREDS = {
    "type": "service_account",
    "project_id": "REPLACE_ME",
    "private_key_id": "REPLACE_ME",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nREPLACE_ME\n-----END RSA PRIVATE KEY-----\n",
    "client_email": "REPLACE_ME@REPLACE_ME.iam.gserviceaccount.com",
    "client_id": "REPLACE_ME",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}

# ── Lot sizes (update when SEBI changes contract sizes) ──────────────────────
LOT_SIZE_NIFTY        = 65
LOT_SIZE_BANKNIFTY    = 30
LOT_SIZE_FINNIFTY     = 60
LOT_SIZE_MIDCPNIFTY   = 120
LOT_SIZE_BAJFINANCE   = 750
LOT_SIZE_SENSEX       = 20

FREEZE_QUANTITY_NIFTY        = 1170
FREEZE_QUANTITY_BANKNIFTY    = 900
FREEZE_QUANTITY_FINNIFTY     = 1800
FREEZE_QUANTITY_MIDCPNIFTY   = 4200
FREEZE_QUANTITY_BAJFINANCE   = 3750
FREEZE_QUANTITY_SENSEX       = 600

HIGHEST_NIFTY_OPTION_PRICE       = 250
HIGHEST_BANKNIFTY_OPTION_PRICE   = 450
HIGHEST_FINNIFTY_OPTION_PRICE    = 250
HIGHEST_MIDCPNIFTY_OPTION_PRICE  = 250
HIGHEST_BAJFINANCE_OPTION_PRICE  = 250
HIGHEST_SENSEX_OPTION_PRICE      = 1500

# ── Paper trading quantities — 10 lots each ──────────────────────────────────
PAPER_QTY_NIFTY        = 650   # 10 × 65
PAPER_QTY_BANKNIFTY    = 300   # 10 × 30
PAPER_QTY_FINNIFTY     = 600   # 10 × 60
PAPER_QTY_MIDCPNIFTY   = 1200  # 10 × 120
PAPER_QTY_BAJFINANCE   = 7500  # 10 × 750
PAPER_QTY_SENSEX       = 200   # 10 × 20
CREDS_EOF
  warn "Template written to utils/credentials.py — fill in your real values before running the bot"
fi

# ── 6. ANTHROPIC_API_KEY env check ───────────────────────────────────────────
hdr "Environment variables"

if [[ -z "$ANTHROPIC_API_KEY" ]]; then
  warn "ANTHROPIC_API_KEY is not set in this shell"
  warn "Add it to .aliases:  export ANTHROPIC_API_KEY=\"sk-ant-...\""
  warn "Then run:            source $ALIASES_FILE"
else
  ok "ANTHROPIC_API_KEY is set (${ANTHROPIC_API_KEY:0:12}...)"
fi

# ── 7. Shell aliases ──────────────────────────────────────────────────────────
hdr "Shell aliases"

SHELL_RC="$HOME/.zshrc"
[[ "$SHELL" == *bash* ]] && SHELL_RC="$HOME/.bashrc"

ALIAS_LINE="source $ALIASES_FILE"
if grep -qF "$ALIAS_LINE" "$SHELL_RC" 2>/dev/null; then
  ok ".aliases already sourced in $SHELL_RC"
else
  echo "" >> "$SHELL_RC"
  echo "# SOT Algo Trading aliases" >> "$SHELL_RC"
  echo "$ALIAS_LINE" >> "$SHELL_RC"
  ok "Added to $SHELL_RC — run: source $SHELL_RC"
fi

# ── 8. Run unit tests ─────────────────────────────────────────────────────────
hdr "Unit tests (smoke check)"

cd "$REPO_DIR"
if "$PYTHON" -m pytest tests/test_llm_signal_parser.py -q --tb=short 2>&1 | tail -5; then
  ok "Unit tests passed"
else
  warn "Some unit tests failed — check output above (may need credentials.py filled in)"
fi

# ── 9. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║                  Setup Complete                      ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Fill in utils/credentials.py with your real API keys"
echo "  2. Set ANTHROPIC_API_KEY in .aliases and run:  source ~/.zshrc"
echo ""
echo "  Daily startup:"
echo "  ┌─────────────────────────────────────────────────┐"
echo "  │  fyers                    # login (before 09:15) │"
echo "  │  launch_ws NIFTY BANKNIFTY SENSEX               │"
echo "  │  telegram_bot             # in a separate tab    │"
echo "  └─────────────────────────────────────────────────┘"
echo ""
echo "  Verify WS servers:  ws_healthcheck"
echo "  Quick LTP check:    n50   (NIFTY) | bn (BANKNIFTY)"
echo ""
