import logging
import threading
import traceback
import uuid
from datetime import date

import chime
from flask import Flask, request
from fyers_apiv3.FyersWebsocket import data_ws
from fyers_apiv3 import fyersModel
from utils.clock import Clock
from utils.constants import Config

app_id = Config.RAM_CLIENT_ID
access_token = Config.RAM_ACCESS_TOKEN
fyers = fyersModel.FyersModel(token=access_token,is_async=False,client_id=app_id,log_path=Config.fyers_log_path) ## Create a fyersModel object if in any scenario you want to call the trading and data apis when certain condiiton in websocket data is met so that can be triggered by calling the method/object after subscribing and before the keep_running method as shown in run_process_background_symbol_data
instrument_name = "FINNIFTY"
strikeList=['NSE:FINNIFTY-INDEX']
exchangeSymbol = "NSE:"

port_number = Config.ws_map.get(instrument_name, None)
assert port_number is not None, f"No Port Nunber Configured for '{instrument_name}'"

expiry = Config.expiry_map.get(instrument_name, None)
assert expiry is not None, f"No Expiry Configured for '{instrument_name}'"

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
console_handler.setFormatter(formatter)

#add handlers
logger.addHandler(console_handler)
logger.addHandler(file_handler)

def getLTP(name):
    ltp = None
    try:
        data = {"symbols":name}
        response = fyers.quotes(data)
        ltp = (response)['d'][0]['v']['lp']
        logger.debug(f"{instrument_name}: {ltp}")
    except Exception as e:
        logger.error(f"{name}: Failed : {e}")
        logger.error(f"Error Trace: {traceback.print_exc()}")
    return ltp

ltp = getLTP(strikeList[0])
if ltp is None:
    raise Exception(f"Unable to Retrive LTP {strikeList[0]}")

for i in range(-6, 6):
    strike = (int(ltp / 100) + i) * 100
    # strikeCE = exchangeSymbol+instrument_name+ expiry["year"]+expiry["month"]+expiry["day"]+str(strike)+"CE"
    strikeCE = exchangeSymbol+instrument_name+ expiry["year"]+expiry["month"]+str(strike)+"CE"
    strikeList.append(strikeCE)
    # strikeCE = exchangeSymbol+instrument_name+ expiry["year"]+expiry["month"]+expiry["day"]+str(strike+50)+"CE"
    strikeCE = exchangeSymbol+instrument_name+ expiry["year"]+expiry["month"]+str(strike+50)+"CE"
    strikeList.append(strikeCE)
    # strikePE = exchangeSymbol+instrument_name+expiry["year"]+expiry["month"]+expiry["day"]+str(strike)+"PE"
    strikePE = exchangeSymbol+instrument_name+expiry["year"]+expiry["month"]+str(strike)+"PE"
    strikeList.append(strikePE)
    # strikePE = exchangeSymbol+instrument_name+expiry["year"]+expiry["month"]+expiry["day"]+str(strike+50)+"PE"
    strikePE = exchangeSymbol+instrument_name+expiry["year"]+expiry["month"]+str(strike+50)+"PE"
    strikeList.append(strikePE)
logger.info(strikeList)
instrumentList = strikeList

##############################################
# print("!! Started getltpDict.py !!")

app = Flask(__name__)
tokenMapping = { }
ltpDict = { }

# setting log level for flask
log = logging.getLogger('werkzeug')
log.setLevel(logging.WARNING)

@app.route('/')
def hello_world():
    return 'Hello World'

@app.route('/ltp')
def getLtp():
    global ltpDict
    # print(ltpDict)
    ltp = -1
    instrumet = request.args.get('instrument')
    try:
        ltp = ltpDict[instrumet]
        logger.debug(f"[WS_{instrument_name}]: {instrumet}: LTP: {ltp}")
    except Exception as e :
        logger.error(f"getLtp: EXCEPTION occured while getting ltpDict(): {e}")
        logger.error(f"getLtp: Error Trace: {traceback.print_exc()}")
        # chime.warning()
    return str(ltp)

@app.route('/spot_price')
def get_spot():
    global ltpDict
    # print(ltpDict)
    ltp = -1
    # instrumet = request.args.get('instrument')
    instrumet = strikeList[0]
    try:
        ltp = ltpDict[instrumet]
        logger.debug(f"[WS_{instrument_name}]: {instrumet}: LTP: {ltp}")
    except Exception as e :
        logger.error(f"get_spot: EXCEPTION occured while getting ltpDict(): {e}")
        logger.error(f"get_spot: Error Trace: {traceback.print_exc()}")
        # chime.warning()
    return str(ltp)

def onmessage(message):
    global ltpDict
    ltpDict[message['symbol']] = message['ltp']
    # print(ltpDict)

def onerror(message):
    logger.debug(f"Error: {message}")

def onclose(message):
    logger.debug(f"Connection closed: {message}")

def onopen():
    logger.debug(f"Connection opened")

def startServer():
    logger.debug(f"Inside startServer()")
    app.run(host='0.0.0.0', port=port_number)

def main():
    logger.debug(f"Inside main()")
    t1 = threading.Thread(target=startServer)
    t1.start()

    access_token_websocket = app_id + ":" + access_token

    fyers = data_ws.FyersDataSocket(access_token=access_token_websocket,
                                    write_to_file=False,
                                    reconnect=True,
                                    on_connect=onopen,
                                    on_close=onclose,
                                    on_error=onerror,
                                    on_message=onmessage,
                                    log_path=Config.fyers_log_path)

    fyers.connect()

    # Specify the data type and symbols you want to subscribe to
    data_type = "SymbolUpdate"

    # Subscribe to the specified symbols and data type
    fyers.subscribe(symbols=instrumentList, data_type=data_type)

    # Keep the socket running to receive real-time data
    fyers.keep_running()

    t1.join()
    logger.debug(f"websocket started !!")


Clock.wait_until(Config.ws_start_hour,Config.ws_start_min,Config.ws_start_sec)
main()