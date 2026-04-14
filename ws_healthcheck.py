import datetime
import os
import time

import chime
import requests
from utils.clock import Clock
from utils.constants import Config

bank_nifty = "NSE:NIFTYBANK-INDEX"
nifty = "NSE:NIFTY50-INDEX"
midcpnifty = "NSE:MIDCPNIFTY-INDEX"
finnifty = "NSE:FINNIFTY-INDEX"
bajfinance = "NSE:BAJFINANCE-EQ"
sensex = "BSE:SENSEX-INDEX"
unhealthy_timer = 15 # In seconds

unhealthy_banknifty = 0
unhealthy_nifty = 0
unhealthy_midcpnifty = 0
unhealthy_finnifty = 0
unhealthy_bajfinance = 0
unhealthy_sensex = 0
startTime = datetime.time(9, 0, 0)
endTime = datetime.time(15, 30, 0)
skip_monitor = ["MIDCPNIFTY", "bajfinance"]
def getLTP(instrument):
	instrument_name = None
	port_number = None
	if "BANK" in instrument:
		instrument_name = bank_nifty
		port_number = Config.ws_map.get("BANKNIFTY", None)
	elif "BAJF" in instrument:
		instrument_name = bajfinance
		port_number = Config.ws_map.get("BAJFINANCE", None)
	elif "FINN" in instrument:
		instrument_name = finnifty
		port_number = Config.ws_map.get("FINNIFTY", None)
	elif "MID" in instrument:
		instrument_name = midcpnifty
		port_number = Config.ws_map.get("MIDCPNIFTY", None)
	elif "SENSEX" in instrument:
		instrument_name = sensex
		port_number = Config.ws_map.get("SENSEX", None)
	else:
		instrument_name = nifty
		port_number = Config.ws_map.get("NIFTY", None)

	assert port_number is not None, f"No Port Nunber Configured for '{instrument_name}'"
	# url = "http://localhost:4001/ltp?instrument=" + instrument if "NIFTYBANK" in instrument else "http://localhost:4002/ltp?instrument=" + instrument
	url = f"http://localhost:{port_number}/ltp?instrument=" + instrument_name
	data = None
	try:
		resp = requests.get(url)
		data = resp.json()
		# print("data: ", data)
	except Exception as e:
		print(datetime.datetime.now(), " Exception @getLTP:", e)
	return data

# def time_in_range(start, end, current):
#     """Returns whether current is in the range [start, end]"""
#     return start <= current <= end


