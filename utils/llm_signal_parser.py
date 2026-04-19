"""
utils/llm_signal_parser.py
───────────────────────────────────────────────────────────────────────────────
LLM-based trade signal parser using Claude API.

Replaces the regex-only approach with context-aware intent classification.
Handles: new signals, re-entries, SL updates, cancellations, deferred SL,
         partial exits, noise filtering.

DayContext resets at market open (09:15 IST) each day.
"""

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, time as dtime
from typing import Optional

import anthropic

# Try to import key from credentials.py as a fallback when env var is not set
try:
    from utils.credentials import ANTHROPIC_API_KEY as _CREDS_API_KEY
except ImportError:
    _CREDS_API_KEY = None

# Optional: openai package used for Ollama fallback (OpenAI-compatible local API)
try:
    import openai as _openai_lib
    _openai_available = True
except ImportError:
    _openai_lib = None
    _openai_available = False

logger = logging.getLogger(__name__)

# ── Noise detection (skip before even calling LLM) ───────────────────────────
# Messages that are pure celebration / update spam — no trading intent
_NOISE_ONLY_RE = re.compile(
    r'^[\s\d🚀💸✅📈📉🔥💰🎯⚡🌟😊😁😄👍🏻👏✨\+\-\.\/\*!,]+$'
)
_NOISE_PHRASE_RE = re.compile(
    r'^\s*(good\s*(morning|night|evening|day)|gm\b|gn\b|'
    r'have a\s*(wonderful|great|profitable)|'
    r'send\s*screenshot|those\s*who\s*booked|'
    r'trades\s*taken\s*today|morning\s*jackpot\s*done|'
    r'watchlist|profit\s*screenshot|'
    r'goood?\s*morning|wonderful\s*and\s*profitable|'
    r'track\s+both\s+levels|watch\s+both\s+levels|keep\s+(an?\s+)?eye\s+on)',
    re.IGNORECASE
)

