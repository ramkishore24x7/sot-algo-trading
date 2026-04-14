import requests
import time
import traceback
from pytz import timezone
from datetime import date, datetime, timedelta
from fyers_apiv3 import fyersModel
from utils.clock import Clock
from utils.constants import Config

fyers = fyersModel.FyersModel(token=Config.RAM_ACCESS_TOKEN, is_async=False, client_id=Config.RAM_CLIENT_ID, log_path=Config.fyers_log_path)

class Option:
    
    def __init__(self, instrument_name, strike, option_type, logger=None):
        self.logger = logger
        
        # Map instrument names to their standardized forms
        instrument_map = {
            "NIF": "NIFTY",
            "MID": "MIDCPNIFTY",
            "FIN": "FINNIFTY",
            "BAN": "BANKNIFTY",
            "BAJF": "BAJFINANCE"
        }
        
        # Standardize instrument_name
        key = instrument_name[:3].upper()
        self.instrument_name = instrument_map.get(key,None)
        
        if self.instrument_name is None:
            exception_content = f"{Clock.tictoc()} Nifty || MidCPNifty || BankNifty || FinNifty || BajFinance ONLY. Received '{instrument_name}'"
            # self.logger.error(exception_content)
            raise Exception(exception_content)
        
        self.strike = strike
        if option_type.upper().endswith(("PE", "CE")):
            self.option_type = option_type.upper()
        else:
            raise Exception(f"{Clock.tictoc()} Options can only be 'CALL' (CE) or 'PUT' (PE). Received '{option_type}'")            
        
        # Get expiry date from configuration
        self.expiry = Config.expiry_map.get(self.instrument_name)
        if self.expiry is None:
            error_message = f"No Expiry Configured for '{self.instrument_name}'"
            # self.logger.error(error_message)
            raise AssertionError(error_message)
        
        # Construct option identifier
        self.option = f"{self.instrument_name}{self.expiry['year']}{self.expiry['month']}{self.expiry['day']}{self.strike}{self.option_type}"
        self.option_port = Config.ws_map.get(self.instrument_name, None)
        if self.expiry is None:
            error_message = f"No Port Configured for '{self.instrument_name}'"
            # self.logger.error(error_message)
            raise AssertionError(error_message)

    def flatten_dict_with_prefix(self, data, prefix=''):
        flattened = {}
        for k, v in data.items():
            new_key = f"{prefix}_{k}" if prefix else k
            if isinstance(v, dict):
                flattened.update(self.flatten_dict_with_prefix(v, new_key))
            else:
                flattened[new_key] = v
        return flattened

    def format_flattened_dict_as_string(self, nested_dict, custom_prefix):
        flattened_dict = self.flatten_dict_with_prefix(nested_dict)
        result = [f"{custom_prefix}"]
        for key, value in flattened_dict.items():
            result.append(f"- {key}: {value}")
        return '\n'.join(result)

    def fetchLTP(self, name, entered_trade=False):
        ltp = -1
        response = "placeholder_response"
        try:
            data = {"symbols":name}
            response = fyers.quotes(data)
            api_response_formatted = self.format_flattened_dict_as_string(response,"API Response:")
            # logger.error(f"🆘 CHUCK EVERYTHING AND LOOK INTO THIS!\n\n{api_response_formatted}")
            # asyncio.run(send_message(f"🆘 CHUCK EVERYTHING AND LOOK INTO THIS!\n\n{api_response_formatted}"))
            self.logger.debug(f"fetchLTP Response: {name}: {response}")
            ltp = (response)['d'][0]['v']['lp']
            self.logger.warning(f"fetchLTP: {name}: {ltp}")
        except Exception as e:
            if entered_trade:
                api_response_formatted = self.format_flattened_dict_as_string(response,"API Response:")
                asyncio.run(send_message(f"🆘 CHUCK EVERYTHING AND LOOK INTO THIS!\n\n{api_response_formatted}"))
            self.logger.error(f"{name}: Failed : {e}")
            self.logger.error(f"Error Trace: {traceback.print_exc()}")
        return ltp,response

    def getPrevCandle(self, option,timeframe):
        today = datetime.now(timezone("Asia/Kolkata")).strftime('%Y-%m-%d')
        data = {
                "symbol": option,
                "resolution": timeframe,
                "date_format":1,
                "range_from":today,
                "range_to":today,
                "cont_flag":1
            }
        # {"symbol":"NSE:SBIN-EQ","resolution":"D","date_format":"0","range_from":"1622097600","range_to":"1622097685","cont_flag":"1"}
        result = None
        try:
            response = fyers.history(data)
            self.logger.info(f"getPrevCandle response: {response}")
            lastCandle = response["candles"][-1]
            lastTwoCandles = response["candles"][-2:]
            self.logger.info(f"lastTwoCandles: {lastTwoCandles}")
            for candle in reversed(lastTwoCandles):
                if self.isLastMinute(int(round(time.time())), candle[0]):
                    lastCandle = candle
                    break
            result = {"open":lastCandle[1],"high":lastCandle[2],"low":lastCandle[3],"close":lastCandle[4],"timestamp":lastCandle[0]}
        except Exception as e:
            self.logger.error(f"Exception @getPrevCandle: {e}")
            self.logger.error(f"Error Trace: {traceback.print_exc()}")
        return result

    def isLastMinute(self, current_timestamp, previous_timestamp,minute=1):
        self.logger.info(f"current_timestamp: {current_timestamp}")
        self.logger.info(f"previous_timestamp: {previous_timestamp}")
        return (current_timestamp // 60) - (previous_timestamp // 60) == minute

    def prevCandle(self, option, timeframe):
        max_retries = 15
        retries = 0

        while retries <= max_retries:
            data = self.getPrevCandle(option, timeframe)
            if data is not None and self.isLastMinute(int(round(time.time())), data["timestamp"]):
                return data

            self.logger.info(f"Retrying PrevCandle... #{retries}")
            time.sleep(0.3)
            retries += 1

        self.logger.error(f"Max Retries exceeded. Data Retrieved isn't the previous {timeframe}min candle.")
        return None

    def getLTP(self, option,spot=False, entered_trade=False):
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
                # chime.warning()
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
                self.logger.error(f"Exception @getLTP:{e}")
                self.logger.error(f"Error Trace: {traceback.print_exc()}")
                self.logger.error(f"Retrying now... attempt #{counter}")
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
            self.logger.error(message_conent)
            asyncio.run(send_message(message_conent,emergency=True))
            exit()

    def ensureGetLTP(self, option,spot=None):
        max_attempts = 10 if entered_trade else 120
        counter = 0
        cmp = getLTP(option) if spot is None else getLTP(option,spot)
        while counter <= max_attempts and cmp == -1:
            self.logger.debug(f"{option}: LTP: {cmp}/- will retry...")
            counter += 1
            time.sleep(1)
            cmp = getLTP(option)
        
        if cmp == -1 and entered_trade:
            send_order_placement_erros("SQUARE-OFF POSITION",trade_manager.square_off_position(position,cmp))
            message_conent = f"😢 🆘 🆘 🆘\n\n Fyers {instrument_name} Websockets are down and API , LTP retrieved is -1 for continuous #{max_attempts} attempts! Squared Off {option} via API, Ensure We've Exited the positions on all LIVE Demats!"
            self.logger.error(message_conent)
            asyncio.run(send_message(message_conent,emergency=True))
            exit()
        elif cmp == -1 and not isInValidTrade:
            message_conent = f"Phew!\n\n Fyers {instrument_name} Websockets are down, LTP retrieved is -1 for continuous #{max_attempts} attempts! You might want to retry this build, if you see Fyers is back and stable!"
            self.logger.error(message_conent)
            asyncio.run(send_message(message_conent,emergency=True))
            exit()
        return cmp

    #This function helps to create OHLC candles. You have to pass the stock name + timeframe
    def ohlc(self, option,timeframe):
        date=pd.to_datetime(datetime.now(timezone("Asia/Kolkata")).strftime('%Y-%m-%d %H:%M:%S'))
        while(int(str(date)[-2::])>=2):
            cmp = self.ensureGetLTP(option)
            self.logger.debug(f"Polling if CMP is crossing safe entry price before closing above expected price: {option} {cmp}")
            print("Polling if CMP is crossing safe entry price before closing above expected price: ",option,next(spinner), end="\r", flush=True)
            if entry_price + safe_entry_price < round(int(cmp)) < target1:
                return {"open":"", "high":"", "low":"", "close":cmp, "timestamp": int(round((datetime.fromtimestamp(int(time.time()))).timestamp()))}
            time.sleep(.3)
            date=pd.to_datetime(datetime.now(timezone("Asia/Kolkata")).strftime('%Y-%m-%d %H:%M:%S'))
        return prevCandle(option,timeframe)


option = Option("Nifty",24000,"PE")
print(option.option)