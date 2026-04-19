import asyncio
import calendar
import chime
import nest_asyncio
import pytz

from trade_planner import OptionCalculator
from utils.trade_manager import TradeManager
nest_asyncio.apply()
import csv
import enchant
import emoji
import json
import logging
import multiprocessing
import os
import psutil
import pyperclip
import re
import requests
import signal
import subprocess
import threading
import time
import traceback
import uuid
import pandas as pd
import gspread
import yaml
from gspread_dataframe import set_with_dataframe
from gspread.exceptions import WorksheetNotFound


from datetime import datetime,date, timedelta
from jenkinsapi.jenkins import Jenkins
from queue import Queue
from textblob import TextBlob
from telethon import TelegramClient, events
from utils.clock import Clock
from utils.constants import Config
from utils.demat import Demat
from utils.position import Position
from utils.llm_signal_parser import LLMSignalParser, is_noise as llm_is_noise
from utils import shadow_mode

# # Remember to use your own values from my.telegram.org!
# ram
api_id = "24665115"
api_hash = '4bb48e7b1dd0fcb763dfe9eb203a6216'
bot_token = "6744137962:AAFOFp0rwyK-ddZ9NRFf0XlgCXgoQgAbyWU"
# Compiled once at startup — used in analyse_event to skip build/log output
_LOG_TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,.]?\d* - (INFO|WARNING|ERROR|DEBUG)\b')
_BUILD_HDR_RE = re.compile(r'^(STARTED BY USER|RUNNING AS SYSTEM|BUILDING IN WORKSPACE|BOT_TOKEN IN USE)', re.IGNORECASE)
client = TelegramClient('anon', api_id, api_hash)
messenger = TelegramClient('messenger', api_id, api_hash)
qwerty_channel = -1001767848638
sot_channel = -1001209833646
sos_channel = -1002016606884
sot_trial_channel = -1001810504797
dictionary = enchant.Dict("en_US")
ignore_typos_list = ["CE","PE","NIFTY","MIDCPNIFTY", "FIN", "FIN NIFTY","FINNIFTY","BAJFINANCE", "BAJAJAUTO", "SENSEX","SL","BANKNIFTY","TARGET1","ABORTING","IF","COULDNT","IF","DIDNT","REMANING","STOPLOSS","TODAYS","DONT","CMP","LTP","EXPCTED","ACC","PLEASS","THATS","TRENDLINE","HEREE","PNL","CMDS","AMAZINGG","FALLL","CONFIG","SJB","CMD","AUTH","KHATA", "HATHA", "VIDHI", "DISTIL", "TARGET2", "TARGET3", "ITM"]
brokenSignal, brokenTargets, brokenSL = None, None, None

bot_name = "SOT_BOTv8"
bot  = f"{bot_name}.py"

jenkins_url = Config.ci_url
job_name = Config.ci_job_name
username = Config.ci_username
api_token = Config.ci_token
password = Config.ci_username
try:
    jenkins = Jenkins(jenkins_url, username, api_token)
except Exception as _jenkins_err:
    print(f"[WARNING] Jenkins not available at {jenkins_url} — CI features disabled. ({_jenkins_err.__class__.__name__})")
    jenkins = None
# demats = [Demat(Config.RAM_DEMAT), Demat(Config.SAI_DEMAT)]

sot_cmds = []
recent_loss_postion = None
recent_message_link = None

# EOD session counters — incremented as intents are handled
_eod_signals_fired    = 0   # NEW_SIGNAL / SL_RESOLVED that triggered SOT_BOT
_eod_reenters         = 0   # REENTER triggers
_eod_sl_updates       = 0   # UPDATE_SL messages handled
_eod_exits            = 0   # FULL_EXIT / PARTIAL_EXIT messages handled
_eod_noise_skipped    = 0   # messages dropped by pre-filter (rough count)
_eod_noise_log: list  = []  # signal-channel messages LLM classified as NOISE

# Signals below this confidence are held for manual review instead of auto-firing
LLM_MIN_FIRE_CONFIDENCE = 0.55

# Configure the logging settings
name = __file__.split("/")[-1].split(".")[0]
name_suffix = str(date.today()) + "_" + str(uuid.uuid4())
log_file = Config.logger_path + "/" + name + "_" + name_suffix + ".log"

buy_regex_pattern = 'buy.*|Bank.*|Nif.*|Fin.*|Baj.*|Nau.*|Sen.*'
target_regex_pattern = 'Traget.*|Target.*|Tatget.*|Taget.*|Taret.*|TARGWT.*|Targst.*'
sl_regex_pattern = 'SL.*'
spot_regex_pattern = r'spot\s*[:\-=]\s*(\d+)'
exit_strategy_regex_pattern = r'strategy\s+(\d+)'

# Create a logger
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# Define the format for log messages
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Create a file handler
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.INFO)
# file_handler.setLevel(eval(f"logging.{Config.flie_log_level.value}"))
file_handler.setFormatter(formatter)

# Create a console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
# console_handler.setLevel(eval(f"logging.{Config.console_log_level.value}"))
console_handler.setFormatter(formatter)

# add handlers
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# ── LLM Signal Parser ────────────────────────────────────────────────────────
try:
    llm_parser = LLMSignalParser(
        persist_path=Config.logger_path + "/llm_context.json",
        fallback_ollama_model="llama3.2",
    )
    logger.info("LLMSignalParser initialised successfully")
except Exception as _llm_err:
    logger.warning(f"LLMSignalParser not available: {_llm_err}. Falling back to regex only.")
    llm_parser = None

demats: Demat = []
# range strategy deployed accounts
for account in Config.accounts_range_strategy:
    demats.append(Demat(account,logger=logger))

# breakout strategy deployed accounts
for account in Config.accounts_breakout_strategy:
    demats.append(Demat(account,logger=logger))

all_live_demats = [demat for demat in demats if not demat.paper_trading]

unique_client_ids = set()
live_demats_unique = []

for demat in all_live_demats:
    if demat.account.client_id not in unique_client_ids:
        unique_client_ids.add(demat.account.client_id)
        live_demats_unique.append(demat)

override_flag = ""
with open(Config.current_day_override, 'r') as file:
    override_flag = file.read()

live_demats_unique_formatted = [f" - {demat.account_name}: {demat.account.client_id}" for demat in live_demats_unique]
live_demats_unique__data = "\n".join(live_demats_unique_formatted) if live_demats_unique_formatted else "- NO LIVE ACCOUNTS!"
logger.warning(f"\n============================ LIVE ACCOUNTS ============================\n\n{live_demats_unique__data}\n")

all_live_demats_formatted = [f"{demat.account_name} - {demat.account.config_type}\n- BankNifty QTY: {demat.account.quantity_banknifty}\n- Nifty QTY: {demat.account.quantity_nifty}\n- MIDCPNifty QTY: {demat.account.quantity_midcpnifty}\n- FinNifty QTY: {demat.account.quantity_finnifty}\n- BajFinance QTY: {demat.account.quantity_bajfinance}\n- Sensex QTY: {demat.account.quantity_sensex}\n- Square-Off at Target1: {demat.account.squareoff_at_first_target}\n- Await Next Target: {demat.account.await_next_target}\n- Aggressive Trail: {demat.account.aggressive_trail}\n- Should Average: {demat.account.should_average}\n\n" for demat in all_live_demats]
all_live_demats_data = "\n".join(all_live_demats_formatted) if all_live_demats_formatted else "- NO LIVE ACCOUNTS WITH CONFIG!"
logger.warning(f"\n============================ LIVE ACCOUNTS CONFIG ============================\n\n{all_live_demats_data}\n\nOverride_Flag: {override_flag}\n\n============================")

trade_manager = TradeManager(live_demats_unique,logger=logger)
latest_live_position_in_loss = None
latest_live_position = None

def getLTP(option, port_number):
    url = f"http://localhost:{port_number}/ltp?instrument={option}"
    success = False
    counter = 1
    while not success and counter < 2:
        try:
            resp = requests.get(url)
            success = True
        except Exception as e:
            # chime.warning()
            logger.error(f"Exception @getLTP:{e}")
            logger.error(f"Error Trace: {traceback.print_exc()}")
            logger.error(f"Retrying now... attempt #{counter}")
            counter+=1
            time.sleep(1)
    try:
        data = resp.json()
        return data
    except Exception as e:
        logger.error(f"Exception @getLTP:{e}")
        logger.error(f"Error Trace: {traceback.print_exc()}")
        return -1

def timeout_handler(signum, frame):
    raise TimeoutError("Timeout occurred.")

def get_user_input_with_timeout(input_text):
    # chime.warning()
    # Register the timeout handler
    signal.signal(signal.SIGALRM, timeout_handler)
    # Set the timeout in seconds
    signal.alarm(5)
    try:
        chime.info()
        user_input = input(input_text)
        # Cancel the timeout alarm
        signal.alarm(0)
        return user_input
    except TimeoutError:
        logger.info("Timeout occurred. No input received.")
        return None


def kill_python_processes():
    for proc in psutil.process_iter(['pid', 'name']):
        if 'python' in proc.info['name'].lower():
            proc.kill()

def close_all_postions(demat: Demat):
    demat.square_off_all_positions()

def send_order_placement_erros(heading,orders):
    message_content = ""
    for order in orders:
        message_content += format_flattened_dict_as_string(order["response"],f"{order['account_name']}") + "\n\n"

    if message_content:
        message_content = f"🚨 {heading}: 🚨\n\n{message_content}"
        logger.warning(f"{message_content}")
        # asyncio.run(send_message(message_content,emergency=True))
        asyncio.run(send_message(message_content,emergency=True))

def squareOff_all_postions():
    send_order_placement_erros("SQUARE-OFF ALL POSITIONS - TELEGRAM",trade_manager.exit_position_via_telegram(position=None))

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

def grep_signal(message):
    # with buy as prefix else check if its new format
    regex = r"(BUY\s.*\d)\s"
    match = re.search(regex, message, flags=re.IGNORECASE)

    # without_buy = r"(Bank.*|Nif.*|Baj.*|Fin.*|Nau.*)\s"
    # without_buy = r"(Bank[^\n]*|Nif[^\n]*|Baj[^\n]*|Fin.*|Nau[^\n]*)"
    without_buy = r"^(.*?(Bank|Nif|Baj|Fin|Nau|Mid|Sen).*)"
    match_without_buy = re.search(without_buy, message, flags=re.IGNORECASE)

    if match:
        message = match.group(1)
        signal = re.search(buy_regex_pattern, message, flags=re.IGNORECASE).group()
        signal = signal.replace("-", " ").replace("  "," ").replace("+", "").splitlines()
        signal = [re.sub(r"\s\s+", " ", line.strip()) for line in signal]
        signal = signal[0].split(" ")
        if signal[1].isdigit() and signal[2].isalpha and signal[2] in ["CE","PE"] and signal[3].isalpha:
            signal[2],signal[3] = signal[3],signal[2]
            signal[1],signal[2] = signal[2],signal[1]
        elif signal[1].isdigit() and signal[2].isalpha:
            signal[1],signal[2] = signal[2],signal[1]
        return signal
    elif match_without_buy:
        message = match_without_buy.group(1)
        signal = re.search(without_buy, message, flags=re.IGNORECASE).group()
        signal = signal.replace("-", " ").replace("  "," ").replace("+", "").splitlines()
        signal = [re.sub(r"\s\s+", " ", line.strip()) for line in signal]
        signal = signal[0].split(" ")
        if signal[0].isdigit() and signal[1].isalpha and signal[1] in ["CE","PE"] and signal[2].isalpha:
            signal[1],signal[2] = signal[2],signal[1]
            signal[0],signal[1] = signal[1],signal[0]
        elif signal[0].isdigit() and signal[1].isalpha:
            signal[0],signal[1] = signal[1],signal[0]
        signal = ['BUY'] + signal
        return signal


def grep_targets(message):
    match = re.search(target_regex_pattern, message, flags=re.IGNORECASE)
    if match:
        target = re.search(target_regex_pattern, message, flags=re.IGNORECASE).group()
        target = target.replace("-", " ").replace("  "," ").replace("+", "").replace("/", " ").splitlines()
        target = [re.sub(r"\s\s+", " ", line.strip()) for line in target]
        targets = target[0].split(" ")
        return targets
    else:
        return None


def grep_sl(message):
    match = re.search(sl_regex_pattern, message, flags=re.IGNORECASE)
    if match:
        sl = re.search(sl_regex_pattern, message, flags=re.IGNORECASE).group()
        # sl = sl.replace("-", " ").replace("  ", " ").replace("+", "").splitlines()
        sl = sl.replace("-", " ").replace("  ", " ").replace("+", "").replace("OF", "").replace("AT", "").splitlines()
        sl = [re.sub(r"\s\s+", " ", line.strip()) for line in sl]
        sl = sl[0].split(" ")
        return sl
    else:
        return None
    
def grep_spot(message):
    match = re.search(spot_regex_pattern, message, flags=re.IGNORECASE)
    
    if match:
        spot = match.group(1)  # Extract the first captured group (the digits after "spot")
        spot_cleaned = re.sub(r'[-+]', '', spot)  # Remove '-' and '+'
        spot = re.split(r'\s+', spot_cleaned)  # Split by whitespace into words
        spot = [word.strip() for word in spot if word.strip()][0]  # Remove empty strings and strip spaces
        
        return spot
    else:
        return None

