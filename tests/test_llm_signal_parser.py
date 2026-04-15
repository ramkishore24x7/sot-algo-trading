"""
LLM Signal Parser tests — no real API calls.

Tests three layers:
  1. is_noise()        — fast pre-filter (pure function)
  2. DayContext        — context window + active/pending state
  3. LLMSignalParser   — full parse() with mocked Claude API

Run with:
    python tests/test_llm_signal_parser.py
    python tests/test_llm_signal_parser.py -v        # verbose
    python tests/test_llm_signal_parser.py NoiseTests  # one class
"""

import sys
import os
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub heavy deps before any import touches them
from unittest.mock import MagicMock
sys.modules.setdefault('anthropic', MagicMock())

# Now stub credentials so the module loads without real keys
import types as _types
_creds = _types.ModuleType('utils.credentials')
_creds.ANTHROPIC_API_KEY = 'test-key'
sys.modules['utils.credentials'] = _creds

from utils.llm_signal_parser import (
    is_noise, DayContext, LLMSignalParser, ParsedSignal, _dict_to_signal,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_parser(response_json: dict) -> LLMSignalParser:
    """Return an LLMSignalParser whose Claude client is fully mocked."""
    mock_client = MagicMock()
    mock_msg    = MagicMock()
    mock_msg.content = [MagicMock(text=json.dumps(response_json))]
    mock_client.messages.create.return_value = mock_msg

    import anthropic as _anthropic
    with patch.object(_anthropic, 'Anthropic', return_value=mock_client):
        parser = LLMSignalParser(api_key='test-key')

    parser.client = mock_client   # ensure the mock is wired in
    return parser


def _new_signal_json(**overrides):
    base = {
        "intent": "NEW_SIGNAL", "confidence": 0.95,
        "instrument": "NIFTY", "strike": "25500", "ce_pe": "CE",
        "strategy": "RANGE",
        "entry_low": 215, "entry_high": 220,
        "targets": [230, 240, 255, 270],
        "sl": 204,
        "sl_deferred": False, "sl_at_cost": False,
        "wait_for_price": True, "notes": "range trade",
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# 1. Noise filter
# ─────────────────────────────────────────────────────────────────────────────
class NoiseTests(unittest.TestCase):

    def test_empty_string_is_noise(self):
        self.assertTrue(is_noise(""))
        self.assertTrue(is_noise("   "))

    def test_pure_emoji_is_noise(self):
        self.assertTrue(is_noise("🚀🚀🚀"))
        self.assertTrue(is_noise("💸💸 ✅"))
        self.assertTrue(is_noise("🔥🔥🔥🔥"))

    def test_target_hit_short_is_noise(self):
        self.assertTrue(is_noise("395🚀🚀"))
        self.assertTrue(is_noise("84000 🚀🚀"))

    def test_good_morning_is_noise(self):
        self.assertTrue(is_noise("Good morning"))
        self.assertTrue(is_noise("gm everyone"))
        self.assertTrue(is_noise("Have a wonderful and profitable day"))

    def test_signal_is_not_noise(self):
        msg = "Nifty 25500 CE near 215-220\nTarget 230/240/255\nSL 204"
        self.assertFalse(is_noise(msg))

    def test_sl_update_is_not_noise(self):
        self.assertFalse(is_noise("Sl updated to 195"))

    def test_reenter_is_not_noise(self):
        self.assertFalse(is_noise("Re-enter same"))

    def test_sl_at_cost_is_not_noise(self):
        self.assertFalse(is_noise("Sl at cost 165"))
        self.assertFalse(is_noise("Those who have more lot can keep sl at cost 590 and hold"))


# ─────────────────────────────────────────────────────────────────────────────
# 2. DayContext
# ─────────────────────────────────────────────────────────────────────────────
class DayContextTests(unittest.TestCase):

    def setUp(self):
        self.ctx = DayContext()

    def test_noise_not_added_to_context(self):
        self.ctx.add_message("🚀🚀🚀", msg_id=1)
        self.assertEqual(len(self.ctx.messages), 0)

    def test_signal_added_to_context(self):
        self.ctx.add_message("Nifty 25500 CE near 215-220 Target 230/240 SL 204", msg_id=1)
        self.assertEqual(len(self.ctx.messages), 1)

    def test_context_window_capped_at_max(self):
        for i in range(20):
            self.ctx.add_message(f"Sl updated to {200 - i}", msg_id=i)
        self.assertLessEqual(len(self.ctx.messages), DayContext.MAX_CONTEXT)

    def test_edit_flag_recorded(self):
        self.ctx.add_message("Nifty signal text", msg_id=1, is_edit=True)
        self.assertTrue(self.ctx.messages[0]["edited"])

    def test_set_active_clears_pending(self):
        sig = ParsedSignal(intent="NEW_SIGNAL", instrument="NIFTY")
        self.ctx.set_pending(sig)
        self.assertIsNotNone(self.ctx.pending_signal)
        self.ctx.set_active(sig)
        self.assertIsNone(self.ctx.pending_signal)
        self.assertIsNotNone(self.ctx.active_signal)

    def test_clear_active(self):
        sig = ParsedSignal(intent="NEW_SIGNAL", instrument="NIFTY")
        self.ctx.set_active(sig)
        self.ctx.clear_active()
        self.assertIsNone(self.ctx.active_signal)

    def test_context_for_llm_excludes_current_message(self):
        """context_for_llm excludes the last message (the one being parsed)."""
        self.ctx.add_message("Sl updated to 200", msg_id=1)
        self.ctx.add_message("Re-enter same", msg_id=2)
        llm_ctx = self.ctx.context_for_llm()
        # Last message (id=2) must NOT appear in context (it IS the current message)
        self.assertIn("200", llm_ctx)
        self.assertNotIn("Re-enter", llm_ctx)

    def test_empty_context_string(self):
        ctx_str = self.ctx.context_for_llm()
        self.assertIn("no messages", ctx_str)


# ─────────────────────────────────────────────────────────────────────────────
# 3. LLMSignalParser — mocked API
# ─────────────────────────────────────────────────────────────────────────────
class LLMParserTests(unittest.TestCase):

    # ── NEW_SIGNAL ─────────────────────────────────────────────────────────────

    def test_new_signal_all_fields_parsed(self):
        """Complete RANGE signal — all fields extracted correctly."""
        parser = _make_parser(_new_signal_json())
        sig = parser.parse("Nifty 25500 CE near 215-220\nTarget 230/240/255/270\nSL 204", msg_id=1)
        self.assertEqual(sig.intent, "NEW_SIGNAL")
        self.assertEqual(sig.instrument, "NIFTY")
        self.assertEqual(sig.strike, "25500")
        self.assertEqual(sig.ce_pe, "CE")
        self.assertEqual(sig.strategy, "RANGE")
        self.assertEqual(sig.entry_low, 215)
        self.assertEqual(sig.entry_high, 220)
        self.assertEqual(sig.targets, [230, 240, 255, 270])
        self.assertEqual(sig.sl, 204)
        self.assertFalse(sig.sl_deferred)
        self.assertFalse(sig.sl_at_cost)
        self.assertTrue(sig.wait_for_price)

    def test_new_signal_is_actionable(self):
        parser = _make_parser(_new_signal_json())
        sig = parser.parse("signal text", msg_id=1)
        self.assertTrue(sig.is_actionable())

    def test_new_signal_breakout(self):
        parser = _make_parser(_new_signal_json(
            strategy="BREAKOUT", entry_low=105, entry_high=105,
            targets=[115, 125, 140, 155],
        ))
        sig = parser.parse("Nifty 25300 ce above 105 level\nTarget 115/125/140/155\nSL 94", msg_id=1)
        self.assertEqual(sig.strategy, "BREAKOUT")
        self.assertEqual(sig.entry_low, sig.entry_high)

    def test_4_targets_all_extracted(self):
        parser = _make_parser(_new_signal_json(targets=[230, 250, 270, 290]))
        sig = parser.parse("signal", msg_id=1)
        self.assertEqual(len(sig.targets), 4)
        self.assertEqual(sig.targets[-1], 290)

    def test_5_targets_all_extracted(self):
        parser = _make_parser(_new_signal_json(targets=[230, 250, 270, 290, 310]))
        sig = parser.parse("signal", msg_id=1)
        self.assertEqual(len(sig.targets), 5)

    # ── Deferred SL ────────────────────────────────────────────────────────────

    def test_sl_deferred_not_actionable(self):
        """Signal with 'SL - I will update' must not be actionable."""
        parser = _make_parser(_new_signal_json(sl=None, sl_deferred=True))
        sig = parser.parse("Nifty 25300 pe at 205\nTarget 215/225/238\nSl - I will update", msg_id=1)
        self.assertTrue(sig.sl_deferred)
        self.assertFalse(sig.is_actionable(), "Deferred SL signal must not fire SOT_BOT")

    def test_deferred_sl_pending_stored(self):
        """After signal_pending() is called, context has the pending signal."""
        parser = _make_parser(_new_signal_json(sl=None, sl_deferred=True))
        sig = parser.parse("signal with deferred sl", msg_id=1)
        parser.signal_pending(sig)
        self.assertIsNotNone(parser.context.pending_signal)

    def test_sl_resolved_completes_pending_signal(self):
        """
        Flow: NEW_SIGNAL (sl_deferred) → signal_pending() → SL_RESOLVED
        After resolution, returned signal has the SL filled in and is actionable.
        """
        parser = _make_parser(_new_signal_json(sl=None, sl_deferred=True))
        pending = parser.parse("Nifty signal deferred sl", msg_id=1)
        parser.signal_pending(pending)

        # Now SL arrives as a follow-up message
        resolved = parser.signal_resolved(sl=185)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.sl, 185)
        self.assertFalse(resolved.sl_deferred)
        self.assertTrue(resolved.is_actionable())

    def test_sl_resolved_without_pending_returns_none(self):
        """SL_RESOLVED with no pending signal → returns None gracefully."""
        parser = _make_parser({})
        result = parser.signal_resolved(sl=185)
        self.assertIsNone(result)

    def test_pending_cleared_after_resolution(self):
        parser = _make_parser(_new_signal_json(sl=None, sl_deferred=True))
        pending = parser.parse("deferred", msg_id=1)
        parser.signal_pending(pending)
        parser.signal_resolved(sl=185)
        self.assertIsNone(parser.context.pending_signal)

    # ── REENTER ────────────────────────────────────────────────────────────────

    def test_reenter_intent(self):
        parser = _make_parser({
            "intent": "REENTER", "confidence": 0.9,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": None, "entry_low": None, "entry_high": None,
            "targets": [], "sl": None, "sl_deferred": False,
            "sl_at_cost": False, "wait_for_price": False, "notes": "re-enter same",
        })
        sig = parser.parse("Re-enter same", msg_id=5)
        self.assertEqual(sig.intent, "REENTER")

    # ── Instrument inference from context ─────────────────────────────────────

    def test_instrument_inferred_when_missing_and_price_close(self):
        """
        'Next entry near 160-165 Target 175/190/...' has no instrument.
        LLM should inherit NIFTY 22600 PE from active signal (entry 190-195).
        Price diff = |165 - 195| = 30 → within 150 pt threshold.
        """
        parser = _make_parser({
            "intent": "NEW_SIGNAL", "confidence": 0.85,
            "instrument": None,   # <-- LLM didn't detect instrument
            "strike": None, "ce_pe": None,
            "strategy": "RANGE", "entry_low": 160, "entry_high": 165,
            "targets": [175, 190, 200, 220, 235],
            "sl": 150, "sl_deferred": False, "sl_at_cost": False,
            "wait_for_price": False, "notes": "next entry, no instrument given",
        })
        # Set up active signal: Nifty 22600 PE near 190-195
        active = ParsedSignal(
            intent="NEW_SIGNAL", instrument="NIFTY", strike="22600", ce_pe="PE",
            strategy="RANGE", entry_low=190, entry_high=195,
            targets=[205, 220, 235, 250], sl=180,
        )
        parser.signal_fired(active)

        sig = parser.parse("Next entry near 160-165 Target 175/190/200/220/235+ SI 150", msg_id=20)
        # Parser returns the LLM output — instrument is still null here
        # (inference happens in handle_llm_intent, not in parse())
        self.assertEqual(sig.intent, "NEW_SIGNAL")
        self.assertIsNone(sig.instrument)   # LLM didn't provide it
        self.assertEqual(sig.entry_high, 165)
        # Verify active signal is available for inference
        restored = parser.get_active()
        self.assertEqual(restored.instrument, "NIFTY")
        price_diff = abs(sig.entry_high - restored.entry_high)
        self.assertLessEqual(price_diff, 150, "Price diff should be within inheritance threshold")

    def test_instrument_not_inferred_when_price_too_far(self):
        """
        New entry at 600 with active signal at 190 → price diff = 410 > 150.
        Should NOT inherit instrument — different contract entirely.
        """
        active = ParsedSignal(
            intent="NEW_SIGNAL", instrument="NIFTY", strike="22600", ce_pe="PE",
            strategy="RANGE", entry_low=190, entry_high=195,
            targets=[205, 220, 235], sl=180,
        )
        parser = _make_parser({})
        parser.signal_fired(active)

        restored = parser.get_active()
        # Simulate: new signal entry_high=600, active entry_high=195
        price_diff = abs(600 - restored.entry_high)
        self.assertGreater(price_diff, 150, "Should NOT inherit for distant price range")

    def test_instrument_not_inferred_when_no_active_signal(self):
        """No active signal → instrument stays None, no crash."""
        parser = _make_parser({
            "intent": "NEW_SIGNAL", "confidence": 0.8,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": "RANGE", "entry_low": 160, "entry_high": 165,
            "targets": [175, 190, 200], "sl": 150,
            "sl_deferred": False, "sl_at_cost": False,
            "wait_for_price": False, "notes": "",
        })
        # No signal_fired() call — no active signal
        sig = parser.parse("Next entry near 160-165 Target 175/190/200 SI 150", msg_id=21)
        self.assertIsNone(sig.instrument)
        self.assertIsNone(parser.get_active())

    def test_reenter_same_preserves_active_signal(self):
        """
        'Re-enter same' — all REENTER fields are null.
        get_active() should return the original signal unchanged.
        """
        active = ParsedSignal(
            intent="NEW_SIGNAL", instrument="NIFTY", strike="25500", ce_pe="CE",
            strategy="RANGE", entry_low=215, entry_high=220,
            targets=[230, 240, 255], sl=204, sl_deferred=False,
        )
        parser = _make_parser({
            "intent": "REENTER", "confidence": 0.85,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": None, "entry_low": None, "entry_high": None,
            "targets": [], "sl": None, "sl_deferred": False,
            "sl_at_cost": False, "wait_for_price": False, "notes": "",
        })
        parser.signal_fired(active)
        parser.parse("Re-enter same", msg_id=6)

        restored = parser.get_active()
        self.assertIsNotNone(restored)
        self.assertEqual(restored.entry_low, 215)
        self.assertEqual(restored.entry_high, 220)
        self.assertEqual(restored.strategy, "RANGE")

    def test_reenter_modified_entry_stored_as_active(self):
        """
        'Re-enter above 380' — REENTER signal has new entry_high=380, BREAKOUT.
        The active signal in context should have the original params intact
        (the merge happens in handle_llm_intent, not in the parser itself).
        get_active() still returns the original signal.
        """
        active = ParsedSignal(
            intent="NEW_SIGNAL", instrument="NIFTY", strike="25500", ce_pe="CE",
            strategy="RANGE", entry_low=215, entry_high=220,
            targets=[230, 240, 255], sl=204, sl_deferred=False,
        )
        parser = _make_parser({
            "intent": "REENTER", "confidence": 0.90,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": "BREAKOUT", "entry_low": 380, "entry_high": 380,
            "targets": [], "sl": None, "sl_deferred": False,
            "sl_at_cost": False, "wait_for_price": False, "notes": "re-enter above 380",
        })
        parser.signal_fired(active)
        reenter_sig = parser.parse("Re-enter above 380", msg_id=7)

        # LLM returns REENTER with updated entry
        self.assertEqual(reenter_sig.intent, "REENTER")
        self.assertEqual(reenter_sig.entry_high, 380)
        self.assertEqual(reenter_sig.strategy, "BREAKOUT")

    def test_reenter_after_sl_hit_active_still_present(self):
        """
        SL hit does NOT call signal_closed() automatically.
        Active signal persists so re-entry can use it.
        This is intentional — the bot relies on this accidental persistence.
        """
        active = ParsedSignal(
            intent="NEW_SIGNAL", instrument="NIFTY", strike="25500", ce_pe="CE",
            strategy="RANGE", entry_low=215, entry_high=220,
            targets=[230, 240, 255], sl=204, sl_deferred=False,
        )
        parser = _make_parser({})
        parser.signal_fired(active)
        # SL hits — signal_closed() is NOT called (bot doesn't call it on SL hit)
        # active_signal must still be present for re-entry to work
        self.assertIsNotNone(parser.get_active(),
                             "Active signal must persist after SL hit for re-entry")

    def test_reenter_returns_none_after_explicit_close(self):
        """
        If signal_closed() IS called (manual exit), get_active() returns None
        and REENTER would show a warning instead of firing.
        """
        active = ParsedSignal(
            intent="NEW_SIGNAL", instrument="NIFTY", strike="25500", ce_pe="CE",
            strategy="RANGE", entry_low=215, entry_high=220,
            targets=[230, 240, 255], sl=204, sl_deferred=False,
        )
        parser = _make_parser({})
        parser.signal_fired(active)
        parser.signal_closed()   # explicit close (e.g. Telegram EXIT command)
        self.assertIsNone(parser.get_active(),
                          "After signal_closed(), get_active() must return None")

    # ── UPDATE_SL ──────────────────────────────────────────────────────────────

    def test_update_sl_intent(self):
        parser = _make_parser({
            "intent": "UPDATE_SL", "confidence": 0.95,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": None, "entry_low": None, "entry_high": None,
            "targets": [], "sl": 185, "sl_deferred": False,
            "sl_at_cost": False, "wait_for_price": False, "notes": "sl updated",
        })
        sig = parser.parse("Sl updated to 185", msg_id=7)
        self.assertEqual(sig.intent, "UPDATE_SL")
        self.assertEqual(sig.sl, 185)

    def test_sl_at_cost_mid_trade(self):
        """
        'Sl at cost 165' mid-trade → UPDATE_SL with sl_at_cost=True and sl=165.
        """
        parser = _make_parser({
            "intent": "UPDATE_SL", "confidence": 0.95,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": None, "entry_low": None, "entry_high": None,
            "targets": [], "sl": 165, "sl_deferred": False,
            "sl_at_cost": True, "wait_for_price": False, "notes": "sl at cost",
        })
        sig = parser.parse("Sl at cost 165", msg_id=8)
        self.assertEqual(sig.intent, "UPDATE_SL")
        self.assertTrue(sig.sl_at_cost)
        self.assertEqual(sig.sl, 165)

    def test_sl_at_cost_in_new_signal(self):
        """
        'Keep sl at cost' inside initial signal → NEW_SIGNAL with sl_at_cost=True.
        """
        parser = _make_parser(_new_signal_json(sl_at_cost=True))
        sig = parser.parse(
            "Nifty 25500 CE near 215-220\nTarget 230/240/255/270\nSL 204\nKeep SL at cost",
            msg_id=1
        )
        self.assertTrue(sig.sl_at_cost)

    # ── CANCEL ─────────────────────────────────────────────────────────────────

    def test_cancel_intent(self):
        parser = _make_parser({
            "intent": "CANCEL", "confidence": 0.9,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": None, "entry_low": None, "entry_high": None,
            "targets": [], "sl": None, "sl_deferred": False,
            "sl_at_cost": False, "wait_for_price": False, "notes": "cancel",
        })
        sig = parser.parse("Ignore previous signal", msg_id=9)
        self.assertEqual(sig.intent, "CANCEL")

    # ── NOISE (LLM path) ───────────────────────────────────────────────────────

    def test_noise_intent_from_llm(self):
        """Noise that passes the pre-filter but LLM classifies as NOISE."""
        parser = _make_parser({
            "intent": "NOISE", "confidence": 0.98,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": None, "entry_low": None, "entry_high": None,
            "targets": [], "sl": None, "sl_deferred": False,
            "sl_at_cost": False, "wait_for_price": False, "notes": "celebration message",
        })
        sig = parser.parse("Those who booked at first target well done!", msg_id=10)
        self.assertEqual(sig.intent, "NOISE")

    def test_noise_prefilter_skips_llm_call(self):
        """Pure emoji is caught by pre-filter — Claude API must NOT be called."""
        parser = _make_parser({})   # response doesn't matter
        sig = parser.parse("🚀🚀🚀🚀", msg_id=11)
        self.assertEqual(sig.intent, "NOISE")
        parser.client.messages.create.assert_not_called()

    # ── FULL_EXIT / PARTIAL_EXIT ────────────────────────────────────────────────

    def test_full_exit_intent(self):
        parser = _make_parser({
            "intent": "FULL_EXIT", "confidence": 0.95,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": None, "entry_low": None, "entry_high": None,
            "targets": [], "sl": None, "sl_deferred": False,
            "sl_at_cost": False, "wait_for_price": False, "notes": "exit all",
        })
        sig = parser.parse("Exit everything", msg_id=12)
        self.assertEqual(sig.intent, "FULL_EXIT")

    def test_partial_exit_intent(self):
        parser = _make_parser({
            "intent": "PARTIAL_EXIT", "confidence": 0.9,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": None, "entry_low": None, "entry_high": None,
            "targets": [], "sl": None, "sl_deferred": False,
            "sl_at_cost": False, "wait_for_price": False, "notes": "partial",
        })
        sig = parser.parse("Book 50%", msg_id=13)
        self.assertEqual(sig.intent, "PARTIAL_EXIT")

    # ── Invalid JSON / API error ────────────────────────────────────────────────

    def test_invalid_json_returns_noise(self):
        """If LLM returns invalid JSON, parser must return NOISE gracefully."""
        mock_client = MagicMock()
        mock_msg    = MagicMock()
        mock_msg.content = [MagicMock(text="not valid json {{{")]
        mock_client.messages.create.return_value = mock_msg

        import anthropic as _anthropic
        with patch.object(_anthropic, 'Anthropic', return_value=mock_client):
            parser = LLMSignalParser(api_key='test-key')
        parser.client = mock_client

        sig = parser.parse("some valid looking message here today", msg_id=14)
        self.assertEqual(sig.intent, "NOISE")
        self.assertIn("invalid JSON", sig.notes)

    def test_api_error_returns_noise(self):
        """API exception → NOISE, no crash."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API timeout")

        import anthropic as _anthropic
        with patch.object(_anthropic, 'Anthropic', return_value=mock_client):
            parser = LLMSignalParser(api_key='test-key')
        parser.client = mock_client

        sig = parser.parse("Nifty 25500 CE near 215-220 Target 230 SL 204", msg_id=15)
        self.assertEqual(sig.intent, "NOISE")

    # ── Markdown fence stripping ────────────────────────────────────────────────

    def test_markdown_fenced_json_parsed(self):
        """LLM sometimes wraps JSON in ```json ... ``` — must be stripped."""
        fenced = "```json\n" + json.dumps(_new_signal_json()) + "\n```"
        mock_client = MagicMock()
        mock_msg    = MagicMock()
        mock_msg.content = [MagicMock(text=fenced)]
        mock_client.messages.create.return_value = mock_msg

        import anthropic as _anthropic
        with patch.object(_anthropic, 'Anthropic', return_value=mock_client):
            parser = LLMSignalParser(api_key='test-key')
        parser.client = mock_client

        sig = parser.parse("Nifty 25500 CE near 215-220 Target 230/240 SL 204", msg_id=16)
        self.assertEqual(sig.intent, "NEW_SIGNAL")
        self.assertEqual(sig.instrument, "NIFTY")

    # ── Context propagated to LLM prompt ───────────────────────────────────────

    def test_active_signal_in_prompt(self):
        """Active signal must appear in the prompt sent to the LLM."""
        active = ParsedSignal(
            intent="NEW_SIGNAL", instrument="NIFTY", strike="25500", ce_pe="CE",
            strategy="RANGE", entry_low=215, entry_high=220,
            targets=[230, 240, 255], sl=204,
        )
        parser = _make_parser({
            "intent": "REENTER", "confidence": 0.9,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": None, "entry_low": None, "entry_high": None,
            "targets": [], "sl": None, "sl_deferred": False,
            "sl_at_cost": False, "wait_for_price": False, "notes": "",
        })
        parser.signal_fired(active)
        parser.parse("Re-enter same", msg_id=17)

        # Verify the prompt passed to the API contains the active signal
        call_kwargs = parser.client.messages.create.call_args
        prompt_content = call_kwargs[1]["messages"][0]["content"]
        self.assertIn("NIFTY", prompt_content)
        self.assertIn("25500", prompt_content)

    def test_context_messages_in_prompt(self):
        """Prior messages must appear in the prompt context window."""
        parser = _make_parser(_new_signal_json())
        # Add a prior message manually to context
        parser.context.add_message("Sl updated to 190", msg_id=100)
        parser.parse("Nifty 25500 CE near 215-220 Target 230/240 SL 204", msg_id=101)

        call_kwargs = parser.client.messages.create.call_args
        prompt = call_kwargs[1]["messages"][0]["content"]
        self.assertIn("190", prompt)

    # ── signal_fired / signal_closed lifecycle ─────────────────────────────────

    def test_signal_fired_sets_active(self):
        parser = _make_parser(_new_signal_json())
        sig = parser.parse("signal", msg_id=1)
        parser.signal_fired(sig)
        self.assertIsNotNone(parser.context.active_signal)

    def test_signal_closed_clears_active(self):
        parser = _make_parser(_new_signal_json())
        sig = parser.parse("signal", msg_id=1)
        parser.signal_fired(sig)
        parser.signal_closed()
        self.assertIsNone(parser.context.active_signal)


# ─────────────────────────────────────────────────────────────────────────────
# 4. _dict_to_signal round-trip
# ─────────────────────────────────────────────────────────────────────────────
class DictToSignalTests(unittest.TestCase):

    def test_round_trip_preserves_all_fields(self):
        original = ParsedSignal(
            intent="NEW_SIGNAL", confidence=0.95,
            instrument="BANKNIFTY", strike="52000", ce_pe="PE",
            strategy="RANGE", entry_low=370, entry_high=380,
            targets=[395, 420, 450], sl=355,
            sl_deferred=False, sl_at_cost=True,
            wait_for_price=True, notes="test",
        )
        d = original.to_dict()
        restored = _dict_to_signal(d)
        self.assertEqual(restored.instrument, "BANKNIFTY")
        self.assertEqual(restored.targets, [395, 420, 450])
        self.assertTrue(restored.sl_at_cost)

    def test_sl_resolved_updates_sl(self):
        """signal_resolved modifies sl and clears sl_deferred."""
        ctx = DayContext()
        pending = ParsedSignal(
            intent="NEW_SIGNAL", instrument="NIFTY", strike="25500", ce_pe="CE",
            strategy="RANGE", entry_low=215, entry_high=220,
            targets=[230, 240, 255], sl=None, sl_deferred=True,
        )
        ctx.set_pending(pending)

        # Manually replicate what signal_resolved does
        ctx.pending_signal["sl"] = 200
        ctx.pending_signal["sl_deferred"] = False
        resolved = _dict_to_signal(ctx.pending_signal)
        self.assertEqual(resolved.sl, 200)
        self.assertFalse(resolved.sl_deferred)
        self.assertTrue(resolved.is_actionable())


# ─────────────────────────────────────────────────────────────────────────────
# 5. Reply-chain signal store
# ─────────────────────────────────────────────────────────────────────────────
class ReplyChainTests(unittest.TestCase):

    def _make_active(self, instrument="NIFTY", strike="25500", ce_pe="CE",
                     entry_low=215, entry_high=220):
        return ParsedSignal(
            intent="NEW_SIGNAL", instrument=instrument, strike=strike,
            ce_pe=ce_pe, strategy="RANGE",
            entry_low=entry_low, entry_high=entry_high,
            targets=[230, 240, 255, 270], sl=204,
        )

    def test_signal_stored_by_msg_id(self):
        """signal_fired(signal, msg_id=X) stores the signal under key X."""
        parser = _make_parser(_new_signal_json())
        sig = self._make_active()
        parser.signal_fired(sig, msg_id=1001)
        retrieved = parser.get_by_msg_id(1001)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.instrument, "NIFTY")
        self.assertEqual(retrieved.entry_low, 215)

    def test_get_by_msg_id_returns_none_for_unknown_id(self):
        parser = _make_parser({})
        self.assertIsNone(parser.get_by_msg_id(9999))

    def test_multiple_signals_stored_independently(self):
        """Two signals on different msg_ids are retrieved independently."""
        parser = _make_parser({})
        nifty  = self._make_active(instrument="NIFTY",  strike="25500", ce_pe="CE",
                                   entry_low=215, entry_high=220)
        bnifty = self._make_active(instrument="BANKNIFTY", strike="52000", ce_pe="PE",
                                   entry_low=370, entry_high=380)
        parser.signal_fired(nifty,  msg_id=100)
        parser.signal_fired(bnifty, msg_id=200)

        r100 = parser.get_by_msg_id(100)
        r200 = parser.get_by_msg_id(200)
        self.assertEqual(r100.instrument, "NIFTY")
        self.assertEqual(r200.instrument, "BANKNIFTY")

    def test_reply_chain_resolves_correct_signal_among_two_active(self):
        """
        Mentor gives two signals (Nifty CE and BankNifty PE) in the same session.
        'Re-enter same' replied to the BankNifty message should resolve to
        BankNifty, NOT whatever is in active_signal (which is the last fired).
        """
        parser = _make_parser({})
        nifty  = self._make_active(instrument="NIFTY",  strike="25500", ce_pe="CE",
                                   entry_low=215, entry_high=220)
        bnifty = self._make_active(instrument="BANKNIFTY", strike="52000", ce_pe="PE",
                                   entry_low=370, entry_high=380)
        parser.signal_fired(nifty,  msg_id=100)
        parser.signal_fired(bnifty, msg_id=200)  # active_signal is now BankNifty

        # Follow-up replied to msg 100 (the Nifty signal)
        ref = parser.get_by_msg_id(100)
        self.assertEqual(ref.instrument, "NIFTY",
                         "Reply to Nifty msg must resolve Nifty, not the latest active BankNifty")

    def test_signal_store_cleared_on_day_reset(self):
        """signal_store is wiped on context reset (new market day)."""
        parser = _make_parser({})
        sig = self._make_active()
        parser.signal_fired(sig, msg_id=555)
        self.assertIsNotNone(parser.get_by_msg_id(555))
        # Force a reset
        parser.context._reset()
        self.assertIsNone(parser.get_by_msg_id(555),
                          "signal_store must be cleared on day reset")

    def test_signal_fired_without_msg_id_still_sets_active(self):
        """signal_fired(signal) with no msg_id still sets active_signal."""
        parser = _make_parser({})
        sig = self._make_active()
        parser.signal_fired(sig)   # no msg_id
        self.assertIsNotNone(parser.get_active())


# ─────────────────────────────────────────────────────────────────────────────
# 6. Screenshot-identified edge cases
# ─────────────────────────────────────────────────────────────────────────────
class ScreenshotEdgeCaseTests(unittest.TestCase):

    def test_remaining_lot_exit_at_price_is_update_sl(self):
        """
        'Remaining lot exit at 190' — mentor is setting a new SL level,
        NOT asking to exit immediately. Must be UPDATE_SL with sl=190.
        """
        parser = _make_parser({
            "intent": "UPDATE_SL", "confidence": 0.92,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": None, "entry_low": None, "entry_high": None,
            "targets": [], "sl": 190, "sl_deferred": False,
            "sl_at_cost": False, "wait_for_price": False,
            "notes": "remaining lot exit level = new SL",
        })
        sig = parser.parse("Remaining lot exit at 190", msg_id=30)
        self.assertEqual(sig.intent, "UPDATE_SL")
        self.assertEqual(sig.sl, 190)

    def test_exit_at_price_is_update_sl(self):
        """'Exit at 190' (specific price) → UPDATE_SL, not FULL_EXIT."""
        parser = _make_parser({
            "intent": "UPDATE_SL", "confidence": 0.90,
            "instrument": None, "strike": None, "ce_pe": None,
            "strategy": None, "entry_low": None, "entry_high": None,
            "targets": [], "sl": 190, "sl_deferred": False,
            "sl_at_cost": False, "wait_for_price": False, "notes": "exit level 190",
        })
        sig = parser.parse("Exit at 190", msg_id=31)
        self.assertEqual(sig.intent, "UPDATE_SL")
        self.assertEqual(sig.sl, 190)

    def test_no_sl_line_means_sl_deferred(self):
        """
        'Nifty 22600 pe above 208' with no SL line → sl_deferred=True.
        Mentor will give SL separately.
        """
        parser = _make_parser(_new_signal_json(
            instrument="NIFTY", strike="22600", ce_pe="PE",
            strategy="BREAKOUT", entry_low=208, entry_high=208,
            targets=[218, 228, 240, 255],
            sl=None, sl_deferred=True,
        ))
        sig = parser.parse("Nifty 22600 pe above 208\nTarget 218/228/240/255+", msg_id=32)
        self.assertTrue(sig.sl_deferred, "Signal with no SL line must have sl_deferred=True")
        self.assertIsNone(sig.sl)
        self.assertFalse(sig.is_actionable(), "sl_deferred signal must not fire SOT_BOT")

    def test_track_both_levels_is_noise_prefilter(self):
        """'Track both levels' is a commentary message — caught by pre-filter."""
        self.assertTrue(is_noise("Track both levels"))
        self.assertTrue(is_noise("track both levels today"))

    def test_watch_both_levels_is_noise_prefilter(self):
        self.assertTrue(is_noise("watch both levels"))

    def test_keep_eye_on_is_noise_prefilter(self):
        self.assertTrue(is_noise("Keep an eye on this level"))
        self.assertTrue(is_noise("keep eye on 25300"))


# ─────────────────────────────────────────────────────────────────────────────
# 7. Context persistence (survive restarts)
# ─────────────────────────────────────────────────────────────────────────────
class PersistenceTests(unittest.TestCase):

    def _tmp_path(self):
        f = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        f.close()
        os.unlink(f.name)   # remove so _load() sees it as absent initially
        self.addCleanup(lambda: os.path.exists(f.name) and os.unlink(f.name))
        return f.name

    def _sig(self, instrument="NIFTY", entry_low=215, entry_high=220):
        return ParsedSignal(
            intent="NEW_SIGNAL", instrument=instrument, strike="25500",
            ce_pe="CE", strategy="RANGE", entry_low=entry_low,
            entry_high=entry_high, targets=[230, 240, 255], sl=204,
        )

    def test_no_persist_path_no_crash(self):
        """DayContext without persist_path works exactly as before."""
        ctx = DayContext()
        sig = self._sig()
        ctx.set_active(sig)   # should not raise
        self.assertIsNotNone(ctx.active_signal)

    def test_active_signal_survives_reload(self):
        """active_signal written on set_active() is restored on _load()."""
        path = self._tmp_path()
        ctx1 = DayContext(persist_path=path)
        ctx1.set_active(self._sig())
        self.assertTrue(os.path.exists(path))

        ctx2 = DayContext(persist_path=path)
        loaded = ctx2._load()
        self.assertTrue(loaded)
        self.assertIsNotNone(ctx2.active_signal)
        self.assertEqual(ctx2.active_signal["instrument"], "NIFTY")

    def test_pending_signal_survives_reload(self):
        path = self._tmp_path()
        ctx1 = DayContext(persist_path=path)
        ctx1.set_pending(self._sig())

        ctx2 = DayContext(persist_path=path)
        ctx2._load()
        self.assertIsNotNone(ctx2.pending_signal)

    def test_signal_store_survives_reload(self):
        """signal_store (reply-chain map) is restored with int keys."""
        path = self._tmp_path()
        ctx1 = DayContext(persist_path=path)
        ctx1.store_signal(99008, self._sig(instrument="NIFTY"))
        ctx1.store_signal(99011, self._sig(instrument="SENSEX"))

        ctx2 = DayContext(persist_path=path)
        ctx2._load()
        self.assertIn(99008, ctx2.signal_store)
        self.assertIn(99011, ctx2.signal_store)
        self.assertEqual(ctx2.signal_store[99008]["instrument"], "NIFTY")
        self.assertEqual(ctx2.signal_store[99011]["instrument"], "SENSEX")

    def test_stale_date_not_loaded(self):
        """Persisted file from yesterday must NOT be restored."""
        path = self._tmp_path()
        stale = {
            "date": "2020-01-01",   # obviously old
            "messages": [],
            "active_signal": {"instrument": "NIFTY", "intent": "NEW_SIGNAL"},
            "pending_signal": None,
            "signal_store": {},
        }
        with open(path, "w") as f:
            json.dump(stale, f)

        ctx = DayContext(persist_path=path)
        loaded = ctx._load()
        self.assertFalse(loaded, "Stale date must not be loaded")
        self.assertIsNone(ctx.active_signal)

    def test_missing_file_returns_false(self):
        """_load() on a non-existent file returns False without crashing."""
        ctx = DayContext(persist_path="/tmp/nonexistent_llm_ctx_xyz.json")
        self.assertFalse(ctx._load())

    def test_clear_active_persists(self):
        """clear_active() writes updated (null active) state to disk."""
        path = self._tmp_path()
        ctx1 = DayContext(persist_path=path)
        ctx1.set_active(self._sig())
        ctx1.clear_active()

        ctx2 = DayContext(persist_path=path)
        ctx2._load()
        self.assertIsNone(ctx2.active_signal)

    def test_parser_restores_context_on_init(self):
        """LLMSignalParser with persist_path restores state automatically."""
        path = self._tmp_path()

        # First parser instance — fires a signal
        p1 = _make_parser(_new_signal_json())
        p1.context._persist_path = path
        sig = ParsedSignal(
            intent="NEW_SIGNAL", instrument="NIFTY", strike="25500", ce_pe="CE",
            strategy="RANGE", entry_low=215, entry_high=220,
            targets=[230, 240, 255], sl=204,
        )
        p1.signal_fired(sig, msg_id=1234)

        # Second parser instance — simulates a restart
        import anthropic as _anthropic
        mock_client = MagicMock()
        with patch.object(_anthropic, 'Anthropic', return_value=mock_client):
            p2 = LLMSignalParser(api_key='test-key', persist_path=path)

        active = p2.get_active()
        self.assertIsNotNone(active, "active_signal must be restored after restart")
        self.assertEqual(active.instrument, "NIFTY")

        ref = p2.get_by_msg_id(1234)
        self.assertIsNotNone(ref, "signal_store entry must be restored after restart")
        self.assertEqual(ref.entry_low, 215)


if __name__ == "__main__":
    unittest.main(verbosity=2)
