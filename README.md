# SOT Algo Trading System

Automated intraday options trading system for Indian markets (NSE/BSE) built on Fyers API. Listens to mentor signals on Telegram, classifies them with Claude AI, and executes trades across multiple demat accounts in real time.

---

## Architecture

```
Telegram Signal Channel
        │
        ▼
telegram_BOT.py          ← master event handler (Telethon)
        │
        ├── LLMSignalParser (Claude Haiku)
        │       └── Ollama llama3.2 (local fallback)
        │
        ▼ NEW_SIGNAL / REENTER / UPDATE_SL / ...
SOT_BOTv8.py             ← trade executor (subprocess, CLI args)
        │
        ├── PriceDispatcher  ← polls WebSocket servers (one per instrument)
        │       ├── ws_fyers_NIFTY_v3.py      → port 4002
        │       ├── ws_fyers_BANKNIFTY_v3.py  → port 4001
        │       ├── ws_fyers_FINNIFTY_v3.py   → port 4003
        │       ├── ws_fyers_BAJFINANCE_v3.py → port 4004
        │       ├── ws_fyers_MIDCPNIFTY_v3.py → port 4005
        │       └── ws_fyers_SENSEX_v3.py     → port 4006
        │
        └── Demat (RAM / SAI)
                └── Fyers API  ← placeOrder / modifyOrder / cancelOrder
```

**Supporting outputs:**
- `Trades/{date}/shadow_v2_{date}.jsonl` — v2 signal log (no orders fired)
- `Trades/{date}/*.csv` — P&L records per closed position
- Google Sheets — live trade log

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | Use the venv at `~/Documents/algo/venv` |
| Homebrew | latest | macOS only |
| Ollama | 0.21+ | Local LLM fallback (`brew install ollama`) |
| iTerm2 | any | Shell aliases use iTerm AppleScript |

---

## One-Time Setup

```bash
cd ~/Documents/algo/Fyers
bash onboarding.sh
```

The script installs all dependencies, creates a `credentials.py` template, starts the Ollama service, and verifies the environment.

---

## Credentials

All secrets live in `utils/credentials.py` (git-ignored). Copy the template printed by `onboarding.sh` and fill in your values:

```python
# ── Anthropic ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = "sk-ant-api03-..."      # console.anthropic.com/settings/keys

# ── Fyers — RAM account ───────────────────────────────────────────────────────
RAM_DEMAT = {
    "account_name": "RAM_DEMAT",
    "client_id":    "XXXXXXXX-100",         # App ID from Fyers dashboard
    "secret_key":   "XXXXXXXXXX",
    "FY_ID":        "XXXXXXXX",             # Fyers login ID
    "TOTP_KEY":     "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",  # 32-char TOTP secret
    "PIN":          "XXXX",
    "redirect_uri": "https://myapi.fyers.in/",
}

# ── Fyers — SAI account ───────────────────────────────────────────────────────
SAI_DEMAT = { ... }                         # same structure as RAM_DEMAT

# ── Google Sheets ─────────────────────────────────────────────────────────────
GSHEET_CREDS = { ... }                      # service account JSON from Google Cloud
GSHEET_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ── Lot sizes (update when SEBI changes contract sizes) ───────────────────────
LOT_SIZE_NIFTY        = 75
LOT_SIZE_BANKNIFTY    = 30
LOT_SIZE_FINNIFTY     = 65
LOT_SIZE_MIDCPNIFTY   = 120
LOT_SIZE_BAJFINANCE   = 750
LOT_SIZE_SENSEX       = 20
```

Also set the API key in your shell (sourced from `.aliases`):

```bash
export ANTHROPIC_API_KEY="sk-ant-api03-..."
```

---

## Shell Aliases

Source `.aliases` in your shell profile:

```bash
echo "source ~/Documents/algo/Fyers/.aliases" >> ~/.zshrc
source ~/.zshrc
```

Key aliases after sourcing:

| Alias | What it does |
|---|---|
| `fyers` | Daily OAuth login — generates today's config YAML |
| `telegram_bot` | Start main bot (auto-logins if config missing) |
| `launch_ws [INSTRUMENTS]` | Wait until 09:15, start selected WS servers in background |
| `nifty_ws_bg` | Start NIFTY WebSocket server in background |
| `banknifty_ws_bg` | Start BANKNIFTY WebSocket server in background |
| `ws_healthcheck` | Check all WebSocket servers are alive |
| `n50` | Quick NIFTY50 LTP from local WS server |
| `bn` | Quick BANKNIFTY LTP |
| `ssr_start` | Full combo: login + upstox + telegram bot |

---

## Daily Workflow

### 1. Login (run before 09:15 IST)

```bash
fyers
```

Authenticates via Fyers OAuth (TOTP 2FA) and generates:
- `Trades/{YYYY-MM-DD}/config_{YYYY-MM-DD}.yml` — expiry dates, lot sizes, enabled instruments

### 2. Start WebSocket Price Servers

