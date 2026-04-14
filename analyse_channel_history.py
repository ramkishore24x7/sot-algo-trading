"""
analyse_channel_history.py
───────────────────────────────────────────────────────────────────────────────
Fetches full history from the SOT signal channel and produces a plain-English
report of:
  - How the mentor formats trade signals
  - Common typos / alternate spellings handled
  - Breakout vs range patterns
  - Edit patterns (what gets corrected after the fact)
  - Messages that would be rejected by SOT_BOT (no signal / no target / no SL)
  - Instrument breakdown
  - Entry price ranges per instrument
  - Target / SL structure patterns

Usage:
    python analyse_channel_history.py

Output:
    Prints a structured report to console + writes channel_history_analysis.txt
    Writes raw message dump to channel_messages_raw.txt
"""

import asyncio
import re
import json
from collections import defaultdict, Counter
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.tl.types import MessageEntityBold, MessageEntityItalic

# ── Config ───────────────────────────────────────────────────────────────────
SESSION_FILE   = "anon"
API_ID         = None   # read from session — will prompt if needed
API_HASH       = None

# Channel IDs from telegram_BOT.py
SOT_CHANNEL        = -1001209833646
SOT_TRIAL_CHANNEL  = -1001810504797
QWERTY_CHANNEL     = -1001767848638

TARGET_CHANNELS = {
    "sot_channel":       SOT_CHANNEL,
    "sot_trial_channel": SOT_TRIAL_CHANNEL,
}

MAX_MESSAGES = None   # None = fetch entire channel history (no limit)

# ── Regex mirrors from telegram_BOT.py ───────────────────────────────────────
BUY_RE          = re.compile(r'buy.*|Bank.*|Nif.*|Fin.*|Baj.*|Nau.*', re.IGNORECASE)
TARGET_RE       = re.compile(r'Traget.*|Target.*|Tatget.*|Taget.*|Taret.*|TARGWT.*|Targst.*', re.IGNORECASE)
SL_RE           = re.compile(r'SL.*', re.IGNORECASE)
BREAKOUT_RE     = re.compile(r'\b(above|avove|abv|ave|abve)\b', re.IGNORECASE)
NEAR_RE         = re.compile(r'\bnear\b', re.IGNORECASE)
ENTRY_PRICE_RE  = re.compile(r'\b(\d{2,4})\b')
TARGETS_RE      = re.compile(r'(\d{2,4})', re.IGNORECASE)
INSTRUMENT_RE   = re.compile(
    r'\b(BANKNIFTY|BANK\s*NIFTY|NIFTY|FINNIFTY|FIN\s*NIFTY|MIDCPNIFTY|BAJFINANCE|SENSEX)\b',
    re.IGNORECASE
)
TYPO_TARGET_RE  = re.compile(
    r'\b(Traget|Tatget|Taget|Taret|TARGWT|Targst)\b', re.IGNORECASE
)
TYPO_ABOVE_RE   = re.compile(r'\b(avove|abv|ave|abve)\b', re.IGNORECASE)

# ── Helpers ───────────────────────────────────────────────────────────────────
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
    """Return dict with parsed fields or rejection reason."""
    has_buy    = bool(BUY_RE.search(text))
    has_target = bool(TARGET_RE.search(text))
    has_sl     = bool(SL_RE.search(text))
    is_breakout = bool(BREAKOUT_RE.search(text))
    is_near     = bool(NEAR_RE.search(text))

    instrument_match = INSTRUMENT_RE.search(text)
    instrument = normalise_instrument(instrument_match.group()) if instrument_match else None

    typo_target = TYPO_TARGET_RE.findall(text)
    typo_above  = TYPO_ABOVE_RE.findall(text)

    ce_pe = None
    if re.search(r'\bCE\b', text, re.IGNORECASE): ce_pe = "CE"
    elif re.search(r'\bPE\b', text, re.IGNORECASE): ce_pe = "PE"

    valid = has_buy and has_target and has_sl and instrument and ce_pe

    rejection = None
    if not valid:
        if not instrument:    rejection = "NO_INSTRUMENT"
        elif not ce_pe:       rejection = "NO_CE_PE"
        elif not has_target:  rejection = "NO_TARGET"
        elif not has_sl:      rejection = "NO_SL"
        elif not has_buy:     rejection = "NO_BUY_LINE"
        else:                 rejection = "INCOMPLETE"

    return {
        "valid":       valid,
        "rejection":   rejection,
        "instrument":  instrument,
        "ce_pe":       ce_pe,
        "is_breakout": is_breakout,
        "is_near":     is_near,
        "typo_target": typo_target,
        "typo_above":  typo_above,
        "has_level":   "LEVEL" in text.upper(),
        "has_hero":    "HERO" in text.upper(),
        "has_edit_words": any(w in text.upper() for w in ["EDIT","UPDATED","CORRECTION","IGNORE","REJECT","CANCEL","VOID"]),
    }


