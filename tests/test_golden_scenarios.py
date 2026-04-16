"""
Golden scenario tests — REAL Anthropic API calls, no mocking.

Replays the exact mentor message sequences from Apr 15 and Apr 16
through the live LLM parser and checks that every message is classified
correctly.  Run this before each trading day to catch LLM drift or
prompt regressions before they hit production.

Usage:
    python tests/test_golden_scenarios.py            # run all
    python tests/test_golden_scenarios.py -v         # verbose (show all passes too)
    python tests/test_golden_scenarios.py Apr15      # one session only

Requires ANTHROPIC_API_KEY in environment (or ~/.aliases).
Cost: ~20 LLM calls ≈ a few paise.
"""

import os
import sys
import time
import argparse
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Load API key from .aliases if not in environment ─────────────────────────
if not os.environ.get("ANTHROPIC_API_KEY"):
    aliases_path = os.path.expanduser("~/.aliases")
    if os.path.exists(aliases_path):
        with open(aliases_path) as f:
            for line in f:
                if "ANTHROPIC_API_KEY" in line and "export" in line:
                    key = line.split("=", 1)[-1].strip().strip('"')
                    os.environ["ANTHROPIC_API_KEY"] = key
                    break

import types as _types
_creds = _types.ModuleType("utils.credentials")
_creds.ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
sys.modules["utils.credentials"] = _creds

from utils.llm_signal_parser import LLMSignalParser, ParsedSignal

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

# ── Expected outcome spec ─────────────────────────────────────────────────────
# Each entry: (raw_message, expected_intent, extra_checks_dict, note)
#
# extra_checks keys (all optional):
#   instrument, strike, ce_pe, strategy, sl, entry_low, entry_high,
#   sl_deferred, sl_at_cost, actionable, targets_min_len

def _sig(msg, intent, note="", **checks):
    return {"msg": msg, "intent": intent, "note": note, "checks": checks}

# ── Apr 15 session ────────────────────────────────────────────────────────────
# Context: SENSEX 78500 PE range trade.  Mentor gave signal, it ran, then came
# back for a re-entry.  Key edge cases:
#   - "Book 2 lots at 25 points"  → PARTIAL_EXIT
#   - "Shift sl to 530"           → UPDATE_SL
#   - "Take exit at 530 level"    → UPDATE_SL (price-based exit level)
#   - "next entry we will take above 580 level" → NEW_SIGNAL BREAKOUT

APR15 = [
    _sig(
        "Sensex 78500 pe near 530-540\nTarget 560/580/600/630+\nSl 515",
        "NEW_SIGNAL",
        "Range signal, first trade of the day",
        instrument="SENSEX", strike="78500", ce_pe="PE",
        strategy="RANGE", entry_low=530, entry_high=540, sl=515,
        actionable=True,
    ),
    _sig("Level active",           "NOISE", "Price commentary"),
    _sig(
        "Book 2 lots at 25 points", "PARTIAL_EXIT",
        "Partial exit — 2 lots at 25 pt gain",
    ),
    _sig("Target 1 done",          "NOISE",  "Price update / commentary"),
    _sig(
        "Shift sl to 530",          "UPDATE_SL",
        "Trail SL up",
        sl=530,
    ),
    _sig(
        "Take exit at 530 level",   "UPDATE_SL",
        "Price-based exit → UPDATE_SL not FULL_EXIT",
        sl=530,
    ),
    _sig(
        "next entry we will take above 580 level",
        "NEW_SIGNAL",
        "Breakout entry above 580 — no explicit SL → default 15 pts",
        instrument="SENSEX", strike="78500", ce_pe="PE",
        strategy="BREAKOUT", entry_low=580,
        sl=565,           # 580 - 15 default
        actionable=True,
    ),
    _sig("perfect entry",          "NOISE",  "Congratulatory"),
    _sig("No movement right now",  "NOISE",  "Market commentary"),
    _sig("just consolidating",     "NOISE",  "Market commentary"),
    _sig(
        "Sensex 78500 pe near 490-500\nTarget 520/540/570/600+\nSl 475 \n\n(Wait for price)",
        "NEW_SIGNAL",
        "Second range signal same day",
        instrument="SENSEX", strike="78500", ce_pe="PE",
        strategy="RANGE", entry_low=490, entry_high=500, sl=475,
        actionable=True,
    ),
]