MARKET_OPEN_TIME = dtime(9, 15, 0)   # IST — context resets here


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ParsedSignal:
    """Clean structured signal from LLM."""
    intent: str                        # see INTENTS below
    confidence: float = 0.0
    instrument: Optional[str] = None
    strike: Optional[str] = None
    ce_pe: Optional[str] = None
    strategy: Optional[str] = None    # RANGE | BREAKOUT
    entry_low: Optional[int] = None
    entry_high: Optional[int] = None
    targets: list = field(default_factory=list)
    sl: Optional[int] = None
    sl_deferred: bool = False
    sl_at_cost: bool = False
    wait_for_price: bool = True
    notes: str = ""
    raw_message: str = ""

    def is_actionable(self) -> bool:
        """True if we have enough to fire SOT_BOT."""
        return (
            self.intent == "NEW_SIGNAL"
            and self.instrument is not None
            and self.strike is not None
            and self.ce_pe is not None
            and len(self.targets) >= 2
            and not self.sl_deferred
            and self.sl is not None
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        if self.intent == "NOISE":
            return "NOISE"
        parts = [f"intent={self.intent}({self.confidence:.0%})"]
        if self.instrument:
            parts.append(f"{self.instrument} {self.strike} {self.ce_pe}")
        if self.strategy:
            parts.append(self.strategy)
        if self.entry_low:
            parts.append(f"entry={self.entry_low}-{self.entry_high}")
        if self.targets:
            parts.append(f"T={'/'.join(str(t) for t in self.targets[:3])}...")
        if self.sl:
            parts.append(f"SL={self.sl}")
        elif self.sl_deferred:
            parts.append("SL=DEFERRED")
        if self.sl_at_cost:
            parts.append("SL@COST")
        if self.notes:
            parts.append(f"[{self.notes}]")
        return " | ".join(parts)


# Valid intents the LLM can return
INTENTS = {
    "NEW_SIGNAL",     # Fresh trade signal
    "REENTER",        # Re-enter same signal (use active/last signal params)
    "UPDATE_SL",      # Mentor sent a new SL for the active trade
    "UPDATE_TARGET",  # Mentor revised targets
    "SL_RESOLVED",    # This message resolves a previously deferred SL
    "CANCEL",         # Ignore / cancel / don't take the pending signal
    "PARTIAL_EXIT",   # Book X lots / X%
    "FULL_EXIT",      # Exit everything
    "NOISE",          # No trading content
    "LLM_ERROR",      # API/parse failure — never silently dropped, always SOS-alerted
}


# ── Context window ────────────────────────────────────────────────────────────

class DayContext:
    """
    Maintains today's meaningful message history and active signal state.
    Resets at 09:15 IST (or when date changes).
    """

    MAX_CONTEXT = 12   # max messages to send to LLM

    def __init__(self, persist_path: Optional[str] = None):
        self._persist_path = persist_path
        self._reset(save=False)  # don't write on first init — caller loads first

    def _reset(self, save: bool = True):
        self.messages: list[dict] = []
        self.active_signal: Optional[dict] = None
        self.pending_signal: Optional[dict] = None
        self.signal_store: dict = {}       # msg_id → signal dict (reply-chain)
        self._date = date.today()
        logger.info("DayContext reset for new day/session")
        if save:
            self._save()

    def _save(self):
        """Atomically persist context state to disk so restarts can reload it."""
        if not self._persist_path:
            return
        state = {
            "date":          str(self._date),
            "messages":      self.messages,
            "active_signal": self.active_signal,
            "pending_signal": self.pending_signal,
            "signal_store":  {str(k): v for k, v in self.signal_store.items()},
        }
        try:
            dir_ = os.path.dirname(self._persist_path)
            with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False,
                                             suffix=".tmp") as f:
                json.dump(state, f, indent=2)
                tmp = f.name
            os.replace(tmp, self._persist_path)   # atomic on POSIX
        except Exception as e:
            logger.warning(f"[DayContext] Failed to persist state: {e}")

    def _load(self):
        """Reload today's context from disk if the file exists and is current."""
        if not self._persist_path or not os.path.exists(self._persist_path):
            return False
        try:
            with open(self._persist_path) as f:
                state = json.load(f)
            if state.get("date") != str(date.today()):
                logger.info("[DayContext] Persisted state is from a previous day — ignoring.")
                return False
            self.messages      = state.get("messages", [])
            self.active_signal = state.get("active_signal")
            self.pending_signal = state.get("pending_signal")
            # signal_store keys are stored as strings; convert back to int
            self.signal_store  = {int(k): v for k, v in state.get("signal_store", {}).items()}
            self._date         = date.today()
            logger.info(
                f"[DayContext] Restored from disk: "
                f"{len(self.messages)} msgs, "
                f"active={'yes' if self.active_signal else 'no'}, "
                f"pending={'yes' if self.pending_signal else 'no'}, "
                f"signal_store={len(self.signal_store)} entries"
            )
            return True
        except Exception as e:
            logger.warning(f"[DayContext] Failed to load persisted state: {e}")
            return False

    def _should_reset(self) -> bool:
        now = datetime.now()
        if now.date() != self._date:
            return True
        if now.time() >= MARKET_OPEN_TIME and self._date != date.today():
            return True
        return False

    def add_message(self, text: str, msg_id: int, is_edit: bool = False, signal_channel: bool = False):
        if self._should_reset():
            self._reset()
        if not signal_channel and is_noise(text):
            return
        entry = {
            "id": msg_id,
            "time": datetime.now().strftime("%H:%M"),
            "text": text.strip(),
            "edited": is_edit,
        }
        self.messages.append(entry)
        if len(self.messages) > self.MAX_CONTEXT:
            self.messages = self.messages[-self.MAX_CONTEXT:]
        # Messages are high-frequency — don't persist every tick, only on signals
        # (signal_fired / signal_pending persist the important state changes)

    def set_active(self, signal: ParsedSignal):
        self.active_signal = signal.to_dict()
        self.pending_signal = None
        self._save()

    def set_pending(self, signal: ParsedSignal):
        self.pending_signal = signal.to_dict()
        self._save()

    def clear_active(self):
        self.active_signal = None
        self._save()

    def clear_pending(self):
        self.pending_signal = None
        self._save()

    def store_signal(self, msg_id: int, signal: ParsedSignal):
        """Store a fired signal keyed by its Telegram message ID."""
        self.signal_store[msg_id] = signal.to_dict()
        self._save()

    def get_signal_by_id(self, msg_id: int):
        """Look up a previously fired signal by its Telegram message ID."""
        d = self.signal_store.get(msg_id)
        return _dict_to_signal(d) if d else None

    def context_for_llm(self) -> str:
        if not self.messages:
            return "(no messages yet today)"
        lines = []
        for m in self.messages[:-1]:  # exclude current message
            prefix = "[EDIT]" if m["edited"] else ""
            lines.append(f"[{m['time']}]{prefix} {m['text']}")
        return "\n".join(lines) if lines else "(this is the first message today)"


