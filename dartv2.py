import datetime
import os
import requests
import subprocess
import time
import uuid
from datetime import date
from utils.clock import Clock
from utils.constants import Config
from threading import Thread
from collections import deque
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
startTime = datetime.time(9, 0, 0)
endTime = datetime.time(17, 30, 0)

# file names of websocket scripts
websocket_scripts = {
    "BANKNIFTY": "ws_fyers_BANKNIFTY_v3.py",
    "NIFTY": "ws_fyers_NIFTY_v3.py",
    "MIDCPNIFTY": "ws_fyers_MIDCPNIFTY_v3.py",
    "FINNIFTY": "ws_fyers_FINNIFTY_v3.py",
    "BAJFINANCE": "ws_fyers_BAJFINANCE_v3.py"
}

# List of instruments to skip
instruments_to_skip = []

# Shared dictionary to store the latest LTP for each instrument
ltp_dict = {}

# Configure the logging settings
name = __file__.split("/")[-1].split(".")[0]
name_suffix = str(date.today()) + "_" + str(uuid.uuid4())
log_file = Config.logger_path + "/" + name + "_" + name_suffix + ".log"


#Create a logger
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# Define the format for log messages
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Create a file handler
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(eval(f"logging.{Config.flie_log_level.value}"))
file_handler.setFormatter(formatter)

# Create a console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(eval(f"logging.{Config.console_log_level.value}"))
# console_handler.setLevel(logging.ERROR)
console_handler.setFormatter(formatter)

#add handlers
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Define a class to monitor each websocket
class WebSocketMonitor(Thread):
    def __init__(self, logger,instrument, script, unhealthy_timer=15):
        super().__init__()
        self.logger = logger
        self.instrument = instrument
        self.script = script
        self.unhealthy_timer = unhealthy_timer
        self.ltp_values = deque(maxlen=unhealthy_timer)
        self.unhealthy_count = 0
        self.process = None

    def run(self):
        self.logger.debug(f"Thread {self.name} - {self.instrument}: Started - Run Method") # Confirm thread execution

        # Start the websocket process
        self.start_websocket_process()
        time.sleep(10)
        # Print the thread data
        self.logger.debug(f"Started WebSocket for {self.instrument} in thread {self.name}")# Print the thread data
        first_time = True
        # Monitor the health of the websocket
        while Clock.time_in_range(startTime, endTime, datetime.datetime.now().time()):
            ltp = self.getLTP()
            self.logger.debug(f"Thread {self.name} - {self.instrument}: Fetching LTP")    # Confirm before fetching LTP 
            self.ltp_values.append(ltp)
            if first_time:
                self.logger.debug(f"Thread {self.name} - {self.instrument}: First Time, will pool once again...")
                time.sleep(2)
                self.ltp_values.append(self.getLTP())
                first_time = False
            self.ltp_values.append(3) if self.instrument not in ["BAJFINANCE"] else "pass"
            ltp_dict[self.instrument] = self.ltp_values[-1]  # Store the latest LTP in the shared dictionary
            self.logger.debug(f"Thread {self.name} - {self.instrument}: LTP = {ltp}")

            if len(set(self.ltp_values)) == 1:
                self.unhealthy_count += 1
                self.logging.warning(f"{Clock.tictoc()} {self.instrument} WebSocket Unhealthy for {self.unhealthy_count} times today. Rebooting...")
                self.restart_websocket_process()

            time.sleep(2)

        # Kill the websocket process at the end of the day
        self.kill_websocket_process()

    def getLTP(self):
        self.logger.debug(f"{self.instrument} In GetLTP")  # Print the entire API response
        try:
            response = requests.get(f'http://localhost:4001/ltp?instrument={self.instrument}')
            response.raise_for_status()  # This will raise an exception if the response contains an HTTP error status code
            data = response.json()
            self.logger.debug(f"{self.instrument} API response: {data}")  # Print the entire API response
            return data
        except Exception as e:
            self.logger.error(f"Error getting LTP for {self.instrument}: {e}")  # Print any error that occurs
            return None

    def start_websocket_process(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(current_dir, self.script)
        self.logger.debug(f"script_path: {script_path}")
        subprocess.Popen(['python3.11', script_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def restart_websocket_process(self):
        self.kill_websocket_process()
        time.sleep(5)
        self.start_websocket_process()
        time.sleep(5)
        self.ltp_values.clear()

    def kill_websocket_process(self):
        os.system(getattr(Config, f"kill_{self.instrument.lower()}_ws"))

def print_ltp():
    while Clock.time_in_range(startTime, endTime, datetime.datetime.now().time()):
        # print(f"{Clock.tictoc()} " + " || ".join(f"{instrument}: @{ltp}" for instrument, ltp in ltp_dict.items()), end="\r", flush=True)
        if not ltp_dict:
            print("Loading...")
        else:
            print(f"{Clock.tictoc()} " + " || ".join(f"{instrument}: @{ltp}" for instrument, ltp in ltp_dict.items()), end="\r", flush=True)
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
            logging.info(f"Skipping WebSocket for {instrument}...")
            continue

        logging.info(f"Starting WebSocket for {instrument}...")
        monitor = WebSocketMonitor(logger,instrument, script)
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