"""
analyse_recent_signals.py
───────────────────────────────────────────────────────────────────────────────
Reads the already-fetched channel_messages_raw.json and produces an analysis
report filtered to the last N years (default: 2).

No Telegram fetching — runs instantly from the cached dump.

Usage:
    python analyse_recent_signals.py              # last 2 years
    python analyse_recent_signals.py --years 1    # last 1 year
"""

import re
import json
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta

# ── Config ───────────────────────────────────────────────────────────────────
RAW_FILE    = "channel_messages_raw.json"
REPORT_FILE = "recent_signals_analysis.txt"

# ── Regex mirrors (same as analyse_channel_history.py) ───────────────────────
BUY_RE         = re.compile(r'buy.*|Bank.*|Nif.*|Fin.*|Baj.*|Nau.*|Sen.*', re.IGNORECASE)
TARGET_RE      = re.compile(r'Traget.*|Target.*|Tatget.*|Taget.*|Taret.*|TARGWT.*|Targst.*', re.IGNORECASE)
SL_RE          = re.compile(r'SL.*', re.IGNORECASE)
BREAKOUT_RE    = re.compile(r'\b(above|avove|abv|ave|abve)\b', re.IGNORECASE)
NEAR_RE        = re.compile(r'\bnear\b', re.IGNORECASE)
INSTRUMENT_RE  = re.compile(
    r'\b(BANKNIFTY|BANK\s*NIFTY|NIFTY|FINNIFTY|FIN\s*NIFTY|MIDCPNIFTY|BAJFINANCE|SENSEX)\b',
    re.IGNORECASE
)
TYPO_TARGET_RE = re.compile(r'\b(Traget|Tatget|Taget|Taret|TARGWT|Targst)\b', re.IGNORECASE)
TYPO_ABOVE_RE  = re.compile(r'\b(avove|abv|ave|abve)\b', re.IGNORECASE)
TARGETS_NUM_RE = re.compile(r'(\d{2,4})')
SL_VALUE_RE    = re.compile(r'sl[\s\-:]*(\d+)', re.IGNORECASE)
ENTRY_RE       = re.compile(r'(\d{2,4})\s*[-–]\s*(\d{2,4})')

def normalise_instrument(raw: str) -> str:
    r = raw.upper().replace(" ", "")
    if "BANK" in r:   return "BANKNIFTY"
    if "FIN" in r:    return "FINNIFTY"
    if "MID" in r:    return "MIDCPNIFTY"
    if "BAJ" in r:    return "BAJFINANCE"
    if "SENSEX" in r: return "SENSEX"
    if "NIF" in r:    return "NIFTY"
    return r

def classify_message(text: str):
    has_buy     = bool(BUY_RE.search(text))
    has_target  = bool(TARGET_RE.search(text))
    has_sl      = bool(SL_RE.search(text))
    is_breakout = bool(BREAKOUT_RE.search(text))
    is_near     = bool(NEAR_RE.search(text))

    m = INSTRUMENT_RE.search(text)
    instrument = normalise_instrument(m.group()) if m else None

    ce_pe = None
    if re.search(r'\bCE\b', text, re.IGNORECASE): ce_pe = "CE"
    elif re.search(r'\bPE\b', text, re.IGNORECASE): ce_pe = "PE"

    valid = has_buy and has_target and has_sl and instrument and ce_pe

    rejection = None
    if not valid:
        if not instrument:   rejection = "NO_INSTRUMENT"
        elif not ce_pe:      rejection = "NO_CE_PE"
        elif not has_target: rejection = "NO_TARGET"
        elif not has_sl:     rejection = "NO_SL"
        elif not has_buy:    rejection = "NO_BUY_LINE"
        else:                rejection = "INCOMPLETE"

    # extract SL value
    sl_match = SL_VALUE_RE.search(text)
    sl_value = int(sl_match.group(1)) if sl_match else None

    # extract targets
    target_line = ""
    for line in text.splitlines():
        if TARGET_RE.search(line):
            target_line = line
            break
    targets = [int(x) for x in TARGETS_NUM_RE.findall(target_line)] if target_line else []

    # entry range
    entry_match = ENTRY_RE.search(text)
    entry_range = (int(entry_match.group(1)), int(entry_match.group(2))) if entry_match else None

    return {
        "valid":       valid,
        "rejection":   rejection,
        "instrument":  instrument,
        "ce_pe":       ce_pe,
        "is_breakout": is_breakout,
        "is_near":     is_near,
        "sl":          sl_value,
        "targets":     targets,
        "entry_range": entry_range,
        "typo_target": TYPO_TARGET_RE.findall(text),
        "typo_above":  TYPO_ABOVE_RE.findall(text),
        "has_level":   "LEVEL" in text.upper(),
        "has_hero":    "HERO" in text.upper(),
        "sl_deferred": bool(re.search(r'sl\s*[-–]\s*(i will|will share|will update|tbd|later)', text, re.IGNORECASE)),
        "wait_for_price": bool(re.search(r'wait for price', text, re.IGNORECASE)),
    }