# ── Noise filter ─────────────────────────────────────────────────────────────

def is_noise(text: str) -> bool:
    """Fast pre-filter — skip before calling LLM."""
    t = text.strip()
    if not t:
        return True
    if _NOISE_ONLY_RE.fullmatch(t):
        return True
    if _NOISE_PHRASE_RE.match(t):
        return True
    # Short price-hit messages: "395🚀🚀" "84000 target was also done"
    if len(t) < 40 and re.fullmatch(r'[\d\s🚀💸✅\+\-\.!,]+', t):
        return True
    # Profit/points announcements: "35 points", "100+ points done", "20 points book done"
    if re.fullmatch(r'\d+\+?\s*points?[\s\w!🚀]*', t, re.IGNORECASE):
        return True
    return False


# ── LLM parser ───────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a trade signal parser for an Indian options trading automation bot.

The mentor sends signals for NSE/BSE options. Instruments: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, BAJFINANCE, SENSEX.
Exchange: NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY/BAJFINANCE trade on NSE. SENSEX trades on BSE.

## Typical signal formats

Range trade (entry range):
  Nifty 25500 ce near 215-220
  Target 230/240/255/270/290+
  Sl 204
  (Wait for price)

Breakout trade (entry above a price):
  Nifty 25300 ce above 105 level
  Target 115/125/140/155+
  Sl 94
  (Wait for price)

Exact price trade (entry at a single price — treat like RANGE with entry_low=entry_high):
  Nifty 25300 pe at 205
  Target 215/225/238/260/300+
  Sl - I will update

Older/uppercase format (same logic, just all caps and BUY prefix):
  BUY BANKNIFTY 42600 PE NEAR 370-380
  TARGET 400/420/450+++
  SL - 355
  ( Wait for price )

## Instrument name typos to normalise
- BANKNFITY, BANK NIFTY → BANKNIFTY
- FIN NIFTY → FINNIFTY

## Target line typos to recognise (still extract numbers from these)
- TARGST, TATGET, TRAGET, TAGET, TARET, TARGWT → all mean Target

## Common follow-up patterns and their intents

### Re-entry / revised entry (REENTER)
- "Re-enter", "re-enter same", "add more near X", "same call", "same trade", "same signal" → REENTER
- "Enter above X", "entry above X", "try above X", "enter at X" (short reply revising entry) → REENTER with strategy=BREAKOUT
- "Entry again near X", "enter again near X", "will enter near X", "will take near X", "re entry near X" → REENTER
- "From this zone market gave good move", "we will take small risk", "entering again here", "will try again" → REENTER
- A bare price range "480-490" or single level "530" as a reply to an active signal → REENTER (mentor revising the entry level)
- "Near X-Y" or "above X" as a standalone reply (no full signal format) → REENTER with new entry extracted

