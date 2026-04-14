"""
Trade scenario tests — no API connections, no Telegram, no Fyers.

Tests two layers:
  1. Quantity calculations (demat.get_sell_quantity_at_target1/2)
  2. Full tick-state machine (TradeSimulator mirrors on_price logic)

Run with:
    python tests/test_trade_scenarios.py
    python tests/test_trade_scenarios.py --visual    # visual scenario replay
    python tests/test_trade_scenarios.py QuantityTests   # one class
"""

import sys
import os
import math
import logging
import unittest
from types import SimpleNamespace

# ── make imports work from repo root ─────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── stub out the entire heavy dependency chain BEFORE any utils.* import ─────
from unittest.mock import MagicMock

_STUBS = [
    'pandas', 'yaml', 'chime',
    'fyers_apiv3', 'fyers_apiv3.fyersModel',
    'utils.credentials', 'utils.account_config',
    'utils.custom_calendar', 'utils.deco',
    'utils.clock',
]
for _m in _STUBS:
    sys.modules.setdefault(_m, MagicMock())

# ── stub utils.constants so Position.__init__ can call Config.lot_size_map ───
import types as _types
_constants_mod = _types.ModuleType('utils.constants')

class _Config:
    lot_size_map      = {'NSE': 1, 'NIFTY': 50, 'BANKNIFTY': 15, 'FINNIFTY': 40}
    freeze_quantity_map = {'NSE': 1800, 'NIFTY': 1800, 'BANKNIFTY': 900, 'FINNIFTY': 1800}
    logger_path       = '/tmp'
    fyers_log_path    = '/tmp'

_constants_mod.Config = _Config
sys.modules['utils.constants'] = _constants_mod

from utils.position import Position   # noqa: E402  (after sys.modules patches)

logging.basicConfig(level=logging.WARNING)


# ─────────────────────────────────────────────────────────────────────────────
# Minimal fake Demat — uses the REAL quantity-calculation methods but replaces
# placeOrderFyers / generatePnL / print_demat_status with no-ops.
# We bypass Demat.__init__ (which creates Fyers API) via object.__new__ and
# then set exactly the attributes the methods we care about actually touch.
# ─────────────────────────────────────────────────────────────────────────────
def make_fake_demat(position: Position, total_lots: int,
                    squareoff_at_first_target=False):
    """
    Create a Demat-shaped object with the real quantity methods,
    seeded with `total_lots` lots at the position's lot_size.
    """
    # Import Demat only after sys.modules patches are in place.
    # We need the class for its methods but not its __init__.
    from utils.demat import Demat

    d = object.__new__(Demat)
    d.position = position
    d.total_trading_quantity = total_lots * position.lot_size
    d.remaining_quantity     = total_lots * position.lot_size
    d.squareoff_at_first_target = squareoff_at_first_target
    d.average_price          = position.entry_price
    d.position_open          = True
    d.PnL                    = 0
    d.logger                 = logging.getLogger('FakeDemat')
    d.account_name           = 'TEST'
    # Stub out side-effectful helpers
    d.placeOrderFyers    = lambda *a, **kw: 'fake-order-id'
    d.generatePnL        = lambda: None
    d.print_demat_status = lambda: None
    # Stub account attribute (used in book_at_target log lines)
    acc = MagicMock(); acc.name = 'TEST'
    d.account = acc
    return d