def analyse(messages: list, channel_name: str, cutoff: datetime) -> dict:
    recent = [m for m in messages if datetime.fromisoformat(m["date"]) >= cutoff]
    total  = len(recent)

    valid_signals    = []
    rejected         = []
    breakout_signals = []
    near_signals     = []
    level_msgs       = []
    hero_msgs        = []
    edited_signals   = []
    deferred_sl_msgs = []
    wait_msgs        = []
    typo_target_msgs = []
    typo_above_msgs  = []
    instrument_counter = Counter()
    cepe_counter       = Counter()
    rejection_counter  = Counter()
    hour_counter       = Counter()
    target_counts      = Counter()
    sl_values          = []
    entry_ranges       = []

    for msg in recent:
        text = msg["text"]
        dt   = datetime.fromisoformat(msg["date"])
        hour_counter[dt.hour] += 1

        c = classify_message(text)

        if c["valid"]:
            valid_signals.append(msg)
            instrument_counter[c["instrument"]] += 1
            cepe_counter[c["ce_pe"]] += 1
            if c["is_breakout"]: breakout_signals.append(msg)
            if c["is_near"]:     near_signals.append(msg)
            if c["sl"]:          sl_values.append(c["sl"])
            if c["entry_range"]: entry_ranges.append(c["entry_range"])
            target_counts[len(c["targets"])] += 1
        else:
            rejected.append((msg, c["rejection"]))
            rejection_counter[c["rejection"]] += 1

        if msg["edited"]:          edited_signals.append(msg)
        if c["typo_target"]:       typo_target_msgs.append((msg, c["typo_target"]))
        if c["typo_above"]:        typo_above_msgs.append((msg, c["typo_above"]))
        if c["has_level"]:         level_msgs.append(msg)
        if c["has_hero"]:          hero_msgs.append(msg)
        if c["sl_deferred"]:       deferred_sl_msgs.append(msg)
        if c["wait_for_price"]:    wait_msgs.append(msg)

    return {
        "channel":           channel_name,
        "total":             total,
        "cutoff":            cutoff.date(),
        "valid_signals":     valid_signals,
        "rejected":          rejected,
        "edited":            edited_signals,
        "breakout":          breakout_signals,
        "near":              near_signals,
        "level_msgs":        level_msgs,
        "hero_msgs":         hero_msgs,
        "deferred_sl":       deferred_sl_msgs,
        "wait_msgs":         wait_msgs,
        "typo_target":       typo_target_msgs,
        "typo_above":        typo_above_msgs,
        "instrument_counter": instrument_counter,
        "cepe_counter":      cepe_counter,
        "rejection_counter": rejection_counter,
        "hour_counter":      hour_counter,
        "target_counts":     target_counts,
    }


