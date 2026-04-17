"""
shadow_mode.py — v2 shadow logger.

Records what v2 would do for every signal-channel message without firing
any orders. Wire in via record() after llm_parser.parse(); call eod_summary()
from wrapup_day() for EOD reconciliation.

Log format: one JSON object per line → Trades/{date}/shadow_v2_{date}.jsonl
"""

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

# Confidence below this → v2 would hold for manual review
MIN_FIRE_CONFIDENCE = 0.55

_log_path: str | None = None
_entries: list[dict] = []


def _get_log_path() -> str:
    global _log_path
    if _log_path is None:
        today = datetime.now().strftime("%Y-%m-%d")
        os.makedirs(f"Trades/{today}", exist_ok=True)
        _log_path = f"Trades/{today}/shadow_v2_{today}.jsonl"
    return _log_path


def _decide(signal, v1_fired: bool) -> str:
    """Return a short label for what v2 would do."""
    intent = signal.intent
    conf = signal.confidence or 0.0

    if intent == "NOISE":
        return "SKIP_NOISE"

    if intent == "NEW_SIGNAL":
        if signal.sl_deferred:
            return "HOLD_SL_DEFERRED"
        if not signal.is_actionable():
            missing = []
            if signal.instrument is None:       missing.append("instrument")
            if signal.strike is None:           missing.append("strike")
            if signal.ce_pe is None:            missing.append("CE/PE")
            if len(signal.targets) < 2:         missing.append("targets<2")
            if signal.sl is None:               missing.append("SL")
            return f"INCOMPLETE({','.join(missing)})"
        if conf < MIN_FIRE_CONFIDENCE:
            return f"HOLD_LOW_CONF({conf:.0%})"
        return "FIRE"

    if intent == "SL_RESOLVED":
        return "FIRE_PENDING"

    if intent == "REENTER":
        return "FIRE_REENTER"

    if intent == "UPDATE_SL":
        return f"UPDATE_SL→{signal.sl}"

    if intent == "UPDATE_TARGET":
        return f"UPDATE_TARGETS→{signal.targets}"

    if intent == "CANCEL":
        return "CANCEL"

    if intent == "PARTIAL_EXIT":
        return "PARTIAL_EXIT"

    if intent == "FULL_EXIT":
        return "FULL_EXIT"

    return f"UNKNOWN({intent})"


def record(signal, raw_message: str = "", event_id=None, chat_id=None, v1_fired: bool = False):
    """Call this for every signal-channel message after llm_parser.parse()."""
    action = _decide(signal, v1_fired)

    entry = {
        "ts": datetime.now().isoformat(),
        "event_id": event_id,
        "chat_id": chat_id,
        "intent": signal.intent,
        "confidence": round(signal.confidence, 3) if signal.confidence else None,
        "instrument": signal.instrument,
        "strike": str(signal.strike) if signal.strike else None,
        "ce_pe": signal.ce_pe,
        "entry_low": signal.entry_low,
        "entry_high": signal.entry_high,
        "sl": signal.sl,
        "sl_deferred": signal.sl_deferred,
        "targets": signal.targets,
        "strategy": signal.strategy,
        "v2_action": action,
        "v1_fired": v1_fired,
        "agree": _agree(action, v1_fired),
        "notes": signal.notes,
        "raw": raw_message[:300],
    }

    _entries.append(entry)
    with open(_get_log_path(), "a") as f:
        f.write(json.dumps(entry) + "\n")

    agree_tag = "✓" if entry["agree"] else "⚡ DISAGREE"
    logger.info(
        f"[SHADOW] {signal.intent} conf={signal.confidence:.0%} "
        f"v2={action} v1_fired={v1_fired} {agree_tag}"
    )


def _agree(v2_action: str, v1_fired: bool) -> bool:
    """True if v1 and v2 would take the same fire/no-fire decision."""
    v2_would_fire = v2_action in ("FIRE", "FIRE_PENDING", "FIRE_REENTER")
    return v2_would_fire == v1_fired


def eod_summary() -> str:
    entries = _entries
    if not entries:
        return "[SHADOW_EOD] No signal-channel messages recorded."

    total     = len(entries)
    fires     = [e for e in entries if e["v2_action"] in ("FIRE", "FIRE_PENDING", "FIRE_REENTER")]
    held      = [e for e in entries if e["v2_action"].startswith("HOLD")]
    incomplete= [e for e in entries if e["v2_action"].startswith("INCOMPLETE")]
    skipped   = [e for e in entries if e["v2_action"] == "SKIP_NOISE"]
    disagree  = [e for e in entries if not e["agree"]]

    lines = [
        f"[SHADOW_EOD] total={total} | fire={len(fires)} | held={len(held)} "
        f"| incomplete={len(incomplete)} | noise={len(skipped)} | disagree={len(disagree)}",
    ]

    if disagree:
        lines.append("  ⚡ DISAGREEMENTS (v1 vs v2):")
        for e in disagree:
            v1 = "FIRED" if e["v1_fired"] else "no-fire"
            lines.append(
                f"    [{e['ts'][11:19]}] intent={e['intent']} conf={e['confidence']} "
                f"v1={v1} v2={e['v2_action']} | {e['raw'][:80]}"
            )

    if incomplete:
        lines.append("  ⚠️ INCOMPLETE signals:")
        for e in incomplete:
            lines.append(f"    [{e['ts'][11:19]}] {e['v2_action']} | {e['raw'][:80]}")

    if held:
        lines.append("  ⏳ HELD signals:")
        for e in held:
            lines.append(f"    [{e['ts'][11:19]}] {e['v2_action']} | {e['raw'][:80]}")

    lines.append(f"  Log → {_get_log_path()}")
    return "\n".join(lines)
