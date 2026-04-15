import os
import subprocess
import platform
import threading
import pandas as pd
import re
import yaml

from datetime import date, datetime, timedelta
from enum import Enum
from utils.account_config import AccountConfig
from utils.credentials import FREEZE_QUANTITY_MIDCPNIFTY, HIGHEST_MIDCPNIFTY_OPTION_PRICE, LOT_SIZE_MIDCPNIFTY, RAM_DEMAT, SAI_DEMAT, LOT_SIZE_NIFTY, LOT_SIZE_BANKNIFTY, LOT_SIZE_FINNIFTY, LOT_SIZE_BAJFINANCE, FREEZE_QUANTITY_BANKNIFTY, FREEZE_QUANTITY_NIFTY, FREEZE_QUANTITY_FINNIFTY, FREEZE_QUANTITY_BAJFINANCE, HIGHEST_BANKNIFTY_OPTION_PRICE, HIGHEST_NIFTY_OPTION_PRICE, HIGHEST_FINNIFTY_OPTION_PRICE, HIGHEST_BAJFINANCE_OPTION_PRICE, LOT_SIZE_SENSEX, FREEZE_QUANTITY_SENSEX, HIGHEST_SENSEX_OPTION_PRICE, GSHEET_SCOPES, GSHEET_CREDS
from utils.custom_calendar import MyCalendar

# Get the directory containing the main script
script_directory = os.path.dirname(os.path.abspath(__file__))
smart_extractor = True

# Navigate up to the root directory by using os.path.dirname() repeatedly
root_directory = script_directory
while not os.path.exists(os.path.join(root_directory, 'README.md')):
    root_directory = os.path.dirname(root_directory)

def get_processor_info():
    if platform.system() == "Darwin":
        info = platform.uname()
        processor = info.processor
        if "arm" in processor:
            return "Apple M1"
        else:
            return "Intel"
    return None

def isM1():
    return True if "M1" in get_processor_info() else False

def read_yaml_file(filename):
    if not os.path.exists(filename):
        raise FileNotFoundError(
            f"\n\n{'='*60}\n"
            f"  Daily config not found: {filename}\n"
            f"  Run 'fyers' first to log in and generate today's config.\n"
            f"{'='*60}\n"
        )
    with open(filename, 'r') as stream:
        try:
            data = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)
    return data

