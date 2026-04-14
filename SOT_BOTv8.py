import argparse
import asyncio
import csv
import nest_asyncio
import os

from utils.trade_manager import TradeManager
nest_asyncio.apply()
import datetime
import itertools
import json
import logging
import pprint
import re
import sys
import threading
import time
import traceback
import uuid
from datetime import date, datetime, timedelta

import chime
import pandas as pd
import pandas_ta as pta
import requests
import ta  # Python TA Lib
from fyers_apiv3 import fyersModel
from pytz import timezone
from queue import Queue
from telethon import TelegramClient
from utils.clock import Clock
from utils.constants import Config
from utils.demat import Demat
from utils.position import Position
from utils.price_dispatcher import PriceDispatcher

fyers = fyersModel.FyersModel(token=Config.RAM_ACCESS_TOKEN, is_async=False, client_id=Config.RAM_CLIENT_ID, log_path=Config.fyers_log_path)
spinner = itertools.cycle([" -", " /", " |", " \\"])
bot_name = __file__.split("/")[-1].split(".")[0]

def str2bool(v):
    if isinstance(v, bool):
       return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    elif v.lower() in ('none', 'null', 'nil', 'na', 'n/a'):
        return None
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')

def int_or_false(v):
    if v is None:
        return False
    elif isinstance(int(v), int):
        return int(v)
    else:
        raise argparse.ArgumentTypeError('Int value expected.')

def str2int_or_none(v):
    if v.lower() in ('none', 'null', 'nil', 'na', 'n/a'):
        return None
    else:
        try:
            return int(v)
        except ValueError:
            raise argparse.ArgumentTypeError('Integer or None value expected.')

parser = argparse.ArgumentParser(description='SOT BOT v8 for Algo Trading on Fyers')
parser.add_argument('-i', type=str, help='Instrument')
parser.add_argument('-s', type=str, help='Strike')
parser.add_argument('-cepe', type=str, help='CE or PE')
parser.add_argument('-e', type=int, help='Entry Price')
parser.add_argument('-e2', type=str2int_or_none, nargs='?', default=None, help='Second Entry Price')
parser.add_argument('-t1', type=int, help='Target 1')
parser.add_argument('-t2', type=int, help='Target 2')
parser.add_argument('-t3', type=int, help='Target 3')
parser.add_argument('-targets', type=str, default=None, help='All targets comma-separated e.g. 370,390,410,430,450')
parser.add_argument('-sl', type=int, help='Stoploss')
parser.add_argument('-b', type=int, help='Build Number')
parser.add_argument('-bo', type=str2bool, nargs='?', const=True, default=False, help='is Breakout')
parser.add_argument('-efpa', type=str2bool, nargs='?', const=True, default=False, help='Shold Enter Above Few Points')
parser.add_argument('-oca', type=str2bool, nargs='?', const=True, default=False, help='is On Crossing Above')
parser.add_argument('-es', type=str2int_or_none, nargs='?', default=None, help='Exit Strategy')
parser.add_argument('-spot', type=int_or_false, nargs='?', default=False, help='Spot Price')

args = parser.parse_args()

messenger = False
points = 0
qwerty_channel = -1001767848638
sos_channel = -1002016606884
exchangeSymbol = "NSE:"  # overridden after instrument_name is resolved
max_loss = -30

instrument = args.i
strike = args.s
PE_CE = args.cepe
isBreakoutStrategy = args.bo
entry_price = args.e
second_entry_price = args.e2
target1 = args.t1
target2 = args.t2
target3 = args.t3
# Full target list — prefer -targets if provided, fall back to t1/t2/t3
if args.targets:
    targets_list = [int(t) for t in args.targets.split(',')]
else:
    targets_list = [t for t in [target1, target2, target3] if t is not None]
# _booked[i] tracks whether we've already booked at targets_list[i]
_booked = [False] * len(targets_list)
sot_stoploss = args.sl
static_stoploss,stop_loss,stop_loss_aggressive_trailing, lazy_stoploss = args.sl,args.sl,args.sl, args.sl
enterFewPointsAbove = True
onCrossingAbove = args.oca
buildNumber = args.b
spot = args.spot
exit_strategy = args.es

re_entered = False
re_entry = False
trade_exit_hour,trade_exit_minute = 15,18
bot_exit_hour,bot_exit_minute = 15,20
isInValidTrade = True
play_safe = True if target1 - entry_price > 20 else False
precise_trailing = True
pnl_sent = False

if re_entry:
    stop_loss = entry_price - 15

stock_option_input = instrument+strike+PE_CE
aggressive_trailing_points = 0
avg_price = 0
peak_profit = 0
peak_gain = 0
safe_entry_price = (target1-entry_price) * 0.35 if not stock_option_input.upper().startswith("BAJ") else 0
almost_breakout_price = (target1-entry_price) * 0.20 if not stock_option_input.upper().startswith("BAJ") else 0
aggressive_trailing_points1 = int((target1 - entry_price)/2)
aggressive_trailing_points2 = int((target2 - target1)/2)
aggressive_trailing_points3 = int((target3 - target2)/2)

async def send_message(message,emergency=False):
    if not messenger:
        logger.warning("messenger isn't initialised!")
        return
    try:
        message = f"```[{bot_name}] Build: #{buildNumber} 🦉\n\n{position.strike}: {message}```"
        if emergency:
            await client.send_message(sos_channel, message,parse_mode='md')
        await client.send_message(qwerty_channel, message,parse_mode='md')
    except Exception as e:
        logger.error(f"Error in send_message. {e}")
        logger.error(f"Error Trace: {traceback.print_exc()}")

instrument_name = None
if stock_option_input.upper().startswith("NIF"):
    instrument_name = "NIFTY"
elif stock_option_input.upper().startswith("MID"):
    instrument_name = "MIDCPNIFTY"
elif stock_option_input.upper().startswith("FIN"):
    instrument_name = "FINNIFTY"
elif stock_option_input.upper().startswith("BAN"):
    instrument_name = "BANKNIFTY"
