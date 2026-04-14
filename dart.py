import datetime
import os
import requests
import subprocess
import sys
import time
import uuid
from datetime import date
from utils.clock import Clock
from utils.constants import Config
from threading import Thread
from collections import deque
import logging

# Setup logging
logging.basicConfig(level=logging.DEBUG)
startTime = datetime.time(9, 0, 0)
endTime = datetime.time(15, 30, 0)

# File names of websocket scripts
websocket_scripts = {
    "BANKNIFTY": "ws_fyers_BANKNIFTY_v3.py",
    "NIFTY": "ws_fyers_NIFTY_v3.py",
    "MIDCPNIFTY": "ws_fyers_MIDCPNIFTY_v3.py",
    "FINNIFTY": "ws_fyers_FINNIFTY_v3.py",
    "BAJFINANCE": "ws_fyers_BAJFINANCE_v3.py"
}

index_key = {
    "BANKNIFTY" : "NSE:NIFTYBANK-INDEX",
    "NIFTY" : "NSE:NIFTY50-INDEX",
    "MIDCPNIFTY" : "NSE:MIDCPNIFTY-INDEX",
    "FINNIFTY" : "NSE:FINNIFTY-INDEX",
    "BAJFINANCE" : "NSE:BAJFINANCE-EQ"
}

# List of instruments to skip
instruments_to_skip = ["MIDCPNIFTY", "BAJFINANCE"]

# Shared dictionary to store the latest LTP for each instrument
ltp_dict = {}

# Configure the logging settings
name = os.path.basename(__file__).split(".")[0]
name_suffix = str(date.today()) + "_" + str(uuid.uuid4())
log_file = os.path.join(Config.logger_path, f"{name}_{name_suffix}.log")

# Create a logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.WARN)

# Define the format for log messages
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Create a file handler
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.WARNING)  # Change this to the appropriate level you want for the file
file_handler.setFormatter(formatter)

# Create a console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)  # Change this to the appropriate level you want for the console
console_handler.setFormatter(formatter)

# Add handlers
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Suppress DEBUG logging for urllib3 and other libraries
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("chardet").setLevel(logging.WARNING)
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