def monitorHealth():
	# time.sleep(15)
	global unhealthy_banknifty
	global unhealthy_nifty
	global unhealthy_midcpnifty
	global unhealthy_finnifty
	global unhealthy_bajfinance
	global unhealthy_sensex
	NIFTY_LTP = []
	MIDCPNIFTY_LTP = []
	FINNIFTY_LTP = []
	BANKNIFTY_LTP = []
	BAJFINANCE_LTP = []
	SENSEX_LTP = []
	
	while Clock.time_in_range(startTime, endTime, datetime.datetime.now().time()):
		bn_ltp = getLTP(bank_nifty)
		n_ltp = getLTP(nifty)
		fin_ltp = getLTP(finnifty)
		sen_ltp = getLTP(sensex)
		# baj_ltp = getLTP(bajfinance)
		# mid_ltp = getLTP(midcpnifty)

		if len(BANKNIFTY_LTP) >= unhealthy_timer:
			BANKNIFTY_LTP.pop(0)
			BANKNIFTY_LTP.append(bn_ltp)
			while len(set(BANKNIFTY_LTP)) == 1:
					unhealthy_banknifty +=1
					print(Clock.tictoc(), "BANKNIFTY WebSocket Unhealthy for ", unhealthy_banknifty, " times today. Rebooting...")
					# chime.warning()
					os.system(Config.kill_banknifty_ws)
					time.sleep(5)
					os.system("osascript -e 'tell application \"iTerm\" to activate' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"D\" using command down' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"banknifty_ws\"' -e 'tell application \"System Events\" to tell process \"iTerm\" to key code 52'")
					time.sleep(5)
					BANKNIFTY_LTP = []
		else:
			BANKNIFTY_LTP.append(bn_ltp)

		if len(NIFTY_LTP) >= unhealthy_timer:
			NIFTY_LTP.pop(0)
			NIFTY_LTP.append(n_ltp)
			while len(set(NIFTY_LTP)) == 1:
					unhealthy_nifty +=1
					print(Clock.tictoc(), "NIFTY WebSocket Unhealthy for ", unhealthy_nifty, " times today. Rebooting...")
					# chime.warning()
					os.system(Config.kill_nifty_ws)	
					time.sleep(5)
					os.system("osascript -e 'tell application \"iTerm\" to activate' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"D\" using command down' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"nifty_ws\"' -e 'tell application \"System Events\" to tell process \"iTerm\" to key code 52'")
					time.sleep(5)
					NIFTY_LTP = []
		else:
			NIFTY_LTP.append(n_ltp)

		# if len(MIDCPNIFTY_LTP) >= unhealthy_timer:
		# 	MIDCPNIFTY_LTP.pop(0)
		# 	MIDCPNIFTY_LTP.append(mid_ltp)
		# 	while len(set(MIDCPNIFTY_LTP)) == 1:
		# 			unhealthy_midcpnifty +=1
		# 			print(Clock.tictoc(), "MIDCPNIFTY WebSocket Unhealthy for ", unhealthy_midcpnifty, " times today. Rebooting...")
		# 			# chime.warning()
		# 			os.system(Config.kill_midcpnifty_ws)	
		# 			time.sleep(5)
		# 			os.system("osascript -e 'tell application \"iTerm\" to activate' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"D\" using command down' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"midcpnifty_ws\"' -e 'tell application \"System Events\" to tell process \"iTerm\" to key code 52'")
		# 			time.sleep(5)
		# 			MIDCPNIFTY_LTP = []
		# else:
		# 	MIDCPNIFTY_LTP.append(mid_ltp)
		
		if len(FINNIFTY_LTP) >= unhealthy_timer:
			FINNIFTY_LTP.pop(0)
			FINNIFTY_LTP.append(fin_ltp)
			while len(set(FINNIFTY_LTP)) == 1:
					unhealthy_finnifty +=1
					print(Clock.tictoc(), "FINNIFTY WebSocket Unhealthy for ", unhealthy_finnifty, " times today. Rebooting...")
					# chime.warning()
					os.system(Config.kill_finnifty_ws)	
					time.sleep(5)
					os.system("osascript -e 'tell application \"iTerm\" to activate' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"D\" using command down' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"finnifty_ws\"' -e 'tell application \"System Events\" to tell process \"iTerm\" to key code 52'")
					time.sleep(5)
					FINNIFTY_LTP = []
		else:
			FINNIFTY_LTP.append(fin_ltp)

		if len(SENSEX_LTP) >= unhealthy_timer:
			SENSEX_LTP.pop(0)
			SENSEX_LTP.append(sen_ltp)
			while len(set(SENSEX_LTP)) == 1:
					unhealthy_sensex +=1
					print(Clock.tictoc(), "SENSEX WebSocket Unhealthy for ", unhealthy_sensex, " times today. Rebooting...")
					os.system(Config.kill_sensex_ws)
					time.sleep(5)
					os.system("osascript -e 'tell application \"iTerm\" to activate' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"D\" using command down' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"sensex_ws\"' -e 'tell application \"System Events\" to tell process \"iTerm\" to key code 52'")
					time.sleep(5)
					SENSEX_LTP = []
		else:
			SENSEX_LTP.append(sen_ltp)

		# if len(BAJFINANCE_LTP) >= unhealthy_timer:
		# 	BAJFINANCE_LTP.pop(0)
		# 	BAJFINANCE_LTP.append(baj_ltp)
		# 	while len(set(BAJFINANCE_LTP)) == 1:
		# 			unhealthy_bajfinance +=1
		# 			print(Clock.tictoc(), "BAJFINANCE WebSocket Unhealthy for ", unhealthy_bajfinance, " times today. Rebooting...")
		# 			# chime.warning()
		# 			os.system(Config.kill_bajfinance_ws)	
		# 			time.sleep(5)
		# 			os.system("osascript -e 'tell application \"iTerm\" to activate' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"D\" using command down' -e 'tell application \"System Events\" to tell process \"iTerm\" to keystroke \"bajfinance_ws\"' -e 'tell application \"System Events\" to tell process \"iTerm\" to key code 52'")
		# 			time.sleep(5)
		# 			BAJFINANCE_LTP = []
		# else:
		# 	BAJFINANCE_LTP.append(baj_ltp)

		# if len(BANKNIFTY_LTP) > 0 and len(NIFTY_LTP) > 0 and len(MIDCPNIFTY_LTP) > 0 and len(FINNIFTY_LTP) > 0 and len(BAJFINANCE_LTP) > 0:
		# 	print(f"{Clock.tictoc()} Nifty: @{NIFTY_LTP[-1]} || MIDCPNifty: @{MIDCPNIFTY_LTP[-1]} || BankNifty: @{BANKNIFTY_LTP[-1]} || FinNifty: @{FINNIFTY_LTP[-1]} || BajFinance: @{BAJFINANCE_LTP[-1]} ", end="\r", flush=True)
		# if len(BANKNIFTY_LTP) > 0 and len(NIFTY_LTP) > 0 and len(FINNIFTY_LTP) > 0 and len(BAJFINANCE_LTP) > 0:
		# 	print(f"{Clock.tictoc()} Nifty: @{NIFTY_LTP[-1]} || BankNifty: @{BANKNIFTY_LTP[-1]} || FinNifty: @{FINNIFTY_LTP[-1]} || BajFinance: @{BAJFINANCE_LTP[-1]} ", end="\r", flush=True)
		if len(BANKNIFTY_LTP) > 0 and len(NIFTY_LTP) > 0 and len(FINNIFTY_LTP) > 0 and len(SENSEX_LTP) > 0:
			print(f"{Clock.tictoc()} Nifty: @{NIFTY_LTP[-1]} || BankNifty: @{BANKNIFTY_LTP[-1]} || FinNifty: @{FINNIFTY_LTP[-1]} || Sensex: @{SENSEX_LTP[-1]}", end="\r", flush=True)
		time.sleep(2)
	os.system(Config.kill_banknifty_ws)
	time.sleep(5)
	os.system(Config.kill_nifty_ws)
	time.sleep(5)
	os.system(Config.kill_midcpnifty_ws)
	time.sleep(5)
	os.system(Config.kill_finnifty_ws)
	time.sleep(5)
	os.system(Config.kill_bajfinance_ws)
	time.sleep(5)
	os.system(Config.kill_sensex_ws)
	time.sleep(5)
	exit()

Clock.wait_until(Config.ws_start_hour,Config.ws_start_min+1,Config.ws_start_sec)
monitorHealth()