elif stock_option_input.upper().startswith("BAJF"):
    instrument_name = "BAJFINANCE"
elif stock_option_input.upper().startswith("SEN"):
    instrument_name = "SENSEX"
else:
    exception_content = f"{Clock.tictoc()} Nifty || MidCPNifty || BankNifty || FinNifty || BajFinance || Sensex ONLY. Received '{stock_option_input}'"
    asyncio.run(send_message(exception_content,emergency=True))
    raise Exception(exception_content)

exchangeSymbol = Config.exchange_map.get(instrument_name, "NSE:")
expiry = Config.expiry_map.get(instrument_name, None)
assert expiry is not None, f"No Expiry Configured for '{instrument_name}'"

port_number = Config.ws_map.get(instrument_name, None)
assert port_number is not None, f"No Port Nunber Configured for '{instrument_name}'"

stock_option = exchangeSymbol + instrument_name + expiry["year"] + expiry["month"] + expiry["day"] if instrument_name in Config.index_symbols else exchangeSymbol + instrument_name + expiry["year"] + expiry["month"]
if stock_option_input.upper().endswith("PE"):
    stock_option = stock_option + re.findall(r"\d+", stock_option_input)[0] + "PE"
elif stock_option_input.upper().endswith("CE"):
    stock_option = stock_option + re.findall(r"\d+", stock_option_input)[0] + "CE"
else:
    chime.warning()
    raise Exception(Clock.tictoc(), "Options can only be call or put. received '", stock_option )

quantity = Config.lot_size_map.get(instrument, None)
qty,qty2 = quantity,quantity
breakout_qty = quantity
total_trading_qty,remaining_qty = 0,0
entered_trade = False
PnL = 0

