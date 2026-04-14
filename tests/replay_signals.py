"""
tests/replay_signals.py
───────────────────────────────────────────────────────────────────────────────
Replay real Telegram message dumps through the LLM signal parser to verify
intent classification, reply-chain resolution, and context reconstruction.

Usage:
    # Replay one day (real LLM calls):
    python tests/replay_signals.py --date 2026-04-06

    # Replay without spending API credits (dry-run heuristic classifier):
    python tests/replay_signals.py --date 2026-04-06 --dry-run

    # Replay a date range:
    python tests/replay_signals.py --from 2026-04-06 --to 2026-04-07

    # Run against a specific channel:
    python tests/replay_signals.py --date 2026-04-06 --channel sot_trial_channel

    # Load an expected-outcomes file and assert matches:
    python tests/replay_signals.py --date 2026-04-06 --expected tests/expected_2026-04-06.json

    # Write a skeleton expected file from this replay (for future regression):
    python tests/replay_signals.py --date 2026-04-06 --dry-run --save-expected tests/expected_2026-04-06.json

Expected file format (JSON):
    {
        "99008": {"intent": "NEW_SIGNAL", "instrument": "NIFTY"},
        "99010": {"intent": "NOISE"},
        "99042": {"intent": "UPDATE_SL", "sl_at_cost": true}
    }
    Keys are message IDs as strings.  Only listed fields are checked.

Environment:
    ANTHROPIC_API_KEY  must be set for real LLM mode.
    Set in ~/.aliases or utils/credentials.py.
"""

import sys
import os
import json
import time
import argparse
import re
from datetime import datetime, timezone

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Stub heavy deps only in dry-run mode (resolved later after arg parse)
# For real mode we need real imports, so we stub lazily.

# ── ANSI colours ──────────────────────────────────────────────────────────────
_USE_COLOUR = sys.stdout.isatty()
def _c(code, text): return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text
GREEN  = lambda t: _c("32", t)
YELLOW = lambda t: _c("33", t)
RED    = lambda t: _c("31", t)
CYAN   = lambda t: _c("36", t)
GREY   = lambda t: _c("90", t)
BOLD   = lambda t: _c("1",  t)


# ── Dry-run heuristic classifier (no API cost) ────────────────────────────────

_SIGNAL_RE  = re.compile(
    r'(nifty|banknifty|finnifty|midcpnifty|bajfinance|sensex)\s+\d+\s+(ce|pe)',
    re.IGNORECASE
)
_SL_UPD_RE  = re.compile(
    r'\bsl\b.{0,20}\d+|\bshift\s+sl\b|\bkeep\s+sl\b|sl\s+at\s+cost', re.IGNORECASE
)
_REENTER_RE = re.compile(r're[\s-]?enter|re-entry|add more|next entry', re.IGNORECASE)
_CANCEL_RE  = re.compile(r'ignore|cancel|don\'?t take|avoid this', re.IGNORECASE)
_EXIT_RE    = re.compile(r'\bexit\b|\bclose all\b|\bsquare off\b|\bbook all\b', re.IGNORECASE)
_SL_COST_RE = re.compile(r'sl\s+at\s+cost|keep\s+sl\s+at\s+cost|sl\s+cost', re.IGNORECASE)