async def fetch_history(client, channel_id, channel_name, limit):
    print(f"\n[{channel_name}] Fetching up to {limit} messages...")
    messages = []
    edited_ids = set()
    async for msg in client.iter_messages(channel_id, limit=limit):
        if msg.text:
            entry = {
                "id":       msg.id,
                "date":     msg.date.isoformat(),
                "text":     msg.text,
                "edited":   msg.edit_date is not None,
                "edit_date": msg.edit_date.isoformat() if msg.edit_date else None,
                "reply_to": msg.reply_to_msg_id,
            }
            messages.append(entry)
            if msg.edit_date:
                edited_ids.add(msg.id)
        if len(messages) % 500 == 0 and len(messages) > 0:
            oldest = messages[-1]["date"][:10]
            print(f"[{channel_name}] {len(messages)} messages fetched... (oldest so far: {oldest})", end="\r", flush=True)
    print(f"\n[{channel_name}] Done — {len(messages)} messages ({len(edited_ids)} edited)")
    return messages, edited_ids


def analyse(all_messages: list, channel_name: str) -> dict:
    total = len(all_messages)
    valid_signals      = []
    rejected           = []
    edited_signals     = []
    breakout_signals   = []
    near_signals       = []
    typo_target_msgs   = []
    typo_above_msgs    = []
    level_msgs         = []
    hero_msgs          = []
    instrument_counter = Counter()
    cepe_counter       = Counter()
    rejection_counter  = Counter()
    hour_counter       = Counter()

    for msg in all_messages:
        text = msg["text"]
        dt   = datetime.fromisoformat(msg["date"])
        hour_counter[dt.hour] += 1

        c = classify_message(text)

        if c["valid"]:
            valid_signals.append(msg)
            instrument_counter[c["instrument"]] += 1
            cepe_counter[c["ce_pe"]] += 1
            if c["is_breakout"]:   breakout_signals.append(msg)
            if c["is_near"]:       near_signals.append(msg)
        else:
            rejected.append((msg, c["rejection"]))
            rejection_counter[c["rejection"]] += 1

        if msg["edited"]:
            edited_signals.append(msg)
        if c["typo_target"]:
            typo_target_msgs.append((msg, c["typo_target"]))
        if c["typo_above"]:
            typo_above_msgs.append((msg, c["typo_above"]))
        if c["has_level"]:
            level_msgs.append(msg)
        if c["has_hero"]:
            hero_msgs.append(msg)

    return {
        "channel":           channel_name,
        "total":             total,
        "valid_signals":     valid_signals,
        "rejected":          rejected,
        "edited":            edited_signals,
        "breakout":          breakout_signals,
        "near":              near_signals,
        "typo_target":       typo_target_msgs,
        "typo_above":        typo_above_msgs,
        "level_msgs":        level_msgs,
        "hero_msgs":         hero_msgs,
        "instrument_counter": instrument_counter,
        "cepe_counter":      cepe_counter,
        "rejection_counter": rejection_counter,
        "hour_counter":      hour_counter,
    }