### SL management (UPDATE_SL)
- "Sl updated to 210", "sl now 195", "sl 185" as standalone → UPDATE_SL
- "Trail sl to X", "move sl to X", "shift sl to X", "sl shift to X" → UPDATE_SL
- "Sl at cost 165", "keep sl at cost 590", "sl at cost and hold" (mid-trade) → UPDATE_SL with sl_at_cost=true, sl=<the number if present>

### Cancellation (CANCEL)
- "Ignore previous", "cancel", "don't take", "avoid this", "don't enter", "no trade", "avoid" → CANCEL
- "Mistake", "wrong call", "ignore that" → CANCEL

### Partial exit (PARTIAL_EXIT)
- "Book X lots", "book X lot", "book 50%", "book half", "book 1 lot", "partial book here" → PARTIAL_EXIT

### Full exit (FULL_EXIT)
- "Exit", "close all", "square off" (without a specific price level) → FULL_EXIT
- "Remaining lot exit at 190", "exit at 190", "exit remaining at X" → UPDATE_SL with sl=X (mentor setting new exit/SL level, not asking to exit now)

### Deferred SL
- "Sl - I will update" / "Sl - Will update" / "Sl - I will update on the basis of market move" → NEW_SIGNAL with sl_deferred=true
- A message that gives just a number after a signal with deferred SL → SL_RESOLVED

### Noise (NOISE)
- "395🚀🚀", "target done", "target was also done", "T1 done", "T2 done", "target 1 hit", "next target" → NOISE
- "Good morning", "wonderful day", screenshots → NOISE
- "Track both levels", "watch both", "keep eye on" → NOISE
- Price-running updates: "🚀🚀", emojis only, celebration messages → NOISE
- Expiry mention like "12th june expiry" / "May exp" at end of a signal → part of signal notes, not a separate intent

## Important rules
- Signals may or may not start with "BUY" — the instrument name alone is enough
- If SL field contains text (not a number) → sl_deferred=true, sl=null
- If a NEW_SIGNAL message has NO SL line at all (SL completely absent, not mentioned) → sl_deferred=true, sl=null
- If message references "same", "previous", "re-enter" without instrument → REENTER
- If a NEW_SIGNAL message has entry/targets but NO instrument name, infer instrument/strike/ce_pe
  from the active signal in context whose entry price range is closest to the new entry.
  e.g. active="Nifty 22600 PE near 190-195", new message="Next entry near 160-165 Target 175/190/..."
  → inherit instrument=NIFTY, strike=22600, ce_pe=PE (same option, lower premium level)
  Only inherit when the price range is plausibly the same contract (within ~150 pts of active entry).
  If no active signal exists, leave instrument=null.