```bash
# Start specific instruments (waits for 09:15 automatically):
launch_ws NIFTY BANKNIFTY SENSEX

# Or start all five:
launch_ws
```

Logs go to `Trades/{date}/{instrument_ws}.out`. PIDs saved to `Trades/{date}/{instrument_ws}.pid`.

### 3. Start the Telegram Bot

In a separate terminal:

```bash
telegram_bot
```

The bot will:
1. Connect via Telethon and listen to `sot_channel` / `qwerty_channel` / `sot_trial_channel`
2. Pass each message to `LLMSignalParser` (Claude → Ollama fallback)
3. Route the parsed intent to the right handler
4. Spawn `SOT_BOTv8.py` as a subprocess with extracted trade parameters

### 4. Verify Everything

```bash
ws_healthcheck
n50       # should return current NIFTY50 price
bn        # should return current BANKNIFTY price
```

---

## Signal Intents (LLM)

The LLM classifier (`utils/llm_signal_parser.py`) maps every mentor message to one of:

| Intent | Example trigger | Bot action |
|---|---|---|
| `NEW_SIGNAL` | "Nifty 25500 CE near 215-220, T1 230, T2 240, SL 204" | Fire `SOT_BOTv8` with extracted params |
| `REENTER` | "Re-enter same", "above 530", bare price reply | Update entry on active signal, re-fire |
| `UPDATE_SL` | "SL at 185", "trail SL to 200" | Modify SL on running build |
| `UPDATE_TARGET` | "Shift target to 650/700" | Update targets on running build |
| `SL_RESOLVED` | Bare price resolves a deferred-SL signal | Fire the pending signal |
| `CANCEL` | "Ignore", "don't take", "mistake" | Discard pending signal |
| `PARTIAL_EXIT` | "Book half", "book 1 lot" | Partial square-off |
| `FULL_EXIT` | "Exit", "close all", "square off" | Square off all positions |
| `NOISE` | Greetings, screenshots, target-hit confirmations | Ignored |
| `LLM_ERROR` | API failure / balance exhausted / expired key | 🆘 SOS Telegram alert with raw message |

**LLM failure chain:**
```
Claude Haiku (primary)
    → fails (balance/key/network)
llama3.2 via Ollama (local, free, offline)
    → fails (Ollama not running)
🆘 SOS alert with raw message → act manually
```

---

## Trade Execution (`SOT_BOTv8.py`)

Spawned by `telegram_BOT.py` as a subprocess. Can also be run manually:

```bash
sot_bot -i NIFTY -s 25500 -cepe CE -e 215 -t1 230 -t2 240 -t3 255 -sl 204
```

Key CLI flags:

| Flag | Description |
|---|---|
| `-i` | Instrument (`NIFTY`, `BANKNIFTY`, `FINNIFTY`, `MIDCPNIFTY`, `BAJFINANCE`, `SENSEX`) |
| `-s` | Strike price |
| `-cepe` | `CE` or `PE` |
| `-e` | Entry price (mid-point) |
| `-t1 -t2 -t3` | Target prices |
| `-sl` | Stop-loss price |
| `--paper` | Paper trade mode (no real orders) |
| `--strategy` | `RANGE` or `BREAKOUT` |

**Target adjustment:** when re-entry price is higher than T1, targets are automatically bumped — invalid targets at or below entry are dropped and extended using the last inter-target delta, guaranteeing at least 4 valid targets.

---

## Account Configuration

Configured in `telegram_BOT.py` via `AccountConfig`:

```python
AccountConfig(
    name="RAM_DEMAT",
    quantity_nifty=65,          # 1 lot
    quantity_banknifty=30,      # 1 lot
    paper_trade=True,           # flip to False for live trading
    should_average=False,
    squareoff_at_first_target=True,
    aggressive_trail=False,
    lazy_trail=False,
)
```

Key flags:

| Flag | Behaviour |
|---|---|
| `paper_trade=True` | Log orders, skip actual API calls |
| `should_average=True` | Add to position on dip |
| `squareoff_at_first_target=True` | Exit full position at T1 |
| `await_next_target=True` | Hold until T2/T3 before any exit |
| `aggressive_trail=True` | Trail SL more tightly |
| `lazy_trail=True` | Relax trail; hold longer |

> `squareoff_at_first_target` and `await_next_target` cannot both be `True`.

---

## WebSocket Price Servers

Each instrument runs a dedicated Flask server maintaining a live LTP cache via Fyers WebSocket:

| Instrument | Port | Health check |
|---|---|---|
| BANKNIFTY | 4001 | `curl http://localhost:4001/ltp?instrument=NSE:NIFTYBANK-INDEX` |
| NIFTY | 4002 | `curl http://localhost:4002/ltp?instrument=NSE:NIFTY50-INDEX` |
| FINNIFTY | 4003 | `curl http://localhost:4003/ltp?instrument=NSE:FINNIFTY-INDEX` |
| BAJFINANCE | 4004 | `curl http://localhost:4004/ltp?instrument=NSE:BAJFINANCE-EQ` |
| MIDCPNIFTY | 4005 | `curl http://localhost:4005/ltp?instrument=NSE:MIDCPNIFTY-INDEX` |
| SENSEX | 4006 | `curl http://localhost:4006/ltp?instrument=BSE:SENSEX-INDEX` |