def grep_exit_strategy(message):
    match = re.search(exit_strategy_regex_pattern, message, flags=re.IGNORECASE)
    
    if match:
        es_data = match.group(1)  # Extract the first captured group (the digits after "spot")
        es_cleaned = re.sub(r'[-+]', '', es_data)  # Remove '-' and '+'
        es = re.split(r'\s+', es_cleaned)  # Split by whitespace into words
        es = [word.strip() for word in es if word.strip()][0]  # Remove empty strings and strip spaces
        
        return es
    else:
        return None

def grep_sl_at_cost(message):
    """Detect 'SL at cost' / 'keep SL at cost' / 'SL cost' instruction."""
    return bool(re.search(r'\bsl\s+at\s+cost\b|\bsl\s+cost\b|\bkeep\s+sl\s+at\s+cost\b',
                          message, flags=re.IGNORECASE))

def grep_additional_points(message):
    regex_pattern = r"(enter above \d{1,2}-\d{1,2} points|enter \d{1,2}-\d{1,2} points above|above \d{1,2}-\d{1,2} points)"
    additional_points = True if re.search(regex_pattern, message, flags=re.IGNORECASE) else False
    return additional_points


def generate_signal(message):
    """ Sample Incoming Message on channel
    BUY BANKNIFTY 41600 PE NEAR 310-320 
    TARGET 335/360/390+++ 
    SL - 295

    BUY BANKNIFTY 41000 CE ABOVE 400 
    TARGET 420/430/460+++ 
    SL - 380
    """
    signal, target, sl, additional_points, spot, exit_strategy, sl_at_cost = "", "", "", False, None, None, False
    if re.search(buy_regex_pattern, message, flags=re.IGNORECASE):
        signal = grep_signal(message)

    if re.search(target_regex_pattern, message, flags=re.IGNORECASE):
        target = grep_targets(message)

    if re.search(sl_regex_pattern, message, flags=re.IGNORECASE):
        sl = grep_sl(message)

    if re.search(spot_regex_pattern, message, flags=re.IGNORECASE):
        spot = grep_spot(message)

    if re.search(exit_strategy_regex_pattern, message, flags=re.IGNORECASE):
        exit_strategy = grep_exit_strategy(message)

    if re.search('enter.*', message, flags=re.IGNORECASE) or re.search('entry.*', message, flags=re.IGNORECASE) or "WICK" in message.upper():
        additional_points = grep_additional_points(message)

    sl_at_cost = grep_sl_at_cost(message)

    return signal, target, sl, additional_points, spot, exit_strategy, sl_at_cost


def build_position_data(message):
    global brokenSignal, brokenTargets, brokenSL
    signal, targets, sl, additional_points, spot, exit_strategy, sl_at_cost = generate_signal(message)
    
    if signal and targets and sl:
        # if "LEVEL" in message and not message.upper().startswith("SOT_BOT"):
        if "LEVE" in message and not message.upper().startswith("SOT_BOT"):
            asyncio.run(send_message(f"ATTENTION NEEDED:⚠️\nInstrument Level is given, overseeing might be required.\n\nSOT_MESSAGE:\n{message}"))
        if "HERO" in message and not message.upper().startswith("SOT_BOT"):
            asyncio.run(send_message(f"ATTENTION NEEDED:⚠️\nHERO-ZERO Call recevied, validation of singal might be required.\n\nSOT_MESSAGE:\n{message}"))
        logger.error(f"Signal: {signal} Target: {targets} Stoploss: {sl}")
        instrument = signal[1]
        strike = signal[2]
        PE_CE = signal[3]
        breakoutPattern = re.compile(r'\b(above|avove|abv|ave|abve)\b', re.IGNORECASE)
        isBreakoutStrategy = bool(breakoutPattern.findall(message))
        has_level_suffix = signal[-1].upper() == "LEVEL"
        if has_level_suffix:
            # e.g. ['Nifty','25500','ce','near','235','240','level']
            #   or ['Sensex','73200','pe','above','575','level']
            entry_price = int(signal[-2])
        else:
            entry_price = int(signal[-1])
        second_entry_price = None
        if not isBreakoutStrategy:
            # For NEAR signals the lower bound sits one position further back when LEVEL is present
            second_entry_price = int(signal[-3]) if has_level_suffix else int(signal[-2])
        stoploss = int(sl[1])
        target1 = int(targets[1])
        target2 = int(targets[2])
        # Always use the mentor's LAST stated target as T3 so remaining lots
        # hold as far as intended (handles 3, 4, 5, 6-target signals uniformly).
        # If only 2 targets given, extrapolate one step as before.
        # Build the full ordered target list from the mentor's signal.
        # targets[0] is the keyword ('Target'), rest are the numbers.
        all_targets = [int(targets[i]) for i in range(1, len(targets))]
        if len(all_targets) >= 3:
            target3 = all_targets[-1]
        else:
            # extrapolate a third target if only 2 given
            target3 = target2 + (target2 - target1)
            all_targets.append(target3)
        brokenSignal, brokenTargets, brokenSL = None, None, None
        return Position(instrument=instrument,strike=strike,ce_pe=PE_CE,entry_price=entry_price,second_entry_price=second_entry_price,stoploss=stoploss,target1=target1,target2=target2,target3=target3,isBreakoutStrategy=isBreakoutStrategy,enterFewPointsAbove=additional_points,spot=spot,exit_strategy=exit_strategy,targets=all_targets,sl_at_cost=sl_at_cost)
    elif signal and not targets and not sl and ("CE" in signal or "PE" in signal):
        if not message.startswith("INSTRUMENT:"):
            asyncio.run(send_message(f"SOT_BREACH: Missing Target and SL.\n\nSOT_MESSAGE:\n{message}"))
            brokenSignal = signal
            return None
    elif not signal and targets and sl:
        asyncio.run(send_message(f"SOT_BREACH: Missing Signal.\n\nSOT_MESSAGE:\n{message}"))
        brokenTargets, brokenSL = targets, sl
        if brokenSignal and brokenTargets and brokenSL:
            formatted_signal = " ".join(brokenSignal)
            formatted_targets = " ".join(brokenTargets)
            formatted_sl = " ".join(brokenSL)
            asyncio.run(send_message(f"FIXED SIGNAL:\n{formatted_signal}\n{formatted_targets}\n{formatted_sl}")    )
        return None
    elif signal and targets and not sl:
        if not message.startswith("INSTRUMENT:"):
            asyncio.run(send_message(f"SOT_BREACH: Missing SL.\n\nSOT_MESSAGE:\n{message}"))
            return None
    else:
        if brokenSignal or brokenTargets or brokenSL:
            asyncio.run(send_message(f"Have reset the broken variables to None as the second message was not in conjunction to build position data."))
            brokenSignal, brokenTargets, brokenSL = None, None, None
        return None


def verify_postion_data(position_data: Position):
    # verify targets and sl for typos...
    if (position_data.target1 <= position_data.entry_price) or ((position_data.target1 - position_data.entry_price) < 10):
        logger.info("Found typo in target1, adjusting via script...")
        if "BANK" in position_data.instrument:
            position_data.target1 = position_data.entry_price + 20
        else:
            position_data.target1 = position_data.entry_price + 10
    
    if position_data.target2 < position_data.target1 or ((position_data.target2 - position_data.target1) < 10):
        logger.info("Found typo in target2, adjusting via script...")
        if "BANK" in position_data.instrument:
            position_data.target2 = position_data.target1 + 20
        else:
            position_data.target2 = position_data.target1 + 10
    
    if position_data.target3 < position_data.target2 or ((position_data.target3 - position_data.target2) < 10):
        logger.info("Found typo in target3, adjusting via script...")
        if "BANK" in position_data.instrument:
            position_data.target3 = position_data.target2 + 20
        else:
            position_data.target3 = position_data.target2 + 10
    elif position_data.target3 is None:
        # sometimes there are chances where we don't receieve a target3, will keep the target 3 as traget 2 itself
        if "BANK" in position_data.instrument:
            position_data.target3 = position_data.target2 + 20
        else:
            position_data.target3 = position_data.target2 + 10
    
    if position_data.entry_price < position_data.stoploss or position_data.entry_price - position_data.stoploss > 25:
        logger.info("Found typo in stoploss, adjusting via script...")
        if "BANK" in position_data.instrument:
            position_data.stoploss = position_data.entry_price - 15 if position_data.isBreakoutStrategy else position_data.entry_price - 25
        else:
            # chime.warning()
            logger.info("Adjusted Nifty Stoploss via script, adjust as per updated message if needed.")
            position_data.stoploss = position_data.entry_price - 10
    logger.info(f"Recieved Trade To Enter: Instrument: {position_data.instrument}, Strike: {position_data.strike}, PE_CE: {position_data.ce_pe}, isBreakoutStrategy?: {position_data.isBreakoutStrategy}, Entry_price: {position_data.entry_price}, target1: {position_data.target1}, target2: {position_data.target2}, target3: {position_data.target3}, stoploss: {position_data.stoploss}, enterFewPointsAbove: {position_data.enterFewPointsAbove}")
    return position_data

def is_duplicate_cmd(cmd):
    for cmd_dict in sot_cmds:
        if cmd in cmd_dict.values():
            created_time = cmd_dict["created_time"]
            time_difference = int(time.time()) - int(created_time)
            return True if time_difference <= 120 else False
    return False

def send_build_summary(emergency=False):
    message_content = "Ummm, NO Signals YET!"
    # get_todays_builds(job_name)
    if len(sot_cmds) > 0:
        regex_s = r"-s=(\d+)"  # Regular expression for -s
        regex_e = r"-e=(\d+)"  # Regular expression for -e
        regex_cepe = r"-cepe=([A-Z]+)"
        # message_content = 'ALL BUILDS SUMMARY:\n\n' + '\n\n'.join([f"- #{cmd['Build_Number']} [ {time.strftime('%H:%M:%S', time.localtime(cmd['created_time']))} ] {re.findall(regex_s, str(cmd))[0]} {re.findall(regex_cepe, str(cmd))[0]} @ {re.findall(regex_e, str(cmd))[0]}/-" for cmd in sot_cmds])
        message_content = 'ALL BUILDS SUMMARY:\n\n' + '\n\n'.join([f"- #{int(cmd['Build_Number'])} [ {time.strftime('%H:%M:%S', time.localtime(int(cmd['created_time'])))} ] {re.findall(regex_s, str(cmd))[0]} {re.findall(regex_cepe, str(cmd))[0]} @ {re.findall(regex_e, str(cmd))[0]}/-" for cmd in sot_cmds])
        # f"- #{int(cmd['Build_Number'])} [ {time.strftime('%H:%M:%S', time.localtime(int(cmd['created_time'])))} ] {re.findall(regex_s, str(cmd))[0]} {re.findall(regex_cepe, str(cmd))[0]} @ {re.findall(regex_e, str(cmd))[0]}/-"
        message_content = message_content + "\n\nSend 'SBJ <#build number>' to stop a build"
    asyncio.run(send_message(message_content,emergency=emergency))
        
def get_cmd_created_time(cmd):
    for cmd_dict in sot_cmds:
        if cmd in cmd_dict.values():
            return time.strftime("%H:%M:%S", time.localtime(int(cmd_dict["created_time"])))

def trigger_SOT_BOT(postion_data: Position):
    try:
        cmd = None
        targets_str = ",".join(str(t) for t in postion_data.targets)
        slc_flag = " -slc" if getattr(postion_data, 'sl_at_cost', False) else ""
        if postion_data.entry_price != 0 and not postion_data.isBreakoutStrategy:
            cmd = bot  + " -i=" + postion_data.instrument+" -s="+str(postion_data.strike)+" -cepe="+str(postion_data.ce_pe)+" -bo="+str(postion_data.isBreakoutStrategy)+" -e="+str(postion_data.entry_price)+" -t1="+str(postion_data.target1)+" -t2="+str(postion_data.target2)+" -t3="+str(postion_data.target3)+" -sl="+str(postion_data.stoploss)+" -efpa="+str(postion_data.enterFewPointsAbove)+" -oca="+str(postion_data.onCrossingAbove) + " -e2="+str(postion_data.second_entry_price)+" -targets="+targets_str+slc_flag
            logger.info(f"[SOT_BOT]: {cmd}")
            pyperclip.copy(cmd)
        elif postion_data.isBreakoutStrategy:
            cmd = bot  + " -i=" + postion_data.instrument+" -s="+str(postion_data.strike)+" -cepe="+str(postion_data.ce_pe)+" -bo="+str(not postion_data.isBreakoutStrategy)+" -e="+str(postion_data.entry_price)+" -t1="+str(postion_data.target1)+" -t2="+str(postion_data.target2)+" -t3="+str(postion_data.target3)+" -sl="+str(postion_data.stoploss)+" -efpa="+str(postion_data.enterFewPointsAbove)+" -oca="+str(not postion_data.onCrossingAbove) + " -e2="+str(postion_data.second_entry_price)+" -targets="+targets_str+slc_flag
            if postion_data.spot:
               cmd = cmd + " -spot=" + str(postion_data.spot)
            if postion_data.exit_strategy:
                cmd = cmd + " -es=" + str(postion_data.exit_strategy)
            logger.info(f"[SOT_BOT][CROSSING_ABOVE] Launching  SOT_BOT with crossing above strategy instead of waiting for close! (:")
            pyperclip.copy(cmd)
        else:
            message_content = f"Strategy Can either be Range or Breakout Only:\n{json.dumps(postion_data)}"
            logger.error(message_content)
            asyncio.run(send_message(message_content,emergency=True))
            return

        if not is_duplicate_cmd(cmd):
            launch_SOT_BOT(cmd)
        else:
            message_content = f"Avoiding Duplicate Entry CMD (same cmd within 2minutes):\n\n{cmd}. \n\nPreviously Created at {get_cmd_created_time(cmd)}"
            logger.warning(f"{message_content}")
            logger.warning(f"List of sot_cmds created so far:\n{sot_cmds}")
            asyncio.run(send_message(message_content,emergency=True))
    except Exception as e:
        message_content = f"💣 Exception trigger_SOT_BOT: 💣 {e}\n\nCMD:{cmd}"
        logger.error(message_content)
        logger.error(f"Error Trace: {traceback.print_exc()}")
        asyncio.run(send_message(message_content,emergency=True))