- LEVEL keyword = buy only exactly at that price, don't chase
- "SL at cost", "keep SL at cost", "sl cost" → sl_at_cost=true (mentor instructs to hold at break-even, don't trail SL to previous targets)
- "Wait for price" / "(Wait for price)" footer = wait_for_price=true
- Entry range "215-220" → entry_low=215, entry_high=220, strategy=RANGE
- "at 205" (single exact price) → entry_low=205, entry_high=205, strategy=RANGE
- Breakout "above 105" or "above 105 level" → entry_low=105, entry_high=105, strategy=BREAKOUT
- Extract ALL targets from the / separated list (typically 3-5 targets)
- Targets with +/++/+++ at end — strip those, just keep the number

Respond ONLY with valid JSON. No markdown. No explanation outside the JSON."""

_USER_TEMPLATE = """\
Active signal (SOT_BOT currently running this trade):
{active_signal}

Pending signal (received but waiting for SL before triggering):
{pending_signal}

Today's meaningful context (oldest → newest, excluding current message):
{context}

New message to classify:
\"\"\"
{message}
\"\"\"

JSON response:
{{
  "intent": "<NEW_SIGNAL|REENTER|UPDATE_SL|UPDATE_TARGET|SL_RESOLVED|CANCEL|PARTIAL_EXIT|FULL_EXIT|NOISE>",
  "confidence": <0.0-1.0>,
  "instrument": "<NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY|BAJFINANCE|SENSEX|null>",
  "strike": "<number as string or null>",
  "ce_pe": "<CE|PE|null>",
  "strategy": "<RANGE|BREAKOUT|null>",
  "entry_low": <integer or null>,
  "entry_high": <integer or null>,
  "targets": [<list of integers>],
  "sl": <integer or null>,
  "sl_deferred": <true|false>,
  "sl_at_cost": <true|false>,
  "wait_for_price": <true|false>,
  "notes": "<one line explanation>"
}}"""


class LLMSignalParser:
    """
    Parses incoming Telegram messages using Claude.
    Maintains per-day context automatically.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-haiku-4-5-20251001",
                 persist_path: Optional[str] = None,
                 fallback_ollama_model: Optional[str] = None,
                 fallback_ollama_url: str = "http://localhost:11434/v1"):
        key = (
            api_key
            or os.environ.get("ANTHROPIC_API_KEY")
            or (_CREDS_API_KEY if _CREDS_API_KEY and not _CREDS_API_KEY.startswith("YOUR_") else None)
        )
        if not key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set. "
                "Set it in ~/.aliases (export ANTHROPIC_API_KEY=...) "
                "or in utils/credentials.py (ANTHROPIC_API_KEY = '...')."
            )
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model
        self.context = DayContext(persist_path=persist_path)

        # Optional local Ollama fallback — used when Claude API fails
        self._ollama_model = fallback_ollama_model
        self._ollama_client = None
        if fallback_ollama_model:
            if _openai_available:
                self._ollama_client = _openai_lib.OpenAI(
                    base_url=fallback_ollama_url,
                    api_key="ollama",  # Ollama ignores API key but openai lib requires one
                )
                logger.info(f"[LLMSignalParser] Ollama fallback enabled: model={fallback_ollama_model} url={fallback_ollama_url}")
            else:
                logger.warning("[LLMSignalParser] fallback_ollama_model set but 'openai' package not installed — fallback disabled. Run: pip install openai")

        # Restore today's context from disk if available (survives restarts)
        if not self.context._load():
            logger.info("[LLMSignalParser] No persisted context found — starting fresh.")
        logger.info(f"LLMSignalParser initialised with model={model}")

    def parse(self, text: str, msg_id: int = 0, is_edit: bool = False, signal_channel: bool = False) -> ParsedSignal:
        """
        Main entry point. Returns a ParsedSignal for every message.
        When signal_channel=True, skip pre-filter — send everything to LLM.
        """
        # Fast path: obvious noise (skip for signal channels to avoid false drops)
        if not signal_channel and is_noise(text):
            logger.debug(f"[LLM] msg={msg_id} → NOISE (pre-filter)")
            return ParsedSignal(intent="NOISE", raw_message=text)

        # Add to context window (before calling LLM so context is up to date)
        self.context.add_message(text, msg_id, is_edit, signal_channel=signal_channel)

        # Build prompt
        user_prompt = _USER_TEMPLATE.format(
            active_signal=json.dumps(self.context.active_signal, indent=2)
                          if self.context.active_signal else "null",
            pending_signal=json.dumps(self.context.pending_signal, indent=2)
                           if self.context.pending_signal else "null",
            context=self.context.context_for_llm(),
            message=text.strip(),
        )

        raw = ""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip()
            # Strip markdown code fences if LLM added them
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"[LLM] JSON parse error: {e} | raw={raw!r}")
            if self._ollama_client:
                logger.warning("[LLM] Trying Ollama fallback after JSON parse error...")
                return self._parse_with_ollama(user_prompt, text)
            return ParsedSignal(intent="LLM_ERROR", notes=f"JSON parse error: {e}", raw_message=text)
        except Exception as e:
            logger.error(f"[LLM] Claude API error: {e}")
            if self._ollama_client:
                logger.warning("[LLM] Trying Ollama fallback after API error...")
                return self._parse_with_ollama(user_prompt, text)
            return ParsedSignal(intent="LLM_ERROR", notes=f"Claude API error: {e}", raw_message=text)

        # Validate intent
        intent = data.get("intent", "NOISE")
        if intent not in INTENTS:
            intent = "NOISE"

        signal = ParsedSignal(
            intent=intent,
            confidence=float(data.get("confidence", 0.5)),
            instrument=data.get("instrument"),
            strike=str(data.get("strike")) if data.get("strike") else None,
            ce_pe=data.get("ce_pe"),
            strategy=data.get("strategy"),
            entry_low=_int_or_none(data.get("entry_low")),
            entry_high=_int_or_none(data.get("entry_high")),
            targets=[int(t) for t in data.get("targets", []) if _int_or_none(t)],
            sl=_int_or_none(data.get("sl")),
            sl_deferred=bool(data.get("sl_deferred", False)),
            sl_at_cost=bool(data.get("sl_at_cost", False)),
            wait_for_price=bool(data.get("wait_for_price", True)),
            notes=data.get("notes", ""),
            raw_message=text,
        )

        # BREAKOUT entries never carry an explicit SL — convention is entry - 15
        if signal.sl_deferred and signal.strategy == "BREAKOUT" and signal.entry_low is not None:
            signal.sl = signal.entry_low - 15
            signal.sl_deferred = False
            logger.info(f"[LLM] BREAKOUT with no SL — applying default SL = entry({signal.entry_low}) - 15 = {signal.sl}")

        # BREAKOUT addon messages rarely repeat targets — inherit from the active range trade
        if (signal.intent == "NEW_SIGNAL" and signal.strategy == "BREAKOUT"
                and not signal.targets and self.context.active_signal):
            ctx_targets = self.context.active_signal.get("targets", [])
            if ctx_targets:
                signal.targets = list(ctx_targets)
                logger.info(f"[LLM] BREAKOUT with no targets — inheriting from active signal: {ctx_targets}")

        logger.info(f"[LLM] msg={msg_id} → {signal.summary()}")
        return signal

    def _parse_with_ollama(self, user_prompt: str, text: str) -> "ParsedSignal":
        """Fallback parser using local Ollama (OpenAI-compatible API).

        Returns LLM_ERROR if Ollama also fails, so the caller always gets
        a non-NOISE result that triggers the SOS alert in telegram_BOT.py.
        """
        raw = ""
        try:
            response = self._ollama_client.chat.completions.create(
                model=self._ollama_model,
                max_tokens=512,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
            )
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
            data = json.loads(raw)
            intent = data.get("intent", "NOISE")
            if intent not in INTENTS:
                intent = "NOISE"
            signal = ParsedSignal(
                intent=intent,
                confidence=float(data.get("confidence", 0.4)),
                instrument=data.get("instrument"),
                strike=str(data.get("strike")) if data.get("strike") else None,
                ce_pe=data.get("ce_pe"),
                strategy=data.get("strategy"),
                entry_low=_int_or_none(data.get("entry_low")),
                entry_high=_int_or_none(data.get("entry_high")),
                targets=[int(t) for t in data.get("targets", []) if _int_or_none(t)],
                sl=_int_or_none(data.get("sl")),
                sl_deferred=bool(data.get("sl_deferred", False)),
                sl_at_cost=bool(data.get("sl_at_cost", False)),
                wait_for_price=bool(data.get("wait_for_price", True)),
                notes=f"[OLLAMA:{self._ollama_model}] " + data.get("notes", ""),
                raw_message=text,
            )
            logger.warning(f"[LLM] Ollama fallback succeeded → {signal.summary()}")
            return signal
        except Exception as e:
            logger.error(f"[LLM] Ollama fallback also failed: {e} | raw={raw!r}")
            return ParsedSignal(
                intent="LLM_ERROR",
                notes=f"Claude API down AND Ollama failed: {e}",
                raw_message=text,
            )

    def signal_fired(self, signal: ParsedSignal, msg_id: int = None):
        """Call this after SOT_BOT is triggered so context tracks active trade.

        Pass msg_id (the Telegram message ID of the signal) to enable reply-chain
        lookup — future follow-up messages that reply to this message can be
        resolved exactly without relying on context inference.
        """
        self.context.set_active(signal)
        if msg_id is not None:
            self.context.store_signal(msg_id, signal)

    def get_by_msg_id(self, msg_id: int):
        """Return the fired signal whose Telegram message ID matches msg_id.

        Returns None if that message was never stored (e.g. it was a standalone
        message, not a signal we fired on).
        """
        return self.context.get_signal_by_id(msg_id)

    def signal_pending(self, signal: ParsedSignal):
        """Call this when a signal with deferred SL is held."""
        self.context.set_pending(signal)

    def signal_resolved(self, sl: int):
        """SL arrived — complete the pending signal."""
        if self.context.pending_signal:
            self.context.pending_signal["sl"] = sl
            self.context.pending_signal["sl_deferred"] = False
            resolved = _dict_to_signal(self.context.pending_signal)
            self.context.clear_pending()
            return resolved
        return None

    def signal_closed(self):
        """Call when active trade is exited."""
        self.context.clear_active()

    def get_pending(self) -> Optional[ParsedSignal]:
        if self.context.pending_signal:
            return _dict_to_signal(self.context.pending_signal)
        return None

    def get_active(self) -> Optional[ParsedSignal]:
        if self.context.active_signal:
            return _dict_to_signal(self.context.active_signal)
        return None

    def get_best_reference(self, hint: "ParsedSignal" = None) -> Optional["ParsedSignal"]:
        """Best reference signal for a REENTER/follow-up with no explicit reply-chain.

        Scans ALL signals fired today (signal_store), not just the current active one.
        If hint carries instrument/strike/ce_pe, filters to matching signals first.
        Among candidates, returns the most recent (highest msg_id).
        Falls back to get_active() when the store is empty.

        This handles the "5 trades, 2 hit SL, 6th is a re-entry" case correctly —
        the re-entry intent can match any of the day's trades, not just the last one.
        """
        store = self.context.signal_store
        if not store:
            return self.get_active()

        # Sort by msg_id descending so most recent is first
        pairs = sorted(store.items(), key=lambda x: x[0], reverse=True)

        # Filter by instrument/strike/ce_pe if hint provides any of them
        if hint and any([hint.instrument, hint.strike, hint.ce_pe]):
            matched = [
                (mid, d) for mid, d in pairs
                if (hint.instrument is None or d.get("instrument") == hint.instrument)
                and (hint.strike is None or str(d.get("strike", "")) == str(hint.strike or ""))
                and (hint.ce_pe is None or d.get("ce_pe") == hint.ce_pe)
            ]
            if matched:
                pairs = matched
            # If nothing matched the filters, fall through and use all signals

        _, best_dict = pairs[0]
        best = _dict_to_signal(best_dict)
        logger.info(
            f"[LLM] get_best_reference: store has {len(store)} signal(s) today, "
            f"selected most recent match → {best.summary()}"
        )
        return best


# ── Helpers ───────────────────────────────────────────────────────────────────

def _int_or_none(val) -> Optional[int]:
    try:
        return int(val) if val is not None else None
    except (ValueError, TypeError):
        return None


def _dict_to_signal(d: dict) -> ParsedSignal:
    d2 = {k: v for k, v in d.items() if k in ParsedSignal.__dataclass_fields__}
    return ParsedSignal(**d2)
