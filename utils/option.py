import itertools
import time
from datetime import datetime, timedelta

import chime
import requests
from fyers_apiv3 import fyersModel
from pytz import timezone
from utils.clock import Clock
from utils.constants import Config


class Derivative:
    def __init__(self, instrument,moneyness=None,paper_trade=True,custom_qty=None,trail=None):
        self.fyers = fyersModel.FyersModel(token=Config.RAM_ACCESS_TOKEN,is_async=False,client_id=Config.RAM_CLIENT_ID,log_path=Config.fyers_log_path)
        self.instrument = instrument
        self.PnL = 0
        self.points = 0
        self.trail = Config.trailBy  if trail is None else trail
        self.quantity = Config.scalping_quantity if custom_qty is None else custom_qty
        self.paper_trade = paper_trade
        self.spinner = itertools.cycle(['-', '/', '|', '\\'])
        self.wins = 0
        self.peak_profit = 0
        self.live_pnl = 0
        self.entry_time = None
        self.exit_time = None
        self.entry_price = None
        self.exit_price = None
        self.exposure = None
        self.lost = 0
        if moneyness is None:
            self.moneyness = Config.moneyness * 100 if self.instrument.__contains__("BANK") else (Config.moneyness * 100)/2
        else:
            self.moneyness = moneyness * 100 if self.instrument.__contains__("BANK") else (moneyness * 100)/2
        # print("Initialize complete: \n", self.__dict__,"\n")

    def getPrevCandle(self,instrument=None):
        time.sleep(.1)
        today = datetime.now(timezone("Asia/Kolkata")).strftime('%Y-%m-%d')
        data = {
                "symbol": self.instrument if instrument is None else instrument,
                "resolution": 1,
                "date_format":1,
                "range_from":today,
                "range_to":today,
                "cont_flag":1
            }
        prevCandle = None
        try:
            response = self.fyers.history(data)
            # [1682493480, 42740.65, 42740.65, 42717.1, 42717.1, 0]
            #[open:1, high: 2, low: 3, close: 4] #-2 close
            lastCandle = response["candles"][-1]
            prevCandle = {"open":lastCandle[1],"high":lastCandle[2],"low":lastCandle[3],"close":lastCandle[4],"timestamp":lastCandle[0]}
        except Exception as e:
            print(Clock.tictoc(), "response:", response) #can keep this response under exception becasue response could be []. Otherwise, it could be a 400
            print(Clock.tictoc(), "Exception @getPrevCandle:", e)
        return prevCandle
    
    def isLastMinute(self,current_timestamp,previous_timestamp):
        return 50 <= current_timestamp - previous_timestamp <=62

    def prevCandle(self):
        max_retries = 10
        retries = 0
        data = self.getPrevCandle()
        data_timestamp = data["timestamp"]
        now = int(round((datetime.fromtimestamp(int(time.time()))).timestamp()))
        while retries <= max_retries and (data is None or not self.isLastMinute(now,data_timestamp)):
            print("Retrying PrevCandle... #%s"%(retries))
            time.sleep(.2)
            data = self.getPrevCandle()
            data_timestamp = data["timestamp"]
            print( "On retry: now: %s && data_timestamp: %s : timedelta: %sseconds"%(now, data_timestamp,now-data_timestamp))
            retries+=1
        if (not self.isLastMinute(now,data_timestamp)):
            print("Max Retries exceeded. Data Retrived isn't the previous %smin candle."%(1))
            return None
        elif (data is None):
            print("Max Retries exceeded. Data Retrived as None.")
            return None
        return data

    def getCMP(self,instrument=None):
        if not instrument:
            url = "http://localhost:4001/ltp?instrument=" + self.instrument if "BANK" in self.instrument else "http://localhost:4002/ltp?instrument=" + self.instrument
        else:
            url = "http://localhost:4001/ltp?instrument=" + instrument if "BANK" in instrument else "http://localhost:4002/ltp?instrument=" + instrument
        try:
            resp = requests.get(url)
        except Exception as e:
            # chime.warning()
            print(Clock.tictoc(), "Exception @getLTP:", e)
        data = resp.json()
        return data
    
    def getLTP(self,instrument=None):
        max_retries = 10
        retries = 0
        cmp = self.getCMP(instrument=instrument)
        while retries <= max_retries and cmp == -1:
            print(Clock.tictoc(), "Retrying CMP for ", self.instrument if instrument is None else instrument, " #",retries)
            time.sleep(.1)
            cmp = self.getCMP(instrument=instrument)    
            retries+=1
        if (cmp == -1):
            print(Clock.tictoc(),"Max Attempts exceeded but Failed to get LTP: ", self.instrument if instrument is None else instrument, " #",retries)
        return cmp

    def findStrikePriceATM(self, ltp):
        strikeList=[]
        prev_diff = 10000
        closest_Strike=10000
        intExpiry = Config.expiry["year"] + Config.expiry["month"] + Config.expiry["day"]   #22OCT

        #FINDING ATM
        if self.instrument.__contains__("BANK"):
            for i in range(-4, 4):
                strike = (int(ltp / 100) + i) * 100
                strikeList.append(strike)
            for strike in strikeList:
                diff = abs(ltp - strike)
                if (diff < prev_diff):
                    closest_Strike = strike
                    prev_diff = diff
        elif self.instrument.__contains__("NIFTY"):
            for i in range(-4, 4):
                strike = (int(ltp / 100) + i) * 100
                strikeList.append(strike)
                strikeList.append(strike+50)
            for strike in strikeList:
                diff=abs(ltp - strike)
                if (diff < prev_diff):
                    closest_Strike=strike
                    prev_diff=diff

        closest_Strike_CE = int(closest_Strike + (self.moneyness))
        closest_Strike_PE = int(closest_Strike - (self.moneyness))

        if self.instrument.__contains__("BANK"):
            atmCE = "NSE:BANKNIFTY" + str(intExpiry)+str(closest_Strike_CE)+"CE"
            atmPE = "NSE:BANKNIFTY" + str(intExpiry)+str(closest_Strike_PE)+"PE"
        elif self.instrument.__contains__("NIFTY"):
            atmCE = "NSE:NIFTY" + str(intExpiry)+str(closest_Strike_CE)+"CE"
            atmPE = "NSE:NIFTY" + str(intExpiry)+str(closest_Strike_PE)+"PE"
        return atmCE,atmPE

    def placeOrderFyers(self, inst, t_type, qty, order_type, price, variety):
        exch = inst[:3]
        symb = inst[4:]
        dt = datetime.now()
        
        print( dt.hour, ":", dt.minute, ":", dt.second, " => ", t_type, " ", symb, " ", qty, " QTY @", order_type, " PRICE =  ", price)
        if(order_type=="MARKET"):
            type1 = 2
        elif(order_type=="LIMIT"):
            type1 = 1

        if(t_type=="BUY"):
            side1=1
        elif(t_type=="SELL"):
            side1=-1

        data =  {
            "symbol": self.instrument,
            "qty":qty,
            "type":type1,
            "side":side1,
            "productType":"INTRADAY",
            "limitPrice":0,
            "stopPrice":0,
            "validity":"DAY",
            "disclosedQty":0,
            "offlineOrder":"False",
            "stopLoss":0,
            "takeProfit":0
        }
        try:
            if not self.paper_trade:
                orderid = self.fyers.place_order(data)
                print(dt.hour,":",dt.minute,":",dt.second ," => ", symb , orderid)
                return orderid
            else:
                return 0
        except Exception as e:
            chime.warning()
            print(dt.hour,":",dt.minute,":",dt.second ," => ", symb , "Failed : {} ".format(e))
    
    def takeEntry(self, option, shouldTrail=True):
        self.entry_price = self.getLTP(option)
        self.base_price = self.entry_price
        # wait for price to start ticking and then take and entry. therefore, not to calculate price differnece as -1 to the CMP.
        # count = 0
        # while count <= 15 and int(self.entry_price) == -1:
        #     time.sleep(0.1)
        #     self.entry_price = self.getLTP(option)
        #     count+=1
        #     print(Clock.tictoc(),"Pirce ")
        
        # if int(self.entry_price) > 0:
        #     print(Clock.tictoc(), "Price started ticking for option: ", option," entry price: " ,self.entry_price)
        # elif int(self.entry_price) == -1:
        #     print(Clock.tictoc(), "Price haven't started ticking even after #", count," attempts. Aborting taking a trade.")
        #     exit()

        if int(self.entry_price) == -1:
            print(Clock.tictoc(), "Price haven't started ticking. Aborting taking a trade.")
            exit()
        
        stop_loss = int(self.entry_price) - Config.scalping_target_stoploss_trailing
        target = int(self.entry_price) + Config.scalping_target_stoploss_trailing
        no_trail_stop_loss = self.entry_price - Config.scalping_target_stoploss_no_trailing
        no_trail_target = self.entry_price + Config.scalping_target_stoploss_no_trailing
        if shouldTrail:
            print(Clock.tictoc(),"Placing Market Order for '"+ option + "' @", str(self.entry_price),"/-", " Target: ", target, " Stoploss: ", stop_loss, " Trailing: ", True, "Moneyness: ", self.moneyness)        
        else:
            print(Clock.tictoc(),"Placing Market Order for '"+ option + "' @", str(self.entry_price),"/-", " Target: ", no_trail_target, " Stoploss: ", no_trail_stop_loss, " Trailing: ", False, "Moneyness: ", self.moneyness)        

        orderID = self.placeOrderFyers( option, "BUY", self.quantity, "MARKET", self.entry_price, "regular")
        self.entry_time = time.time()
        print(Clock.tictoc(),"The OID of Entry for option '"+ option +"' is: ", str(orderID))
        
        traded = False
        ltpHigh = self.entry_price
        customSpinner = itertools.cycle(["-", "/", "|", "\\"])

        while not traded:
            cmp = self.getLTP(option)
            self.live_pnl = (cmp-self.entry_price) * self.quantity
            if shouldTrail:
                try:
                    if cmp > ltpHigh:
                        ltpHigh = cmp
                        self.peak_profit = self.live_pnl if self.live_pnl > self.peak_profit else self.peak_profit
                        # print(Clock.tictoc(), option, " : Higher High: ", ltpHigh, "/-", " : Live PnL: ", self.live_pnl, "/- : Peak Profit: ", self.peak_profit,"/-")
                    # else:
                        # print(Clock.tictoc(), option, " : CMP: ", cmp,"...",next(customSpinner), " : Live PnL: ", self.live_pnl, "/- : Peak Profit: ", self.peak_profit,"/-", end="\r", flush=True)

                    if round(cmp) < stop_loss:
                        self.exit_price = cmp
                        self.points = cmp - self.entry_price
                        sellOrderID = self.placeOrderFyers( option, "SELL", self.quantity, "MARKET", cmp, "regular")
                        self.exit_time = time.time()
                        self.exposure = self.exit_time - self.entry_time
                        traded = True
                        print(Clock.tictoc(),"Stop-loss hit CMP < stop_loss(",cmp, stop_loss,"). Points Done: ",self.points)
                        print(Clock.tictoc(),"The OID of Exit for option '"+ option +"' is: ", sellOrderID)
                        pnl = int(self.points)*self.quantity
                        self.PnL = self.PnL + pnl
                        if pnl > 0:
                            self.wins+=1 
                        else:
                            self.lost+=1
                        print(Clock.tictoc(),"Current PnL: ", pnl, "/-")
                        print(Clock.tictoc(),"Total winning trades: ", self.wins, " : Total losing trades: ", self.lost)
                        print(Clock.tictoc(),"Total PnL: ", self.PnL, "/-")
                    # elif round(cmp) >= round(target) and ltpHigh - stop_loss >= self.trail:
                    #         prev_stoploss = stop_loss
                    #         stop_loss = ltpHigh - self.trail
                    #         if int(prev_stoploss) != int(stop_loss):
                    #             print(Clock.tictoc(),"Increased stoploss as ltpHigh - stop_loss >= trail. ","CMP: ", cmp, "/-; Revised Stop-loss: ",stop_loss,"/-;prev stop-loss: ", prev_stoploss, "/-")
                    elif round(cmp) >= self.base_price + self.trail:
                        prev_stoploss = stop_loss
                        stop_loss = self.base_price
                        self.base_price = round(cmp)
                        print(Clock.tictoc(),"Stoploss moved: prev_stoploss: ", prev_stoploss, " New Stoploss: ", stop_loss, " CMP: ",cmp )
                    time.sleep(0.1)
                except:
                    print(Clock.tictoc(),"Couldn't find LTP , RETRYING !!")
                    chime.warning()
                    time.sleep(1)
            else:
                try:
                    if cmp > ltpHigh:
                        ltpHigh = cmp
                        print(Clock.tictoc(), option, " : Higher High: ", ltpHigh, " : TARGET:", target)
                    else:
                        print(Clock.tictoc(), option," : TARGET: ", target, " : CMP: ", cmp," ", next(customSpinner), end="\r", flush=True)

                    if round(cmp) <= round(no_trail_stop_loss) or round(cmp) >= round(no_trail_target):
                        self.exit_price = cmp
                        points = cmp - self.entry_price
                        sellOrderID = self.placeOrderFyers( option, "SELL", self.quantity, "MARKET", cmp, "regular")
                        self.exit_time = time.time()
                        self.exposure = self.exit_time - self.entry_time
                        traded = True
                        print(Clock.tictoc(),"The OID of Exit for option '"+ option +"' is: ", sellOrderID)
                        print(Clock.tictoc(),"Points Done: ",points)
                        pnl = int(points)*self.quantity
                        if pnl > 0:
                            self.wins+=1 
                        else:
                            self.lost+=1
                        self.PnL = self.PnL + pnl
                        print(Clock.tictoc(),"Current PnL: ", pnl, "/-")
                        print(Clock.tictoc(),"Total winning trades: ", self.wins, " : Total losing trades: ", self.lost)
                        print(Clock.tictoc(),"Total PnL: ", self.PnL, "/-")
                    time.sleep(0.1)
                except:
                    print(Clock.tictoc()," Couldn't find LTP , RETRYING !!")
                    chime.warning()
                    time.sleep(0.5)