def format_report(stats: dict) -> str:
    lines = []
    sep = "=" * 70

    def h(title): lines.append(f"\n{sep}\n  {title}\n{sep}")
    def s(title): lines.append(f"\n  ── {title} ──")

    h(f"CHANNEL: {stats['channel']}  |  Total messages analysed: {stats['total']}")

    # ── Overview
    s("OVERVIEW")
    lines.append(f"  Valid trade signals    : {len(stats['valid_signals'])}")
    lines.append(f"  Rejected / incomplete  : {len(stats['rejected'])}")
    lines.append(f"  Edited messages        : {len(stats['edited'])}")
    lines.append(f"  Breakout signals       : {len(stats['breakout'])}")
    lines.append(f"  Range (NEAR) signals   : {len(stats['near'])}")
    lines.append(f"  LEVEL warnings         : {len(stats['level_msgs'])}")
    lines.append(f"  HERO-ZERO calls        : {len(stats['hero_msgs'])}")

    # ── Instrument breakdown
    s("INSTRUMENT BREAKDOWN (valid signals only)")
    for inst, cnt in stats['instrument_counter'].most_common():
        lines.append(f"    {inst:<15} : {cnt}")

    # ── CE vs PE
    s("CE vs PE (valid signals)")
    for side, cnt in stats['cepe_counter'].most_common():
        lines.append(f"    {side}  : {cnt}")

    # ── Rejection reasons
    s("REJECTION REASONS")
    for reason, cnt in stats['rejection_counter'].most_common():
        lines.append(f"    {reason:<20} : {cnt}")

    # ── Typos
    s("TYPOS SEEN")
    lines.append(f"  Target word typos : {len(stats['typo_target'])} messages")
    typo_variants = Counter()
    for _, variants in stats['typo_target']:
        for v in variants: typo_variants[v.upper()] += 1
    for v, c in typo_variants.most_common():
        lines.append(f"    '{v}' appeared {c} times")

    lines.append(f"  'Above' word typos : {len(stats['typo_above'])} messages")
    above_variants = Counter()
    for _, variants in stats['typo_above']:
        for v in variants: above_variants[v.upper()] += 1
    for v, c in above_variants.most_common():
        lines.append(f"    '{v}' appeared {c} times")

    # ── Active hours
    s("MOST ACTIVE HOURS (IST approx)")
    for hour, cnt in sorted(stats['hour_counter'].items(), key=lambda x: -x[1])[:8]:
        lines.append(f"    {hour:02d}:00  : {cnt} messages")

    # ── Sample valid signals
    s("SAMPLE VALID SIGNALS (last 10)")
    for msg in stats['valid_signals'][-10:]:
        lines.append(f"\n  [{msg['date'][:16]}] id={msg['id']}")
        for line in msg['text'].split('\n'):
            lines.append(f"    {line}")

    # ── Sample edited messages
    s("SAMPLE EDITED MESSAGES (last 5)")
    for msg in stats['edited'][-5:]:
        lines.append(f"\n  [{msg['date'][:16]}] edited→{msg['edit_date'][:16] if msg['edit_date'] else '?'} id={msg['id']}")
        for line in msg['text'].split('\n'):
            lines.append(f"    {line}")

    # ── Sample rejections
    s("SAMPLE REJECTED MESSAGES (last 10)")
    for msg, reason in stats['rejected'][-10:]:
        lines.append(f"\n  [{msg['date'][:16]}] REASON={reason} id={msg['id']}")
        for line in msg['text'].split('\n')[:4]:
            lines.append(f"    {line}")

    # ── Breakout patterns
    s("SAMPLE BREAKOUT SIGNALS (last 5)")
    for msg in stats['breakout'][-5:]:
        lines.append(f"\n  [{msg['date'][:16]}] id={msg['id']}")
        for line in msg['text'].split('\n'):
            lines.append(f"    {line}")

    return "\n".join(lines)


async def main():
    client = TelegramClient(SESSION_FILE, api_id="24665115", api_hash="4bb48e7b1dd0fcb763dfe9eb203a6216")
    async with client:
        all_stats = []
        all_raw   = {}

        for ch_name, ch_id in TARGET_CHANNELS.items():
            try:
                messages, _ = await fetch_history(client, ch_id, ch_name, MAX_MESSAGES)
                all_raw[ch_name] = messages
                stats = analyse(messages, ch_name)
                all_stats.append(stats)
            except Exception as e:
                print(f"[{ch_name}] ERROR: {e}")

        # Write raw dump
        with open("channel_messages_raw.json", "w") as f:
            json.dump(all_raw, f, indent=2, default=str)
        print("\nRaw messages written to channel_messages_raw.json")

        # Write and print report
        report_lines = []
        for stats in all_stats:
            report_lines.append(format_report(stats))

        report = "\n".join(report_lines)
        print(report)

        with open("channel_history_analysis.txt", "w") as f:
            f.write(report)
        print("\nReport written to channel_history_analysis.txt")


if __name__ == "__main__":
    asyncio.run(main())
