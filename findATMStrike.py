import requests
import time
from utils.clock import Clock
from utils.constants import Config



finnifty = "NSE:FINNIFTY-INDEX"
bajfinance = "NSE:BAJFINANCE-EQ"

n = "NIFTY"
bn = "NIFTYBANK"
f = "FINNIFTY"
bf = "BAJFINANCE"
# stock = "NSE:NIFTYBANK-INDEX"
otm = -1

def getCMP(option):
    port_number = Config.ws_map.get(option, None)
    url = f"http://localhost:{port_number}/ltp?instrument={option}"
    success = False
    counter = 1
    while not success and counter < 120:
        try:
            resp = requests.get(url)
            success = True
        except Exception as e:
            # chime.warning()
            # logger.error(f"Exception @getLTP:{e}")
            # logger.error(f"Error Trace: {traceback.print_exc()}")
            # logger.error(f"Retrying now... attempt #{counter}")
            counter+=1
            time.sleep(1)
    try:
        data = resp.json()
        return data
    except Exception as e:
        return f"🫢\nLooks SOT_BOT couldn't connect to websocket for LTP, gave up after #{counter} attempts!"


def findStrikePriceATM(stock):
    global otm
    if stock.__contains__("BANKNIFTY"):
        name = "NSE:NIFTYBANK-INDEX"
        otm = otm*100
    elif stock.__contains__("NIFTY50"):
        name = "NSE:NIFTY50-INDEX"
        otm = int((otm*100)/2)
    #TO get feed to Nifty: "NSE:NIFTY 50" and banknifty: "NSE: NIFTY BANK"

    strikeList=[]

    prev_diff = 10000
    closest_Strike=10000

    expiry = Config.expiry_map.get(stock, None)
    assert expiry is not None, f"No Expiry Configured for '{stock}'"
    intExpiry = expiry["year"] + expiry["month"] + expiry["day"]

    ######################################################
    #FINDING ATM
    ltp = getCMP(stock)
    counter = 0
    while ltp is None and counter < 10:
        ltp = getCMP(stock)
        counter+=1
        time.sleep(1)
    
    if ltp is None:
        print(f"LTP couldn't be retrived despite {counter} attempts, aborting!")
        exit()
    print(f"{stock}: CMP: {ltp}")

    if stock.__contains__("BANK"):
        for i in range(-2, 2):
            strike = (int(ltp / 100) + i) * 100
            strikeList.append(strike)
        # print(strikeList)
        for strike in strikeList:
            diff = abs(ltp - strike)
            # print("diff==>", diff)
            if (diff < prev_diff):
                closest_Strike = strike
                prev_diff = diff


    elif stock.__contains__("NIFTY"):
        for i in range(-2, 2):
            strike = (int(ltp / 100) + i) * 100
            strikeList.append(strike)
            strikeList.append(strike+50)
        # print(strikeList)
        for strike in strikeList:
            diff=abs(ltp - strike)
            # print("diff==>",diff)
            if (diff < prev_diff):
                closest_Strike=strike
                prev_diff=diff

    print(f"Closest Strike: {closest_Strike}")
    closest_Strike_CE = closest_Strike+otm
    closest_Strike_PE = closest_Strike-otm

    if stock.__contains__("BANK"):
        atmCE = "NSE:BANKNIFTY" + str(intExpiry)+str(closest_Strike_CE)+"CE"
        atmPE = "NSE:BANKNIFTY" + str(intExpiry)+str(closest_Strike_PE)+"PE"
    elif stock.__contains__("NIFTY"):
        atmCE = "NSE:NIFTY" + str(intExpiry)+str(closest_Strike_CE)+"CE"
        atmPE = "NSE:NIFTY" + str(intExpiry)+str(closest_Strike_PE)+"PE"

    print(f"{atmCE}: {getCMP(atmCE)}")
    print(f"{atmPE}: {getCMP(atmPE)}")

findStrikePriceATM(n)