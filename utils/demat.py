import calendar
import csv
import logging
import math
import pprint
import time
import traceback
import uuid
from datetime import datetime, date

import chime
from fyers_apiv3 import fyersModel
from utils.account_config import AccountConfig
from utils.clock import Clock
from utils.constants import Config
from utils.deco import retry
from utils.position import Position


class Demat(Config):
    
    def __init__(self,account: AccountConfig,logger:logging=None) -> None:
        super().__init__()
        self.fyers = fyersModel.FyersModel(token=account.access_token, is_async=False, client_id=account.client_id,log_path=super().fyers_log_path)
        self.paper_trading = account.paper_trade #super().paper_trade
        self.account = account
        self.account_name = account.name
        self.quantity_nifty = account.quantity_nifty
        self.quantity_banknifty = account.quantity_banknifty
        self.quantity,self.quantity2 = None, None
        self.should_average = account.should_average
        self.isAveraged = False
        self.squareoff_at_first_target = account.squareoff_at_first_target
        self.total_trading_quantity = 0
        self.average_price = 0
        self.stoploss_orderID = None
        self.remaining_quantity = 0
        self.position: Position = None
        self.position_open = False
        self.switch_stragey_on_quantity = True
        self.entries = "1st"
        self.PnL = 0
        self.peak_profit = 0
        self.logger = logger or logging.getLogger(__name__)
        self.bot = "bot"
        self.trade_type = "MARGIN"
        self.entry_time = None
        self.exit_time = None
        self.build_number = ""
        self.instrument = None

    def generatePnL(self):
        today = date.today()
        current_month = datetime.now().month
        month_name = calendar.month_name[current_month]

        today_date = datetime.strptime(str(today), "%Y-%m-%d")
        # To get the week number
        week_number = str(today_date.strftime("%U"))
        # name of csv file 
        headers = ["Bot","BuildNumber","Date","Month","Week","Day","EntryTime","ExitTime","AccountName","isBreakoutStrategy","onCrossingAbove","SquaredOffAtFirstTarget","isAveraged","awaitNextTarget","AggressiveTrail","Instrument","Option","Quantity","AvgEntryPrice","ExitPrice","PnL","Exposure","Points","PaperTrading"]
        filename = (str(self.build_number) + "#" + str(self.position.strike).replace("NSE:","") + "_" + str(today) + "_" + str(uuid.uuid4()) + "_pnl.csv")
        # writing to csv file 
        with open(Config.logger_path+"/"+filename, 'w+') as csvfile: 
            # creating a csv writer object 
            csvwriter = csv.writer(csvfile) 
            # writing the fields 
            csvwriter.writerow(headers) 
            # writing the data rows
            csvwriter.writerows([[self.bot,self.build_number,today,month_name,week_number,datetime.now().strftime("%A"),time.strftime("%H:%M:%S", time.localtime(self.entry_time)),time.strftime("%H:%M:%S", time.localtime(self.exit_time)),self.account.name,self.position.isBreakoutStrategy,self.position.onCrossingAbove,self.squareoff_at_first_target,self.isAveraged,self.account.await_next_target,self.account.aggressive_trail,self.instrument,self.position.strike,self.total_trading_quantity,self.average_price,self.position.exit_price,self.PnL,self.exit_time-self.entry_time,self.position.exit_price - self.average_price,self.paper_trading]])
    
    def print_demat_status(self):
        self.logger.debug("------------------------------------------------")
        self.logger.debug(pprint.pformat(self.__dict__))
        self.logger.debug("------------------------------------------------")

    def prepare_for_position(self,position: Position,bot_name,build_number):
        self.position = position
        self.bot = bot_name
        self.build_number = build_number

        # set quatntiy based on Nifty or BankNifty
        # self.quantity = self.quantity_banknifty if "BANK" in self.position.strike else self.quantity_nifty
        if "BANKNIFTY" in self.position.strike:
            self.logger.debug(f"{self.account.name} Setting Quantity for BANKNIFTY: {self.account.quantity_banknifty}")
            self.quantity = self.account.quantity_banknifty
            self.instrument = "BANKNIFTY"

        elif "FINNIFTY" in self.position.strike:
            self.logger.debug(f"{self.account.name} Setting Quantity for FINNIFTY: {self.account.quantity_finnifty}")
            self.quantity = self.account.quantity_finnifty
            self.instrument = "FINNIFTY"

        elif "BAJFINANCE" in self.position.strike:
            self.logger.debug(f"{self.account.name} Setting Quantity for BAJFINANCE: {self.account.quantity_bajfinance}")
            # self.trade_type = "INTRADAY"
            self.quantity = self.account.quantity_bajfinance
            self.instrument = "BAJFINANCE"

        elif "MIDCPNIFTY" in self.position.strike:
            self.logger.debug(f"{self.account.name} Setting Quantity for NIFTY: {self.account.quantity_midcpnifty}")
            self.quantity = self.account.quantity_midcpnifty
            self.instrument = "MIDCPNIFTY"
        
        elif "NIFTY" in self.position.strike:
            self.logger.debug(f"{self.account.name} Setting Quantity for NIFTY: {self.account.quantity_nifty}")
            self.quantity = self.account.quantity_nifty
            self.instrument = "NIFTY"

        self.quantity2 = self.quantity
        
        if self.quantity is None:
            self.paper_trade = True
            self.quantity = self.position.lot_size
            self.quantity2 = self.quantity
            self.logger.warning(f"{self.account.name} Quantity Found to be None. Chosen 1 lot and Marked for paper trade.")

        if self.quantity % self.position.lot_size != 0:
            self.logger.error(f"{self.account.name} Expected quantity to be in multiple of {self.position.lot_size} for {self.position.strike} instead received {self.quantity}")
            raise Exception("Invalid Quantity Received.")
        
        # check if the account should trade in Derivative and set paper trade to true if disabled
        if not self.account.trade_banknifty and "BANKNIFTY" in self.position.strike:
            self.paper_trade = True
            self.logger.warning(f"{self.account.name} Trading on {self.position.strike} is disabled, Marked for Paper Trade.")

        elif not self.account.trade_finnifty and "FINNIFTY" in self.position.strike:
            self.paper_trade = True
            self.logger.warning(f"{self.account.name} Trading on {self.position.strike} is disabled, Marked for Paper Trade.")

        elif not self.account.trade_bajfinance and "BAJFINANCE" in self.position.strike:
            self.paper_trade = True
            self.logger.warning(f"{self.account.name} Trading on {self.position.strike} is disabled, Marked for Paper Trade.")

        elif not self.account.trade_nifty and "NIFTY" in self.position.strike:
            self.paper_trade = True
            self.logger.warning(f"{self.account.name} Trading on {self.position.strike} is disabled, Marked for Paper Trade.")

        # update should average based on range or breakout strategy
        self.should_average = False if self.position.isBreakoutStrategy else self.should_average
        if not self.account.await_next_target:
            self.squareoff_at_first_target = True if not self.quantity >= self.position.lot_size * 3 else self.account.squareoff_at_first_target
        
    def get_sell_quantity_at_target1(self):
        # Base fraction: 1/num_targets (hold as much as possible for later targets).
        n = self.position.num_targets
        base_fraction = 1.0 / n

        # For NEAR trades the SL after T1 moves to range-low (second_entry_price),
        # not to the entry fill.  This creates a residual risk of (entry - range_low)
        # on every remaining lot.  Compute the minimum sell fraction needed so that
        # T1 profits cover that residual risk if price fully reverses to range-low:
        #
        #   break_even_fraction = range_width / (T1_gap + range_width)
        #
        # Use whichever fraction is larger so we never lock in a net loss purely
        # from a T1-hit + SL-to-range-low reversal.
        sell_fraction = base_fraction
        if (self.position.second_entry_price is not None
                and not self.position.isBreakoutStrategy
                and self.position.target1 > self.position.entry_price):
            range_width = self.position.entry_price - self.position.second_entry_price
            t1_gap = self.position.target1 - self.position.entry_price
            if range_width > 0 and t1_gap > 0:
                break_even_fraction = range_width / (t1_gap + range_width)
                sell_fraction = max(base_fraction, break_even_fraction)
                if break_even_fraction > base_fraction:
                    self.logger.debug(
                        f"{self.account.name} T1 break-even floor applied: "
                        f"range={range_width}pts t1_gap={t1_gap}pts "
                        f"min_sell={break_even_fraction:.0%} > 1/{n}={base_fraction:.0%}"
                    )

        # When the break-even floor is active, use ceiling rounding to guarantee
        # T1 profits >= SL-to-range-low residual risk (floor rounding would leave
        # a fractional lot's worth of unhedged loss).
        # When holding proportionally (no floor), floor rounding to preserve lots.
        floor_active = sell_fraction > base_fraction
        raw_lots = self.total_trading_quantity * sell_fraction / self.position.lot_size
        if floor_active:
            quantity_at_target1 = math.ceil(raw_lots) * self.position.lot_size
        else:
            quantity_at_target1 = int(raw_lots) * self.position.lot_size
        # never sell everything — keep at least one lot for the final target
        quantity_at_target1 = min(quantity_at_target1, self.remaining_quantity - self.position.lot_size)
        quantity_at_target1 = max(quantity_at_target1, self.position.lot_size)
        self.logger.debug(f"{self.account.name} Sell at Target1: {quantity_at_target1} ({sell_fraction:.0%} of total)")
        return quantity_at_target1

    def get_sell_quantity_at_target2(self):
        # If only one lot remains, skip T2 entirely — let T3 close it at the last target.
        if self.remaining_quantity <= self.position.lot_size:
            self.logger.debug(f"{self.account.name} Only 1 lot remaining at Target2 — deferring to Target3.")
            return 0
        # Sell 1/(num_targets-1) of REMAINING so the split stays proportional.
        # e.g. after T1 (remaining ≈ (n-1)/n of total): sells another ~1/n of original
        n = max(self.position.num_targets - 1, 1)
        quantity_at_target2 = self.remaining_quantity / n
        quantity_at_target2 = quantity_at_target2 + self.position.lot_size / 2
        quantity_at_target2 = int(quantity_at_target2 - (quantity_at_target2 % self.position.lot_size))
        # never sell everything — keep at least one lot for the final target
        quantity_at_target2 = min(quantity_at_target2, self.remaining_quantity - self.position.lot_size)
        quantity_at_target2 = max(quantity_at_target2, self.position.lot_size)
        self.logger.debug(f"{self.account.name} Quantity to sell at Target2 (1/{n} of remaining): {quantity_at_target2}")
        return quantity_at_target2

    @retry(3,3)
    def isAvailableFundSufficient(self,fundNeeded):
        # return True
        response = self.fyers.funds()
        self.logger.debug(f"fyers.funds: {response}")
        funds = response["fund_limit"]
        available_balance = [item for item in funds if "Available Balance" in item.values()][0]["equityAmount"]
        self.logger.debug(f"{self.account.name} : Available Balance: {available_balance}/-")
        return available_balance > fundNeeded, available_balance, fundNeeded

    def take_position(self,position: Position, price):
        if not self.paper_trading:
            are_funds_sufficient, available_funds, funds_needed = self.isAvailableFundSufficient(self.quantity*position.entry_price)
            if not are_funds_sufficient:
                self.logger.warning(f"{self.account.name} Phew! Insufficient Funds, cannot take an entry!")
                return { "account_name": self.account_name, "response" : {"available_funds":available_funds, "funds_needed": funds_needed, "verdict": "Dude, you don't have enough funds!"} }
        
        self.logger.debug(f"{self.account.name} : Placing {self.entries} Market Order for {self.position.strike} @{self.position.entry_price}/-")
        place_order_response = self.placeOrderFyers(self.position.strike, "BUY", self.quantity, "MARKET", self.position.entry_price, "regular")
        self.entry_time = time.time()
        self.average_price = self.position.entry_price if not self.position_open else ((self.quantity*self.average_price)+(self.quantity2*self.position.entry_price))/(self.quantity+self.quantity2)
        self.total_trading_quantity, self.remaining_quantity = self.total_trading_quantity + self.quantity, self.remaining_quantity + self.quantity
        self.logger.info(f"{self.account.name} : Open Postion: {self.position.strike} : Quantity: {self.remaining_quantity} : Average Price: {self.average_price} : OrderID: {place_order_response}")
        self.entries = "2nd"
        self.position_open = True
        self.print_demat_status()
        return place_order_response

    def add_stoploss(self):
        # self.postion updation shouldn't be done as the memory is shared and is a same object across acounts
        # self.position.stoploss_orderID = 0
        self.logger.debug(f"{self.account.name} :  Position {self.position.strike} : Stoploss Order Now at : {self.position.stoploss}")
        pass

    def update_stoploss(self,cancel=False):
        if not cancel:
            # self.logger.info(self.account.name, " :  Position ", self.position.strike, " : Stoploss Order Now cancelled.")
            pass
        else:
            # self.logger.info(self.account.name, " :  Position ", self.position.strike, " : Stoploss Order Now at : ", self.position.stoploss)
            pass
    
    def average_position(self,position,entry_price):
        if self.should_average and self.position_open:
            if not self.paper_trading:
                are_funds_sufficient, available_funds, funds_needed = self.isAvailableFundSufficient(self.quantity*entry_price)
                if not are_funds_sufficient:
                    self.logger.warning(f"{self.account.name} Phew! Insufficient Funds, cannot take an entry!")
                    return { "account_name": self.account_name, "response" : {"available_funds":available_funds, "funds_needed": funds_needed, "verdict": "Dude, you don't have enough funds!"} }
            self.logger.debug(f"{self.account.name} : Placing {self.entries} Market Order for {self.position.strike} @{entry_price}/-")
            orderID = self.placeOrderFyers(self.position.strike, "BUY", self.quantity, "MARKET", entry_price, "regular")
            self.average_price = entry_price if not self.position_open else ((self.quantity*self.average_price)+(self.quantity2*entry_price))/(self.quantity+self.quantity2)
            self.total_trading_quantity, self.remaining_quantity = self.total_trading_quantity + self.quantity, self.remaining_quantity + self.quantity
            self.isAveraged = True
            self.logger.info(f"{self.account.name} : Averaged Postion: {self.position.strike} : Quantity: {self.remaining_quantity} : Average Price: {self.average_price} : OrderID: {orderID}")

            # this is fishy and needs testing
            if self.remaining_quantity >= self.position.lot_size * 3 and self.switch_stragey_on_quantity:
                self.logger.debug(f"{self.account.name} Open Postion : {self.position.strike} with Quantity {self.remaining_quantity}. Switched Strategy to trail tragets instead of squaring-off at target1.")
            self.print_demat_status()

    def book_target1(self,position,price):
        if self.position_open:
            if self.squareoff_at_first_target:
                sellOrderID = self.placeOrderFyers( self.position.strike, "SELL", self.remaining_quantity, "MARKET", price, "regular")
                self.exit_time = time.time()
                self.PnL = self.PnL + ((price - self.average_price)*self.remaining_quantity)
                self.logger.debug(f"{self.account.name} : Squared-Off Postion: {self.position.strike} : With Remaining Quantity: {self.remaining_quantity} : sellOrderID: {sellOrderID}")
                self.remaining_quantity = self.remaining_quantity - self.remaining_quantity
                self.position_open = False
                self.position.exit_price = price
                self.logger.info(f"{self.account.name} : Position: {self.position.strike} : Total PnL: {self.PnL}/-")
                self.generatePnL()
                self.print_demat_status()
                return sellOrderID
            # else:
            elif not self.account.await_next_target:
                sell_quantity_at_target1 = self.get_sell_quantity_at_target1()
                sellOrderID = self.placeOrderFyers(self.position.strike, "SELL", sell_quantity_at_target1, "MARKET", price, "regular")
                self.remaining_quantity = self.remaining_quantity - sell_quantity_at_target1
                profit_booked_at_target1 = ((price - self.average_price)*sell_quantity_at_target1)
                self.PnL = self.PnL + profit_booked_at_target1
                self.logger.info(f"{self.account.name} : Position: {self.position.strike} : Traded {sell_quantity_at_target1} and Profit Booked at Target1: {profit_booked_at_target1}/- : sellOrderID: {sellOrderID}")
                self.print_demat_status()
                return sellOrderID
        else:
            None
        
    def book_target2(self,position,price):
        if self.position_open and not self.account.await_next_target:
            sell_quantity_at_target2 = self.get_sell_quantity_at_target2()
            if sell_quantity_at_target2 == 0:
                self.logger.debug(f"{self.account.name} Skipping Target2 booking (0 qty) — holding for Target3.")
                return None
            sellOrderID = self.placeOrderFyers(self.position.strike, "SELL", sell_quantity_at_target2, "MARKET", price, "regular")
            self.remaining_quantity = self.remaining_quantity - sell_quantity_at_target2
            self.position_open = True if self.remaining_quantity > 0 else False
            profit_booked_at_target2 = ((price - self.average_price)*sell_quantity_at_target2)
            self.PnL = self.PnL + profit_booked_at_target2
            self.logger.info(f"{self.account.name} : Position: {self.position.strike} : Traded {sell_quantity_at_target2} and Profit Booked at Target2: {profit_booked_at_target2}/- : sellOrderID: {sellOrderID}")
            self.print_demat_status()
            return sellOrderID
        else:
            None

    def book_target3(self,position,price):
        if self.position_open:
            sellOrderID = self.placeOrderFyers(self.position.strike, "SELL", self.remaining_quantity, "MARKET", price, "regular")
            self.exit_time = time.time()
            self.logger.debug(f"{self.account.name} : Closed Postion: {self.position.strike} : Quantity: {self.remaining_quantity} : sellOrderID: {sellOrderID}")
            profit_booked_at_target3 = ((price - self.average_price)*self.remaining_quantity)
            self.PnL = self.PnL + profit_booked_at_target3
            self.position_open = False

            self.position.exit_price = price
            self.logger.debug(f"{self.account.name} : Position: {self.position.strike} : Traded {self.remaining_quantity} and Profit Booked at Target3: {profit_booked_at_target3}/- : sellOrderID: {sellOrderID}")
            self.logger.info(f"{self.account.name} : Position: {self.position.strike} : Total PnL: {self.PnL}/-")
            self.generatePnL()
            self.print_demat_status()
            return sellOrderID
        else:
            None

    def square_off_all_positions(self):
        
        if not self.paper_trading:
            try:
                response = self.fyers.exit_positions({})                
                self.exit_time = time.time()
                if self.logger is not None:
                    self.logger.warning(f"{self.account.name} : SquaredOff All Positions.")
                    self.logger.warning(f"Response: {response}")
                else:
                    print(f"{Clock.tictoc()} : {self.account.name} : SquaredOff All Positions.")
                    print(f"{Clock.tictoc()} : Response: {response}")
                self.print_demat_status()
                return response
            except Exception as e:
                if self.logger is not None:
                    self.logger.error(f"{self.account.name}: Exception at SquaredOff All Positions. {e}")
                    if isinstance(e, dict) and e.get("code") == -398 and e.get("message") == "Looks like you have no open positions.":
                        self.logger.debug(f"{self.account.name} There were no postions to SquareOff.")
                    else:
                        self.logger.error(f"{self.account.name}: Attention Required.")
                        self.logger.error(f"Error Trace: {traceback.print_exc()}")
                        self.logger.error(f"Exception: {e}")
                    self.print_demat_status()
        else:
            if self.logger is not None:
                self.logger.debug(f"{self.account.name} : Paper Trading! Consider Postions SquaredOff.")
            else:
                print(f"{Clock.tictoc()} : {self.account.name} : Paper Trading! Consider Postions SquaredOff.")
            return None
    
    def book_position(self):
        response = self.fyers.exit_positions(data={"id":f"{self.position.strike}-{self.trade_type}"})
        errors = response if response['s'] != "ok" else None
        if errors:
            errors["product_type"] = self.trade_type
            errors["side"] = "EXIT"
            self.logger.critical(f"errors square off: {errors}")
        return { "account_name": self.account_name, "response" : errors } if errors else None
    
    def exit_position_via_telegram(self,position,price):
        payload = {} if position is None else {"id":f"{position}-{self.trade_type}"}
        self.logger.warning(f"Payload to Exit Postion: {payload}")
        response = self.fyers.exit_positions(data=payload)
        errors = response if response['s'] != "ok" else None
        if errors:
            errors["position"] = position if position is not None else "ALL"
            errors["product_type"] = self.trade_type
            errors["side"] = "EXIT"
            self.logger.critical(f"errors square off: {errors}")
        return { "account_name": self.account_name, "response" : errors } if errors else None

    def square_off_position(self,position,price):
        if self.position_open:
            # sellOrderID = self.placeOrderFyers(self.position.strike, "SELL", self.remaining_quantity, "MARKET", price, "regular")
            # sellOrderID = self.fyers.exit_positions(data={"id":f"{self.position.strike}-{self.trade_type}"})
            sellOrderID = None
            if not self.paper_trading:
                sellOrderID = self.book_position()
            self.exit_time = time.time()
            self.logger.debug(f"{self.account.name} : The OID of Exit for option {self.position.strike} with Qunatity {self.remaining_quantity} is: {sellOrderID}")
            self.PnL = self.PnL + (int(price - self.average_price) * self.remaining_quantity)
            self.remaining_quantity = self.remaining_quantity - self.remaining_quantity
            self.logger.info(f"{self.account.name} : Position: {self.position.strike} : Total PnL: {self.PnL}")
            self.position_open = False
            self.position.exit_price = price
            self.generatePnL()
            self.print_demat_status()
            return sellOrderID
        else:
            None
    
    def square_off_position_aggressive_trail(self,position,price):
        if self.position_open and self.account.aggressive_trail:
            return self.square_off_position(position,price)
        else:
            None
    
    def square_off_position_lazy_trail(self,position,price):
        if self.position_open and self.account.lazy_trail:
            return self.square_off_position(position,price)
        else:
            None

    def placeOrderFyers(self,inst, t_type, qty, order_type, price, variety):
        if qty <= 0:
            self.logger.critical(f"{self.account.name}: !!ATTENTION REQUIRED!! Quantiy is less than 0!")
            # chime.warning()
            return
        remaining_quantity_to_confirm = qty
        max_quantity_per_order = self.position.freeze_quantity
        errors = {}
        iteration = 1
        while remaining_quantity_to_confirm > 0:
            if remaining_quantity_to_confirm >= max_quantity_per_order:
                # Perform the buying or selling operation with max_quantity
                result = self.confirmOrderFyers(inst, t_type, max_quantity_per_order, order_type, price, variety)
                if result:
                    errors["product_type"] = self.trade_type
                    result['side'] = t_type
                    errors[f"Itr_#{iteration}"] = result
                remaining_quantity_to_confirm -= max_quantity_per_order
                self.logger.info(f"{self.account.name} Iteration #{iteration}: Placed {max_quantity_per_order} QTY, {remaining_quantity_to_confirm} QTY to be placed")
            else:
                # Perform the buying or selling operation with remaining_quantity_to_confirm
                result = self.confirmOrderFyers(inst, t_type, remaining_quantity_to_confirm, order_type, price, variety)
                if result:
                    errors["product_type"] = self.trade_type
                    result['side'] = t_type
                    errors[f"Itr_#{iteration}"] = result
                self.logger.info(f"{self.account.name} Iteration #{iteration}: Placed final order with {remaining_quantity_to_confirm} QTY!")
                remaining_quantity_to_confirm = 0
            iteration+=1
        status = { "account_name": f"{self.account_name}", "response" : errors }
        return status if errors else None
                
    def confirmOrderFyers(self,inst, t_type, qty, order_type, price, variety):
        exch = inst[:3]
        symb = inst[4:]
        dt = datetime.now()
        self.logger.info(f"{self.account.name} : Place Order Fyers: {t_type} {symb} {qty} QTY @ {order_type} PRICE =  {price}")
        if order_type == "MARKET":
            type1 = 2
            price = 0
        elif order_type == "LIMIT":
            type1 = 1

        if t_type == "BUY":
            side1 = 1
        elif t_type == "SELL":
            side1 = -1

        data = {
            "symbol": inst,
            "qty": qty,
            "type": type1,
            "side": side1,
            # "productType": "INTRADAY",
            # "productType": "CNC",
            "productType": self.trade_type,
            "limitPrice": 0,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder":False,
            "stopLoss": 0,
            "takeProfit": 0,
        }
        try:
            if not self.paper_trading:
                resp = self.fyers.place_order(data)
                self.logger.info(f"{self.account.name}: {symb} OrderID: {resp}")
                # return resp["message"] if resp['s'] != "ok" else None 
                return resp if resp['s'] != "ok" else None 
            else:
                return None
        except Exception as e:
            self.logger.error(f"{self.account.name}: Order Failed for {symb}")
            self.logger.error(f"Exception: {e}")
            self.logger.error(f"Error Trace: {traceback.print_exc()}")
            return f"Exception: {str(e)}\nResponse: - {resp}"