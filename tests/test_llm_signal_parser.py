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

    def test_reenter_uses_active_context(self):
        """
        When REENTER fires, the active signal from context should be usable
        to re-build the position (via signal_fired + the active_signal dict).
        """
        # Set up active signal first
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
        parser.signal_fired(active)   # mark as active
        self.assertIsNotNone(parser.context.active_signal)

        sig = parser.parse("Re-enter same", msg_id=6)
        self.assertEqual(sig.intent, "REENTER")
        # active_signal context should still be set
        self.assertIsNotNone(parser.context.active_signal)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