def make_position(*, instrument='NSE', ce_pe='CE',
                  entry_price, stoploss, targets,
                  second_entry_price=None,
                  isBreakoutStrategy=False, onCrossingAbove=False):
    """Helper to build a Position with an explicit targets list."""
    t1, t2, t3 = targets[0], targets[1], targets[2] if len(targets) > 2 else targets[1] + (targets[1]-targets[0])
    return Position(
        instrument=instrument,
        ce_pe=ce_pe,
        strike=instrument,
        entry_price=entry_price,
        stoploss=stoploss,
        target1=t1,
        target2=t2,
        target3=t3,
        second_entry_price=second_entry_price,
        isBreakoutStrategy=isBreakoutStrategy,
        enterFewPointsAbove=onCrossingAbove,
        onCrossingAbove=onCrossingAbove,
        targets=targets,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Quantity calculation tests
# ─────────────────────────────────────────────────────────────────────────────
class QuantityTests(unittest.TestCase):

    # ── ABOVE ─────────────────────────────────────────────────────────────────

    def test_above_3t_10lots_t1_fraction(self):
        """ABOVE 3T 10L: T1 sells floor(10/3)=3 lots."""
        pos = make_position(entry_price=380, stoploss=365, targets=[395, 420, 445],
                            isBreakoutStrategy=True, onCrossingAbove=True)
        d = make_fake_demat(pos, total_lots=10)
        qty = d.get_sell_quantity_at_target1()
        self.assertEqual(qty, 3 * pos.lot_size)

    def test_above_4t_10lots_t1_fraction(self):
        """ABOVE 4T 10L: T1 sells floor(10/4)=2 lots."""
        pos = make_position(entry_price=380, stoploss=365, targets=[395, 420, 445, 470],
                            isBreakoutStrategy=True, onCrossingAbove=True)
        d = make_fake_demat(pos, total_lots=10)
        qty = d.get_sell_quantity_at_target1()
        self.assertEqual(qty, 2 * pos.lot_size)

    def test_above_5t_10lots_t1_fraction(self):
        """ABOVE 5T 10L: T1 sells floor(10/5)=2 lots."""
        pos = make_position(entry_price=380, stoploss=365, targets=[395, 420, 445, 470, 495],
                            isBreakoutStrategy=True, onCrossingAbove=True)
        d = make_fake_demat(pos, total_lots=10)
        qty = d.get_sell_quantity_at_target1()
        self.assertEqual(qty, 2 * pos.lot_size)

    def test_above_6t_10lots_t1_fraction(self):
        """ABOVE 6T 10L: T1 sells floor(10/6)=1 lot."""
        pos = make_position(entry_price=380, stoploss=365, targets=[395, 420, 445, 470, 495, 520],
                            isBreakoutStrategy=True, onCrossingAbove=True)
        d = make_fake_demat(pos, total_lots=10)
        qty = d.get_sell_quantity_at_target1()
        self.assertEqual(qty, 1 * pos.lot_size)

    # ── NEAR break-even floor ─────────────────────────────────────────────────

    def test_near_3t_10lots_breakeven_floor(self):
        """
        NEAR 3T 10L: entry=350 range-low=340 T1=370
        range_width=10, t1_gap=20 → floor=10/30=33.3% < base=33.3%
        floor not active → floor(10/3)=3 lots.
        """
        pos = make_position(entry_price=350, stoploss=335, targets=[370, 400, 430],
                            second_entry_price=340, isBreakoutStrategy=False)
        d = make_fake_demat(pos, total_lots=10)
        qty = d.get_sell_quantity_at_target1()
        # base=33% ≈ floor — result is 3 lots (floor rounding)
        self.assertEqual(qty, 3 * pos.lot_size)

    def test_near_3t_10lots_breakeven_floor_active(self):
        """
        NEAR 3T 10L: entry=350 range-low=340 T1=360
        range_width=10, t1_gap=10 → floor=10/20=50% > base=33.3%
        floor active → ceil(10*0.5)=5 lots.
        Verify net P&L >= 0 on reversal: 5*(360-350) + 5*(340-350) = 50-50 = 0 ✓
        """
        pos = make_position(entry_price=350, stoploss=335, targets=[360, 390, 420],
                            second_entry_price=340, isBreakoutStrategy=False)
        d = make_fake_demat(pos, total_lots=10)
        qty = d.get_sell_quantity_at_target1()
        self.assertEqual(qty, 5 * pos.lot_size)
        # verify break-even
        t1_profit = (360 - 350) * qty
        remaining  = d.total_trading_quantity - qty
        sl_loss    = (340 - 350) * remaining   # negative
        net = t1_profit + sl_loss
        self.assertGreaterEqual(net, 0, f"Net P&L should be >=0, got {net}")

    def test_near_3t_6lots_breakeven_floor_active(self):
        """
        NEAR 3T 6L: entry=350 range-low=340 T1=360 (same tight scenario)
        floor=50% → ceil(6*0.5)=3 lots.
        """
        pos = make_position(entry_price=350, stoploss=335, targets=[360, 390, 420],
                            second_entry_price=340, isBreakoutStrategy=False)
        d = make_fake_demat(pos, total_lots=6)
        qty = d.get_sell_quantity_at_target1()
        self.assertEqual(qty, 3 * pos.lot_size)

    def test_near_breakeven_guarantee(self):
        """
        Parametric check: for any (range_width, t1_gap, lots), after T1 partial
        sell + full reversal to range-low, net P&L must be >= 0.
        Covers the ceiling-rounding requirement.
        """
        cases = [
            # range_width, t1_gap, lots
            (10, 10, 10),
            (10, 18, 10),   # original example from summary
            (15, 25, 8),
            (10, 10, 6),
            (10, 10, 4),
        ]
        for rw, tg, lots in cases:
            entry = 350
            range_low = entry - rw
            t1 = entry + tg
            pos = make_position(entry_price=entry, stoploss=range_low-5, targets=[t1, t1+25, t1+50],
                                second_entry_price=range_low, isBreakoutStrategy=False)
            d = make_fake_demat(pos, total_lots=lots)
            qty = d.get_sell_quantity_at_target1()
            remaining = d.total_trading_quantity - qty
            net = (t1 - entry) * qty + (range_low - entry) * remaining
            self.assertGreaterEqual(net, 0,
                f"Break-even failed: rw={rw} tg={tg} lots={lots} qty={qty} net={net}")

    # ── T2 edge cases ─────────────────────────────────────────────────────────

    def test_t2_returns_zero_when_one_lot_remains(self):
        """If only 1 lot left at T2, returns 0 (defer to last target)."""
        pos = make_position(entry_price=350, stoploss=335, targets=[370, 400, 430],
                            isBreakoutStrategy=True, onCrossingAbove=True)
        d = make_fake_demat(pos, total_lots=1)
        qty = d.get_sell_quantity_at_target2()
        self.assertEqual(qty, 0)

    def test_t2_never_takes_last_lot(self):
        """T2 must always leave at least 1 lot for the final target."""
        pos = make_position(entry_price=350, stoploss=335, targets=[370, 400, 430, 460],
                            isBreakoutStrategy=True, onCrossingAbove=True)
        for lots in [2, 3, 4, 6, 10]:
            d = make_fake_demat(pos, total_lots=lots)
            # Simulate T1 already taken
            d.remaining_quantity -= pos.lot_size
            qty = d.get_sell_quantity_at_target2()
            remaining_after = d.remaining_quantity - qty
            self.assertGreaterEqual(remaining_after, pos.lot_size,
                f"T2 consumed last lot at {lots}L: qty={qty} remaining={d.remaining_quantity}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. State machine simulation — mirrors the on_price / TradeHandler tick logic
# ─────────────────────────────────────────────────────────────────────────────
class TradeSimulator:
    """
    Lightweight replay of the SOT_BOTv8 on_price state machine.
    Accepts a price sequence and records every event.
    No Fyers, no Telegram, no threads.
    """

    def __init__(self, *, entry_price, fill_price, stoploss,
                 targets, second_entry_price=None,
                 isBreakoutStrategy=False, onCrossingAbove=False,
                 lots=10, lot_size=1, squareoff_at_first_target=False,
                 precise_trailing=True, sl_at_cost=False):
        self.entry_price            = entry_price   # original signal level
        self.fill_price             = fill_price    # actual fill (may differ on re-entry)
        self.stoploss               = stoploss
        self.targets_list           = targets
        self.second_entry_price     = second_entry_price
        self.isBreakoutStrategy     = isBreakoutStrategy
        self.onCrossingAbove        = onCrossingAbove
        self.lot_size               = lot_size
        self.total_qty              = lots * lot_size
        self.remaining_qty          = lots * lot_size
        self.precise_trailing       = precise_trailing
        self.squareoff_at_first_target = squareoff_at_first_target
        self.sl_at_cost             = sl_at_cost

        # avg_price mirrors line 523 of SOT_BOTv8:
        # ABOVE → entry_price (original signal level), NEAR → fill_price
        self.avg_price = entry_price if onCrossingAbove else fill_price

        self._booked       = [False] * len(targets)
        self._sold_at_t1   = False
        self.stop_loss     = stoploss
        self.lazy_stoploss = stoploss
        self.is_open       = True

        self.events = []   # list of (tick_price, event_str, state_snapshot)

        # ── Late-entry guard (mirrors enter() in SOT_BOTv8) ──────────────────
        if onCrossingAbove and targets and fill_price > targets[0]:
            self._booked[0]  = True
            self._sold_at_t1 = True
            sl_anchor = entry_price
            self.stop_loss     = sl_anchor - (2 if onCrossingAbove else 0)
            self.lazy_stoploss = self.stop_loss
            self._log(fill_price,
                      f"LATE-ENTRY: fill {fill_price} > T1 {targets[0]} — "
                      f"T1 skipped, SL→{self.stop_loss}")

    def _log(self, price, msg):
        snap = {
            'stop_loss': self.stop_loss,
            'remaining_qty': self.remaining_qty,
            'booked': list(self._booked),
        }
        self.events.append((price, msg, snap))

    def _sell(self, qty, price, label):
        self.remaining_qty -= qty
        profit = (price - self.avg_price) * qty
        self._log(price, f"{label}: sold {qty} @ {price}, profit={profit:+.0f}, remaining={self.remaining_qty}")
        return profit

    def tick(self, cmp: float):
        if not self.is_open:
            return

        # SL hit
        if round(cmp) <= self.stop_loss:
            self._sell(self.remaining_qty, cmp, "SL-HIT")
            self.is_open = False
            return

        # ── Target booking loop (sequential if, not elif) ─────────────────────
        for i, tgt in enumerate(self.targets_list):
            if round(cmp) < tgt or self._booked[i]:
                continue

            is_last = (i == len(self.targets_list) - 1)

            if is_last or self.squareoff_at_first_target:
                qty = self.remaining_qty
            elif i == 0:
                # Reproduce get_sell_quantity_at_target1 math
                n = len(self.targets_list)
                base_frac = 1.0 / n
                sell_frac = base_frac
                if (self.second_entry_price is not None
                        and not self.isBreakoutStrategy
                        and tgt > self.fill_price):
                    rw = self.fill_price - self.second_entry_price
                    t1g = tgt - self.fill_price
                    if rw > 0 and t1g > 0:
                        be_frac = rw / (t1g + rw)
                        sell_frac = max(base_frac, be_frac)
                floor_active = sell_frac > base_frac
                raw = self.total_qty * sell_frac / self.lot_size
                lots_to_sell = math.ceil(raw) if floor_active else int(raw)
                qty = lots_to_sell * self.lot_size
                qty = min(qty, self.remaining_qty - self.lot_size)
                qty = max(qty, self.lot_size)
            else:
                # Reproduce get_sell_quantity_at_target2 math
                if self.remaining_qty <= self.lot_size:
                    self._log(cmp, f"T{i+1}: 0 qty (1 lot remaining, deferring)")
                    self._booked[i] = True
                    continue
                n = max(len(self.targets_list) - 1, 1)
                raw = self.remaining_qty / n + self.lot_size / 2
                qty = int(raw - (raw % self.lot_size))
                qty = min(qty, self.remaining_qty - self.lot_size)
                qty = max(qty, self.lot_size)

            self._sell(qty, cmp, f"TARGET{i+1}")
            self._booked[i] = True
            if i == 0:
                self._sold_at_t1 = True

            # SL adjustment
            if i == 0:
                if self.sl_at_cost:
                    sl_anchor = self.avg_price
                elif self.second_entry_price is not None and not self.isBreakoutStrategy:
                    sl_anchor = self.second_entry_price
                else:
                    sl_anchor = self.avg_price
                self.stop_loss = sl_anchor if self.precise_trailing else sl_anchor + 3
                if self.onCrossingAbove:
                    self.stop_loss -= 2
                self.lazy_stoploss = self.stop_loss
                mode = "cost" if self.sl_at_cost else ("range-low" if self.second_entry_price and not self.isBreakoutStrategy else "breakeven")
                self._log(cmp, f"T1 SL moved to {self.stop_loss} (anchor={sl_anchor}, mode={mode})")
            elif not is_last:
                if self.sl_at_cost:
                    self._log(cmp, f"T{i+1} hit — SL stays at cost {self.stop_loss} (sl_at_cost mode)")
                else:
                    self.stop_loss = self.targets_list[i - 1]
                    self._log(cmp, f"T{i+1} SL moved to T{i}={self.stop_loss}")

            if is_last:
                self.is_open = False
                return

    def run(self, prices):
        for p in prices:
            self.tick(p)
        return self

    def print_summary(self):
        print(f"\n{'='*60}")
        print(f"  TRADE SUMMARY")
        print(f"  entry={self.entry_price} fill={self.fill_price} "
              f"SL={self.stoploss} T={'|'.join(str(t) for t in self.targets_list)}")
        print(f"{'='*60}")
        for price, msg, snap in self.events:
            print(f"  @{price:<6}  {msg}")
        print(f"  Final: open={self.is_open} remaining={self.remaining_qty} SL={self.stop_loss}")
        print()


# ─────────────────────────────────────────────────────────────────────────────
# 3. State machine tests
# ─────────────────────────────────────────────────────────────────────────────
class StateMachineTests(unittest.TestCase):

    # ── ABOVE: normal path ────────────────────────────────────────────────────

    def test_above_4t_normal_path(self):
        """ABOVE 4T 10L: T1→T2→T3→T4 hit in sequence."""
        sim = TradeSimulator(
            entry_price=380, fill_price=382,
            stoploss=365, targets=[395, 420, 445, 470],
            isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1
        )
        sim.run([384, 390, 396, 415, 421, 446, 471])
        self.assertTrue(all(sim._booked))
        self.assertFalse(sim.is_open)

    def test_above_4t_all_booked_in_order(self):
        """Each target fires exactly once."""
        sim = TradeSimulator(
            entry_price=380, fill_price=382,
            stoploss=365, targets=[395, 420, 445, 470],
            isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1
        )
        sim.run([396, 421, 446, 471])
        booked_events = [e for e in sim.events if e[1].startswith("TARGET")]
        self.assertEqual(len(booked_events), 4)

    # ── ABOVE: spike scenario ─────────────────────────────────────────────────

    def test_above_4t_spike_to_t3(self):
        """
        Price spikes from entry directly to T3 level (445) skipping T1 and T2.
        All three — T1, T2, T3 — must be booked in the same tick.
        T4 must still be open.
        """
        sim = TradeSimulator(
            entry_price=380, fill_price=382,
            stoploss=365, targets=[395, 420, 445, 470],
            isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1
        )
        sim.tick(446)   # single tick at T3 level
        self.assertTrue(sim._booked[0], "T1 should have been booked")
        self.assertTrue(sim._booked[1], "T2 should have been booked")
        self.assertTrue(sim._booked[2], "T3 should have been booked")
        self.assertFalse(sim._booked[3], "T4 should still be open")
        self.assertTrue(sim.is_open, "Trade should still be open (T4 not hit)")
        self.assertGreater(sim.remaining_qty, 0)

    def test_above_4t_spike_to_last(self):
        """Price spikes directly to T4 — all targets booked, trade closed."""
        sim = TradeSimulator(
            entry_price=380, fill_price=382,
            stoploss=365, targets=[395, 420, 445, 470],
            isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1
        )
        sim.tick(471)
        self.assertTrue(all(sim._booked))
        self.assertFalse(sim.is_open)
        self.assertEqual(sim.remaining_qty, 0)

    # ── ABOVE: late re-entry above T1 ────────────────────────────────────────

    def test_above_late_entry_skips_t1(self):
        """
        Re-entry fill (398) > T1 (395).
        T1 must be pre-marked booked, no sell at T1, SL = entry_price - 2 = 378.
        """
        sim = TradeSimulator(
            entry_price=380, fill_price=398,  # fill above T1!
            stoploss=365, targets=[395, 420, 445, 470],
            isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1
        )
        # T1 should already be skipped at construction
        self.assertTrue(sim._booked[0], "T1 should be pre-booked (skip)")
        self.assertEqual(sim.stop_loss, 378, "SL should be entry_price-2=378")
        # All 10 lots still intact (no T1 sell)
        self.assertEqual(sim.remaining_qty, 10)
        # Now T2 hit — should book normally
        sim.tick(421)
        self.assertTrue(sim._booked[1])
        self.assertLess(sim.remaining_qty, 10)

    def test_above_late_entry_sl_is_signal_level_not_fill(self):
        """SL after late-entry must be at the ORIGINAL breakout level (380), not fill (398)."""
        sim = TradeSimulator(
            entry_price=380, fill_price=398,
            stoploss=365, targets=[395, 420, 445, 470],
            isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1
        )
        self.assertEqual(sim.stop_loss, 378,
                         "SL must reflect breakout level 380 (minus -2 buffer), not fill 398")

    # ── ABOVE: SL after T1 ────────────────────────────────────────────────────

    def test_above_sl_moves_to_entry_price_after_t1(self):
        """After T1 hit, SL must move to entry_price-2 (378), not avg fill price."""
        sim = TradeSimulator(
            entry_price=380, fill_price=382,   # actual fill slightly above
            stoploss=365, targets=[395, 420, 445, 470],
            isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1
        )
        sim.tick(396)   # T1 hit
        self.assertTrue(sim._booked[0])
        self.assertEqual(sim.stop_loss, 378,   # 380 - 2
                         f"SL should be 378 after T1, got {sim.stop_loss}")

    # ── NEAR: SL moves to range-low after T1 ─────────────────────────────────

    def test_near_sl_moves_to_range_low_after_t1(self):
        """
        NEAR trade: entry=350, range-low=340.
        After T1 hit, SL must be range-low (340), not fill price (350).
        """
        sim = TradeSimulator(
            entry_price=350, fill_price=350,
            stoploss=335, targets=[370, 400, 430],
            second_entry_price=340, isBreakoutStrategy=False, onCrossingAbove=False,
            lots=10, lot_size=1
        )
        sim.tick(371)   # T1 hit
        self.assertTrue(sim._booked[0])
        self.assertEqual(sim.stop_loss, 340,
                         f"NEAR SL after T1 should be range-low 340, got {sim.stop_loss}")

    def test_near_t1_reversal_net_pnl_nonnegative(self):
        """
        NEAR 3T 10L: T1 hit, then price reverses to SL (range-low).
        Net P&L across the whole trade must be >= 0.
        """
        entry, range_low, t1 = 350, 340, 360
        sim = TradeSimulator(
            entry_price=entry, fill_price=entry,
            stoploss=335, targets=[t1, 390, 420],
            second_entry_price=range_low, isBreakoutStrategy=False, onCrossingAbove=False,
            lots=10, lot_size=1
        )
        sim.tick(t1 + 1)    # T1 hit
        sim.tick(range_low)  # reversal to SL
        # Collect total profit from all events
        total_net = sum(
            int(e[1].split("profit=")[1].split(",")[0])
            for e in sim.events if "profit=" in e[1]
        )
        self.assertGreaterEqual(total_net, 0,
                                f"Net P&L after T1+reversal should be >=0, got {total_net}")

    # ── NEAR: sequential spike ────────────────────────────────────────────────

    def test_near_4t_spike_skips_t2(self):
        """
        NEAR 4T: price spikes from below T2 directly above T3.
        T2 and T3 must both be booked in the same tick.
        """
        sim = TradeSimulator(
            entry_price=350, fill_price=350,
            stoploss=335, targets=[370, 400, 430, 460],
            second_entry_price=340, isBreakoutStrategy=False, onCrossingAbove=False,
            lots=10, lot_size=1
        )
        sim.tick(371)   # T1 normal
        sim.tick(431)   # spike past T2=400 directly to T3=430 zone
        self.assertTrue(sim._booked[1], "T2 should be booked on spike")
        self.assertTrue(sim._booked[2], "T3 should be booked on same spike tick")
        self.assertFalse(sim._booked[3], "T4 still open")

    # ── SL trailing: each target hit moves SL to previous target ─────────────

    def test_sl_trails_to_previous_target_after_each_hit(self):
        """
        ABOVE 5T: after each intermediate target hit, SL must equal the
        previous target level.  Specifically:
          T2 hit → SL = T1
          T3 hit → SL = T2
          T4 hit → SL = T3
        """
        targets = [395, 420, 445, 470, 495]
        sim = TradeSimulator(
            entry_price=380, fill_price=382,
            stoploss=365, targets=targets,
            isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1
        )
        sim.tick(396)           # T1
        self.assertEqual(sim.stop_loss, 378)   # entry_price - 2

        sim.tick(421)           # T2
        self.assertEqual(sim.stop_loss, targets[0],  # T1 = 395
                         "After T2, SL should be T1")

        sim.tick(446)           # T3
        self.assertEqual(sim.stop_loss, targets[1],  # T2 = 420
                         "After T3, SL should be T2")

        sim.tick(471)           # T4
        self.assertEqual(sim.stop_loss, targets[2],  # T3 = 445
                         "After T4, SL should be T3")

    def test_reversal_from_t3_to_t2_closes_on_sl(self):
        """
        ABOVE 4T: price hits T1, T2, T3 then reverses.
        After T3 hit, SL = T2.  When price falls to T2, remaining lots close.
        """
        targets = [395, 420, 445, 470]
        sim = TradeSimulator(
            entry_price=380, fill_price=382,
            stoploss=365, targets=targets,
            isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1
        )
        sim.tick(396)   # T1
        sim.tick(421)   # T2
        sim.tick(446)   # T3 — SL should now be T2 (420)

        self.assertEqual(sim.stop_loss, targets[1],   # 420
                         "After T3, SL must be at T2=420")
        self.assertTrue(sim.is_open, "Trade still open, T4 not hit")

        sim.tick(420)   # reversal to exactly T2 — should trigger SL
        self.assertFalse(sim.is_open, "Reversal to T2 should close the trade via SL")
        self.assertEqual(sim.remaining_qty, 0)

    def test_reversal_above_sl_does_not_close(self):
        """
        After T3 hit (SL=T2=420), a dip to 425 must NOT close the trade.
        Price needs to actually reach T2 (420) to trigger SL.
        """
        targets = [395, 420, 445, 470]
        sim = TradeSimulator(
            entry_price=380, fill_price=382,
            stoploss=365, targets=targets,
            isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1
        )
        sim.tick(396); sim.tick(421); sim.tick(446)  # T1, T2, T3
        sim.tick(425)   # dip — above T2, should NOT close
        self.assertTrue(sim.is_open, "Dip to 425 with SL=420 should not close trade")

    # ── sl_at_cost mode ───────────────────────────────────────────────────────

    def test_sl_at_cost_near_uses_avg_not_range_low(self):
        """
        NEAR 3T with sl_at_cost=True:
        After T1 hit, SL must be avg_price (350 = cost), NOT range-low (340).
        """
        sim = TradeSimulator(
            entry_price=350, fill_price=350,
            stoploss=335, targets=[370, 400, 430],
            second_entry_price=340, isBreakoutStrategy=False, onCrossingAbove=False,
            lots=10, lot_size=1, sl_at_cost=True
        )
        sim.tick(371)   # T1 hit
        self.assertEqual(sim.stop_loss, 350,
                         "sl_at_cost NEAR: SL after T1 must be avg_price=350 (cost), not 340 (range-low)")

    def test_sl_at_cost_does_not_trail_on_t2(self):
        """
        sl_at_cost: after T2 hit, SL must STAY at cost (350), not move to T1 (370).
        Normal mode would trail to T1.
        """
        sim = TradeSimulator(
            entry_price=350, fill_price=350,
            stoploss=335, targets=[370, 400, 430],
            second_entry_price=340, isBreakoutStrategy=False, onCrossingAbove=False,
            lots=10, lot_size=1, sl_at_cost=True
        )
        sim.tick(371)   # T1 — SL → cost (350)
        sim.tick(401)   # T2 — SL must stay at 350, not move to 370
        self.assertEqual(sim.stop_loss, 350,
                         "sl_at_cost: SL must not trail up after T2")

    def test_sl_at_cost_does_not_trail_on_t3(self):
        """sl_at_cost: after T3 hit, SL stays at cost, not T2."""
        sim = TradeSimulator(
            entry_price=350, fill_price=350,
            stoploss=335, targets=[370, 400, 430, 460],
            second_entry_price=340, isBreakoutStrategy=False, onCrossingAbove=False,
            lots=10, lot_size=1, sl_at_cost=True
        )
        sim.tick(371); sim.tick(401); sim.tick(431)
        self.assertEqual(sim.stop_loss, 350,
                         "sl_at_cost: SL must not trail after T3")

    def test_sl_at_cost_above_also_stays_at_cost(self):
        """
        ABOVE with sl_at_cost: after T1 SL = avg_price (entry_price for ABOVE),
        after T2 SL stays at cost, not T1.
        """
        sim = TradeSimulator(
            entry_price=380, fill_price=382,
            stoploss=365, targets=[395, 420, 445, 470],
            isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1,
            sl_at_cost=True
        )
        sim.tick(396)   # T1 — SL → avg_price (380) - 2 = 378
        cost_sl = sim.stop_loss
        sim.tick(421)   # T2 — SL must stay at cost
        self.assertEqual(sim.stop_loss, cost_sl,
                         "sl_at_cost ABOVE: SL must not trail after T2")

    def test_sl_at_cost_vs_normal_comparison(self):
        """
        Side-by-side: same price sequence, sl_at_cost vs normal.
        After T3 hit (SL=T2=400 in normal vs SL=cost=350 in sl_at_cost),
        reversal to 370 closes the normal trade but NOT the sl_at_cost trade.
        """
        kwargs = dict(
            entry_price=350, fill_price=350, stoploss=335,
            targets=[370, 400, 430, 460],
            second_entry_price=340, isBreakoutStrategy=False, onCrossingAbove=False,
            lots=10, lot_size=1
        )
        prices_up   = [371, 401, 431]
        reversal    = [370]   # dip to T1 level

        normal = TradeSimulator(**kwargs, sl_at_cost=False)
        normal.run(prices_up + reversal)

        cost_mode = TradeSimulator(**kwargs, sl_at_cost=True)
        cost_mode.run(prices_up + reversal)

        # Normal: SL was trailed to T2=400, so T3 hit moved it to T2=400;
        # then reversal to 370 (< 400) closes the trade
        self.assertFalse(normal.is_open,
                         "Normal mode: reversal to T1 level after T3 should close (SL=T2)")
        # sl_at_cost: SL stays at 350 throughout; reversal to 370 > 350 keeps it open
        self.assertTrue(cost_mode.is_open,
                        "sl_at_cost mode: reversal to T1 level with SL still at cost should stay open")

    # ── Minimum-lots validation ───────────────────────────────────────────────

    def test_above_minimum_lots_each_target_gets_one(self):
        """
        For ABOVE Nt signal, minimum lots = N.
        Each target should get at least 1 unit (lot_size=1).
        """
        for n_targets in [3, 4, 5, 6]:
            targets = [380 + i * 25 for i in range(1, n_targets + 1)]
            sim = TradeSimulator(
                entry_price=380, fill_price=382,
                stoploss=365, targets=targets,
                isBreakoutStrategy=True, onCrossingAbove=True,
                lots=n_targets, lot_size=1  # minimum lots = num_targets
            )
            for t in targets:
                sim.tick(t + 1)
            booked_targets = [e for e in sim.events if e[1].startswith("TARGET")]
            self.assertEqual(len(booked_targets), n_targets,
                             f"With {n_targets}T and {n_targets}L, all targets should book")
            for event in booked_targets:
                qty_str = event[1].split("sold ")[1].split(" @")[0]
                self.assertGreaterEqual(int(qty_str), 1,
                                        f"Each target should sell at least 1 lot")


# ─────────────────────────────────────────────────────────────────────────────
# Manual runner — prints readable summary for visual inspection
# ─────────────────────────────────────────────────────────────────────────────
def run_visual_scenarios():
    print("\n" + "="*60)
    print("  VISUAL SCENARIO REPLAY")
    print("="*60)

    scenarios = [
        {
            "name": "ABOVE 4T — normal path (10L)",
            "kwargs": dict(entry_price=380, fill_price=382, stoploss=365,
                           targets=[395, 420, 445, 470],
                           isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1),
            "prices": [384, 390, 396, 415, 421, 446, 471],
        },
        {
            "name": "ABOVE 4T — spike to T3 in one tick (10L)",
            "kwargs": dict(entry_price=380, fill_price=382, stoploss=365,
                           targets=[395, 420, 445, 470],
                           isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1),
            "prices": [384, 446],      # jumps straight past T1 and T2
        },
        {
            "name": "ABOVE 4T — late re-entry above T1 (10L, fill=398 > T1=395)",
            "kwargs": dict(entry_price=380, fill_price=398, stoploss=365,
                           targets=[395, 420, 445, 470],
                           isBreakoutStrategy=True, onCrossingAbove=True, lots=10, lot_size=1),
            "prices": [421, 446, 471],
        },
        {
            "name": "NEAR 3T — T1 then reversal to range-low (10L)",
            "kwargs": dict(entry_price=350, fill_price=350, stoploss=335,
                           targets=[370, 400, 430],
                           second_entry_price=340, isBreakoutStrategy=False,
                           onCrossingAbove=False, lots=10, lot_size=1),
            "prices": [371, 340],
        },
        {
            "name": "NEAR 4T — sl_at_cost: SL stays at cost, not trailing (10L)",
            "kwargs": dict(entry_price=350, fill_price=350, stoploss=335,
                           targets=[370, 400, 430, 460],
                           second_entry_price=340, isBreakoutStrategy=False,
                           onCrossingAbove=False, lots=10, lot_size=1, sl_at_cost=True),
            "prices": [371, 401, 431, 350],  # reversal to cost after T3 → stays open
        },
        {
            "name": "NEAR 4T — spike skips T2 (10L)",
            "kwargs": dict(entry_price=350, fill_price=350, stoploss=335,
                           targets=[370, 400, 430, 460],
                           second_entry_price=340, isBreakoutStrategy=False,
                           onCrossingAbove=False, lots=10, lot_size=1),
            "prices": [371, 431, 461],
        },
    ]

    for s in scenarios:
        print(f"\n>>> {s['name']}")
        sim = TradeSimulator(**s["kwargs"])
        sim.run(s["prices"])
        sim.print_summary()


if __name__ == "__main__":
    if "-v" in sys.argv or "--visual" in sys.argv:
        # Strip the flag before passing to unittest
        sys.argv = [a for a in sys.argv if a not in ("-v", "--visual")]
        run_visual_scenarios()
    unittest.main(verbosity=2)