def _heuristic_classify(text: str) -> dict:
    """Fast rule-based intent guess — no API required."""
    from utils.llm_signal_parser import is_noise
    if is_noise(text):
        return {"intent": "NOISE", "confidence": 1.0, "sl_at_cost": False, "sl": None}

    if _SIGNAL_RE.search(text):
        has_sl = bool(re.search(r'\bsl\b', text, re.IGNORECASE))
        deferred = bool(re.search(r'sl\s*[-–]\s*(i will|will)\s+update', text, re.IGNORECASE))
        if not has_sl or deferred:
            deferred = True
        return {"intent": "NEW_SIGNAL", "confidence": 0.80,
                "sl_deferred": deferred, "sl_at_cost": False, "sl": None}

    if _REENTER_RE.search(text):
        return {"intent": "REENTER", "confidence": 0.75, "sl_at_cost": False, "sl": None}

    if _CANCEL_RE.search(text):
        return {"intent": "CANCEL", "confidence": 0.80, "sl_at_cost": False, "sl": None}

    if _SL_COST_RE.search(text):
        m = re.search(r'\d+', text)
        return {"intent": "UPDATE_SL", "confidence": 0.85,
                "sl_at_cost": True, "sl": int(m.group()) if m else None}

    if _SL_UPD_RE.search(text):
        m = re.search(r'\b(\d{2,4})\b', text)
        return {"intent": "UPDATE_SL", "confidence": 0.75,
                "sl_at_cost": False, "sl": int(m.group()) if m else None}

    if _EXIT_RE.search(text):
        m = re.search(r'\b(\d{2,4})\b', text)
        if m:
            return {"intent": "UPDATE_SL", "confidence": 0.70, "sl_at_cost": False, "sl": int(m.group())}
        return {"intent": "FULL_EXIT", "confidence": 0.70, "sl_at_cost": False, "sl": None}

    return {"intent": "NOISE", "confidence": 0.50, "sl_at_cost": False, "sl": None}


# ── Simulated signal state (mirrors handle_llm_intent without firing SOT_BOT) ─

class SimulatedHandler:
    """Mirrors handle_llm_intent state transitions without touching the bot."""

    def __init__(self, parser):
        self.parser = parser
        self.events = []        # log of (msg_id, event_type, detail)

    def process(self, msg_id: int, signal, reply_to_msg_id=None):
        intent = signal.intent

        def ref():
            if reply_to_msg_id:
                r = self.parser.get_by_msg_id(reply_to_msg_id)
                if r:
                    return r, f"reply→{reply_to_msg_id}"
            a = self.parser.get_active()
            return a, "active"

        if intent == "NEW_SIGNAL":
            if signal.sl_deferred:
                self.parser.signal_pending(signal)
                self.events.append((msg_id, "PENDING", signal.summary()))
            elif signal.is_actionable():
                self.parser.signal_fired(signal, msg_id=msg_id)
                self.events.append((msg_id, "FIRED", signal.summary()))
            else:
                self.events.append((msg_id, "INCOMPLETE", signal.summary()))

        elif intent == "SL_RESOLVED":
            resolved = self.parser.signal_resolved(signal.sl)
            if resolved and resolved.is_actionable():
                self.parser.signal_fired(resolved, msg_id=msg_id)
                self.events.append((msg_id, "FIRED(resolved)", resolved.summary()))

        elif intent == "REENTER":
            reference, ref_src = ref()
            if reference and reference.is_actionable():
                merged = reference
                if signal.entry_high is not None:
                    merged.entry_low  = signal.entry_low or signal.entry_high
                    merged.entry_high = signal.entry_high
                if signal.strategy is not None:
                    merged.strategy = signal.strategy
                if signal.sl is not None:
                    merged.sl = signal.sl
                if signal.targets:
                    merged.targets = signal.targets
                self.parser.signal_fired(merged, msg_id=msg_id)
                self.events.append((msg_id, f"REENTER({ref_src})", merged.summary()))
            else:
                self.events.append((msg_id, "REENTER(no ref)", "⚠️ no reference signal"))

        elif intent == "UPDATE_SL":
            reference, ref_src = ref()
            label = f"SL={signal.sl}" + (" sl@cost" if signal.sl_at_cost else "")
            self.events.append((msg_id, f"UPDATE_SL({ref_src})", label))

        elif intent == "CANCEL":
            pending = self.parser.get_pending()
            self.events.append((msg_id, "CANCEL", pending.summary() if pending else "no pending"))
            self.parser.context.clear_pending()

        elif intent in ("PARTIAL_EXIT", "FULL_EXIT"):
            reference, ref_src = ref()
            self.events.append((msg_id, intent, reference.summary() if reference else "⚠️ no ref"))