def get_active_accounts(cmd):
    accounts = Config.accounts_breakout_strategy if cmd.upper().endswith("TRUE") else Config.accounts_range_strategy
    selected_accounts = []
    
    if "BANKNIFTY" in cmd:
        selected_accounts = [(f"{acc.name} : {acc.quantity_banknifty} Qty") for acc in accounts if not acc.paper_trade]
    elif "FINNIFTY" in cmd:
        selected_accounts = [(f"{acc.name}: {acc.quantity_finnifty} Qty") for acc in accounts if not acc.paper_trade]
    elif "BAJFINANCE" in cmd:
        selected_accounts = [(f"{acc.name}: {acc.quantity_bajfinance} Qty") for acc in accounts if not acc.paper_trade]
    elif "SENSEX" in cmd:
        selected_accounts = [(f"{acc.name}: {acc.quantity_sensex} Qty") for acc in accounts if not acc.paper_trade]
    elif "NIFTY" in cmd:
        selected_accounts = [(f"{acc.name}: {acc.quantity_nifty} Qty") for acc in accounts if not acc.paper_trade]
    
    if len(selected_accounts) == 0:
        selected_accounts.append("None")

    # Convert the list items to the desired format
    formatted_output = [f"- {item}" for item in selected_accounts]

    # Join the list items with a new line
    return "\n".join(formatted_output)

def launch_SOT_BOT(cmd):
    build_params = {"SIGNAL": str(cmd)}
    triggered_build_info = None
    accounts_live = get_active_accounts(build_params['SIGNAL'])
    dup, build_number = is_duplicate_jenkins_job(job_name, build_params)
    # if is_duplicate_jenkins_job(job_name, build_params):
    if dup:
        triggered_build_info = f"The current build is a potential duplicate of #{build_number}\n\n {build_params}. Skipping build."
        logger.info(triggered_build_info)
    else:
        logger.info("The current build is not a duplicate.")
        build_number = build_jenkins_job(job_name, build_params)
        sot_cmds.append({"CMD": str(cmd), "created_time": str(int(time.time())),"Build_Number": str(build_number)})
        if build_number is not None:
            triggered_build_info = f"Build: #{build_number} \n\nSIGNAL: \n- {build_params['SIGNAL']} \n\nAccounts Live: \n{accounts_live} \n\nSit Back & Relax! (: 🍀"
            logger.info(triggered_build_info)
        else:
            chime.warning()
            triggered_build_info = "Failed to Build ):"
            logger.info(triggered_build_info)
    asyncio.run(send_message(triggered_build_info))

def get_random_quote():
    # Fetching a random quote from the ZenQuotes API
    try:
        response = requests.get('https://zenquotes.io/api/random')
        data = response.json()
        quote = data[0]['q'] + " - " + data[0]['a']
        last_hyphen_index = quote.rfind("-")
        if last_hyphen_index != -1:
            quote = quote[:last_hyphen_index].strip() + "\n- " + quote[last_hyphen_index + 1:].strip()
        quote = f"Quote For The Day: \n\n{quote}"
        return quote
    except Exception as e:
        logger.error(f"Get random quote: An error occurred: {e}")
        logger.error(f"Error Trace: {traceback.print_exc()}")
        return "What If I fall? Oh, my darling, what if you fly?\n- Erin Hanson"

async def send_file(message,caption):
    caption = f"```{caption}```"
    try:
        # Write your long message to a file
        with open('build.log', 'w') as f:
            f.write(message)
        await messenger.send_file(qwerty_channel, 'build.log',caption=caption,parse_mode='md')
    except Exception as e:
        logger.error(f"Error in send_file. {e}")
        logger.error(f"Error Trace: {traceback.print_exc()}")

async def send_message(message,emergency=False,event_id=None,source_chat_id=None):
    message = re.sub(r'```', '', message).strip()
    if not messenger:
        logger.warning("messenger isn't initialised!")
        return
    try:
        default_parsing = 'md'
        if event_id == "redirect":
            message = f"[{bot_name}]: 🐯💬 \n\n{message}"
            logger.info("sending redirection now")
            default_parsing = 'HTML'
            redirect_link = f"https://t.me/+tvD9TXULo0A4NDk9"
            message = f"<pre>{message}</pre><a href='{redirect_link}'>QWERTY</a>"
            await messenger.send_message(sos_channel, message,parse_mode=default_parsing)
            return
        
        if event_id is not None:
            message = f"[{bot_name}]: 🐯💬 \n\n{message}"
            _src = source_chat_id if source_chat_id else sot_channel
            _src_label = "QWERTY" if _src == qwerty_channel else "SOT_TRIAL" if _src == sot_trial_channel else "SOT"
            recent_message_link = f"https://t.me/c/{str(_src)[4:]}/{event_id}"
            message = f"<pre>{message}</pre><a href='{recent_message_link}'>{_src_label}</a>"
            default_parsing = 'HTML'
        else:
            message = f"```[{bot_name}]: 🐯💬 \n\n{message}```"
        if emergency:
            await messenger.send_message(sos_channel, message,parse_mode=default_parsing)
        await messenger.send_message(qwerty_channel, message,parse_mode=default_parsing)
    except Exception as e:
        logger.error(f"Error in send_message. {e}")
        logger.error(f"Error Trace: {traceback.print_exc()}")

def build_jenkins_job(job_name, build_params=None):
    # Connect to the Jenkins server
    # jenkins = Jenkins(jenkins_url, username, api_token)

    # Get the job
    job = jenkins[job_name]

    # Get the last build number before the job is invoked
    last_build_number_before = job.get_last_buildnumber()

    # Build the job with parameters
    job.invoke(build_params=build_params)

    counter = 0
    # Wait for the new build to start
    while True and counter < 30:
        # Get the last build number
        last_build_number = job.get_last_buildnumber()

        # If the last build number has changed, return it
        if last_build_number != last_build_number_before:
            return last_build_number

        # Sleep for a while before checking again
        time.sleep(1)
        counter+=1
    return None

def get_todays_builds_of_job(jenkins, job_name):
    # Get today's date
    today = datetime.now().date()

    # Get the job
    job = jenkins[job_name]

    # Filter builds built today
    todays_builds = []
    builds = job.get_builds()
    for build in builds:
        # Get the build date
        build_date = datetime.fromtimestamp(build.get_timestamp() / 1000).date()

        # Check if the build was today
        if build_date == today:
            # Get the build number and parameters
            build_number = build.get_number()
            build_params = build.get_actions()['parameters']

            # Add the build to the list
            todays_builds.append({
                'job': job.name,
                'build_number': build_number,
                'params': build_params
            })

    return todays_builds

def stop_jenkins_build(job_name, build_number=None):
    # jenkins_server = Jenkins(jenkins_url, username=username, password=password)

    # Get job
    job = jenkins.get_job(job_name)

    # Check if the job exists.
    if job is not None:
        # Get the build information
        try:
            build = job.get_build(build_number)

            # Check if the build is running before stopping it.
            if build.is_running():
                build.stop()
                message_content = f"Build #{build_number} of {job_name.upper()} stopped."
                logger.info(message_content)
                asyncio.run(send_message(message_content))
            else:
                message_content = f"Build #{build_number} of '{job_name.upper()}' is not running or does not exist."
                logger.info(message_content)
                asyncio.run(send_message(message_content))
        except Exception as e:
            message_content = f"💣 Exception at stop_jenknis_build: 💣\n\n {e}"
            logger.error(f"Error Trace: {traceback.print_exc()}")
            logger.error(message_content)
            asyncio.run(send_message(message_content))
    else:
        message_content = f"Job {job_name} does not exist."
        logger.info(message_content)
        asyncio.run(send_message(message_content))

def get_console_view(job_name, build_number=None):
    # jenkins_server = Jenkins(jenkins_url, username=username, password=password)

    # Get job
    job = jenkins.get_job(job_name)

    # Check if the job exists.
    if job is not None:
        # Get the build information
        try:
            build = job.get_build(build_number)
            data = build.get_console()
            asyncio.run(send_file(data,caption=f"#{build_number} Build log"))
        except Exception as e:
            message_content = f"💣 Exception at get_console_view: 💣\n\n{e}"
            logger.error(f"Error Trace: {traceback.print_exc()}")
            logger.error(message_content)
            asyncio.run(send_message(message_content))
    else:
        message_content = f"Job {job_name} does not exist."
        logger.info(message_content)
        asyncio.run(send_message(message_content))