name = __file__.split("/")[-1].split(".")[0]
log_file = Config.logger_path+"/"+name + stock_option.replace("NSE:","").replace("BSE:","") + "_" + str(date.today()) + "_" + str(uuid.uuid4()) + ".log"

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(eval(f"logging.{Config.console_log_level.value}"))
console_handler.setFormatter(formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

position = Position(instrument=stock_option,ce_pe=PE_CE,strike=stock_option,entry_price=entry_price,stoploss=stop_loss,target1=target1,target2=target2,target3=target3,isBreakoutStrategy=isBreakoutStrategy,enterFewPointsAbove=enterFewPointsAbove,onCrossingAbove=onCrossingAbove,spot=spot,exit_strategy=exit_strategy)

logger.debug(f"[{bot_name}_LIVE]: Checking For Position:")
logger.info(pprint.pformat(position.__dict__))

accounts = Config.accounts_breakout_strategy if onCrossingAbove else Config.accounts_range_strategy
demats: Demat = []
for account in accounts:
    demats.append(Demat(account,logger=logger))

with open(Config.current_day_override, 'r') as file:
    file_content = file.read()

override_as_paper_trading = True if file_content == "TRUE" else False
if override_as_paper_trading:
    for demat in demats:
        demat.paper_trading = True

logger.debug("[{bot_name}]: Demats Configured:")
for demat in demats:
    demat.prepare_for_position(position,bot_name,buildNumber)
    logger.debug(pprint.pformat(demat.__dict__))

all_live_demats = [demat for demat in demats if not demat.paper_trading]

unique_client_ids = set()
live_demats_unique = []
for demat in all_live_demats:
    if demat.account.client_id not in unique_client_ids:
        unique_client_ids.add(demat.account.client_id)
        live_demats_unique.append(demat)

if exit_strategy is not None:
    bot_name = bot_name + "_SSR"
    if exit_strategy == 1:
        logger.info(f"Overriding with strategy #1 -  Square-off at Target1")
        for demat in all_live_demats:
            demat.account.squareoff_at_first_target = True
            demat.account.await_next_target = False
    elif exit_strategy == 2:
        logger.info(f"Overriding with strategy #2 - Book at 50% at Target 1 || 25% at Target 2 || 25% at Target 3")
        for demat in all_live_demats:
            demat.account.squareoff_at_first_target = False
            demat.account.await_next_target = False
    elif exit_strategy == 3:
        logger.info(f"Overriding with strategy #2 - Await Next Target!")
        for demat in all_live_demats:
            demat.account.squareoff_at_first_target = False
            demat.account.await_next_target = True
    else:
        raise "Invalid Strategy Received to override!"

all_live_demats_formatted = [f"{demat.account_name} - {demat.account.config_type} : {demat.quantity} QTY\n- Square-Off at Target1: {demat.account.squareoff_at_first_target}\n- Await Next Target: {demat.account.await_next_target}\n- Aggressive Trail: {demat.account.aggressive_trail}\n- Should Average: {demat.account.should_average}\n\n" for demat in all_live_demats]
all_live_demats_data = "\n".join(all_live_demats_formatted) if all_live_demats_formatted else "- NO LIVE ACCOUNTS WITH CONFIG!"
logger.info(f"all_live_demats_data:\n{all_live_demats_data}")

def flatten_dict_with_prefix(data, prefix=''):
    flattened = {}
    for k, v in data.items():
        new_key = f"{prefix}_{k}" if prefix else k
        if isinstance(v, dict):
            flattened.update(flatten_dict_with_prefix(v, new_key))
        else:
            flattened[new_key] = v
    return flattened

def format_flattened_dict_as_string(nested_dict, custom_prefix):
    flattened_dict = flatten_dict_with_prefix(nested_dict)
    result = [f"{custom_prefix}"]
    for key, value in flattened_dict.items():
        result.append(f"- {key}: {value}")
    return '\n'.join(result)

api_id = "24665115"
api_hash = '4bb48e7b1dd0fcb763dfe9eb203a6216'
bot_token = "6744137962:AAFOFp0rwyK-ddZ9NRFf0XlgCXgoQgAbyWU"
bot_tokens = ["6476234483:AAHoQHtdKv8aHfmUgJG14ETxsZnqXjtutwU","6412363785:AAE7ULHsAj1Y77ctZCGy_4t06zQVJgkMo8I","6868900803:AAHhCtYa1ypF6VC-oK1BO1SaPBQSEEGSdcY","6868256714:AAGliJ2kDv_Op-EFsqEEcibNOLXfR6EAyUM","6437478156:AAH_K7XwS-_Tj2IhFz4f_dJjPUDMBxWnS0A","6665535957:AAES8e32_jwvLGybQUyJwNjcjhA8sd36mj8","6679914693:AAGGX6E8-nnnc7u0E6RVZW7cgfwDfwssBhI","6483776327:AAGfbgUKheJbw_XTO5wf3KSHvxGnjD_0I_U","6985446292:AAFydqdZtPTrjlKuQu3pk7UT3BugY6dKnZA"]

tc_session_name = f"{Config.logger_path}/{name}_{str(uuid.uuid4())}"
client = TelegramClient(tc_session_name, api_id, api_hash)

trade_manager = TradeManager(demats,logger=logger)

# One PriceDispatcher per bot process — polls once, dispatches to TradeHandler
dispatcher = PriceDispatcher(instrument=stock_option, port=port_number)

def send_calculated_pnL():
    global pnl_sent
    if pnl_sent:
        return
    csv_folder = Config.logger_path
    csv_files = [f for f in os.listdir(csv_folder) if f.startswith(f"{buildNumber}#") and f.endswith(".csv") and "pnl" in f.lower()]

    if csv_files:
        with open(os.path.join(csv_folder, csv_files[0]), 'r') as file:
            reader = csv.reader(file)
            header = next(reader)
            logger.debug(f"Header: {header}")

        pnl_sum = {}

        for filename in csv_files:
            with open(os.path.join(csv_folder, filename), 'r') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    account_name = row['AccountName'].strip()
                    pnl_value = row['PnL']
                    pnl_value = ''.join(c for c in pnl_value if c.isdigit() or c in {'-', '.'})
                    try:
                        pnl_sum[account_name] = pnl_sum.get(account_name, 0) + float(pnl_value)
                    except ValueError:
                        logger.debug(f"Skipping non-numeric PnL value in file {filename}")

        sorted_pnl = sorted(pnl_sum.items(), key=lambda x: x[1], reverse=True)
        pnl = '\n'.join([f"{account}: {round(pnl)}/-" for account, pnl in sorted_pnl])
        asyncio.run(send_message(f"\nCurrent Trade PnL:\n{pnl}"))
        pnl_sent = True
    else:
        asyncio.run(send_message("No PnL generated so far!"))

def send_order_placement_erros(heading,orders):
    message_content = ""
    for order in orders:
        message_content += format_flattened_dict_as_string(order["response"],f"{order['account_name']}") + "\n\n"

    if message_content:
        message_content = f"\n\n🚨 {heading}: 🚨\n\n{message_content}"
        logger.warning(f"{message_content}")
        asyncio.run(send_message(message_content,emergency=True))

def fetchLTP(name):
    ltp = -1
    response = "placeholder_response"
    try:
        data = {"symbols":name}
        response = fyers.quotes(data)
        api_response_formatted = format_flattened_dict_as_string(response,"API Response:")
        logger.debug(f"fetchLTP Response: {name}: {response}")
        ltp = (response)['d'][0]['v']['lp']
        logger.warning(f"fetchLTP: {name}: {ltp}")
    except Exception as e:
        if entered_trade:
            api_response_formatted = format_flattened_dict_as_string(response,"API Response:")
            asyncio.run(send_message(f"🆘 CHUCK EVERYTHING AND LOOK INTO THIS!\n\n{api_response_formatted}"))
        logger.error(f"{name}: Failed : {e}")
        logger.error(f"Error Trace: {traceback.print_exc()}")
    return ltp,response

def getLTP(option,spot=False):
    url = f"http://localhost:{port_number}/ltp?instrument={option}" if not spot else f"http://localhost:{port_number}/spot_price"
    success = False
    counter = 1
    max_attempts = 120
    alert_sent = False
    resp = None
    api_mode = False
    while not success and counter < max_attempts:
        try:
            resp = requests.get(url)
            success = True
        except Exception as e:
            if entered_trade and (counter % 3 == 0):
                asyncio.run(send_message(f"🚨🚨🚨\n\nOpen Postion & {instrument_name} Websocket is acting up, relying on API for every 3 Seconds!",emergency=True))
                success = True
                api_mode = True
                resp, fetchLTP_resp  = fetchLTP(option)

            if entered_trade and (counter % 15 == 0):
                asyncio.run(send_message(f"🆘 \n\nOpen Postion & {instrument_name} Websocket hung up, retried #{counter} times.\n\nReply STOP on this message to gain manual control and EXIT trade when LTP reaches {stop_loss}/-",emergency=True))
                alert_sent = True
            if entered_trade and (counter == (max_attempts - 30)):
                asyncio.run(send_message(f"🆘 \n\nLast 30 Seconds to take manual control ain't will auto square off all postions!",emergency=True))
                alert_sent = True
            logger.error(f"Exception @getLTP:{e}")
            logger.error(f"Error Trace: {traceback.print_exc()}")
            logger.error(f"Retrying now... attempt #{counter}")
            counter+=1
            time.sleep(1)

    if api_mode:
        return resp
    elif resp is not None:
        data = resp.json()
        if alert_sent:
            asyncio.run(send_message(f"👻\n\nHuh, Relax! {instrument_name} Websocket is now restored!",emergency=True))
        return data
    else:
        message_conent = f"🫢\n\nLooks SOT_BOT couldn't connect to {instrument_name} websocket for LTP, gave up after #{counter} attempts!"
        if entered_trade:
            send_order_placement_erros("SQUARE-OFF POSITION",trade_manager.square_off_position(position,-1))
            message_conent = f"😢 🆘 🆘 🆘\n\nLooks SOT_BOT couldn't connect to {instrument_name} websocket for LTP, gave up after #{counter} attempts! Squared Off {option} via API, Ensure We've Exited the positions on all LIVE Demats!"
        logger.error(message_conent)
        asyncio.run(send_message(message_conent,emergency=True))
        exit()

def ensureGetLTP(option,spot=None):
    max_attempts = 10 if entered_trade else 120
    counter = 0
    cmp = getLTP(option) if spot is None else getLTP(option,spot)
    while counter <= max_attempts and cmp == -1:
        logger.debug(f"{option}: LTP: {cmp}/- will retry...")
        counter += 1
        time.sleep(1)
        cmp = getLTP(option)

    if cmp == -1 and entered_trade:
        send_order_placement_erros("SQUARE-OFF POSITION",trade_manager.square_off_position(position,cmp))
        message_conent = f"😢 🆘 🆘 🆘\n\n Fyers {instrument_name} Websockets are down and API , LTP retrieved is -1 for continuous #{max_attempts} attempts! Squared Off {option} via API, Ensure We've Exited the positions on all LIVE Demats!"
        logger.error(message_conent)
        asyncio.run(send_message(message_conent,emergency=True))
        exit()
    elif cmp == -1 and not isInValidTrade:
        message_conent = f"Phew!\n\n Fyers {instrument_name} Websockets are down, LTP retrieved is -1 for continuous #{max_attempts} attempts! You might want to retry this build, if you see Fyers is back and stable!"
        logger.error(message_conent)
        asyncio.run(send_message(message_conent,emergency=True))
        exit()
    return cmp

def ohlc(option,timeframe):
    date=pd.to_datetime(datetime.now(timezone("Asia/Kolkata")).strftime('%Y-%m-%d %H:%M:%S'))
    while(int(str(date)[-2::])>=2):
        cmp = ensureGetLTP(option)
        logger.debug(f"Polling if CMP is crossing safe entry price before closing above expected price: {option} {cmp}")
        print("Polling if CMP is crossing safe entry price before closing above expected price: ",option,next(spinner), end="\r", flush=True)
        if entry_price + safe_entry_price < round(int(cmp)) < target1:
            return {"open":"", "high":"", "low":"", "close":cmp, "timestamp": int(round((datetime.fromtimestamp(int(time.time()))).timestamp()))}
        time.sleep(.3)
        date=pd.to_datetime(datetime.now(timezone("Asia/Kolkata")).strftime('%Y-%m-%d %H:%M:%S'))
    return prevCandle(option,timeframe)

def getPrevCandle(option,timeframe):
        today = datetime.now(timezone("Asia/Kolkata")).strftime('%Y-%m-%d')
        data = {
                "symbol": option,
                "resolution": timeframe,
                "date_format": "1",
                "range_from": today,
                "range_to": today,
                "cont_flag": "1"
        }
        result = None
        try:
            response = fyers.history(data)
            logger.debug(f"getPrevCandle Response: {response}")
            if response["s"] == "ok":
                candles = response["candles"]
                if candles:
                    last_candle = candles[-1]
                    result = {
                        "open": last_candle[1],
                        "high": last_candle[2],
                        "low": last_candle[3],
                        "close": last_candle[4],
                        "timestamp": last_candle[0]
                    }
        except Exception as e:
            logger.error(f"Exception @getPrevCandle: {e}")
            logger.error(f"Error Trace: {traceback.print_exc()}")
        return result

def isLastMinute(current_timestamp, previous_timestamp,minute=1):
    logger.info(f"current_timestamp: {current_timestamp}")
    logger.info(f"previous_timestamp: {previous_timestamp}")
    return (current_timestamp // 60) - (previous_timestamp // 60) == minute

def prevCandle(option, timeframe):
    max_retries = 15
    retries = 0

    while retries <= max_retries:
        data = getPrevCandle(option, timeframe)
        if data is not None and isLastMinute(int(round(time.time())), data["timestamp"]):
            return data

        logger.info(f"Retrying PrevCandle... #{retries}")
        time.sleep(0.3)
        retries += 1

    logger.error(f"Max Retries exceeded. Data Retrieved isn't the previous {timeframe}min candle.")
    return None


# =============================================================================
# TradeHandler — replaces the `while not traded:` loop in takeEntry()
#
# Instead of polling ensureGetLTP() on every iteration, TradeHandler.on_price()
# is called by PriceDispatcher whenever a new price tick arrives. All the
# target/SL/trailing logic is identical to SOT_BOTv7 — only the delivery
# mechanism changed (push vs poll).
# =============================================================================

class TradeHandler:
    """
    Encapsulates the state and logic of a single active trade.

    Lifecycle:
        handler = TradeHandler(option, current_entry_price, ...)
        handler.enter()   # place orders, subscribe to dispatcher
        handler.wait()    # block until trade is closed
    """

    def __init__(self, option: str, current_entry_price: float):
        self.option = option
        self.current_entry_price = current_entry_price

        # --- mutable trade state (was global in v7) ---
        self.avg_price = entry_price if onCrossingAbove else current_entry_price
        self.stop_loss = static_stoploss
        self.lazy_stoploss = static_stoploss
        self.stop_loss_aggressive_trailing = static_stoploss
        self.total_trading_qty = qty
        self.remaining_qty = qty
        self.peak_profit = 0.0
        self.peak_gain = 0.0
        self.ltpHigh = current_entry_price

        # --- per-tick flags ---
        self._isAveraged = False
        self._soldAtTarget1 = False   # kept for play_safe trail references
        self._soldAtTarget2 = False   # kept for play_safe trail references
        self._sold_aggressive_positions = False
        self._sold_lazy_positions = False
        self._message_sent = False

        # --- pre-compute trailing thresholds (same as v7 lines 415–429) ---
        self.averaged_almost_target1 = (target1 - entry_price) * 0.75
        self.averaged_almost_target2 = (target2 - target1) * 0.75
        self.averaged_almost_target3 = (target3 - target2) * 0.75
        self.almost_target1 = (target1 - entry_price) * 0.75
        self.almost_target2 = (target2 - target1) * 0.75
        self.almost_target3 = (target3 - target2) * 0.75

        self._done = threading.Event()

    def enter(self):
        """Place the initial entry orders and subscribe to dispatcher."""
        global entered_trade
        position.entry_price = self.current_entry_price
        send_order_placement_erros("ENTER POSITION", trade_manager.enter_position(position, self.current_entry_price))
        entered_trade = True

        logger.warning(f"almost_target1: {self.almost_target1} i.e {entry_price + self.almost_target1}")
        logger.warning(f"almost_target2: {self.almost_target2} i.e {target1 + self.almost_target2}")
        logger.warning(f"almost_target3: {self.almost_target3} i.e {target2 + self.almost_target3}")
        logger.warning(f"averaged_almost_target1: {self.averaged_almost_target1} i.e breakeven-sl at target1 is {entry_price + self.averaged_almost_target1}")
        logger.warning(f"averaged_almost_target2: {self.averaged_almost_target2} i.e breakeven-sl at target2 is {target1 + self.averaged_almost_target2}")
        logger.warning(f"averaged_almost_target3: {self.averaged_almost_target3} i.e breakeven-sl at target3 is {target2 + self.averaged_almost_target3}")

        dispatcher.subscribe(self.on_price)
        logger.info(f"TradeHandler subscribed to dispatcher for {self.option}")

    def wait(self):
        """Block the calling thread until the trade is closed."""
        self._done.wait()

    def _finish(self):
        """Deregister from dispatcher and unblock wait()."""
        global entered_trade
        dispatcher.unsubscribe(self.on_price)
        entered_trade = False
        self._done.set()

    # ------------------------------------------------------------------
    # on_price — the heart of the change
    # This is the body of the old `while not traded:` loop, now called
    # by PriceDispatcher instead of blocking and polling.
    # NOTE: called from dispatcher's background thread. asyncio.run() is
    # safe here (creates its own loop per call) but is blocking — fine
    # for a single-trade process. A future multi-trade coordinator should
    # use a dedicated thread or async queue for Telegram sends.
    # ------------------------------------------------------------------

    def on_price(self, cmp: float):
        if self._done.is_set():
            return

        live_pnl = (cmp - self.avg_price) * self.remaining_qty
        gain = round(cmp - self.current_entry_price)

        print("Live: ", self.option, " : PnL: ", live_pnl, " INR ", next(spinner), end="\r", flush=True)

        if not self._message_sent:
            asyncio.run(send_message(f"{cmp}/- ♐️🍀 LIVE POSITION"))
            self._message_sent = True

        try:
            if cmp > self.ltpHigh:
                self.ltpHigh = cmp
                self.peak_profit = live_pnl if live_pnl > self.peak_profit else self.peak_profit
                self.peak_gain = gain if gain > self.peak_gain else self.peak_gain
                logger.debug(f"{self.option} : Higher High: {self.ltpHigh}/- | Current PnL: {live_pnl}/- | Peak Profit: {self.peak_profit}/-")
            else:
                logger.debug(f"{self.option} : CMP: {cmp}/-... | Higher High: {self.ltpHigh}/- | Current PnL: {live_pnl}/- | Peak Profit: {self.peak_profit}/-")

            if round(cmp) <= self.stop_loss or not Clock.is_time_less_than(trade_exit_hour, trade_exit_minute):
                send_order_placement_erros("SQUARE-OFF POSITION", trade_manager.square_off_position(position, cmp))
                logger.critical(f"stop_loss hit! closed at {self.stop_loss}/-; Highest LTP: {self.ltpHigh}/-")
                print("Closed: ", self.option)
                smiley = "🤑" if live_pnl > 0 else "🤐 khata khata hatha vidhi!"
                asyncio.run(send_message(f"{gain} Points! {smiley} | Peak Gain: {self.peak_gain}"))
                if not re_entered and not self._soldAtTarget1 and re_entry:
                    self._finish()
                    check_re_entry(self.option)
                    return
                elif re_entered and not self._soldAtTarget1:
                    logger.critical("Phew! SL hit for the second time, no more re-entries.")

            elif round(cmp) <= self.stop_loss_aggressive_trailing and not self._sold_aggressive_positions:
                send_order_placement_erros("SQUARE-OFF AGGRESSIVE TRAIL POSITION", trade_manager.square_off_position_aggressive_trail(position, cmp))
                self._sold_aggressive_positions = True
                logger.critical(f"stop_loss_aggressive_trailing is hit! Closed at {self.stop_loss_aggressive_trailing}/-")

            elif round(cmp) <= self.lazy_stoploss and not self._sold_lazy_positions:
                send_order_placement_erros("SQUARE-OFF LAZY POSITION", trade_manager.square_off_position_lazy_trail(position, cmp))
                self._sold_lazy_positions = True
                logger.critical(f"lazy_stoploss is hit! Closed at {self.lazy_stoploss}/-")

            elif second_entry_price is not None and static_stoploss < int(cmp) < second_entry_price and not self._isAveraged and not isBreakoutStrategy and not onCrossingAbove:
                send_order_placement_erros("AVERAGE POSITION", trade_manager.average_position(position, cmp))
                self._isAveraged = True
                self.total_trading_qty = self.remaining_qty + qty2
                self.remaining_qty = self.remaining_qty + qty2
                self.avg_price = ((qty * self.avg_price) + (qty2 * cmp)) / (qty + qty2)
                logger.critical(f"Averaged at {cmp}/-")

            # almost targets — averaged
            elif play_safe and round(cmp) >= self.avg_price + self.averaged_almost_target1 and self._isAveraged and self.stop_loss < self.avg_price:
                self.stop_loss = self.avg_price if precise_trailing else self.avg_price + 3
                logger.critical(f"Trailed stop_loss to Breakeven as CMP >= avg_price + {self.averaged_almost_target1} and position is averaged.  CMP: {cmp}/- ; Average Price: {self.avg_price}/- ; Next Target: {target1}/- ; stop_loss: {self.stop_loss}/-")
            elif play_safe and second_entry_price is not None and round(cmp) >= self.avg_price + self.averaged_almost_target1 and not self._isAveraged and self.stop_loss < second_entry_price:
                self.stop_loss = second_entry_price if precise_trailing else second_entry_price + 3
                logger.critical(f"Trailed stop_loss to Breakeven as CMP >= avg_price + {self.averaged_almost_target1} and position is averaged.  CMP: {cmp}/- ; Average Price: {self.avg_price}/- ; Next Target: {target1}/- ; stop_loss: {self.stop_loss}/-")
            elif play_safe and round(cmp) >= target1 + self.averaged_almost_target2 and self._isAveraged and self.stop_loss < target1:
                self.stop_loss = target1 if precise_trailing else target1 + 3
                logger.critical(f"Trailed stop_loss to Target1 as CMP >= avg_price + {self.averaged_almost_target2} and position is averaged.  CMP: {cmp}/- ; Average Price: {self.avg_price}/- ; Next Target: {target3}/- ; stop_loss: {self.stop_loss}/-")
            elif play_safe and round(cmp) >= target2 + self.averaged_almost_target3 and self._isAveraged and self.stop_loss < target2:
                self.stop_loss = target2 if precise_trailing else target2 + 3
                logger.critical(f"Trailed stop_loss to Target2 as CMP >= avg_price + {self.averaged_almost_target3} and position is averaged.  CMP: {cmp}/- ; Average Price: {self.avg_price}/- ; Next Target: {target3}/- ; stop_loss: {self.stop_loss}/-")

            # almost targets — not averaged
            elif play_safe and round(cmp) >= self.avg_price + self.almost_target1 and not self._isAveraged and self.stop_loss < self.avg_price:
                # NEAR trade: anchor SL at range-low (second_entry_price), not the fill price
                sl_anchor = (second_entry_price
                             if second_entry_price is not None and not isBreakoutStrategy
                             else self.avg_price)
                self.stop_loss = sl_anchor if precise_trailing else sl_anchor + 3
                logger.critical(f"Trailed stop_loss to {'range-low' if second_entry_price and not isBreakoutStrategy else 'Breakeven'} ({sl_anchor}) as CMP >= avg_price + {self.almost_target1}.  CMP: {cmp}/- ; Average Price: {self.avg_price}/- ; Next Target: {target1}/- ; stop_loss: {self.stop_loss}/-")
            elif play_safe and round(cmp) >= target1 + self.almost_target2 and not self._isAveraged and self.stop_loss < target1:
                self.stop_loss = target1 if precise_trailing else target1 + 3
                logger.critical(f"Trailed stop_loss to Target1 as CMP >= avg_price + {self.almost_target2} and position is not averaged.  CMP: {cmp}/- ; Average Price: {self.avg_price}/- ; Next Target: {target3}/- ; stop_loss: {self.stop_loss}/-")
            elif play_safe and round(cmp) >= target2 + self.almost_target3 and not self._isAveraged and self.stop_loss < target2:
                self.stop_loss = target2 if precise_trailing else target2 + 3
                logger.critical(f"Trailed stop_loss to Target2 as CMP >= avg_price + {self.almost_target3} and position is not averaged.  CMP: {cmp}/- ; Average Price: {self.avg_price}/- ; Next Target: {target3}/- ; stop_loss: {self.stop_loss}/-")

            elif round(cmp) >= target1 - aggressive_trailing_points1 and self.stop_loss_aggressive_trailing < self.avg_price:
                if precise_trailing:
                    self.stop_loss_aggressive_trailing = self.avg_price if not onCrossingAbove else entry_price
                else:
                    self.stop_loss_aggressive_trailing = self.avg_price + 3 if not onCrossingAbove else entry_price + 3
                logger.critical(f"Trailed stop_loss_aggressive_trailing to Breakeven. as CMP>=target1 - {aggressive_trailing_points1}.  CMP: {cmp}/- ; Average Price: {self.avg_price}/- ; Next Target: {target1}/- ; stop_loss_aggressive_trailing: {self.stop_loss_aggressive_trailing}/-")

            else:
                # ── Target booking loop ─────────────────────────────────────
                # Sequential `if` checks (NOT elif) so a spike that skips one
                # or more targets still fires all applicable bookings in one tick.
                for i, tgt in enumerate(targets_list):
                    if round(cmp) < tgt or _booked[i]:
                        continue  # not reached yet, or already booked

                    is_last = (i == len(targets_list) - 1)
                    smileys = "😍" * (i + 1)
                    send_order_placement_erros(f"BOOK TARGET{i+1}", trade_manager.book_at_target(position, cmp, i))
                    _booked[i] = True
                    # keep legacy flags in sync for play_safe trail blocks
                    if i == 0: self._soldAtTarget1 = True
                    if i == 1: self._soldAtTarget2 = True
                    asyncio.run(send_message(f"{smileys} {gain} Points{'!' if is_last else '...'}"))

                    if i == 0:
                        # First target hit: SL → range-low (NEAR) or avg_price (ABOVE)
                        sl_anchor = (second_entry_price
                                     if second_entry_price is not None and not isBreakoutStrategy
                                     else self.avg_price)
                        self.stop_loss = sl_anchor if precise_trailing else sl_anchor + 3
                        if onCrossingAbove:
                            self.stop_loss -= 2
                        self.lazy_stoploss = self.stop_loss
                        logger.critical(f"T1 hit — SL → {sl_anchor} ({'range-low' if second_entry_price and not isBreakoutStrategy else 'breakeven'}). CMP: {cmp}/- SL: {self.stop_loss}/-")
                    elif not is_last:
                        # Intermediate target hit: SL → previous target
                        prev_tgt = targets_list[i - 1]
                        self.stop_loss = prev_tgt if precise_trailing else prev_tgt + 3
                        logger.critical(f"T{i+1} hit — SL → T{i} ({prev_tgt}). CMP: {cmp}/- SL: {self.stop_loss}/-")

                    if is_last:
                        logger.critical(f"Last target (T{i+1}={tgt}) hit! Closed at {cmp}/- ; Highest LTP: {self.ltpHigh}/-")
                        send_calculated_pnL()
                        self._finish()
                        return

            # Check all accounts closed
            status = [demat.position_open for demat in demats]
            if not any(status):
                print("Closed: ", self.option)
                logger.critical(f"Positions in all the accounts are now closed, stopping SOT_BOT!")
                send_calculated_pnL()
                self._finish()

        except Exception as err:
            logger.error(f"Error @TradeHandler.on_price: {err}")
            logger.error(f"Error Trace: {traceback.print_exc()}")
            chime.warning()


# =============================================================================
# takeEntry — now just a thin wrapper around TradeHandler
# =============================================================================

def takeEntry(option, current_entry_price):
    handler = TradeHandler(option, current_entry_price)
    handler.enter()
    handler.wait()


def check_re_entry(option):
    global re_entered
    global stop_loss
    stop_loss = sot_stoploss - 3
    cmp = ensureGetLTP(option)
    logger.debug(f"LTP: {cmp}/- at the time of script trigger for re-entry...")
    closestLTP = cmp

    logger.debug("Attempting to Re-enter!")
    while not re_entered:
        cmp = ensureGetLTP(option)
        if int(cmp) > entry_price:
            re_entered = True
            takeEntry(option, cmp)
        elif int(cmp) <= sot_stoploss:
            logger.debug(f"SOT\'s Stoploss hit, you saved {stop_loss - sot_stoploss} Points")
            exit()
        else:
            closestLTP = cmp if cmp < closestLTP else closestLTP
            print("Awaiting RE-Entry: ", option, next(spinner), end="\r", flush=True)
            logger.debug(f"Waiting for RE-Entry: {option} at price {entry_price}/-, CMP: {cmp} [ closestLTP so far: {closestLTP} ], Capital Required: {entry_price*qty}/-")
            print(next(spinner), end="\r", flush=True)
            time.sleep(.3)

def check_entry(option):
    try:
        global re_entered
        global spot
        almost = False
        cmp = ensureGetLTP(option)

        logger.debug(f"LTP: {cmp}/- at the time of script trigger...")
        if cmp == -1:
            message_conent = f"🙊 Probably an Invalid Strike as CMP is {cmp} or {instrument_name} Websocket has hung up! Run Diagnosis and then RETRY Build: #{buildNumber}"
            logger.error(message_conent)
            asyncio.run(send_message(message_conent,emergency=True))
            raise Exception(message_conent)
        closestLTP = cmp
        logger.warning(f"{spot} received a spot to be checked for")
        if spot and "CE" in PE_CE:
            spot_price = round(ensureGetLTP(option,spot=True))
            logger.info(f"will await for spot to be greater than {spot}; current spot {spot_price}")
            while not entered_trade and Clock.is_time_less_than(bot_exit_hour,bot_exit_minute):
                print(f"Expected spot_price {spot_price} > spot {spot}: ",option,next(spinner), end="\r", flush=True)
                if spot_price > spot:
                    takeEntry(option, cmp)
                time.sleep(1)
                spot_price = round(ensureGetLTP(option, spot=True))
        elif spot and "PE" in PE_CE:
            spot_price = round(ensureGetLTP(option,spot=True))
            logger.info(f"will await for spot to be less than {spot}; current spot {spot_price}")
            while not entered_trade and Clock.is_time_less_than(bot_exit_hour,bot_exit_minute):
                print(f"Expected spot_price {spot_price} < spot {spot}: ",option,next(spinner), end="\r", flush=True)
                if spot_price < spot:
                    takeEntry(option, cmp)
                time.sleep(1)
                spot_price = round(ensureGetLTP(option, spot=True))
        else:
            while not entered_trade and not isBreakoutStrategy and not onCrossingAbove and Clock.is_time_less_than(bot_exit_hour,bot_exit_minute):
                cmp = ensureGetLTP(option)
                if second_entry_price <= int(cmp) < entry_price+1:
                    takeEntry(option, cmp)
                elif cmp < static_stoploss:
                    asyncio.run(send_message(f"🚨 Looks {instrument_name} Websocket hung while awaiting entry at {entry_price}, CMP is {cmp} now below stoploss. Aborting the Trade.\n\n{json.dumps(position)}",emergency=True))
                    exit()
                else:
                    closestLTP = cmp if cmp < closestLTP else closestLTP
                    logger.debug(f"Waiting for Entry: {option} at price {entry_price}/-, CMP: {cmp} [ closestLTP so far: {closestLTP} ], Capital Required: {entry_price*qty}/-")

                    closest_diff = closestLTP - entry_price
                    if not almost:
                        almost = True if closest_diff <= 3 else False
                        if almost:
                            logger.warning(f"Considering as triggered and if prices crosses this traded would be aborted, ain't will take entries")
                    if almost and cmp >= target1:
                        message_content = f"Its likely that we missed the trade by {closest_diff} points. Aborting this call!"
                        logger.warning(message_content)
                        asyncio.run(send_message(message_content))
                        exit()

                    print("Awaiting: ",option,next(spinner), end="\r", flush=True)
                    time.sleep(.3)

            logger.debug(f"Safe Entry Price: {safe_entry_price}")
            almost = False
            almost_price = 0
            while not entered_trade and not isBreakoutStrategy and onCrossingAbove and Clock.is_time_less_than(bot_exit_hour,bot_exit_minute):
                prevCandleClose = ohlc(option,1)["close"] if ohlc(option,1) is not None else None
                if prevCandleClose is not None and prevCandleClose >= entry_price and prevCandleClose <= target1:
                    if enterFewPointsAbove:
                        logger.info("Previous Candle has closed above expectations, will wait for another few points.")
                        while not entered_trade:
                            cmp = ensureGetLTP(option)
                            logger.debug(f"Current Market Price: {cmp}/-")
                            if  entry_price + 5 <= round(int(cmp)) <= target1:
                                logger.info(f"Taking Entry now as its enterFewPointsAbove: {cmp}/-")
                                takeEntry(option, cmp)
                            else:
                                closestLTP = cmp if cmp < closestLTP else closestLTP
                                logger.debug(f"Waiting for Entry: {option} at price {entry_price}/-, CMP: {cmp} [ closestLTP so far: {closestLTP} ], Capital Required: {entry_price*qty}/-")
                                print(next(spinner), end="\r", flush=True)
                                time.sleep(.3)
                    else:
                        logger.info(f"Previous Candle Close: {prevCandleClose}")
                        takeEntry(option, int(prevCandleClose))
                elif  prevCandleClose is not None and prevCandleClose >= target1:
                    logger.info(f"Previous Candle Close price was greater than target1, aborting taking a trade. Prev Candle Close: {prevCandleClose}")
                    asyncio.run(send_message(f"🙊 Previous Candle Close price was greater than target1, aborting taking a trade. Prev Candle Close: {prevCandleClose}",emergency=True))
                    break
                elif prevCandleClose is None:
                    logger.warning(f"Option: {option} Previous Candle retrived as None. Aborting Taking a trade.")
                    asyncio.run(send_message(f"🙊 Previous Candle retrived as None. Aborting Taking a trade!",emergency=True))
                    break
                elif round(int(prevCandleClose)) >= entry_price + almost_breakout_price and not almost:
                    almost_price = int(prevCandleClose)
                    almost = True
                elif almost and round(int(prevCandleClose)) <= static_stoploss:
                    message_content = f"😎 Ah! Price made the high of {almost_price}/- and hit the stoploss. Aborting this call!"
                    logger.warning(message_content)
                    asyncio.run(send_message(f"🙈 \n{message_content}",emergency=True))
                    exit()
                else:
                    print("Awaiting: ",option,next(spinner), end="\r", flush=True)
                    logger.debug(f"Option: {option} Previous Candle Close: {prevCandleClose}; Waiting to close above {entry_price} Capital Required: {entry_price*qty}/-")

        if not Clock.is_time_less_than(bot_exit_hour,bot_exit_minute):
            message_conent = f"🙈🙉🙊 \nTime Up... BOT Aborted!!"
            logger.warning(f"{message_conent}")
            asyncio.run(send_message(message_conent))
    except Exception as e:
        message_conent = f"Exception at Check Entry: {e}\n\nTrace:\n {traceback.print_exc()}"
        logger.error(f"Error Trace: {traceback.print_exc()}")
        asyncio.run(send_message(message_conent,emergency=True))


# =============================================================================
# Entry point — identical to v7, plus dispatcher.start() / dispatcher.stop()
# =============================================================================

if __name__ == '__main__':
    cmp, fetch_response = fetchLTP(stock_option)
    success = False if cmp == -1 or cmp == 0 else True
    max_attempts = 3
    counter = 0

    while not success and counter < max_attempts:
        if cmp != -1:
            success = True
        logger.debug(f"{stock_option}: LTP: {cmp}/- will retry...")
        counter+=1
        time.sleep(1)
        cmp, fetch_response = fetchLTP(stock_option)

    cmp_below_entry_price, cmp_below_second_entry_price, cmp_above_target1 = False, False, False
    pos_data = format_flattened_dict_as_string(position.__dict__, "Position Data:")
    message_content = f"{cmp}/- 🕉️ 🔱\n\nOverride_Paper_Trading: {override_as_paper_trading}\n\nLive Accounts:\n\n{all_live_demats_data}\n\n{pos_data}\n\n~ Hakuna Matata!"
    isInValidTrade = True if cmp == -1 or cmp == 0 else False

    api_response_formatted = format_flattened_dict_as_string(fetch_response,"API Response:")
    if isInValidTrade:
        message_content = f"🤓 Duh, Invalid!\n\n{api_response_formatted}"
    elif second_entry_price is not None and second_entry_price < cmp < entry_price and not onCrossingAbove:
        cmp_below_entry_price = True
        message_content = f"🚨 Improbable Signal:\n\nCMP is {cmp}/- at the time of script trigger is already less than Entry Price i.e {entry_price}/- & signaled to enter NEAR {entry_price}/-\n\n{pos_data}"
    elif second_entry_price is not None and cmp < second_entry_price and not onCrossingAbove:
        cmp_below_second_entry_price = True
        message_content = f"🚨 Improbable Signal:\n\nCMP is {cmp}/- at the time of script trigger is already less than Second Entry Price i.e {second_entry_price}/- & signaled to enter NEAR {second_entry_price}/-\n\n{pos_data}"
    elif cmp > target1 and onCrossingAbove:
        cmp_above_target1 = True
        message_content = f"🚨 Improbable Signal:\n\nCMP is {cmp}/- at the time of script trigger is already hihger than Target1 i.e {target1}/- & signaled to enter ABOVE entry price of {entry_price}/-\n\n{pos_data}"

    for bot_token in bot_tokens:
        try:
            logger.debug(f"bot_token: {bot_token}")
            client.start(bot_token=bot_token)
            messenger = True
            logger.info(f"bot_token in use: {bot_token}")
            break
        except Exception as e:
            logger.error(f"Failed to Start telegram client with token: {bot_token}. {e}")

    if not Config.run_without_alerts and not messenger:
        logger.critical(f"{message_content}")
        logger.critical("Failed to start any of the bots. Thus, aborted!")
        exit()

    if any([isInValidTrade, cmp_below_entry_price, cmp_below_second_entry_price, cmp_above_target1]):
        if messenger:
            asyncio.run(send_message(message_content,emergency=True))
        logger.error(message_content)
        exit()

    asyncio.run(send_message(message_content))
    logger.warning(f"{bot_name} is now activated...")

    # Start the dispatcher — one polling loop for this process
    dispatcher.start()
    check_entry(stock_option)
    dispatcher.stop()