def format_report(stats: dict) -> str:
    lines = []
    sep = "=" * 70
    def h(t): lines.append(f"\n{sep}\n  {t}\n{sep}")
    def s(t): lines.append(f"\n  ── {t} ──")

    h(f"CHANNEL: {stats['channel']}  |  Since: {stats['cutoff']}  |  Messages: {stats['total']}")

    s("OVERVIEW")
    lines.append(f"  Valid trade signals    : {len(stats['valid_signals'])}")
    lines.append(f"  Rejected / incomplete  : {len(stats['rejected'])}")
    lines.append(f"  Edited messages        : {len(stats['edited'])}")
    lines.append(f"  Breakout signals       : {len(stats['breakout'])}")
    lines.append(f"  Range (NEAR) signals   : {len(stats['near'])}")
    lines.append(f"  LEVEL warnings         : {len(stats['level_msgs'])}")
    lines.append(f"  HERO-ZERO calls        : {len(stats['hero_msgs'])}")
    lines.append(f"  Deferred SL signals    : {len(stats['deferred_sl'])}")
    lines.append(f"  'Wait for price' msgs  : {len(stats['wait_msgs'])}")

    s("INSTRUMENT BREAKDOWN (valid signals only)")
    for inst, cnt in stats['instrument_counter'].most_common():
        lines.append(f"    {inst:<15} : {cnt}")

    s("CE vs PE (valid signals)")
    for side, cnt in stats['cepe_counter'].most_common():
        lines.append(f"    {side}  : {cnt}")

    s("TARGET COUNT DISTRIBUTION (valid signals)")
    for n, cnt in sorted(stats['target_counts'].items()):
        lines.append(f"    {n} targets  : {cnt} signals")

    s("REJECTION REASONS")
    for reason, cnt in stats['rejection_counter'].most_common():
        lines.append(f"    {reason:<20} : {cnt}")

    s("TYPOS SEEN")
    lines.append(f"  Target word typos : {len(stats['typo_target'])} messages")
    tv = Counter()
    for _, v in stats['typo_target']:
        for x in v: tv[x.upper()] += 1
    for v, c in tv.most_common():
        lines.append(f"    '{v}' appeared {c} times")
    lines.append(f"  'Above' word typos : {len(stats['typo_above'])} messages")
    av = Counter()
    for _, v in stats['typo_above']:
        for x in v: av[x.upper()] += 1
    for v, c in av.most_common():
        lines.append(f"    '{v}' appeared {c} times")

    s("MOST ACTIVE HOURS (IST approx, UTC+5:30)")
    for hour, cnt in sorted(stats['hour_counter'].items(), key=lambda x: -x[1])[:8]:
        ist = (hour + 5) % 24
        lines.append(f"    {ist:02d}:00 IST ({hour:02d}:00 UTC)  : {cnt} messages")

    s("SAMPLE DEFERRED SL SIGNALS (last 5)")
    for msg in stats['deferred_sl'][-5:]:
        lines.append(f"\n  [{msg['date'][:16]}] id={msg['id']}")
        for line in msg['text'].split('\n'):
            lines.append(f"    {line}")

    s("SAMPLE VALID SIGNALS (last 15)")
    for msg in stats['valid_signals'][-15:]:
        lines.append(f"\n  [{msg['date'][:16]}] id={msg['id']}")
        for line in msg['text'].split('\n'):
            lines.append(f"    {line}")

    s("SAMPLE BREAKOUT SIGNALS (last 8)")
    for msg in stats['breakout'][-8:]:
        lines.append(f"\n  [{msg['date'][:16]}] id={msg['id']}")
        for line in msg['text'].split('\n'):
            lines.append(f"    {line}")

    s("SAMPLE EDITED SIGNALS (last 5)")
    for msg in stats['edited'][-5:]:
        lines.append(f"\n  [{msg['date'][:16]}] edited→{msg['edit_date'][:16] if msg['edit_date'] else '?'} id={msg['id']}")
        for line in msg['text'].split('\n')[:5]:
            lines.append(f"    {line}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=2, help="How many years back to analyse (default: 2)")
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=365 * args.years)
    print(f"Analysing messages since {cutoff.date()} (last {args.years} year(s))...")

    with open(RAW_FILE) as f:
        all_raw = json.load(f)

    # parse dates — add UTC timezone if missing
    for ch_msgs in all_raw.values():
        for m in ch_msgs:
            if not m["date"].endswith("+00:00") and not m["date"].endswith("Z"):
                m["date"] = m["date"] + "+00:00"

    report_lines = []
    for ch_name, messages in all_raw.items():
        stats = analyse(messages, ch_name, cutoff)
        print(f"  {ch_name}: {stats['total']} messages in range, {len(stats['valid_signals'])} valid signals")
        report_lines.append(format_report(stats))

    report = "\n".join(report_lines)
    print(report)

    with open(REPORT_FILE, "w") as f:
        f.write(report)
    print(f"\nReport written to {REPORT_FILE}")


if __name__ == "__main__":
    main()