# ── Apr 16 session ────────────────────────────────────────────────────────────
# Context: SENSEX 78800 PE range trade, then breakout addon, then CE trade.
# Key edge cases:
#   - "Take one entry above 560 also" → NEW_SIGNAL BREAKOUT (sl default 545)
#   - "Book 4 lot total"              → PARTIAL_EXIT (was NOISE in live — known bug)
#   - "Sl at cost"                    → UPDATE_SL sl_at_cost=True
#   - "Those who have more shift sl.."→ UPDATE_SL with new SL + targets
#   - "From this zone market gave.."  → REENTER or NOISE (ambiguous)

APR16 = [
    _sig(
        "Sensex 78800 pe near 440-450\nTarget 470/500/530/550+\nSl 425 \n\n(Wait for price)",
        "NEW_SIGNAL",
        "Range signal, first trade of the day",
        instrument="SENSEX", strike="78800", ce_pe="PE",
        strategy="RANGE", entry_low=440, entry_high=450, sl=425,
        actionable=True,
    ),
    _sig(
        "Take one entry above 560 also \nMarket seems bearish",
        "NEW_SIGNAL",
        "Breakout addon — no SL → default 15 pts applied",
        instrument="SENSEX", strike="78800", ce_pe="PE",
        strategy="BREAKOUT", entry_low=560,
        sl=545,
        sl_deferred=False,
        actionable=True,
    ),
    _sig(
        "Book 2 lot at 580",        "PARTIAL_EXIT",
        "Partial exit 2 lots",
    ),
    _sig(
        "Book 4 lot total",         "PARTIAL_EXIT",
        "Was misclassified as NOISE in live on Apr 16 — should be PARTIAL_EXIT",
    ),
    _sig(
        "Sl at cost",               "UPDATE_SL",
        "Trail SL to break-even",
        sl_at_cost=True,
    ),
    _sig("Book 5 lot",              "PARTIAL_EXIT", "Partial exit 5 lots"),
    _sig(
        "Book all 6 lot at 100 points", "PARTIAL_EXIT",
        "Book all at 100 pt profit",
    ),
    _sig(
        "Those who have more shift sl at 600 and hold for 720/800",
        "UPDATE_SL",
        "Conditional SL update",
        sl=600,
    ),
    _sig("Perfect morning trade done",  "NOISE", "End-of-trade commentary"),
    _sig("Hope all booked decent profits", "NOISE", "Commentary"),
    _sig(
        "From this zone market gave good upward move so we will take small risk",
        "REENTER",
        "Re-entry hint after morning trade closed",
    ),
    _sig(
        "Sensex 78000 ce near 180-190\nTarget 210/230/250/280+\nSl 165",
        "NEW_SIGNAL",
        "New CE trade after PE session closed",
        instrument="SENSEX", strike="78000", ce_pe="CE",
        strategy="RANGE", entry_low=180, entry_high=190, sl=165,
        actionable=True,
    ),
]

SESSIONS = {
    "Apr15": APR15,
    "Apr16": APR16,
}


# ── Checker ───────────────────────────────────────────────────────────────────

def _check(sig: ParsedSignal, spec: dict) -> list[str]:
    """Return list of failure strings, empty = pass."""
    failures = []
    checks = spec["checks"]

    def f(field, expected, actual):
        if actual != expected:
            failures.append(f"  {field}: expected {expected!r}, got {actual!r}")

    if "instrument" in checks:
        f("instrument", checks["instrument"], sig.instrument)
    if "strike" in checks:
        f("strike", str(checks["strike"]), sig.strike)
    if "ce_pe" in checks:
        f("ce_pe", checks["ce_pe"], sig.ce_pe)
    if "strategy" in checks:
        f("strategy", checks["strategy"], sig.strategy)
    if "entry_low" in checks:
        f("entry_low", checks["entry_low"], sig.entry_low)
    if "entry_high" in checks:
        f("entry_high", checks["entry_high"], sig.entry_high)
    if "sl" in checks:
        f("sl", checks["sl"], sig.sl)
    if "sl_deferred" in checks:
        f("sl_deferred", checks["sl_deferred"], sig.sl_deferred)
    if "sl_at_cost" in checks:
        f("sl_at_cost", checks["sl_at_cost"], sig.sl_at_cost)
    if "actionable" in checks:
        f("actionable", checks["actionable"], sig.is_actionable())
    if "targets_min_len" in checks:
        if len(sig.targets) < checks["targets_min_len"]:
            failures.append(f"  targets: expected >={checks['targets_min_len']}, got {len(sig.targets)}")

    return failures