# m1 = 112d8a96a5b428820981a9cb31647dabbd
class custom_log_level(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class ExpiryDateExtractor:
    def __init__(self, csv_url):
        self.csv_url = csv_url
        self.month_mapping = {
            'Jan': '1', 'Feb': '2', 'Mar': '3', 'Apr': '4',
            'May': '5', 'Jun': '6', 'Jul': '7', 'Aug': '8',
            'Sep': '9', 'Oct': 'O', 'Nov': 'N', 'Dec': 'D'
        }
        self.upstox_month_mapping = {
            'Jan': '1', 'Feb': '2', 'Mar': '3', 'Apr': '4',
            'May': '5', 'Jun': '6', 'Jul': '7', 'Aug': '8',
            'Sep': '9', 'Oct': '10', 'Nov': '11', 'Dec': '12'
        }
        self.df = pd.read_csv(self.csv_url, header=None)
        self.expiry_info = {}

    def extract_date(self, text):
        pattern = r'\d{2} \w{3} \d{2}'  # Pattern to match 'DD Mon YY' (e.g. '28 APR 26')
        match = re.search(pattern, text)
        return match.group(0) if match else None

    def process_symbols(self, index_symbols, stock_option_symbols,fyers=True):
        for symbol in index_symbols + stock_option_symbols:
            pattern = r'\b{}\b'.format(symbol)  # Match the exact symbol
            temp_df = self.df[self.df[1].str.contains(pattern, na=False, regex=True)].copy()
            temp_df['extracted_date'] = temp_df[1].apply(self.extract_date)

            if not temp_df.empty:
                date_components = temp_df['extracted_date'].str.split(' ', expand=True).iloc[0]
                # CSV date format is DD Mon YY (e.g. "28 APR 26")
                # date_components[0]=day, [1]=month, [2]=year
                original_date = datetime.strptime(date_components[0] + ' ' + date_components[1] + ' 20' + date_components[2], '%d %b %Y')
                new_date = original_date + timedelta(days=7)
                month_changed = original_date.month != new_date.month
                if smart_extractor and fyers:
                    expiry_day = '' if month_changed else date_components[0]
                    expiry_month = date_components[1]

                    if symbol in index_symbols and not month_changed:
                        expiry_month = self.month_mapping.get(expiry_month, expiry_month) if fyers else self.upstox_month_mapping.get(expiry_month, expiry_month)

                    self.expiry_info[symbol.lower()] = {
                        'expiry_day': expiry_day,
                        'expiry_month': expiry_month.upper(),
                        'expiry_year': date_components[2]
                    }
                else:
                    date_components = temp_df['extracted_date'].str.split(' ', expand=True).iloc[0]
                    expiry_day = date_components[0]
                    expiry_month = date_components[1]
                    if symbol in index_symbols:
                        expiry_month = self.month_mapping.get(expiry_month, expiry_month) if fyers else self.upstox_month_mapping.get(expiry_month, expiry_month)

                    if symbol in stock_option_symbols and not fyers:
                        expiry_month = self.upstox_month_mapping.get(expiry_month, expiry_month)

                    expiry_day = '' if symbol in stock_option_symbols and fyers else expiry_day
                    # if symbol in stock_option_symbols and fyers:
                    #     expiry_day = ''

                    self.expiry_info[symbol.lower()] = {
                        'expiry_day': expiry_day,
                        'expiry_month': expiry_month.upper(),
                        'expiry_year': date_components[2]
                    }
        return self.expiry_info

    def get_expiry_info(self):
        return self.expiry_info


class Config:
    ci_url = "http://localhost:8080"
    ci_username = "ram"
    ci_job_name = "unified"
    ci_token = "116af1b53758ca7aa8db6098e6a36b05fc" if isM1() else "11002dbdc4d4e5f1770d1a535b5f728a5c"
    fyers_log_path = os.path.join(root_directory,"fyers_reserved") #"/Users/ramkishore.gollakota/Documents/algo/Fyers/fyers_reserved"
    logger_path = os.path.join(root_directory,"Trades",date.today().strftime("%Y-%m-%d")) #os.path.join("/Users/ramkishore.gollakota/Documents/algo/Fyers/Trades",date.today().strftime("%Y-%m-%d"))
    current_day_yml = os.path.join(logger_path, "config_"+date.today().strftime("%Y-%m-%d")+".yml")
    current_day_override = os.path.join(logger_path, "override_"+date.today().strftime("%Y-%m-%d")+".txt")
    upstox_token = os.path.join(logger_path, "upstox_token_"+date.today().strftime("%Y-%m-%d")+".txt")

    # logger_path = "/Users/ramkishore.gollakota/Documents/algo/Fyers/Trades/2024-01-03"
    # current_day_yml = "/Users/ramkishore.gollakota/Documents/algo/Fyers/Trades/2024-01-03/config_2024-01-03.yml"
    # current_day_override = "/Users/ramkishore.gollakota/Documents/algo/Fyers/Trades/2024-01-03/override_2024-01-03.txt"

    console_log_level = custom_log_level.INFO
    flie_log_level = custom_log_level.INFO
    lot_size_nifty = LOT_SIZE_NIFTY
    lot_size_banknifty = LOT_SIZE_BANKNIFTY
    freeze_quantity_banknifty = FREEZE_QUANTITY_BANKNIFTY
    freeze_quantity_nifty = FREEZE_QUANTITY_NIFTY
    paper_trade = True
    run_without_alerts = False #bot will not launch if it fails to start the telegram client for communication
    squareoffAtFirstTarget = False
    quantity = 150
    freeze_quantity = 900
    scalping_quantity = 900
    moneyness = -1
    ws_start_hour,ws_start_min,ws_start_sec = 9,20,0
    scalp915_moneyness_BNCE = 0
    scalp915_moneyness_BNPE = 0
    scalp915_moneyness_NCE = 0
    scalp915_moneyness_NPE = 0
    scalping_target_stoploss_trailing = 10
    trailBy = 10 #change this to what should be the trail
    scalping_target_stoploss_no_trailing = 3    
    accounts_data = read_yaml_file(current_day_yml)
    
    if not date.today().strftime("%Y-%m-%d") in accounts_data["RAM_DEMAT"]["created_date"]:
        raise "Config isn't created today!"
    
    RAM_CLIENT_ID,RAM_SECRET_KEY,RAM_ACCESS_TOKEN,RAM_BANKNIFTY_QTY,RAM_NIFTY_QTY,RAM_MIDCPNIFTY_QTY,RAM_FINNIFTY_QTY,RAM_BAJFINANCE_QTY = RAM_DEMAT["client_id"],RAM_DEMAT["client_id"],accounts_data["RAM_DEMAT"]["access_token"],accounts_data["RAM_DEMAT"]["max_quantity_bank_nifty"],accounts_data["RAM_DEMAT"]["max_quantity_nifty"],accounts_data["RAM_DEMAT"]["max_quantity_midcpnifty"],accounts_data["RAM_DEMAT"]["max_quantity_finnifty"],accounts_data["RAM_DEMAT"]["max_quantity_bajfinance"]
    SAI_CLIENT_ID,SAI_SECRET_KEY,SAI_ACCESS_TOKEN,SAI_BANKNIFTY_QTY,SAI_NIFTY_QTY,SAI_MIDCPNIFTY_QTY,SAI_FINNIFTY_QTY,SAI_BAJFINANCE_QTY = SAI_DEMAT["client_id"],SAI_DEMAT["client_id"],accounts_data["SAI_DEMAT"]["access_token"],accounts_data["SAI_DEMAT"]["max_quantity_bank_nifty"],accounts_data["SAI_DEMAT"]["max_quantity_nifty"],accounts_data["SAI_DEMAT"]["max_quantity_midcpnifty"],accounts_data["SAI_DEMAT"]["max_quantity_finnifty"],accounts_data["SAI_DEMAT"]["max_quantity_bajfinance"]
    RAM_SENSEX_QTY = accounts_data["RAM_DEMAT"].get("max_quantity_sensex", 0)
    SAI_SENSEX_QTY = accounts_data["SAI_DEMAT"].get("max_quantity_sensex", 0)
    
    accounts_range_strategy = [
        AccountConfig(name="[ SAI_LIVE ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=SAI_BANKNIFTY_QTY,quantity_nifty=SAI_NIFTY_QTY,quantity_midcpnifty=SAI_MIDCPNIFTY_QTY,quantity_finnifty=SAI_FINNIFTY_QTY,quantity_bajfinance=SAI_BAJFINANCE_QTY,quantity_sensex=SAI_SENSEX_QTY,paper_trade=True,squareoff_at_first_target=False,await_next_target=True,should_average=True,config_type="Range"),
        AccountConfig(name="[ RAM_LIVE ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=RAM_BANKNIFTY_QTY,quantity_nifty=RAM_NIFTY_QTY,quantity_midcpnifty=RAM_MIDCPNIFTY_QTY,quantity_finnifty=RAM_FINNIFTY_QTY,quantity_bajfinance=RAM_BAJFINANCE_QTY,quantity_sensex=RAM_SENSEX_QTY,paper_trade=True,squareoff_at_first_target=True,config_type="Range",trade_banknifty=True,trade_bajfinance=False,trade_finnifty=False,trade_midcpnifty=False,trade_nifty=False,trade_sensex=False),

        AccountConfig(name="[ SAI's Demat1 ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY,quantity_nifty=FREEZE_QUANTITY_NIFTY,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE,quantity_sensex=FREEZE_QUANTITY_SENSEX,paper_trade=True,squareoff_at_first_target=True,should_average=True,config_type="Range"),
        AccountConfig(name="[ RAM's Demat1 ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=True,config_type="Range"),

        AccountConfig(name="[ SAI's Demat2 ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY,quantity_nifty=FREEZE_QUANTITY_NIFTY,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE,quantity_sensex=FREEZE_QUANTITY_SENSEX,paper_trade=True,squareoff_at_first_target=False,should_average=True,config_type="Range"),
        AccountConfig(name="[ RAM's Demat2 ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,config_type="Range"),

        AccountConfig(name="[ SAI's Demat3 ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY,quantity_nifty=FREEZE_QUANTITY_NIFTY,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE,quantity_sensex=FREEZE_QUANTITY_SENSEX,paper_trade=True,squareoff_at_first_target=False,await_next_target=True,should_average=True,config_type="Range"),
        AccountConfig(name="[ RAM's Demat3 ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,await_next_target=True,config_type="Range"),

        AccountConfig(name="[ Nivi's Demat1 ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY,quantity_nifty=FREEZE_QUANTITY_NIFTY,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE,quantity_sensex=FREEZE_QUANTITY_SENSEX,paper_trade=True,squareoff_at_first_target=False,await_next_target=False,should_average=True,lazy_trail=True,config_type="Range"),
        AccountConfig(name="[ Nivi's Demat2 ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,await_next_target=False,lazy_trail=True,config_type="Range"),

        AccountConfig(name="[ SAI's Demat1_Aggressive ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY,quantity_nifty=FREEZE_QUANTITY_NIFTY,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE,quantity_sensex=FREEZE_QUANTITY_SENSEX,paper_trade=True,squareoff_at_first_target=True,should_average=True,aggressive_trail=True,config_type="Range"),
        AccountConfig(name="[ RAM's Demat1_Aggressive ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=True,aggressive_trail=True,config_type="Range"),

        AccountConfig(name="[ SAI's Demat2_Aggressive ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY,quantity_nifty=FREEZE_QUANTITY_NIFTY,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE,quantity_sensex=FREEZE_QUANTITY_SENSEX,paper_trade=True,squareoff_at_first_target=False,should_average=True,aggressive_trail=True,config_type="Range"),
        AccountConfig(name="[ RAM's Demat2_Aggressive ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,aggressive_trail=True,config_type="Range"),

        AccountConfig(name="[ SAI's Demat3_Aggressive ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY,quantity_nifty=FREEZE_QUANTITY_NIFTY,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE,quantity_sensex=FREEZE_QUANTITY_SENSEX,paper_trade=True,squareoff_at_first_target=False,await_next_target=True,should_average=True,aggressive_trail=True,config_type="Range"),
        AccountConfig(name="[ RAM's Demat3_Aggressive ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,await_next_target=True,aggressive_trail=True,config_type="Range"),
    ]

    # code check in place for not to average when its a breakout trade
    accounts_breakout_strategy = [
        AccountConfig(name="[ SAI_LIVE ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=SAI_BANKNIFTY_QTY,quantity_nifty=SAI_NIFTY_QTY,quantity_midcpnifty=SAI_MIDCPNIFTY_QTY,quantity_finnifty=SAI_FINNIFTY_QTY,quantity_bajfinance=SAI_BAJFINANCE_QTY,quantity_sensex=SAI_SENSEX_QTY,paper_trade=True,squareoff_at_first_target=False,await_next_target=True,aggressive_trail=True,config_type="Breakout"),
        AccountConfig(name="[ RAM_LIVE ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=RAM_BANKNIFTY_QTY,quantity_nifty=RAM_NIFTY_QTY,quantity_midcpnifty=RAM_MIDCPNIFTY_QTY,quantity_finnifty=RAM_FINNIFTY_QTY,quantity_bajfinance=RAM_BAJFINANCE_QTY,quantity_sensex=RAM_SENSEX_QTY,paper_trade=True,squareoff_at_first_target=True,trade_banknifty=True,trade_bajfinance=False,trade_finnifty=False,trade_midcpnifty=False,trade_nifty=False,trade_sensex=False,config_type="Breakout"),

        AccountConfig(name="[ SAI's Demat1 ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=True,should_average=True,config_type="Breakout"),
        AccountConfig(name="[ RAM's Demat1 ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=True,config_type="Breakout"),

        AccountConfig(name="[ SAI's Demat2 ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,should_average=True,config_type="Breakout"),
        AccountConfig(name="[ RAM's Demat2 ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,config_type="Breakout"),

        AccountConfig(name="[ SAI's Demat3 ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,await_next_target=True,should_average=True,config_type="Breakout"),
        AccountConfig(name="[ RAM's Demat3 ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,await_next_target=True,config_type="Breakout"),

        AccountConfig(name="[ Nivi's Demat1 ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,await_next_target=True,should_average=True,lazy_trail=True,config_type="Breakout"),
        AccountConfig(name="[ Nivi's Demat2 ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,await_next_target=True,lazy_trail=True,config_type="Breakout"),

        AccountConfig(name="[ SAI's Demat1_Aggressive ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=True,should_average=True,aggressive_trail=True,config_type="Breakout"),
        AccountConfig(name="[ RAM's Demat1_Aggressive ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=True,aggressive_trail=True,config_type="Breakout"),

        AccountConfig(name="[ SAI's Demat2_Aggressive ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,should_average=True,aggressive_trail=True,config_type="Breakout"),
        AccountConfig(name="[ RAM's Demat2_Aggressive ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,aggressive_trail=True,config_type="Breakout"),

        AccountConfig(name="[ SAI's Demat3_Aggressive ]",client_id=SAI_CLIENT_ID,secret_key=SAI_SECRET_KEY,access_token=SAI_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,await_next_target=True,should_average=True,aggressive_trail=True,config_type="Breakout"),
        AccountConfig(name="[ RAM's Demat3_Aggressive ]",client_id=RAM_CLIENT_ID,secret_key=RAM_SECRET_KEY,access_token=RAM_ACCESS_TOKEN,quantity_banknifty=FREEZE_QUANTITY_BANKNIFTY*2,quantity_nifty=FREEZE_QUANTITY_NIFTY*2,quantity_midcpnifty=FREEZE_QUANTITY_MIDCPNIFTY*2,quantity_finnifty=FREEZE_QUANTITY_FINNIFTY*2,quantity_bajfinance=FREEZE_QUANTITY_BAJFINANCE*2,quantity_sensex=FREEZE_QUANTITY_SENSEX*2,paper_trade=True,squareoff_at_first_target=False,await_next_target=True,aggressive_trail=True,config_type="Breakout")
    ]

    # weekly expiry
    # Oct/Nov/Dec == O/N/D
    # last week == monthly expiry "JAN" | "FEB" | "MAR" ...
    # expiry_nifty = {
    #     "year": "23",
    #     "month": "O",
    #     "day": "19",
    # }

    # weekly expiry
    # Oct/Nov/Dec == O/N/D
    # last week == monthly expiry "JAN" | "FEB" | "MAR" ...
    # expiry_banknifty = {
    #     "year": "23",
    #     "month": "O",
    #     "day": "18",
    # }

    csv_url = 'https://public.fyers.in/sym_details/NSE_FO.csv'
    bse_csv_url = 'https://public.fyers.in/sym_details/BSE_FO.csv'
    index_symbols = ['NIFTY']
    # index_symbols = ['BANKNIFTY', 'FINNIFTY', 'NIFTY', ]
    # index_symbols = ['FINNIFTY', 'NIFTY', 'MIDCPNIFTY']
    stock_option_symbols = ['BAJFINANCE','BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY']
    bse_index_symbols = ['SENSEX']

    # Create an instance of ExpiryDateExtractor
    extractor = ExpiryDateExtractor(csv_url)

    # Process the symbols
    expiry_info = extractor.process_symbols(index_symbols, stock_option_symbols)
    print("expiry_info: ", expiry_info)

    # Retrieve and print the expiry information
    # expiry_info = extractor.get_expiry_info()

    # BSE extractor for SENSEX
    bse_extractor = ExpiryDateExtractor(bse_csv_url)
    bse_expiry_info = bse_extractor.process_symbols(bse_index_symbols, [])
    print("bse_expiry_info: ", bse_expiry_info)

    # Create an instance of ExpiryDateExtractor
    upstox_extractor = ExpiryDateExtractor(csv_url)
    # Process the symbols
    upstox_expiry_info = upstox_extractor.process_symbols(index_symbols, stock_option_symbols,fyers=False)

    # Retrieve and print the expiry information
    # upstox_expiry_info = upstox_extractor.get_expiry_info()

    # print("expiry_info:\n", expiry_info)
    # print("upstox_expiry_info:\n", upstox_expiry_info)

    # Now you can access the information like this:
    # expiry_info['banknifty']['expiry_day']
    # expiry_info['banknifty']['expiry_year']
    # expiry_info['banknifty']['expiry_month']

    expiry_midcpnifty = {
        "year": expiry_info['midcpnifty']['expiry_year'],
        "month": expiry_info['midcpnifty']['expiry_month'],
        "day": expiry_info['midcpnifty']['expiry_day'],
    }
    
    expiry_finnifty = {
        "year": expiry_info['finnifty']['expiry_year'],
        "month": expiry_info['finnifty']['expiry_month'],
        "day": expiry_info['finnifty']['expiry_day'],
    }

    expiry_banknifty = {
        "year": expiry_info['banknifty']['expiry_year'],
        "month": expiry_info['banknifty']['expiry_month'],
        "day": expiry_info['banknifty']['expiry_day'],
    }

    # expiry_banknifty = {
    #     "year": expiry_info['banknifty']['expiry_year'],
    #     "month": "4",
    #     "day": "30",
    # }

    expiry_bajfinance = {
        "year": expiry_info['bajfinance']['expiry_year'],
        "month": expiry_info['bajfinance']['expiry_month'],
        "day": expiry_info['bajfinance']['expiry_day'],
    }
    
    expiry_nifty = {
        "year": expiry_info['nifty']['expiry_year'],
        "month": expiry_info['nifty']['expiry_month'],
        "day": expiry_info['nifty']['expiry_day'],
    }
    # expiry_nifty = {
    #     "year": expiry_info['nifty']['expiry_year'],
    #     "month": "4",
    #     "day": "30",
    # }

    expiry_sensex = {
        "year": bse_expiry_info['sensex']['expiry_year'],
        "month": bse_expiry_info['sensex']['expiry_month'],
        "day": bse_expiry_info['sensex']['expiry_day'],
    }

    # print("expiry_midcpnifty: ", expiry_midcpnifty)
    # print("expiry_finnifty: ", expiry_finnifty)
    # print("expiry_banknifty: ", expiry_banknifty)
    # print("expiry_bajfinance: ", expiry_bajfinance)
    # print("expiry_nifty: ", expiry_nifty)
    

    # expiry_midcpnifty = {
    #     "year": "24",
    #     "month": "4 ",
    #     "day": "08",
    # }
    
    # expiry_finnifty = {
    #     "year": "24",
    #     "month": "4",
    #     "day": "09",
    # }

    # expiry_banknifty = {
    #     "year": "24",
    #     "month": "4",
    #     "day": "03",
    # }

    # expiry_bajfinance = {
    #     "year": "24",
    #     "month": "APR",
    #     "day": "",
    # }
    
    # expiry_nifty = {
    #     "year": "24",
    #     "month": "4",
    #     "day": "04",
    # }
    
    expiry_map = {
        "BANKNIFTY": expiry_banknifty,
        "NIFTY": expiry_nifty,
        "MIDCPNIFTY": expiry_midcpnifty,
        "FINNIFTY": expiry_finnifty,
        "BAJFINANCE": expiry_bajfinance,
        "SENSEX": expiry_sensex,
    }

    ws_map = {
        "BANKNIFTY": 4001,
        "NIFTY": 4002,
        "FINNIFTY": 4003,
        "BAJFINANCE": 4004,
        "MIDCPNIFTY": 4005,
        "SENSEX": 4006,
    }

    lot_size_map = {
        "BANKNIFTY": LOT_SIZE_BANKNIFTY,
        "NIFTY": LOT_SIZE_NIFTY,
        "MIDCPNIFTY": LOT_SIZE_MIDCPNIFTY,
        "FINNIFTY": LOT_SIZE_FINNIFTY,
        "BAJFINANCE": LOT_SIZE_BAJFINANCE,
        "SENSEX": LOT_SIZE_SENSEX,
    }

    freeze_quantity_map = {
        "BANKNIFTY": FREEZE_QUANTITY_BANKNIFTY,
        "NIFTY": FREEZE_QUANTITY_NIFTY,
        "MIDCPNIFTY": FREEZE_QUANTITY_MIDCPNIFTY,
        "FINNIFTY": FREEZE_QUANTITY_FINNIFTY,
        "BAJFINANCE": FREEZE_QUANTITY_BAJFINANCE,
        "SENSEX": FREEZE_QUANTITY_SENSEX,
    }

    highest_option_price_map = {
        "BANKNIFTY": HIGHEST_BANKNIFTY_OPTION_PRICE,
        "NIFTY": HIGHEST_NIFTY_OPTION_PRICE,
        "MIDCPNIFTY": HIGHEST_MIDCPNIFTY_OPTION_PRICE,
        "FINNIFTY": HIGHEST_FINNIFTY_OPTION_PRICE,
        "BAJFINANCE": HIGHEST_BAJFINANCE_OPTION_PRICE,
        "SENSEX": HIGHEST_SENSEX_OPTION_PRICE,
    }

    exchange_map = {
        "BANKNIFTY": "NSE:",
        "NIFTY": "NSE:",
        "MIDCPNIFTY": "NSE:",
        "FINNIFTY": "NSE:",
        "BAJFINANCE": "NSE:",
        "SENSEX": "BSE:",
    }

    # the below doesnt' work for Oct | Nov | Dec as the format changes to alphabet instead of month
    # # '2023-07-14 23:59:59'
    # if not MyCalendar.is_future_date("20"+expiry_nifty["year"] + "-" + expiry_nifty["month"] + "-" + expiry_nifty["day"] + " " + "23:59:59"):
    #     chime.warning()
    #     raise Exception("Nifty Expiry provided has expired!")
    
    # # '2023-07-14 23:59:59'
    # if not MyCalendar.is_future_date("20"+expiry_banknifty["year"] + "-" + expiry_banknifty["month"] + "-" + expiry_banknifty["day"] + " " + "23:59:59"):
    #     chime.warning()
    #     raise Exception("BankNifty Expiry provided has expired!")

    expiry = {
            "year": "23",
            "month": str(int(datetime.now().strftime("%m"))),
            "day": MyCalendar.current_weekly_exipry_date() 
        } if not MyCalendar.is_last_week_of_month(datetime.now().date()) else {
        "year": "23",
        "month": str(datetime.now().strftime("%b")),
        "day": ""
    }

    # GSHEET_SCOPES and GSHEET_CREDS imported from utils.credentials (kept out of git)

    password = "Srisairam1606$"
    # kill_banknifty_ws = f"echo '{password}' | sudo -S kill -9 $(lsof -t -i:4001 -sTCP:LISTEN); sleep 5; exit"
    # kill_nifty_ws = f"echo '{password}' | sudo -S kill -9 $(lsof -t -i:4002 -sTCP:LISTEN); sleep 5; exit"
    # kill_midcpnifty_ws = f"echo '{password}' | sudo -S kill -9 $(lsof -t -i:4005 -sTCP:LISTEN); sleep 5; exit"
    # kill_finnifty_ws = f"echo '{password}' | sudo -S kill -9 $(lsof -t -i:4003 -sTCP:LISTEN); sleep 5; exit"
    # kill_bajfinance_ws = f"echo '{password}' | sudo -S kill -9 $(lsof -t -i:4004 -sTCP:LISTEN); sleep 5; exit"
    # kill_all_python_processess = "ps aux | grep -i python | awk '{print $2}' | xargs kill -9; sleep 5; exit"

    kill_banknifty_ws = f"echo '{password}' | sudo -S kill -9 $(lsof -t -i:4001 -sTCP:LISTEN); sleep 1; exit"
    kill_nifty_ws = f"echo '{password}' | sudo -S kill -9 $(lsof -t -i:4002 -sTCP:LISTEN); sleep 1; exit"
    kill_midcpnifty_ws = f"echo '{password}' | sudo -S kill -9 $(lsof -t -i:4005 -sTCP:LISTEN); sleep 1; exit"
    kill_finnifty_ws = f"echo '{password}' | sudo -S kill -9 $(lsof -t -i:4003 -sTCP:LISTEN); sleep 1; exit"
    kill_bajfinance_ws = f"echo '{password}' | sudo -S kill -9 $(lsof -t -i:4004 -sTCP:LISTEN); sleep 1; exit"
    kill_sensex_ws = f"echo '{password}' | sudo -S kill -9 $(lsof -t -i:4006 -sTCP:LISTEN); sleep 1; exit"
    kill_all_python_processess = "ps aux | grep -i python | awk '{print $2}' | xargs kill -9; sleep 1; exit"