def _thread_send(message, chat_id=None):
    """Send a Telegram message from a background thread via Bot API (no event loop needed).
    Defaults to sos_channel so build logs don't re-enter the LLM event handler."""
    import requests as _requests
    try:
        target = chat_id if chat_id is not None else sos_channel
        text = message[:4000]
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        _requests.post(url, json={"chat_id": target, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        logger.error(f"_thread_send error: {type(e).__name__}: {e}")


def stream_build_log(job_name, build_number, poll_interval=5, chunk_lines=20):
    """
    Stream a Jenkins build log to Telegram in real-time.

    Instead of dumping the full log as a file at the end, this polls the
    Jenkins console progressively and sends only new lines every poll_interval
    seconds. Stops automatically when the build finishes and sends a final
    SUCCESS / FAILURE summary.

    Run in a background thread so it doesn't block the Telegram event loop:
        threading.Thread(target=stream_build_log, args=(job_name, build_number), daemon=True).start()

    Args:
        job_name:      Jenkins job name
        build_number:  Build number to stream
        poll_interval: Seconds between polls (default 5)
        chunk_lines:   Max lines per Telegram message (default 20, keeps messages readable)
    """
    try:
        build_number = int(build_number)
        job = jenkins.get_job(job_name)
        if job is None:
            _thread_send(f"Job <code>{job_name}</code> not found.")
            return

        # Wait briefly for Jenkins to register the build
        time.sleep(3)
        build = job.get_build(build_number)
        _thread_send(f"📡 Streaming <code>{job_name}</code> #{build_number}... (every {poll_interval}s)")

        sent_offset = 0  # character offset of how much console text we've already sent

        while True:
            full_log = build.get_console()
            new_text = full_log[sent_offset:]

            if new_text.strip():
                lines = new_text.splitlines()
                for i in range(0, len(lines), chunk_lines):
                    chunk = "\n".join(lines[i:i + chunk_lines])
                    # HTML escape special chars, then wrap in <pre> for monospace rendering
                    safe_chunk = chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    _thread_send(f"<pre>{safe_chunk}</pre>")
                sent_offset = len(full_log)

            if not build.is_running():
                status = build.get_status()
                icon = "✅" if status == "SUCCESS" else "❌"
                duration_secs = build.get_duration().seconds if build.get_duration() else "?"
                _thread_send(f"{icon} <code>{job_name}</code> #{build_number} finished: <b>{status}</b> in {duration_secs}s")
                break

            time.sleep(poll_interval)

    except Exception as e:
        message_content = f"💣 Exception in stream_build_log: {e}"
        logger.error(f"Error Trace: {traceback.print_exc()}")
        logger.error(message_content)
        asyncio.run(send_message(message_content))

def stop_all_jenkins_builds(job_name):
    # Check if the job exists
    if job_name in jenkins:
        job = jenkins[job_name]
        # Flag to track if a running build is found
        running_build_found = False

        # Iterate through all builds and stop running builds
        for build_number, build_info in job.get_build_dict().items():
            build = job.get_build(build_number)
            if build.is_running():
                running_build_found = True
                build.stop()
                message_content = f"Stopped running build #{build_number} of job {job_name}"
                logger.info(message_content)
                asyncio.run(send_message(message_content))
        if not running_build_found:
            asyncio.run(send_message("No running builds found to stop!"))
    else:
        message_content = f"Job {job_name} does not exist."
        logger.info(message_content)
        asyncio.run(send_message(message_content))

def retry_jenkins_job(job_name, build_number):
    # # Connect to Jenkins server
    # jenkins_server = Jenkins(jenkins_url, username=username, password=password)

    # Get job
    job = jenkins.get_job(job_name)

    # Get the last build number before the job is invoked
    last_build_number_before = job.get_last_buildnumber()

    # Get build metadata
    build = job.get_build_metadata(build_number)

    # Get build parameters
    build_params_list = build.get_actions()["parameters"]

    # Convert list of dictionaries to a single dictionary
    build_params = {item['name']: item['value'] for item in build_params_list}

    # Rebuild job with same parameters
    job.invoke(build_params=build_params)
    
    counter = 0
    current_build_number = 0
    # Wait for the new build to start
    while True and counter < 30:
        # Get the last build number
        last_build_number = job.get_last_buildnumber()

        # If the last build number has changed, return it
        if last_build_number != last_build_number_before:
            current_build_number = last_build_number
            break

        # Sleep for a while before checking again
        time.sleep(1)
        counter+=1
    accounts_live = get_active_accounts(build_params['SIGNAL'])
    triggered_build_info = f"Build: #{current_build_number} \n\nSIGNAL: \n- {build_params['SIGNAL']} \n\nAccounts Live: \n{accounts_live} \n\nSit Back & Relax! (: 🍀"
    asyncio.run(send_message(triggered_build_info))

def is_duplicate_jenkins_job(job_name, build_params, username=None, password=None):
    # Connect to the Jenkins server
    # jenkins = Jenkins(jenkins_url, username, password)

    # Get the job
    job = jenkins[job_name]

    # Iterate over the running builds
    for build_number, build_info in job.get_build_dict().items():
            # Get the build
            build = job.get_build(build_number)

            # Check if the build is running
            if build.is_running():
                # Convert the running build parameters to a dictionary
                running_build_params = {param['name']: param['value'] for param in build.get_actions()['parameters']}

                # Compare the build parameters
                if running_build_params == build_params:
                    return True, build_number
    return False, None

def get_running_builds(job_name,emergency=False):
    # Get instance of Jenkins server
    # jenkins = Jenkins(jenkins_url, username, password)
    
    # Get specific job
    job = jenkins[job_name]
    running_build_found = False
    message_content = "RUNNING BUILDS:\n\n"
    # Get all builds from specific job
    for build_number, build_info in job.get_build_dict().items():
        build_instance = job.get_build(build_number)
        if build_instance.is_running():
            running_build_found = True
            build_number = build_instance.get_number()
            build_params = build_instance.get_actions()["parameters"]
            build_timestamp = build_instance.get_timestamp()
            # Convert to Indian Standard Time
            ist_tz = pytz.timezone('Asia/Kolkata')  # IST timezone
            ist_dt = build_timestamp.astimezone(ist_tz)
            build_timestamp = ist_dt.strftime('%H:%M:%S')

            signal_params = [param['value'] for param in build_params if param['name'] == 'SIGNAL'][0]
            regex_s = r"-s=(\d+)"  # Regular expression for -s
            regex_e = r"-e=(\d+)"  # Regular expression for -e
            regex_cepe = r"-cepe=([A-Z]+)"
            regex_i = r"-i=([A-Z]+)"

            message_content = message_content + f"- #{build_number} [ {build_timestamp} ] {re.findall(regex_i, str(signal_params))[0]} {re.findall(regex_s, str(signal_params))[0]} {re.findall(regex_cepe, str(signal_params))[0]} @ {re.findall(regex_e, str(signal_params))[0]}/- \n\n"
            # asyncio.run(send_message(f"Build: #{build_number} \n\nSIGNAL: \n- {signal_params}"))
        
    if not running_build_found:
        message_content = message_content + f"There are no running builds at the moment for {job_name.upper()} Job!\n"
    message_content = message_content + "\nSend 'SBJ <#build number>' to stop a build"
    asyncio.run(send_message(message_content,emergency))

def get_todays_builds(job_name,detailed=False,emergency=False):
    # Get instance of Jenkins server
    # jenkins = Jenkins(jenkins_url, username, password)
    
    # Get today's date
    today = datetime.now().date()

    # Get specific job
    job = jenkins[job_name]
    todays_builds_found = False
    message_content = "TODAY'S BUILDS:\n\n"
    # Get all builds from specific job
    for build_number, build_info in job.get_build_dict().items():
        build_instance = job.get_build(build_number)
        # Get the build date
        build_date = build_instance.get_timestamp().date()

        # Check if the build was today
        if build_date == today:
            todays_builds_found = True
            build_number = build_instance.get_number()
            build_params = build_instance.get_actions()["parameters"]
            build_timestamp = build_instance.get_timestamp()
            # Convert to Indian Standard Time
            ist_tz = pytz.timezone('Asia/Kolkata')  # IST timezone
            ist_dt = build_timestamp.astimezone(ist_tz)
            build_timestamp = ist_dt.strftime('%H:%M:%S')

            signal_params = [param['value'] for param in build_params if param['name'] == 'SIGNAL'][0]
            regex_i = r"-i=([A-Z]+)"  # Regular expression for -i
            regex_s = r"-s=(\d+)"  # Regular expression for -s
            regex_e = r"-e=(\d+)"  # Regular expression for -e
            regex_cepe = r"-cepe=([A-Z]+)"

            # message_content = message_content + f"- #{build_number} [ {build_timestamp} ] {re.findall(regex_i, str(signal_params))[0]} {re.findall(regex_s, str(signal_params))[0]} {re.findall(regex_cepe, str(signal_params))[0]} @ {re.findall(regex_e, str(signal_params))[0]}/- \n\n"
            if not detailed:
                message_content = message_content + f"- #{build_number} {build_timestamp} {re.findall(regex_i, str(signal_params))[0]} {re.findall(regex_s, str(signal_params))[0]}{re.findall(regex_cepe, str(signal_params))[0]} {re.findall(regex_e, str(signal_params))[0]}/- \n\n"
            else:
                message_content = message_content + f"- #{build_number} {build_timestamp} \n{signal_params}\n\n"
            # asyncio.run(send_message(f"Build: #{build_number} \n\nSIGNAL: \n- {signal_params}"))
    
    if not todays_builds_found:
        message_content = "No builds have been triggered today."
    asyncio.run(send_message(message_content,emergency))

def execute_on_iterm(cmd):
    asyncio.run(send_message(f"executing on iterm: {cmd}"))
    # cmd = "ps aux | grep -i python | awk '{print $2}' | xargs kill -9"
    escaped_cmd = cmd.replace("'", "'\\''").lower() + "; sleep 5; exit"
    logger.info(f"[iTerm]: {escaped_cmd}")
    osascript_command = f"osascript -e 'tell application \"iTerm\" to activate' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"D\" using command down' -e 'delay 3' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"{escaped_cmd}\"' -e 'tell application \"System Events\" to tell process \"iTerm\" to key code 52'"
    logger.info(f"osascript_command: {osascript_command}")
    # os.system(osascript_command)
    run_os_cmd(osascript_command)

def run_os_cmd(cmd):
    # return_value = os.system(cmd)
    # if return_value != 0:
    #     if return_value.stdout:
    #         std_out = f"Standard output: {return_value.stdout}"
    #     if return_value.stderr:
    #         std_err = f"Standard error: {return_value.stderr}"
    #     message_content = f"RUN_OS_CMD Failed:\nSTD_OUT: {std_out}\nSTD_ERR: {std_err} Return value:\n{return_value >> 8}"
    #     asyncio.run(send_message(message_content))
    try:
        # Run the command and capture stdout and stderr
        logger.info(f"run_os_cmd: {cmd}")
        result = subprocess.run(cmd, shell=True, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result
    except subprocess.CalledProcessError as e:
        # In case of an error, create a message with the details
        message_content = f"RUN_OS_CMD Failed:\nCMD: {cmd}\nSTD_OUT: {e.stdout}\nSTD_ERR: {e.stderr}\nReturn value: {e.returncode}"
        asyncio.run(send_message(message_content))
        return e

def run_sub_proc_kill_cmd(cmd):
    # Use try-except block to handle potential errors
    try:
        completed_process = subprocess.run(f"echo {Config.password} | sudo -S pkill -f {cmd}", shell=True, check=False)
        if completed_process.returncode != 0:
            message_content = f"RUN_SUB_PROC_KILL_CMD Failed:\nCMD: {cmd} \nError Message: {completed_process.stderr}\nReturn value: {completed_process.returncode}"
            logger.error(message_content)
            asyncio.run(send_message(message_content))
    except subprocess.CalledProcessError as e:
        message_content = f"Error occurred: {e}"
        logger.error(message_content)
        asyncio.run(send_message(message_content))
        

def re_enter_position():
    logger.info("--------------------------------------------------------------------\n")
    logger.info("SOT Commands generated so far:")
    for cmd in sot_cmds:
        logger.info(f"{cmd}")
    logger.info("--------------------------------------------------------------------")
    decision = get_user_input_with_timeout("Re-Enter Position?: (SOT CMD or N): ")
    if decision is None:
        logger.info("No input received in 15seconds, considering not to re-enter!")
        decision = "N"
    if decision.upper().startswith("SOT"):
        launch_SOT_BOT(decision)
    elif decision.upper().startswith("N"):
        decision = False

def generate_SOT_Summary():
    if len(sot_cmds) > 0:
        # CSV file path
        sot_summary = Config.logger_path + "/sot_summary" + str(date.today()) + ".txt"

        # format to human readable timestamp
        for cmd_dict in sot_cmds:
            if cmd_dict["created_time"].isdigit():
                cmd_dict["created_time"] = str(datetime.fromtimestamp(int(cmd_dict["created_time"])).strftime('%H:%M:%S'))

        # Write the list of dictionaries to the CSV file
        with open(sot_summary, mode='w', newline='') as file:
            fieldnames = sot_cmds[0].keys()
            writer = csv.DictWriter(file, fieldnames=fieldnames)

            # Write the header row
            writer.writeheader()

            # Write each row
            writer.writerows(sot_cmds)
    else:
        logger.warning("No SOT calls found")

def update_gsheet():
    current_month = datetime.now().month
    month_name = f"{calendar.month_name[current_month]} {datetime.now().year}"
    # month_name = "January"
    report = Config.logger_path + "/" + str(date.today()) + ".csv"
    # Load the data
    if os.path.exists(report) and os.path.getsize(report) > 0:
        data = pd.read_csv(report)
        # Authorize the client
        client = gspread.service_account_from_dict(Config.GSHEET_CREDS)
        # Open the existing Google Sheets document
        spreadsheet = client.open("SOT_BOT_DATA")

        headers = data.columns.tolist()

        # Try to get the worksheet by title
        try:
            worksheet = spreadsheet.worksheet(month_name)
            existing_rows = worksheet.get_all_values()
            # Write headers if tab exists but is empty
            if not existing_rows:
                worksheet.append_row(headers)
        except WorksheetNotFound:
            # New tab — write headers first, then data rows will follow
            worksheet = spreadsheet.add_worksheet(title=month_name, rows="500", cols="40")
            worksheet.append_row(headers)

        # Append data rows only (no headers — already written above)
        data_list = data.values.tolist()
        worksheet.append_rows(data_list)

        asyncio.run(send_message("Uploaded PnL!"))
    else:
        asyncio.run(send_message("No PnL report to upload!"))

def generate_PnL_report():
    """
    #!/bin/bash
    # Specify the path where the CSV files are located
    csv_path="/path/to/csv/files"

    # Merge CSV files
    cat "${csv_path}"/*.csv > merged.csv

    # Remove duplicate rows
    sort -u -o merged.csv merged.csv
    """
    report = Config.logger_path + "/" + str(date.today()) + ".csv"
    run_os_cmd(f"cat {Config.logger_path}/*.csv > {report}")
    time.sleep(2)
    run_os_cmd(f"sort -u -o {report} {report}")
    time.sleep(2)

def calculate_PnL(eod=True):
    """
    #!/bin/bash
    awk -F',' 'NR>1 {arr[$7]+=$18} END {for (i in arr) print i ":", arr[i] "/-"}' /Users/ramkishore.gollakota/Documents/algo/Fyers/Trades/2023-11-10/2023-11-10.csv | sort -t':' -k2,2nr
    """
    csv_folder = Config.logger_path
    # Filter files based on "pln" in their name
    csv_files = [f for f in os.listdir(csv_folder) if f.endswith(".csv") and "pnl" in f.lower()]

    # Print the header row
    if csv_files:
        with open(os.path.join(csv_folder, csv_files[0]), 'r') as file:
            reader = csv.reader(file)
            header = next(reader)
            logger.debug(f"Header: {header}")

        pnl_sum = {}

        # Iterate over filtered CSV files
        for filename in csv_files:
            with open(os.path.join(csv_folder, filename), 'r') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    # Adjust the column name based on the actual header
                    account_name = row['AccountName'].strip()
                    pnl_value = row['PnL']

                    # Remove non-numeric characters from PnL
                    pnl_value = ''.join(c for c in pnl_value if c.isdigit() or c in {'-', '.'})
                    
                    try:
                        pnl_sum[account_name] = pnl_sum.get(account_name, 0) + float(pnl_value)
                    except ValueError:
                        logger.debug(f"Skipping non-numeric PnL value in file {filename}")

        # Sort the results by PnL in descending order
        sorted_pnl = sorted(pnl_sum.items(), key=lambda x: x[1], reverse=True)

        pnl = '\n'.join([f"{account}: {round(pnl)}/-" for account, pnl in sorted_pnl])
        header = "Overall PnL" if eod else "Running PnL"
        asyncio.run(send_message(f"{header}:\n{pnl}"))
    else:
        # print("No matching files found.")
        asyncio.run(send_message("No PnL generated so far!"))



def wrapup_day():
    logger.warning(f"Wrapping up, squaring off all trades and calling it a day!")
    squareOff_all_postions()

    logger.warning(f"Wrapping up, stopping all builds!")
    stop_all_jenkins_builds(job_name)
    time.sleep(2)

    logger.info(f"Generating SOT calls given summary!")
    generate_SOT_Summary()
    time.sleep(2)

    logger.critical(
        f"[EOD_SUMMARY] signals_fired={_eod_signals_fired} | reenters={_eod_reenters} "
        f"| sl_updates={_eod_sl_updates} | exits={_eod_exits} "
        f"| builds_triggered={len(sot_cmds)}"
    )
    if _eod_noise_log:
        noise_preview = "\n".join(f"  - {m}" for m in _eod_noise_log[:20])
        logger.info(f"[EOD_NOISE_LOG] signal-channel msgs classified NOISE ({len(_eod_noise_log)} total, first 20):\n{noise_preview}")
    logger.warning(shadow_mode.eod_summary())

    logger.info(f"Received Stop Trading Singal - Generating Today's PnL Report!")
    generate_PnL_report()
    time.sleep(2)
    
    update_gsheet()
    time.sleep(2)

    calculate_PnL()
    time.sleep(2)

    asyncio.run(send_message("Squared off all postions, Generated PnL. Shutting Down Now...!"))
    logger.info(f"Received Stop Trading Singal - Shutting Down All Websockets & Aborting Pending Trades!")
    execute_on_iterm(Config.kill_all_python_processess)
    exit()

def remove_emojis(input_string):
    return re.sub(r'":[^:]*:','',emoji.demojize(input_string)).replace(":"," ").replace("_"," ")

def check_for_typos(input_string, ignore_list=ignore_typos_list, event_id=None):
    if input_string.upper().startswith("SOT_BOT") or input_string.upper().startswith("[SOT_BOT"):
        logger.debug(f"Ignored SOT_BOT Message: {input_string}")
        return
    input_string = remove_emojis(input_string)
    input_string = re.sub(r'[./@+\-()#_,!]|Europe-Africa|\n|\s{2,}', ' ', input_string)
    input_words = [i for i in re.split('[ _:]',input_string) if i]
    typos = [word for word in input_words if not word.isnumeric() and not dictionary.check(word) and word not in ignore_list]
    formatted_typos = [f"- {item}" for item in typos]
    formatted_typos = "\n".join(formatted_typos)

    if formatted_typos:
        message_content = f"ATTENTION NEEDED:⚠️\n\nPotential typos:\n{formatted_typos}"
        logger.warning(message_content)
        asyncio.run(send_message(message_content,event_id=event_id))

def remove_word_from_first_line(text, word):
    # Split the text into lines
    lines = text.split('\n')
    
    # Check if there is at least one line
    if lines:
        # Get the first line
        first_line = lines[0]
        
        # Remove the word if it is in the first line
        if word in first_line.split():
            first_line = ' '.join([w for w in first_line.split() if w != word])
            # Rebuild the text
            lines[0] = first_line
            text = '\n'.join(lines)
    
    return text

def remove_multiple_instances(incoming_message,event_id=None):
    message = incoming_message
    words_to_replace = ["BAJFINANCE","SENSEX","NIFTY","BANKNIFTY","FINNIFTY"]
    for word_to_replace in words_to_replace:
        first_occurrence = message.find(word_to_replace)
        if first_occurrence != -1:
            # Find the second occurrence of the word
            second_occurrence = message.find(word_to_replace, first_occurrence + 1)
            if second_occurrence != -1:
                # Replace all subsequent occurrences of the word
                new_string = message[:second_occurrence] + message[second_occurrence:].replace(word_to_replace, "")
                logger.info(f"Refined message after filtering duplicates is: {new_string}")
                message = new_string
                if "LEVEL" in message and not message.upper().startswith("SOT_BOT"):
                    asyncio.run(send_message(f"ATTENTION NEEDED:⚠️\nMultiple instances of word {word_to_replace} and Instrument Level is given.\n\nSOT_MESSAGE:\n{incoming_message}",event_id=event_id))
                else:
                    asyncio.run(send_message(f"ATTENTION NEEDED:⚠️\nMultiple instances of word {word_to_replace} is given. Please have a look!\n\nSOT_MESSAGE:\n{incoming_message}",event_id=event_id))
            else:
                logger.debug(f"No second occurrence found for {word_to_replace}.")

# SOT & qerty channels
@client.on(events.NewMessage(chats=[sot_channel, qwerty_channel, sot_trial_channel]))
async def new_message_event_handler(event):
    await analyse_event(event)


@client.on(events.MessageEdited(chats=[sot_channel, qwerty_channel, sot_trial_channel]))
async def edited_message_event_handler(event):
    # chime.warning()
    logger.info(f"[#SOT PREMIUM]: EDITED MESSAGE!!!")
    message = event.raw_text.upper()
    if "CE" in message or "PE" in message or "/" in message or "+" in message and "FIXED SIGNAL" not in message:
        message_contnet = f"\nEDITED_MESSAGE: 👨🏻‍💻\n\n{message}"
        asyncio.run(send_message(message_contnet))
    await analyse_event(event)

def kill_specific_python_process(process_name):
    subprocess.run(f"echo {Config.password} | sudo -S pkill -f {process_name}", shell=True)

def kill_all_sockets():
    asyncio.run(send_message("Shutting all websockets..."))
    run_sub_proc_kill_cmd("dart.py")
    # run_sub_proc_kill_cmd("ws_healthcheck.py")
    run_os_cmd(Config.kill_banknifty_ws)
    run_os_cmd(Config.kill_nifty_ws)
    run_os_cmd(Config.kill_finnifty_ws)
    run_os_cmd(Config.kill_bajfinance_ws)

def start_all_sockets():
    # execute_on_iterm("banknifty_ws")
    # time.sleep(5)
    # execute_on_iterm("nifty_ws")
    # time.sleep(5)
    # execute_on_iterm("finnifty_ws")
    # time.sleep(5)
    # execute_on_iterm("bajfinance_ws")
    # time.sleep(5)
    # execute_on_iterm("ws_healthcheck")
    asyncio.run(send_message("Starting all websockets..."))
    execute_on_iterm("dart")


def grep_build_number(message_replied_for):
    # Extract the number using regular expressions
    message_replied_for = message_replied_for.replace("*","")
    # build_number_received = re.search(r"BUILD NUMBER: #(\d+)", message_replied_for)
    build_number_received = re.search(r"#(\d+)", message_replied_for)
    # Check if the build_number_received is found and print the output
    if build_number_received:
        build_number_received = int(build_number_received.group(1))
        logger.info(f"build_number_received: {build_number_received}")
        # stop_jenkins_build(job_name,build_number_received)
        return build_number_received
    else:
        message_content = f"Couldn't Find the build number in recieved message: '{message_replied_for}'\n\nEnsure there's 'Build: #<number>' in the message you're replying to..."
        logger.info(message_content)
        asyncio.run(send_message(message_content))
        return None

def build_strike_from_postion(position_data: Position):
    max_loss = -30
    instrument = position_data.instrument #sys.argv[1]
    strike = position_data.strike #sys.argv[2]
    PE_CE = position_data.ce_pe #sys.argv[3]
    isBreakoutStrategy = position_data.isBreakoutStrategy #True if "True" in sys.argv[4] else False
    entry_price = position_data.entry_price #int(sys.argv[5])
    target1 = position_data.target1 #int(sys.argv[6])
    target2 = position_data.target2 #int(sys.argv[7])
    target3 = position_data.target3 #int(sys.argv[8])
    sot_stoploss = position_data.stoploss #int(sys.argv[9])
    # static_stoploss,stop_loss,stop_loss_aggressive_trailing = int(sys.argv[9]),int(sys.argv[9]),int(sys.argv[9])
    enterFewPointsAbove = position_data.enterFewPointsAbove #True if "True" in sys.argv[10] else False
    onCrossingAbove = position_data.onCrossingAbove #True if "True" in sys.argv[11] else False

    stock_option_input = instrument+strike+PE_CE
    instrument_name = None
    if stock_option_input.upper().startswith("NIF"):
        instrument_name = "NIFTY"
    elif stock_option_input.upper().startswith("FIN"):
        instrument_name = "FINNIFTY"
    elif stock_option_input.upper().startswith("BAN"):
        instrument_name = "BANKNIFTY"
    elif stock_option_input.upper().startswith("BAJF"):
        instrument_name = "BAJFINANCE"
    elif stock_option_input.upper().startswith("SEN"):
        instrument_name = "SENSEX"
    else:
        exception_content = f"Can Build Strike for Nifty || BankNifty || FinNifty || BajFinance || Sensex ONLY. Received '{stock_option_input}'"
        asyncio.run(send_message(exception_content,emergency=True))
        # raise Exception(exception_content)
        return None

    exchangeSymbol = Config.exchange_map.get(instrument_name, "NSE:")
    expiry = Config.expiry_map.get(instrument_name, None)
    assert expiry is not None, f"No Expiry Configured for '{instrument_name}'"

    port_number = Config.ws_map.get(instrument_name, None)
    assert port_number is not None, f"No Port Nunber Configured for '{instrument_name}'"

    stock_option = exchangeSymbol + instrument_name + expiry["year"] + expiry["month"] + expiry["day"]
    if stock_option_input.upper().endswith("PE"):
        stock_option = stock_option + re.findall(r"\d+", stock_option_input)[0] + "PE"
    elif stock_option_input.upper().endswith("CE"):
        stock_option = stock_option + re.findall(r"\d+", stock_option_input)[0] + "CE"
    else:
        exception_content = f"Options can only be call or put. Receveid {stock_option}"
        asyncio.run(send_message(exception_content,emergency=True))
        return None
    
    return stock_option

def is_auth_created_now():
    # Load the YAML file
    with open(Config.current_day_yml, 'r') as file:
        data = yaml.safe_load(file)

    # Iterate over all keys in the data
    for key in data:
        # Get the created_date for the current key
        created_date_str = data[key]['created_date']
        # Convert the created_date to a datetime object
        created_date = datetime.strptime(created_date_str, '%Y-%m-%d %H:%M:%S.%f')

        # Check if the created_date is within the last 2 minutes
        if datetime.now() - created_date <= timedelta(minutes=2):
            logger.info(f"The auth key '{key}' was created within the last 2 minutes. {created_date_str}")
            return True
        else:
            logger.error(f"The auth key '{key}' was created within the last 2 minutes. {created_date_str}")
            return False

def extract_nse_instrument(data):
    text = re.sub(r'```', '', data).strip()
    # match = re.search(r'NSE:(.*?)(?=:)', text)
    match = re.search(r'(NSE:\S+)', text)
    return match.group(0) if match else None

def _adjust_targets_for_reentry(targets: list, entry_price: float, min_count: int = 4) -> list:
    """
    Drop targets at/below entry_price, then extend with same inter-target delta
    until min_count targets remain. Applies to NEW_SIGNAL and REENTER alike.
    """
    if len(targets) >= 2:
        delta = max(targets[-1] - targets[-2], 5)
    else:
        delta = 20
    valid = [t for t in targets if t > entry_price]
    base = valid[-1] if valid else entry_price
    while len(valid) < min_count:
        base += delta
        valid.append(int(base))
    return valid


def _position_from_llm(signal) -> Position:
    """Convert a ParsedSignal to a Position object for SOT_BOT."""
    entry = signal.entry_high  # use the higher end of range as the working price
    all_targets = list(signal.targets) if signal.targets else []
    # Always ensure targets are above entry and we have at least 4
    if entry:
        adjusted = _adjust_targets_for_reentry(all_targets, entry)
        if adjusted != all_targets:
            logger.info(f"[POSITION] targets adjusted for entry={entry}: {all_targets} → {adjusted}")
        all_targets = adjusted
    t1 = all_targets[0] if len(all_targets) > 0 else entry + 15
    t2 = all_targets[1] if len(all_targets) > 1 else t1 + 15
    if len(all_targets) < 3:
        all_targets.append(t2 + (t2 - t1))
    t3 = all_targets[-1]
    return Position(
        instrument=signal.instrument,
        strike=signal.strike,
        ce_pe=signal.ce_pe,
        entry_price=entry,
        second_entry_price=signal.entry_low if signal.entry_low != signal.entry_high else None,
        stoploss=signal.sl,
        target1=t1,
        target2=t2,
        target3=t3,
        isBreakoutStrategy=(signal.strategy == "BREAKOUT"),
        enterFewPointsAbove=False,
        spot=None,
        exit_strategy=None,
        targets=all_targets,
        sl_at_cost=getattr(signal, 'sl_at_cost', False),
    )


async def handle_llm_intent(signal, event_id, raw_message, source_chat_id=None, reply_to_msg_id=None):
    """Route LLM-parsed intent to the right action."""
    global llm_parser

    global _eod_signals_fired, _eod_reenters, _eod_sl_updates, _eod_exits

    intent = signal.intent
    conf   = signal.confidence

    sc = source_chat_id  # shorthand

    # ── Reply-chain resolution ────────────────────────────────────────────────
    # When a follow-up message is a Telegram reply, look up the exact signal
    # that was fired for that parent message.  This is far more reliable than
    # relying on "the most recent active signal" — especially when the mentor
    # gives multiple signals in a day or re-enters the same trade from scratch.
    def _reference_signal(hint=None):
        """Return the most relevant signal for this follow-up message.

        Priority:
          1. Telegram reply-chain — exact msg_id match (most reliable)
          2. signal_store scan — all signals fired today, filtered by
             instrument/strike/ce_pe from hint if available, most recent first
             (handles "5 trades, 2 SL-hit, re-enter 6th" correctly)
        """
        if reply_to_msg_id:
            ref = llm_parser.get_by_msg_id(reply_to_msg_id)
            if ref:
                logger.info(f"[LLM] Reply-chain: resolved to msg_id={reply_to_msg_id} → {ref.summary()}")
                return ref
        # Fallback: scan today's full signal history, prefer instrument/strike match
        return llm_parser.get_best_reference(hint)

    # ── LLM_ERROR — Claude API down/balance/key expired ─────────────────────
    if intent == "LLM_ERROR":
        await send_message(
            f"🆘 SOS: LLM PARSE FAILED — signal NOT processed!\n\n"
            f"Reason: {signal.notes}\n\n"
            f"Act manually on this message:\n\n{raw_message}",
            emergency=True, event_id=event_id, source_chat_id=sc,
        )
        return

    # ── NEW_SIGNAL ───────────────────────────────────────────────────────────
    if intent == "NEW_SIGNAL":
        # If instrument is missing, try to inherit from the active signal
        # whose price range is closest to the new entry.
        # e.g. "Next entry near 160-165" after "Nifty 22600 PE near 190-195"
        # → inherit NIFTY 22600 PE (same option at a lower premium).
        if signal.instrument is None and signal.entry_high is not None:
            active = llm_parser.get_active()
            if active and active.instrument and active.entry_high is not None:
                price_diff = abs((signal.entry_high or 0) - (active.entry_high or 0))
                if price_diff <= 150:
                    signal.instrument = active.instrument
                    signal.strike     = active.strike
                    signal.ce_pe      = active.ce_pe
                    logger.info(
                        f"[LLM] Inherited instrument from active signal: "
                        f"{signal.instrument} {signal.strike} {signal.ce_pe} "
                        f"(price diff={price_diff})"
                    )

        if signal.sl_deferred:
            llm_parser.signal_pending(signal)
            await send_message(
                f"⏳ LLM: New signal received but SL is deferred.\n"
                f"Waiting for mentor to post SL before triggering.\n\n"
                f"{signal.summary()}",
                event_id=event_id, source_chat_id=sc
            )
            logger.info(f"[LLM] Signal held — awaiting SL: {signal.summary()}")
            return

        if not signal.is_actionable():
            missing = []
            if signal.instrument is None:              missing.append("instrument")
            if signal.strike is None:                  missing.append("strike")
            if signal.ce_pe is None:                   missing.append("CE/PE")
            if len(signal.targets) < 2:                missing.append(f"targets(<2, got {len(signal.targets)})")
            if signal.sl is None and not signal.sl_deferred: missing.append("SL")
            llm_parser.signal_pending(signal)
            await send_message(
                f"🔴 INCOMPLETE SIGNAL — NOT fired. Missing: {', '.join(missing)}\n"
                f"Confidence: {conf:.0%}\n{signal.summary()}\n\n"
                f"Waiting for follow-up or intervene manually.\n\nRaw:\n{raw_message}",
                emergency=True, event_id=event_id, source_chat_id=sc
            )
            return

        if conf < LLM_MIN_FIRE_CONFIDENCE:
            llm_parser.signal_pending(signal)
            await send_message(
                f"⚠️ LOW CONFIDENCE ({conf:.0%}) — NOT auto-fired. Review manually.\n\n"
                f"{signal.summary()}\n\nRaw:\n{raw_message}",
                emergency=True, event_id=event_id, source_chat_id=sc
            )
            return

        position = _position_from_llm(signal)
        logger.info(f"[LLM] Firing SOT_BOT → {signal.summary()}")
        await send_message(
            f"🤖 LLM Signal Parsed ({conf:.0%}):\n{signal.summary()}",
            event_id=event_id, source_chat_id=sc
        )
        trigger_SOT_BOT(position)
        llm_parser.signal_fired(signal, msg_id=event_id)
        _eod_signals_fired += 1

    # ── SL_RESOLVED ──────────────────────────────────────────────────────────
    elif intent == "SL_RESOLVED":
        resolved = llm_parser.signal_resolved(signal.sl)
        if resolved and resolved.is_actionable():
            position = _position_from_llm(resolved)
            logger.info(f"[LLM] SL resolved, firing SOT_BOT → {resolved.summary()}")
            await send_message(
                f"🤖 LLM: SL received, triggering pending signal.\n{resolved.summary()}",
                event_id=event_id, source_chat_id=sc
            )
            trigger_SOT_BOT(position)
            llm_parser.signal_fired(resolved, msg_id=event_id)
            _eod_signals_fired += 1
        else:
            logger.info(f"[LLM] SL_RESOLVED but no pending signal to complete")

    # ── REENTER ──────────────────────────────────────────────────────────────
    elif intent == "REENTER":
        # Prefer the reply-chain reference — if the mentor replied to the
        # original signal message, that's the exact trade to re-enter.
        # Falls back to signal_store scan (all today's signals, best match).
        reference = _reference_signal(hint=signal)
        if reference and reference.is_actionable():
            # Merge any non-null fields from this message over the reference —
            # handles "re-enter above 380" (new entry level / strategy) while
            # "re-enter same" just re-fires the original params unchanged.
            merged = reference
            if signal.entry_high is not None:
                merged.entry_low  = signal.entry_low  or signal.entry_high
                merged.entry_high = signal.entry_high
            if signal.strategy is not None:
                merged.strategy = signal.strategy
            if signal.sl is not None:
                merged.sl = signal.sl
            if signal.targets:
                merged.targets = signal.targets
            position = _position_from_llm(merged)  # target adjustment done inside
            logger.info(f"[LLM] REENTER → {merged.summary()}")
            await send_message(
                f"🔁 LLM: Re-entry signal detected.\nRe-triggering: {merged.summary()}",
                event_id=event_id, source_chat_id=sc
            )
            trigger_SOT_BOT(position)
            llm_parser.signal_fired(merged, msg_id=event_id)
            _eod_reenters += 1
        else:
            await send_message(
                f"⚠️ LLM: Re-enter received but no reference signal found.\n\nRaw: {raw_message}",
                event_id=event_id, source_chat_id=sc
            )

    # ── UPDATE_SL ────────────────────────────────────────────────────────────
    elif intent == "UPDATE_SL":
        # Use reply-chain to identify which trade this SL update belongs to.
        reference = _reference_signal(hint=signal)
        await send_message(
            f"📢 LLM: SL Update detected.\n"
            f"New SL: {signal.sl}"
            + (f" (sl_at_cost mode)" if signal.sl_at_cost else "")
            + f"\nTrade: {reference.summary() if reference else 'unknown'}\n\n"
            f"⚠️ Manual action may be needed in running SOT_BOT.",
            event_id=event_id, source_chat_id=sc
        )
        _eod_sl_updates += 1

    # ── UPDATE_TARGET ────────────────────────────────────────────────────────
    elif intent == "UPDATE_TARGET":
        await send_message(
            f"📢 LLM: Target Update detected.\n"
            f"New targets: {signal.targets}\n\n"
            f"⚠️ Review running SOT_BOT if needed.",
            event_id=event_id, source_chat_id=sc
        )

    # ── CANCEL ───────────────────────────────────────────────────────────────
    elif intent == "CANCEL":
        pending = llm_parser.get_pending()
        if pending:
            llm_parser.context.clear_pending()
            await send_message(
                f"🚫 LLM: CANCEL received. Pending signal dropped.\n{pending.summary()}",
                event_id=event_id, source_chat_id=sc
            )
        else:
            await send_message(
                f"🚫 LLM: CANCEL/IGNORE received.\n"
                f"⚠️ Stop the active SOT_BOT build if a trade is running.\n\nRaw: {raw_message}",
                emergency=True,
                event_id=event_id, source_chat_id=sc
            )

    # ── PARTIAL_EXIT / FULL_EXIT ─────────────────────────────────────────────
    elif intent in ("PARTIAL_EXIT", "FULL_EXIT"):
        await send_message(
            f"🚨 LLM: {intent} detected!\n\n{signal.notes}\n\nRaw: {raw_message}",
            emergency=True,
            event_id=event_id, source_chat_id=sc
        )
        if intent == "FULL_EXIT":
            squareOff_all_postions()
            llm_parser.signal_closed()
            logger.info("[LLM] FULL_EXIT — squared off all positions and cleared active signal.")
        _eod_exits += 1

    # ── NOISE ────────────────────────────────────────────────────────────────
    elif intent == "NOISE":
        logger.debug(f"[LLM] NOISE — skipped: {raw_message[:60]}")


async def analyse_event(event):
    global recent_loss_postion
    global latest_live_position
    global latest_live_position_in_loss
    global _eod_signals_fired, _eod_reenters
    
    # Pattern to match revised signals by SOT
    revised_signal = r"(?:take one entry (?:at|above|near|mear)|buy \d{1,2}[-\d{1,2}]? lots?|buy \d{1,2}[-\d{1,2}]? lot (?:at|above|near|mear)|now entry (?:at|above|near|mear)|now enter (?:at|above|near|mear))|one entry|take entry|now enter|enter now|entry now|now entry|more move|next move"
    
    # Pattern to match exit signals by SOT
    exit_regex_pattern = r"(take exit|exit at \d{2,3}|take exit at cost|exit at cost|exit now|book small.*exit|all can book|close at cost|book at cost|book at \d{2,3}-\d{2,3} cost|book small in this call|book small|much time|lot of time|(don'?t|do not) hold)"
    dicey_exit_regex_pattern = r"(exit|book|(don'?t|do not) hold|much time|lot of time)"
    
    # Pattern to match digits after "at" or "above" or "near" or "mear" :D typo by SOT
    entry_price_pattern = r"(?:at|above|near|mear|between|betw|bitw)\D*(\d+-\d+|\d+)"
    done_for_the_day_pattern = r"(calls given today|done for today|done for the day|THOSE WHO BOOKED SEND SCREENSHOT|SEND SCREENSHOT|SOS|ALL CAN BOOK NOW)"
    
    try:
        actual_message = event.raw_text
        message = event.raw_text.upper()
        message_replied_for = None

        if message.upper().startswith("[SOT_BOT") and "FIXED SIGNAL" not in message.upper() and "KHATA KHATA HATHA VIDHI!" not in message.upper() and "LIVE POSITION" not in message.upper():
            logger.debug(f"Ignored SOT_BOT Message: {message}")
            return
        event_id = event.message.id

        # ── LLM intent routing (signal channels) ──
        _is_signal_channel = llm_parser and event.chat_id in (sot_channel, sot_trial_channel, qwerty_channel)
        if _is_signal_channel:
            is_edit = hasattr(event, 'message') and getattr(event.message, 'edit_date', None) is not None
            # Capture reply-chain link — if this message is a Telegram reply,
            # reply_to_msg_id is the ID of the parent message (the original signal).
            reply_to_msg_id = getattr(event.message, 'reply_to_msg_id', None)
            llm_signal = llm_parser.parse(actual_message, msg_id=event_id, is_edit=is_edit, signal_channel=True)
            _v1_eod_before = _eod_signals_fired + _eod_reenters
            if llm_signal.intent == "NOISE":
                _eod_noise_log.append(actual_message[:120])
                _eod_noise_skipped += 1
            if llm_signal.intent not in ("NOISE",):
                await handle_llm_intent(llm_signal, event_id, actual_message,
                                        source_chat_id=event.chat_id,
                                        reply_to_msg_id=reply_to_msg_id)
            _v1_fired = (_eod_signals_fired + _eod_reenters) > _v1_eod_before
            shadow_mode.record(llm_signal, raw_message=actual_message,
                               event_id=event_id, chat_id=event.chat_id,
                               v1_fired=_v1_fired)
            if llm_signal.intent != "NOISE":
                return
            # LLM said NOISE for a signal-channel message — skip the actionable
            # regex blocks (exit, revised_signal, re-entry) which have broad
            # patterns that misfire on innocent commentary.  Only informational
            # checks (SJB, HERO, mistake, level-update) are safe to still run.
            # Fall through to those below after the actionable section.

        # Skip raw build/log output sent by SOT_BOTv8.py to this channel
        if _LOG_TS_RE.match(actual_message) or _BUILD_HDR_RE.match(actual_message):
            logger.debug(f"Skipping build/log message: {actual_message[:60]}")
            return

        check_for_typos(message,event_id=event_id)

        if event.is_reply:
            msg = await event.message.get_reply_message()
            actual_message_replied_for = msg.text.strip()
            message_replied_for = msg.text.strip().upper()
            logger.info(f"Previous Message:")
            logger.info(f"{message_replied_for}\n\n")
            logger.info(f"Reply for previous message:")
            logger.info(f"{message}\n\n")
        else:
            logger.info(f"[#SOT PREMIUM]: {message}")
        
        # fixes the space in between and let the trade contiune
        message = message.replace("FIN NIFTY","FINNIFTY") if "FIN NIFTY" in message else message
        
        # message = remove_word_from_first_line(message,"LEVEL")
        # removes multiple instances for the list of instruments from second occurance and checks for level mentioning
        remove_multiple_instances(message,event_id=event_id)
        
        relaxed_exit_keywords = ("NEXT TARGET", "EXITED", "SL AT COST", "REM", "REL", "REK", "LOT", "BOOKED", "NEXT")
        # check if the received message is an exit singal
        # Skip actionable regex blocks for signal-channel messages — LLM owns those.
        # The _is_signal_channel flag was set above; NOISE fallthrough still reaches here
        # but the broad patterns (exit, revised_signal, re-entry) must not misfire on
        # innocent commentary.  Non-signal channels (admin, personal) still use regex.
        if not _is_signal_channel and re.search(exit_regex_pattern, message, flags=re.IGNORECASE) and message_replied_for is not None and not any(keyword in message for keyword in relaxed_exit_keywords):
            logger.info(f"Received EXIT Signal")
            recent_loss_postion = build_position_data(message_replied_for)
            if recent_loss_postion is not None:
                logger.info(f"Captured Recent loss making postion: {recent_loss_postion.__dict__}")
                postition_to_exit = build_strike_from_postion(recent_loss_postion)
                send_order_placement_erros("EARLY EXIT POSITION - TELEGRAM",trade_manager.exit_position_via_telegram(postition_to_exit))
            else:
                message_content = f"Failed to capture Recent loss making postion.\n\nMessage Replied for:\n\n{message_replied_for} \n\nReceived Message:\n\n{message}"
                logger.info(f"Failed to capture Recent loss making postion. message replied for: {message_replied_for}")
                asyncio.run(send_message(f"🐠 🆘 Early EXIT Signal! 🆘\n\n{message_content}",emergency=True,event_id=event_id))

        elif not _is_signal_channel and re.search(exit_regex_pattern, message, flags=re.IGNORECASE) and message_replied_for is None and not any(keyword in message for keyword in relaxed_exit_keywords):
            asyncio.run(send_message(f"🐠 🆘 Early EXIT Signal as orphan message! 🆘\n\nRequesting early exit of Position, please take a look!\n\nSOT_MESSAGE:\n\n{message}",emergency=True,event_id=event_id))

        elif not _is_signal_channel and re.search(dicey_exit_regex_pattern, message, flags=re.IGNORECASE) and not any(keyword in message for keyword in relaxed_exit_keywords):
            message_content = f"🐠 🆘 Fishy EXIT Signal! 🆘 \n\nExit Position as needed or stop the Build if it didn't get triggered.!\n\nMessage_Replied_For:\n\n{message_replied_for} \n\nSOT_MESSAGE:\n\n{message}"
            logger.info(message_content)
            asyncio.run(send_message(message_content,emergency=True,event_id=event_id))


        # check message received is a revised signal:
        if not _is_signal_channel and re.search(revised_signal, message, flags=re.IGNORECASE) and message_replied_for is not None:
            logger.info("------------------------------------------------------------------")
            logger.info(f"Signal Update Recevied For:\n")
            logger.info(f"{message_replied_for}\n\n")
            logger.info(f"New Signal:\n")
            logger.info(f"{message}\n\n")
            
            match = re.search(entry_price_pattern, message, flags=re.IGNORECASE)
            if match:
                logger.info(f"price matched")
                price = match.group(1)
                revised_entry_price = int(str(price).split("-")[1]) if "-" in str(price) else int(price)
                second_entry_price = int(str(price).split("-")[0]) if "-" in str(price) else None
                signal = grep_signal(message_replied_for)
                instrument = signal[1]
                strike = signal[2]
                PE_CE = signal[3]
                breakoutPattern = re.compile(r'\b(above|avove|abv|ave|abve)\b', re.IGNORECASE)
                isBreakoutStrategy = bool(breakoutPattern.findall(message))
                if not isBreakoutStrategy and second_entry_price is None:
                    second_entry_price = revised_entry_price - 5
                onCrossingAbove = False
                additional_points = grep_additional_points(message)

                # revise targets for nifty and bank nifty based on revised entry price
                targets = grep_targets(message)
                if targets is not None:
                    target1 = int(targets[1])
                    target2 = int(targets[2])
                    target3 = None
                    if len(targets) >= 4:
                        target3 = int(targets[3])
                    else:
                        target3 = target2 + int(targets[2]-targets[1])
                else:
                    target_increment = 20 if "BANK" in instrument else 10
                    target1 = revised_entry_price + target_increment
                    target2 = target1 + target_increment
                    target3 = target2 + target_increment
                
                stoploss = None
                sl = grep_sl(message)
                if sl is not None:
                    logger.critical(f"sl: {sl}")
                    try:
                        stoploss = int(sl[1])
                    except:
                        stoploss = revised_entry_price - 15 if isBreakoutStrategy else revised_entry_price - 25
                else:
                    stoploss = revised_entry_price - 15 if isBreakoutStrategy else revised_entry_price - 25
                
                # stoploss for nifty trades
                if "BANK" not in instrument:
                    stoploss = revised_entry_price - 10
                logger.info(f"Recieved a Revised Trade To Enter: Instrument: {instrument}, Strike: {strike}, PE_CE: {PE_CE}, isBreakoutStrategy?: {isBreakoutStrategy}, Entry_price: {revised_entry_price}, target1: {target1}, target2: {target2}, target3: {target3}, stoploss: {stoploss}, enterFewPointsAbove: {additional_points}, onCrossingAbove: {onCrossingAbove}")
                postion_data = Position(instrument=instrument,strike=strike,ce_pe=PE_CE,entry_price=revised_entry_price,second_entry_price=second_entry_price,stoploss=stoploss,target1=target1,target2=target2,target3=target3,isBreakoutStrategy=isBreakoutStrategy,enterFewPointsAbove=additional_points,onCrossingAbove=onCrossingAbove)
                trigger_SOT_BOT(postion_data)
            else:
                asyncio.run(send_message(f"⚠️ Broken Revised Entry Signal:\n\n{message}\n\nmay be missing near or above",emergency=True,event_id=event_id))
            logger.info("------------------------------------------------------------------")
        elif not _is_signal_channel and re.search(revised_signal, message, flags=re.IGNORECASE):
            asyncio.run(send_message(f"⚠️ Broken Revised Entry Signal:\n\n{message}\n\nI Don't know what to do with this!",emergency=True,event_id=event_id))
            message_content = f"Recent Loss Position:\n- {latest_live_position_in_loss}\n\nRecent Live Postion:\n- {latest_live_position}"
            asyncio.run(send_message(message_content))

        #  check if received message is a re-entry signal
        # entry_agin_regex_pattern = r"(entry again|enter again|again enter|will enter|will take|re entry|entry)"
        entry_agin_regex_pattern = r"(entry again|enter again|again enter|will enter|will take|re entry|re ent)"
        if not _is_signal_channel and re.search(entry_agin_regex_pattern, message, flags=re.IGNORECASE):
            logger.info("------------------------------------------------------------------")
            logger.info(f"Re-Entry Signal Update Recevied: {message}")
            if recent_loss_postion is not None:
                logger.info(f"Recent Loss Making Position: {recent_loss_postion.__dict__}")

            # find the re_entry_price grep all the numbers of 2-5 digit lenght and filter out less than 500 to remove nifty and banknifty levels
            re_entry_price_pattern = r'\b(?:at|above|avove|abv|near|mear)\s+(\d{2,5})|(\d{2,5})-(\d{2,5})|(\d{2,5}) - (\d{2,5})\b|(\d{2,5})- (\d{2,5})\b|(\d{2,5}) -(\d{2,5})\b'
            matches = re.findall(re_entry_price_pattern, message, flags=re.IGNORECASE)

            # find the hightest prices for entry if its a range based entry i.e upper range
            numbers = [num for match in matches for num in match if num and int(num) < 500]
            re_entry_price = max(numbers) if len(numbers) > 0 else None
            position_data = None
            position_to_re_enter = build_position_data(message_replied_for) if message_replied_for is not None else None
            
            if position_to_re_enter is None:
                # postion should attribute to onCrossingAbove as the price is approaching from below on re-entry
                position_data = recent_loss_postion
            else:
                position_data = position_to_re_enter
            
            if position_data is None:
                # chime.warning()
                message_content = "Phew! Position to re-enter and Recent Loss Making Position are None. Unable to take Trade."
                logger.info(message_content)
                asyncio.run(send_message(message_content,emergency=True,event_id=event_id))
                return

            
            if re_entry_price is not None:
                position_data.entry_price = int(re_entry_price)
            else:
                # chime.info()
                logger.info(f"No revised price to re-enter, will enter on crossing the previous upper range")
            
            breakoutPattern = re.compile(r'\b(above|avove|abv|ave|abve)\b', re.IGNORECASE)
            rangePattern = re.compile(r'\b(near|mear)\b', re.IGNORECASE)
            breakoutPatternStrategy  = bool(breakoutPattern.findall(message))
            rangePatternStrategy  = bool(rangePattern.findall(message))
            if not breakoutPatternStrategy and not rangePatternStrategy:
                logger.info(f"we're asked to enter again, will consider based on message replied for")
            elif breakoutPatternStrategy:
                position_data.isBreakoutStrategy = True
            elif rangePatternStrategy:
                position_data.isBreakoutStrategy = False
            elif re_entry_price is None and not position_data.isBreakoutStrategy:
                position_data.isBreakoutStrategy = True
            else:
                position_data.isBreakoutStrategy = True

            position_data = verify_postion_data(position_data)

            asyncio.run(send_message(f"Alert: Enter Again about to be built\n\n{position_data.__dict__} ",event_id=event_id))
            logger.info(f"Re-entry position: {position_data.__dict__}")
            trigger_SOT_BOT(position_data)
            logger.info("------------------------------------------------------------------")
            # re_enter_position()

        mistake_pattern = r"(mistake|ignore|avoid|missed|sorry|don\'t enter|do not enter|dont enter)"
        if re.search(mistake_pattern, message, flags=re.IGNORECASE) and "WICK" not in message.upper(): # and wick not in will ensure its not a regular message to take entry above for breakout trades
            message_content = f"Phew! ATTENTION NEEDED:⛔️\nSOT has sent something by mistake or wants to ignore a pending call. \n\nSOT_MESSAGE: \n{message}"
            if not message.upper().startswith("SOT_BOT"):
                logger.error(f"Ignored SOT_BOT Message: {message}")
                asyncio.run(send_message(message_content,emergency=True,event_id=event_id))
                return
            logger.warning(message_content)
            chime.warning()

        if "LEVEL UPDATE" in message or "UPDAT" in message and message_replied_for is not None:
            message_content = f"\nLEVEL_UPDATED: 👨🏻‍💻\n\n{str(message_replied_for)}"
            asyncio.run(send_message(message_content,emergency=True,event_id=event_id))
        if "HERO" in message and not message.upper().startswith("SOT_BOT"):
            asyncio.run(send_message(f"ATTENTION NEEDED:⚠️\nHERO-ZERO Call recevied, validation of singal might be required.\n\nSOT_MESSAGE:\n{message}",emergency=True,event_id=event_id))

        if "SJB" in message:
            # Extract the number using regular expressions
            build_number_received = re.search(r"SJB (\d+)", message)

            # Check if the build_number_received is found and print the output
            if build_number_received:
                build_number_received = int(build_number_received.group(1))
                logger.info(f"build_number_received: {build_number_received}")
                stop_jenkins_build(job_name,build_number_received)
            else:
                message_content = f"Couldn't Find the build number in [SJB] recieved message: '{message}'"
                logger.info(message_content)
                asyncio.run(send_message(message_content))
        # elif "REF" in message:
        #     # Extract the number using regular expressions
        #     build_number_received = re.search(r"REF (\d+)", message)

        #     # Check if the build_number_received is found and print the output
        #     if build_number_received:
        #         build_number_received = int(build_number_received.group(1))
        #         logger.info(f"build_number_received: {build_number_received}")
        #         stop_jenkins_build(job_name,build_number_received)
        #         retry_jenkins_job(job_name,build_number_received)
        #     else:
        #         message_content = f"Couldn't Find the build number in [REF] recieved message: '{message}'"
        #         logger.info(message_content)
        #         asyncio.run(send_message(message_content))
        elif "VIEW" in message:
            # Extract the number using regular expressions
            build_number_received = re.search(r"VIEW (\d+)", message)
            build_number_received = int(build_number_received.group(1)) if  build_number_received else build_number_received
            logger.info(f"build_number_received: {build_number_received}")
            if not build_number_received and message_replied_for is not None:
                build_number_received = grep_build_number(message_replied_for)

            # Check if the build_number_received is found and print the output
            if build_number_received:
                logger.info(f"build_number_received message replied for: {build_number_received}")
                get_console_view(job_name,build_number_received)
            else:
                message_content = f"Couldn't Find the build number in [VIEW] recieved message: '{message}'"
                logger.info(message_content)
                asyncio.run(send_message(message_content))
        elif "STREAM" in message:
            build_number_received = re.search(r"STREAM (\d+)", message)
            build_number_received = int(build_number_received.group(1)) if build_number_received else None
            if not build_number_received and message_replied_for is not None:
                build_number_received = grep_build_number(message_replied_for)
            if build_number_received:
                threading.Thread(target=stream_build_log, args=(job_name, build_number_received), daemon=True, name=f"stream-{build_number_received}").start()
            else:
                asyncio.run(send_message(f"Couldn't find build number in [STREAM] message: '{message}'"))
        elif message == "STOP" and message_replied_for is not None:
            build_number_received = grep_build_number(message_replied_for)
            if build_number_received:
                stop_jenkins_build(job_name,build_number_received)
        elif "KHATA KHATA HATHA VIDHI!" in message.upper():
            latest_live_position_in_loss = extract_nse_instrument(message)
            message_content = "No Recent Loss making postion!" if latest_live_position_in_loss is None else f"{latest_live_position_in_loss} In Loss"
            # asyncio.run(send_message(message_content))
        elif "LIVE POSITION" in message.upper():
            latest_live_position = extract_nse_instrument(message)
            message_content = "No Recent Position Live!" if latest_live_position is None else f"{latest_live_position} is Live"
            # asyncio.run(send_message(message_content))
        elif "RECENT" in message.upper():
            message_content = f"Recent Loss Position:\n- {latest_live_position_in_loss}\n\nRecent Live Postion:\n- {latest_live_position}"
            asyncio.run(send_message(message_content))
        elif message == "RETRY" and message_replied_for is not None:
            build_number_received = grep_build_number(message_replied_for)
            if build_number_received:
                retry_jenkins_job(job_name,build_number_received)
        elif message == "REFRESH":
            build_number_received = grep_build_number(message_replied_for)
            if build_number_received:
                stop_jenkins_build(job_name,build_number_received)
                retry_jenkins_job(job_name,build_number_received)
        elif message == "RESET":
            stop_all_jenkins_builds(job_name)
        elif message in ("CLOSE", "CODE","RED"):
            squareOff_all_postions()
        elif message == "PAPER":
            logger.info("Receied CMD TO ENABLE Paper Trade Override!")
            # Write content to the file
            with open(Config.current_day_override, 'w') as file:
                file.write("TRUE")
            # Read content from the file
            with open(Config.current_day_override, 'r') as file:
                file_content = file.read()

            message_content = f"Paper Trading: {file_content}\n\n'REFRESH' Builds as needed..."
            asyncio.run(send_message(message_content))
            get_running_builds(job_name)
        elif message == "REAL":
            logger.info("Receied CMD TO DISABBLE Paper Trade Override!")
            # Write content to the file
            with open(Config.current_day_override, 'w') as file:
                file.write("FALSE")
            time.sleep(2)
            # Read content from the file
            with open(Config.current_day_override, 'r') as file:
                file_content = file.read()

            message_content = f"Paper Trading: {file_content}\n\n Refresh Builds as needed..."
            asyncio.run(send_message(message_content))
            get_running_builds(job_name)
        elif message == "RESTART":
            kill_all_sockets()
            time.sleep(5)
            start_all_sockets()
            asyncio.run(send_message("Restarted All Websockets! 🙌🏻"))
        elif message == "AUTH":
            kill_all_sockets()
            time.sleep(5)
            execute_on_iterm("fyers")
            time.sleep(60)
            execute_on_iterm("upstox")
            time.sleep(10)
            message = ""
            if is_auth_created_now():
                message = "LoggedIn! 🙌🏻"
                start_all_sockets()
            else:
                message = "Login Failed 🛑 Refrained from starting sockets, Try again"
            asyncio.run(send_message(message))
        elif message == "UPLOAD":
            update_gsheet()
        elif message == "HALT":
            kill_all_sockets()
            asyncio.run(send_message("Stopped All Websockets! 🛑"))
        elif message == "GO":
            start_all_sockets()
            message_content = f"hey there, \nStarted All Websockets! 🍀 Try saying 'Hey Hi' after 10secs...\n\n{get_random_quote()}"
            asyncio.run(send_message(message_content))
        elif message == "JOBS" or message == "LIVE" or message == "RUNNING":
            get_running_builds(job_name)
        elif message == "PNL":
            calculate_PnL(eod=False)
        elif message == "CMDS" or message == "CMD" or message == "SOT":
            message_content = "There are no signals generated yet!"
            if len(sot_cmds) > 0:
                # print(sot_cmds)
                # for cmd in sot_cmds:
                #     # print("1: ", str(time.strftime('%H:%M:%S', time.localtime(int(cmd['created_time'])))))
                #     # print("2: ", str(cmd['Build_Number']))
                #     # print("3: ", str(cmd['CMD']))
                message_content = '- ' + '\n\n- '.join([f"{str(time.strftime('%H:%M:%S', time.localtime(int(cmd['created_time']))))}: #Build: {str(cmd['Build_Number'])}\n{str(cmd['CMD'])}" for cmd in sot_cmds])
            asyncio.run(send_message(message_content))
        elif message == "SUMMARY":
            # send_build_summary()
            get_todays_builds(job_name)
        elif message == "SIGNALS":
            # send_build_summary()
            get_todays_builds(job_name,detailed=True)
        elif actual_message.startswith(bot):
            if len(actual_message.split(" ")) >= 12:
                launch_SOT_BOT(actual_message)
            else:
                asyncio.run(send_message("Insufficient Params to be a valid signal!"))
        # elif message_replied_for is not None and (message == "FIRE" or message == "HIT" or message == "SQUARE-OFF" or message == "SQUAREOFF" or message == "OFF"):
        elif message_replied_for is not None and (message in ("FIRE","HIT","SQUARE-OFF","SQUAREOFF" ,"OFF","HOT")):
            text = re.sub(r'```', '', actual_message_replied_for).strip()
            match = re.search(r'NSE:(.*?)(?=:)', text)
            if match:
                # exit_position(match.group(0))
                send_order_placement_erros("CMD(on-demand) EXIT POSITION - TELEGRAM",trade_manager.exit_position_via_telegram(match.group(0)))
            else:
                message_content = "No Position found to Square-Off!"
                logger.error(f"{message_content}")
                asyncio.run(send_message(message_content))
        elif message == "REBUILD" and message_replied_for is not None:
            text = re.sub(r'```', '', actual_message_replied_for).strip()
            match = re.search(r'SOT_BOTv\d+\.py.*?(?=\n|$)', text)

            # Extracting the value if the match is found
            if match:
                cmd = match.group(0).strip()
                launch_SOT_BOT(cmd)
            else:
                message_content = "No SIGNAL found to rebuild!"
                logger.error(f"{message_content}")
                asyncio.run(send_message(message_content))
        elif message == "GREP" or message == "PARSE" or message == "SIGNAL" and message_replied_for is not None:
            text = re.sub(r'```', '', actual_message_replied_for).strip()
            match = re.search(r'SOT_BOTv\d+\.py.*?(?=\n|$)', text)

            # Extracting the value if the match is found
            if match:
                cmd = match.group(0).strip()
                asyncio.run(send_message(cmd))
            else:
                message_content = "No SIGNAL found to grep!"
                logger.error(f"{message_content}")
                asyncio.run(send_message(message_content))
        elif message.startswith("EXECUTE"):
            pattern = r'^EXECUTE (.*)'
            match = re.match(pattern, message)
            if match:
                execute_on_iterm(match.group(1))
            else:
                logger.info("Nothing to execute")
        elif message.startswith("INSTRUMENT:"):
            # Example message received in Telegram
# message = """
# INSTRUMENT: BANKNIFTY
# SPOT: 52590
# STRIKE: 45800
# CMP: 465
# PROXIMAL_LINE: 52765
# DISTIL_LINE: 52805
# DELTA: 0.64
# GAMMA: .004
# OPTION_TYPE: PE
# TRADE_TYPE: RANGE
# SPOT_TARGET1: 52580
# SPOT_TARGET2: 52579
# SPOT_TARGET3: 52578
# ITM: -2
# """
            # Parse the parameters
            params = {line.split(": ")[0].lower(): line.split(": ")[1] for line in message.strip().split("\n")}

            # Convert the necessary parameters to float
            float_params = ['spot', 'strike', 'cmp', 'proximal_line', 'distil_line', 'delta', 'gamma', 'spot_target1', 'spot_target2', 'spot_target3', 'itm']
            for param in float_params:
                params[param] = float(params[param])

            # Output the parsed and converted parameters
            # print(params)

            optionCalc = OptionCalculator(spot=params['spot'],strike=params['strike'], cmp=params['cmp'], proximal_line=params['proximal_line'], distil_line=params['distil_line'], delta=params['delta'], gamma=params['gamma'], option_type=params['option_type'], trade_type=params['trade_type'], spot_target1=params['spot_target1'], spot_target2=params['spot_target2'], spot_target3=params['spot_target3'])
            result = optionCalc.calculate()
            if "error" in result:
                print(result["error"])
            else:
                print("Range CE Entry: ", result["entry"])
                print("Range CE Stop Loss: ", result["stop_loss"])
                print("Range CE Strike Target 1: ", result["strike_target1"])
                print("Range Ce Strike Target 2: ", result["strike_target2"])
                print("Range CE Strike Target 3: ", result["strike_target3"])
                print("\n")
            strategy = "NEAR" if params['trade_type'] == "RANGE" else "ABOVE"
            formatted_string = f"Buy {params['instrument']} {params['strike']} {params['option_type']} {strategy} {result['entry']}\nTarget {result['strike_target1']}/{result['strike_target2']}/{result['strike_target3']}\nSL {result['stop_loss']}"
            asyncio.run(send_message(f"{formatted_string}"))
        elif message == "HELP":
            message_content = f"""Hey There, you have the following options to choose from on the {job_name.upper()} Job. Send the exact text to get the task done\n

WebSockets: 🐧
- GO: Starts all the websockets\n
- HALT: Kills all the websockets and health checks\n
- RESTART: Kills and Starts all the websockets and health checks\n\n

Jenkins: 🐦
- JOBS: Get all the running builds\n
- SUMMARY: Get all the triggered builds\n
- SIGNALS: Get all the triggered builds with the parameters\n
- RESET: Stop's all the running builds\n
- VIEW <Build_Number>: Sends the build log as an attachment\n
- {bot}: {bot} ... and all SIGNAL params to build a Jenkins Job if isn't already running -es=1|2|3 to override exit strategy and -spot= to cross check\n
- STOP: Stop's specific build when replied to BOT's message which has 'Build: #'\n
- RETRY: Rebuilds's Job with for a given build number even if a build is running.\n
- REFRESH: STOP's the current running build and RETRY\n
- REF <Build_Number>: STOP and RETRY a particular build\n
- REBUILD: Rebuilds's the SIGNAL in the message replied for if a build isn't already running\n
- SJB <Build_Number>: STOP a particular build\n

Trading: 🐤
- CMDS: Send the summary of so far SOT signals generated\n
- PNL: Send the PnL of so far trades taken\n
- CONFIG: Send the current config of accounts and override\n
- PAPER: Set trading to Paper Trading\n
- REAL: Set trading as per account configuration defined\n
- HIT or FIRE or SQUAREOFF or HOT: Square-off's the postion in message. [NES:.*]\n
- AUTH: Regenerates access token of all accounts in config.yml
- CLOSE or CODE or RED: Square-Off All position and continue to trade\n
- Send the SOT's message here to generate a signal and activate the BOT\n
- SOS: SHUTDOWN the trading setup\n


{get_random_quote()}

~ Hakuna Matata! 🍀 
"""
            asyncio.run(send_message(message_content))
        elif message == "HELLO" or message == "CHECK" or message == "HEY" or message == "HEY HI" or message == "CONFIG":
            with open(Config.current_day_override, 'r') as file:
                file_content = file.read()
            override_as_paper_trading = True if file_content == "TRUE" else False
            bank_nifty_ltp = getLTP("NSE:NIFTYBANK-INDEX", 4001)
            nifty_ltp = getLTP("NSE:NIFTY50-INDEX", 4002)
            midcpnifty_ltp = getLTP("NSE:MIDCPNIFTY-INDEX", 4005)
            finnifty_ltp = getLTP("NSE:FINNIFTY-INDEX", 4003)
            bajfinance_ltp = getLTP("NSE:BAJFINANCE-EQ", 4004)
            sensex_ltp = getLTP("BSE:SENSEX-INDEX", 4006)
            message_content = f"Hey there, am here!\n\n- Nifty: {nifty_ltp}\n- BankNifty: {bank_nifty_ltp}\n- FinNifty: {finnifty_ltp}\n- MidCPNifty: {midcpnifty_ltp}\n- BajFianance: {bajfinance_ltp}\n- Sensex: {sensex_ltp} \n\nLive Accounts Config:\n\n{all_live_demats_data}\n\nOverride_Paper_Trading: {override_as_paper_trading}\n\n{get_random_quote()}"
            asyncio.run(send_message(message_content))
            
        if re.search(done_for_the_day_pattern, message, flags=re.IGNORECASE):
            logger.warning(f"SOT called it a day!")
            wrapup_day()
        else:
            if not actual_message.startswith(f"{bot}"):
                postion_data = build_position_data(message)
                if postion_data is not None:
                    postion_data = verify_postion_data(postion_data)
                    trigger_SOT_BOT(postion_data)
    except Exception as e:
        logger.error(f"Exception at new event: {e}")
        logger.error(f"Error Trace: {traceback.print_exc()}")
        asyncio.run(send_message(f"💣 Exception at analyse_event: 💣\n\n{e}",emergency=True,event_id=event_id))

def countdown(seconds=10, message=None):
    countdown_duration = seconds
    for i in range(countdown_duration, 0, -1):
        disaply_message = f"{message} in {i} seconds..." if message else "Waiting {i} seconds..."
        print(disaply_message, end='\r', flush=True)
        time.sleep(1)

if __name__ == '__main__':
    client.start()
    messenger.start(bot_token=bot_token)
    logger.info("Started Telegram BOT!")
    client.run_until_disconnected()
    messenger.run_until_disconnected()