from utils.constants import Config
import re


class Position():
    def __init__(self,strike,entry_price,stoploss,target1,target2,target3,isBreakoutStrategy,enterFewPointsAbove,second_entry_price=None,onCrossingAbove=False,instrument=None,ce_pe=None,spot=None,exit_strategy=None,num_targets=3,targets=None,sl_at_cost=False) -> None:
        self.instrument = instrument
        self.ce_pe = ce_pe
        self.strike = strike
        self.entry_price = int(entry_price)
        self.stoploss = int(stoploss)
        self.target1 = int(target1)
        self.target2 = int(target2)
        self.target3 = int(target3)
        self.second_entry_price = second_entry_price
        self.stoploss_orderID = None
        self.exit_price = None #populated on exit at runtime
        self.isBreakoutStrategy = isBreakoutStrategy
        self.enterFewPointsAbove = enterFewPointsAbove
        self.onCrossingAbove = onCrossingAbove
        self.spot = spot
        self.exit_strategy = exit_strategy
        # Full ordered list of all targets from the mentor's signal.
        # Drives per-target partial booking and spike-skip handling.
        if targets:
            self.targets = [int(t) for t in targets]
        else:
            self.targets = [int(target1), int(target2), int(target3)]
        self.num_targets = max(len(self.targets), 2)
        self.sl_at_cost = sl_at_cost
        self.isAveraged = False
        if ":" not in instrument:
            self.lot_size = Config.lot_size_map.get(instrument, None)
            self.freeze_quantity = Config.freeze_quantity_map.get(instrument, None)
        elif ":" in instrument:
            match = re.search(r':([A-Za-z]+)', instrument)
            if match:
                self.lot_size = Config.lot_size_map.get(match.group(1), None)
                self.freeze_quantity = Config.freeze_quantity_map.get(match.group(1), None)
                if self.lot_size is None:
                    raise ValueError(f"Couldn't Resolve Lot Size for {match}")
                if self.freeze_quantity is None:
                    raise ValueError(f"Couldn't Resolve Freeze Quantity for {match}")
        else:
            self.lot_size = None
            self.freeze_quantity = None