---

## Telegram Channels

| Variable | Channel ID | Purpose |
|---|---|---|
| `sot_channel` | `-1001209833646` | Main SOT signals |
| `qwerty_channel` | `-1001767848638` | Qwerty mentor signals |
| `sot_trial_channel` | `-1001810504797` | Trial / paper signals |
| `sos_channel` | `-1002016606884` | Bot alerts and SOS messages |

---

## Running Tests

```bash
# Unit tests — no API calls, fully mocked
python -m pytest tests/test_llm_signal_parser.py -v

# Trade state machine tests — quantity math, SL/target logic
python -m pytest tests/test_trade_scenarios.py -v

# Golden scenario tests — real Claude API
python tests/test_golden_scenarios.py -v

# Single session only
python tests/test_golden_scenarios.py Apr15 -v
```

---

## Shadow Mode

Every signal processed by the LLM path is mirrored to a JSONL log without firing orders. Used to compare v2 (LLM-parsed) vs v1 (regex-only) behaviour, and to backtest prompt changes.

Log: `Trades/{YYYY-MM-DD}/shadow_v2_{YYYY-MM-DD}.jsonl`

Each line records: parsed signal, raw message, intent, confidence, whether v1 would have fired.

---

## Logs

| Log | Location |
|---|---|
| Main bot | `Trades/{date}/telegram_BOT_{date}.log` |
| SOT bot | `Trades/{date}/SOT_BOTv8_{date}_{build}.log` |
| WS servers | `Trades/{date}/{instrument_ws}.out` |
| LLM context (persisted across restarts) | `{logger_path}/llm_context.json` |
| Shadow mode | `Trades/{date}/shadow_v2_{date}.jsonl` |
| P&L CSVs | `Trades/{date}/*.csv` |

---

## Troubleshooting

**`No config for today` on bot start**
```bash
fyers    # re-run login to regenerate today's config YAML
```

**`ANTHROPIC_API_KEY not set`**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # then restart the bot
```

**WS server port already in use**
```bash
lsof -i :4002        # find the PID holding the port
kill -9 <PID>
nifty_ws_bg          # restart
```

**Ollama not running (fallback disabled)**
```bash
brew services start ollama
ollama list          # should show llama3.2:latest
```

**SOS alert: `LLM PARSE FAILED`**
Claude API was unreachable and Ollama also failed. Check:
1. `echo $ANTHROPIC_API_KEY` — key is set and has balance
2. `brew services list | grep ollama` — Ollama service is running
3. Act on the raw message shown in the SOS alert manually

**`A coroutine object is required` in SOT_BOTv8**
Telegram bot client was started outside an async context. Fixed in commit `67c1af4` — pull latest `main`.

---

## Repository Structure

```
Fyers/
├── telegram_BOT.py               # Master bot: signal listener + dispatcher
├── SOT_BOTv8.py                  # Trade executor (current version)
├── SOT_BOTv7.py                  # Previous version (legacy, still active)
├── Login_Fyers.py                # Daily Fyers OAuth login + config generation
├── Login_RAM.py / Login_SAI.py   # Account-specific logins
├── ws_fyers_NIFTY_v3.py         # NIFTY WebSocket price server
├── ws_fyers_BANKNIFTY_v3.py
├── ws_fyers_FINNIFTY_v3.py
├── ws_fyers_MIDCPNIFTY_v3.py
├── ws_fyers_BAJFINANCE_v3.py
├── ws_fyers_SENSEX_v3.py
├── ws_healthcheck.py             # Health check for all WS servers
├── trade_planner.py              # Options Greeks calculator (Black-Scholes)
├── analyse_recent_signals.py     # Parse cached Telegram history
├── requirements.txt
├── .aliases                      # Shell aliases (source in ~/.zshrc)
├── onboarding.sh                 # First-time setup script
├── utils/
│   ├── llm_signal_parser.py      # LLM intent classifier (Claude + Ollama fallback)
│   ├── shadow_mode.py            # v2 comparison logger
│   ├── demat.py                  # Fyers order execution wrapper
│   ├── position.py               # Position data structure
│   ├── trade_manager.py          # Multi-account orchestration
│   ├── price_dispatcher.py       # Centralized price polling hub
│   ├── account_config.py         # Per-account trading parameters
│   ├── constants.py              # Global Config, expiry date maps
│   ├── credentials.py            # ⚠ git-ignored — all secrets live here
│   ├── clock.py                  # IST timing utilities
│   └── candles.py                # OHLC candle utilities
└── tests/
    ├── test_llm_signal_parser.py  # Unit tests (no API calls)
    ├── test_trade_scenarios.py    # Trade state machine tests
    ├── test_golden_scenarios.py   # End-to-end LLM tests (real API)
    └── replay_signals.py          # Historical signal replay / backtesting
```