# ── Replay engine ─────────────────────────────────────────────────────────────

def _load_messages(dump_path: str, channel: str, date_from: str, date_to: str):
    with open(dump_path) as f:
        data = json.load(f)
    msgs = data.get(channel, [])
    msgs = sorted(msgs, key=lambda m: m['id'])   # oldest first
    return [
        m for m in msgs
        if date_from <= m['date'][:10] <= date_to and m.get('text', '').strip()
    ]


def _msg_lookup(msgs: list) -> dict:
    return {m['id']: m for m in msgs}


def _intent_colour(intent: str) -> str:
    if intent == "NOISE":
        return GREY(intent)
    if intent == "NEW_SIGNAL":
        return GREEN(intent)
    if intent in ("REENTER", "SL_RESOLVED"):
        return CYAN(intent)
    if intent == "UPDATE_SL":
        return YELLOW(intent)
    if intent in ("FULL_EXIT", "PARTIAL_EXIT", "CANCEL"):
        return RED(intent)
    return intent


def replay(
    dump_path:      str,
    channel:        str     = "sot_channel",
    date_from:      str     = None,
    date_to:        str     = None,
    dry_run:        bool    = False,
    expected:       dict    = None,   # {msg_id_str: {field: value}}
    save_expected:  str     = None,   # path to write skeleton expected file
    api_sleep:      float   = 0.5,    # seconds between real API calls
    verbose:        bool    = False,
):
    today = datetime.now().strftime("%Y-%m-%d")
    date_from = date_from or today
    date_to   = date_to   or date_from

    msgs = _load_messages(dump_path, channel, date_from, date_to)
    lookup = _msg_lookup(msgs)
    if not msgs:
        print(RED(f"No messages found for {channel} {date_from}..{date_to}"))
        return

    # ── set up parser ─────────────────────────────────────────────────────────
    if dry_run:
        # Stub anthropic so the parser loads without a real client
        import unittest.mock as _mock, types as _types
        sys.modules.setdefault('anthropic', _mock.MagicMock())
        _creds = _types.ModuleType('utils.credentials')
        _creds.ANTHROPIC_API_KEY = 'dry-run-key'
        sys.modules['utils.credentials'] = _creds

    from utils.llm_signal_parser import LLMSignalParser, is_noise, ParsedSignal

    if dry_run:
        # Monkey-patch parse() to use heuristic classifier
        def _dry_parse(self_p, text, msg_id=0, is_edit=False):
            if is_noise(text):
                return ParsedSignal(intent="NOISE", raw_message=text)
            self_p.context.add_message(text, msg_id, is_edit)
            data = _heuristic_classify(text)
            intent = data["intent"]
            # Extract fields naively for NEW_SIGNAL
            targets, entry_low, entry_high, sl, instrument, strike, ce_pe, strategy = \
                [], None, None, None, None, None, None, None
            if intent == "NEW_SIGNAL":
                m = _SIGNAL_RE.search(text)
                if m:
                    instrument = m.group(1).upper()
                    ce_pe      = m.group(2).upper()
                    sm = re.search(r'(nifty|banknifty|finnifty|midcpnifty|bajfinance|sensex)\s+(\d+)', text, re.IGNORECASE)
                    if sm:
                        strike = sm.group(2)
                em = re.search(r'(?:near|above|at)\s+(\d+)[-–]?(\d*)', text, re.IGNORECASE)
                if em:
                    entry_low  = int(em.group(1))
                    entry_high = int(em.group(2)) if em.group(2) else entry_low
                    strategy   = "BREAKOUT" if re.search(r'\babove\b', text, re.I) else "RANGE"
                for t in re.findall(r'\b(\d{2,4})\b', re.sub(r'.*?(?:target|tgt)', '', text, flags=re.I)):
                    targets.append(int(t))
                sm2 = re.search(r'\bsl\s+(\d+)', text, re.IGNORECASE)
                if sm2:
                    sl = int(sm2.group(1))
            return ParsedSignal(
                intent=intent,
                confidence=float(data.get("confidence", 0.6)),
                instrument=instrument,
                strike=str(strike) if strike else None,
                ce_pe=ce_pe,
                strategy=strategy,
                entry_low=entry_low,
                entry_high=entry_high,
                targets=targets[:6],
                sl=data.get("sl") or sl,
                sl_deferred=bool(data.get("sl_deferred", False)),
                sl_at_cost=bool(data.get("sl_at_cost", False)),
                wait_for_price=bool(re.search(r'wait for price', text, re.I)),
                raw_message=text,
            )
        LLMSignalParser.parse = _dry_parse
        parser = LLMSignalParser(api_key='dry-run-key')
    else:
        parser = LLMSignalParser()

    handler  = SimulatedHandler(parser)
    failures = []
    skeleton = {}   # for --save-expected
    counts   = {"NOISE": 0, "signal": 0, "other": 0, "fail": 0}

    # ── header ────────────────────────────────────────────────────────────────
    mode_label = YELLOW("DRY-RUN (heuristic)") if dry_run else GREEN("REAL LLM")
    print()
    print(BOLD("═" * 70))
    print(BOLD(f"  Replay  {date_from}..{date_to}  │  {channel}  │  {len(msgs)} msgs  │  {mode_label}"))
    print(BOLD("═" * 70))

    for msg in msgs:
        mid          = msg['id']
        time_str     = msg['date'][11:16]
        text         = msg.get('text', '').strip()
        is_edit      = bool(msg.get('edited'))
        reply_to     = msg.get('reply_to')

        # Resolve reply parent text for display
        parent_text  = ""
        if reply_to and reply_to in lookup:
            parent_text = lookup[reply_to]['text'][:50].replace('\n', ' ')

        # Parse
        if dry_run:
            signal = parser.parse(text, msg_id=mid, is_edit=is_edit)
        else:
            signal = parser.parse(text, msg_id=mid, is_edit=is_edit)
            if signal.intent != "NOISE":
                time.sleep(api_sleep)

        # State transitions
        handler.process(mid, signal, reply_to_msg_id=reply_to)

        # ── display ───────────────────────────────────────────────────────────
        if signal.intent == "NOISE" and not verbose:
            counts["NOISE"] += 1
            continue

        edit_mark = YELLOW(" [EDITED]") if is_edit else ""
        reply_mark = ""
        if reply_to:
            ref_sig = parser.get_by_msg_id(reply_to)
            if ref_sig:
                reply_mark = CYAN(f" [reply→{reply_to}: {ref_sig.instrument} {ref_sig.strike} {ref_sig.ce_pe}]")
            else:
                reply_mark = GREY(f" [reply→{reply_to}: {parent_text!r}]")

        intent_str = _intent_colour(signal.intent)
        conf_str   = f" ({signal.confidence:.0%})" if signal.intent != "NOISE" else ""
        print(f"\n{BOLD(f'[{mid}]')} {time_str}{edit_mark}{reply_mark}")
        print(f"  {intent_str}{conf_str}")
        print(f"  {GREY(repr(text[:80]))}")
        if signal.intent not in ("NOISE",):
            print(f"  {signal.summary()}")

        # Instrument inference note
        if signal.intent == "NEW_SIGNAL" and signal.instrument is None:
            active = parser.get_active()
            if active and active.entry_high is not None and signal.entry_high is not None:
                diff = abs(signal.entry_high - active.entry_high)
                if diff <= 150:
                    print(f"  {YELLOW(f'⚑ instrument=None, active={active.instrument} {active.strike} {active.ce_pe}, diff={diff} → will inherit')}")
                else:
                    print(f"  {RED(f'⚑ instrument=None, active diff={diff} > 150 → no inference')}")
            else:
                print(f"  {RED('⚑ instrument=None and no active signal for inference')}")

        counts["signal" if signal.intent == "NEW_SIGNAL" else "other"] += 1

        # Skeleton for --save-expected
        skeleton[str(mid)] = {
            "intent": signal.intent,
            **({"instrument": signal.instrument} if signal.instrument else {}),
            **({"sl_at_cost": True}              if signal.sl_at_cost  else {}),
            **({"sl_deferred": True}             if signal.sl_deferred else {}),
            **({"sl": signal.sl}                 if signal.sl          else {}),
        }

        # ── assertion ─────────────────────────────────────────────────────────
        if expected and str(mid) in expected:
            exp = expected[str(mid)]
            for field, exp_val in exp.items():
                got_val = getattr(signal, field, None)
                if got_val != exp_val:
                    failures.append({
                        "msg_id": mid, "field": field,
                        "expected": exp_val, "got": got_val,
                        "text": text[:60],
                    })
                    counts["fail"] += 1
                    print(f"  {RED(f'✗ FAIL [{field}]: expected={exp_val!r}  got={got_val!r}')}")
                else:
                    print(f"  {GREEN(f'✓ {field}={got_val!r}')}")

    # ── summary ───────────────────────────────────────────────────────────────
    print()
    print(BOLD("─" * 70))
    noise_note = f"  ({counts['NOISE']} NOISE skipped)" if not verbose else ""
    print(f"  Signals: {counts['signal']}  |  Other intents: {counts['other']}  |  Noise: {counts['NOISE']}{noise_note}")
    if expected:
        if failures:
            print(RED(f"  FAILURES: {counts['fail']}"))
            for f in failures:
                print(RED(f"    [{f['msg_id']}] {f['field']}: expected={f['expected']!r} got={f['got']!r}"))
        else:
            print(GREEN("  All assertions passed ✓"))
    print(BOLD("─" * 70))

    if save_expected:
        with open(save_expected, 'w') as f:
            json.dump(skeleton, f, indent=2)
        print(f"\n  Expected skeleton saved to: {CYAN(save_expected)}")
        print("  Edit it to add/remove assertions, then run with --expected to enforce.\n")

    return failures


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Replay Telegram message dump through LLM signal parser",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument('--dump',    default='channel_messages_raw.json',
                        help='Path to message dump JSON (default: channel_messages_raw.json)')
    parser.add_argument('--channel', default='sot_channel',
                        help='Channel key in dump (default: sot_channel)')
    parser.add_argument('--date',    help='Single date to replay (YYYY-MM-DD)')
    parser.add_argument('--from',    dest='date_from', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--to',      dest='date_to',   help='End date (YYYY-MM-DD)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Use heuristic classifier, no API calls')
    parser.add_argument('--expected', help='JSON file with expected outcomes per msg_id')
    parser.add_argument('--save-expected', dest='save_expected',
                        help='Write skeleton expected file from this replay')
    parser.add_argument('--sleep',   type=float, default=0.5,
                        help='Seconds between API calls (default: 0.5)')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Show NOISE messages too')
    args = parser.parse_args()

    date_from = args.date_from or args.date
    date_to   = args.date_to   or args.date

    expected = None
    if args.expected:
        with open(args.expected) as f:
            expected = json.load(f)

    dump_path = args.dump
    if not os.path.isabs(dump_path):
        # Try relative to project root
        candidate = os.path.join(ROOT, dump_path)
        if os.path.exists(candidate):
            dump_path = candidate

    failures = replay(
        dump_path     = dump_path,
        channel       = args.channel,
        date_from     = date_from,
        date_to       = date_to,
        dry_run       = args.dry_run,
        expected      = expected,
        save_expected = args.save_expected,
        api_sleep     = args.sleep,
        verbose       = args.verbose,
    )

    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