def _sot_command(sig: ParsedSignal) -> str:
    """Build a rough SOT_BOT command string from a NEW_SIGNAL for display."""
    if sig.intent != "NEW_SIGNAL" or not sig.is_actionable():
        return ""
    e_hi = sig.entry_high or 0
    e_lo = sig.entry_low if sig.entry_low != sig.entry_high else None
    t = sig.targets
    t1 = t[0] if len(t) > 0 else "?"
    t2 = t[1] if len(t) > 1 else "?"
    t3 = t[-1] if len(t) > 2 else "?"
    bo = sig.strategy == "BREAKOUT"
    cmd = (
        f"SOT_BOTv8.py -i={sig.instrument} -s={sig.strike} -cepe={sig.ce_pe}"
        f" -bo={not bo} -e={e_hi} -t1={t1} -t2={t2} -t3={t3} -sl={sig.sl}"
        f" -efpa=False -oca={bo}"
    )
    if e_lo:
        cmd += f" -e2={e_lo}"
    return cmd


# ── Runner ────────────────────────────────────────────────────────────────────

def run_session(name: str, specs: list, verbose: bool) -> tuple[int, int]:
    """Run one session. Returns (passed, failed)."""
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  Session: {name}  ({len(specs)} messages){RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")

    parser = LLMSignalParser(api_key=os.environ["ANTHROPIC_API_KEY"])

    passed = failed = 0

    for i, spec in enumerate(specs, 1):
        msg      = spec["msg"]
        expected = spec["intent"]
        note     = spec["note"]
        short    = msg.replace("\n", " / ")[:60]

        sig = parser.parse(msg, msg_id=i)
        time.sleep(0.3)   # gentle rate-limit

        # Mirror real bot lifecycle: mark signal as active so subsequent messages
        # (BREAKOUT addons, UPDATE_SL, PARTIAL_EXIT) have the correct active context.
        if sig.intent == "NEW_SIGNAL" and sig.is_actionable():
            parser.signal_fired(sig, msg_id=i)
        elif sig.intent in ("FULL_EXIT",):
            parser.signal_closed()

        intent_ok   = sig.intent == expected
        field_fails = _check(sig, spec)
        ok          = intent_ok and not field_fails

        if ok:
            passed += 1
            if verbose:
                print(f"  {GREEN}✓{RESET} [{i:02d}] {short}")
                print(f"       → {sig.summary()}")
        else:
            failed += 1
            print(f"  {RED}✗{RESET} [{i:02d}] {short}")
            if note:
                print(f"       {YELLOW}note:{RESET} {note}")
            if not intent_ok:
                print(f"       {RED}intent:{RESET} expected {expected!r}, got {sig.intent!r}")
            for fl in field_fails:
                print(f"       {RED}field:{RESET}{fl}")
            print(f"       → {sig.summary()}")

        # Always show the command for NEW_SIGNAL so you can eyeball it
        if sig.intent == "NEW_SIGNAL":
            cmd = _sot_command(sig)
            if cmd:
                label = f"{GREEN}cmd:{RESET}" if ok else f"{YELLOW}cmd:{RESET}"
                print(f"       {label} {cmd}")

    return passed, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", choices=list(SESSIONS), help="Run one session only")
    ap.add_argument("-v", "--verbose", action="store_true", help="Show passing tests too")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"{RED}ANTHROPIC_API_KEY not set. Source ~/.aliases or set the env var.{RESET}")
        sys.exit(1)

    to_run = {args.session: SESSIONS[args.session]} if args.session else SESSIONS

    total_pass = total_fail = 0
    for name, specs in to_run.items():
        p, f = run_session(name, specs, args.verbose)
        total_pass += p
        total_fail += f

    print(f"\n{BOLD}{'─'*60}{RESET}")
    colour = GREEN if total_fail == 0 else RED
    print(f"{colour}{BOLD}  {total_pass} passed  {total_fail} failed  "
          f"({total_pass+total_fail} total){RESET}")
    print()
    sys.exit(1 if total_fail else 0)


if __name__ == "__main__":
    main()