# Define a class to monitor each websocket
class WebSocketMonitor(Thread):
    def __init__(self, logger, instrument, script, unhealthy_timer=15):
        super().__init__()
        self.logger = logger
        self.instrument = instrument
        self.instrument_name = None
        self.port_number = None
        self.script = script
        self.unhealthy_timer = unhealthy_timer
        self.ltp_values = deque(maxlen=unhealthy_timer)
        self.unhealthy_count = 0
        self.process = None
        self.first_time = True

    def run(self):
        self.logger.debug(f"Thread {self.name} - {self.instrument}: Started - Run Method")
        self.setlocalhost()
        # Start the websocket process
        self.start_websocket_process()
        time.sleep(15)
        self.logger.debug(f"Started WebSocket for {self.instrument} in thread {self.name}")
        # first_time = True

        # Monitor the health of the websocket
        while Clock.time_in_range(startTime, endTime, datetime.datetime.now().time()):
            ltp = self.getLTP()
            self.logger.debug(f"Thread {self.name} - {self.instrument}: Fetching LTP")
            self.ltp_values.append(ltp)
            if self.first_time:
                for _ in range(5):
                    self.logger.debug(f"Thread {self.name} - {self.instrument}: #{_} pooling...")
                    time.sleep(2)
                    self.ltp_values.append(self.getLTP())
                self.first_time = False
            # self.ltp_values.append(3) if self.instrument not in ["BAJFINANCE"] else "pass"
            ltp_dict[self.instrument] = self.ltp_values[-1]
            self.logger.debug(f"Thread {self.name} - {self.instrument}: LTP = {ltp}")

            if len(set(self.ltp_values)) == 1:
                self.unhealthy_count += 1
                self.logger.warning(f"{Clock.tictoc()} {self.instrument} WebSocket Unhealthy for {self.unhealthy_count} times today. Rebooting...")
                self.restart_websocket_process()

            time.sleep(2)

        # Kill the websocket process at the end of the day
        self.kill_websocket_process()

    def setlocalhost(self):
        if "BANK" in self.instrument:
            self.instrument_name = index_key.get('BANKNIFTY', None)
            self.port_number = Config.ws_map.get("BANKNIFTY", None)
        elif "BAJF" in self.instrument:
            self.instrument_name = index_key.get('BAJFINANCE', None)
            self.port_number = Config.ws_map.get("BAJFINANCE", None)
        elif "FINN" in self.instrument:
            self.instrument_name = index_key.get('FINNIFTY', None)
            self.port_number = Config.ws_map.get("FINNIFTY", None)
        elif "MID" in self.instrument:
            self.instrument_name = index_key.get('MIDCPNIFTY', None)
            self.port_number = Config.ws_map.get("MIDCPNIFTY", None)
        else:
            self.instrument_name = index_key.get('NIFTY', None)
            self.port_number = Config.ws_map.get("NIFTY", None)
        logger.debug(f"instrument_name: {self.instrument_name} || port_number : {self.port_number} ")
        assert self.instrument_name is not None, f"No Such Instrument Found Nunber Configured for '{self.instrument_name}'"
        assert self.port_number is not None, f"No Port Nunber Configured for '{self.instrument_name}'"

    def getLTP(self):
        url = f"http://localhost:{self.port_number}/ltp?instrument=" + self.instrument_name
        data = None
        try:
            resp = requests.get(url)
            data = resp.json()
        except Exception as e:
            print(datetime.datetime.now(), " Exception @getLTP:", e)
        return data

    def start_websocket_process(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(current_dir, self.script)
        self.logger.debug(f"script_path: {script_path}")
        self.process = subprocess.Popen(['python3.11', script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.first_time = True

    def restart_websocket_process(self):
        self.kill_websocket_process()
        time.sleep(5)
        self.ltp_values.clear()
        # self.start_websocket_process()
        time.sleep(5)
        # self.ltp_values.clear()
        self.start_websocket_process()
        time.sleep(15)

    def kill_websocket_process(self):
        if self.process:
            self.process.terminate()
            self.process.wait()
        os.system(getattr(Config, f"kill_{self.instrument.lower()}_ws"))


def loading_indicator(duration=10, interval=0.5):
    start_time = time.time()
    loading_text = "Loading"
    while (time.time() - start_time) < duration:
        for i in range(6):
            if (time.time() - start_time) >= duration:
                break
            sys.stdout.write("\r" + loading_text + "." * i + " " * (5 - i))
            sys.stdout.flush()
            time.sleep(interval)  # Adjust the delay as needed

def print_ltp():
    while Clock.time_in_range(startTime, endTime, datetime.datetime.now().time()):
        if not ltp_dict:
            try:
                loading_indicator(duration=10)  # Customize the duration as needed
            except KeyboardInterrupt:
                print("\nLoading stopped!")
        else:
            # Desired order of instruments
            desired_order = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "BAJFINANCE"]
            # Filter ltp_dict to only include items in the desired order and format the string
            # print(f"{Clock.tictoc()} " + " || ".join(f"{instrument}: @{ltp}" for instrument, ltp in ltp_dict.items()), end="\r", flush=True)
            data = f"{Clock.tictoc()} " + " || ".join(f"{instrument}: @{ltp_dict[instrument]}" for instrument in desired_order if instrument in ltp_dict)
            sys.stdout.write("\r" + " " * 100 + "\r")
            print(data, end="\r", flush=True)
        time.sleep(2)

def kill_all_websockets():
    for instrument in websocket_scripts.keys():
        kill_command = getattr(Config, f"kill_{instrument.lower()}_ws")
        logger.debug(f"kill_command: {kill_command}")
        subprocess.run(kill_command, shell=True)

if __name__ == "__main__":
    # Kill all running websockets
    kill_all_websockets()

    # Wait until 9:20 AM
    Clock.wait_until(Config.ws_start_hour, Config.ws_start_min, Config.ws_start_sec)

    # Start the WebSocketMonitor for each instrument
    monitors = []
    for instrument, script in websocket_scripts.items():
        if instrument in instruments_to_skip:
            logger.debug(f"Skipping WebSocket for {instrument}...")
            continue

        logger.info(f"Starting WebSocket for {instrument}...")
        monitor = WebSocketMonitor(logger, instrument, script)
        monitor.start()
        monitors.append(monitor)
    
    # Start a separate thread to print the LTP values
    print_thread = Thread(target=print_ltp)
    print_thread.start()

    logger.debug(f"Started print thread {print_thread.name}")

    # Wait for all monitors to finish
    for monitor in monitors:
        monitor.join()

    # Wait for the print thread to finish
    print_thread.join()
