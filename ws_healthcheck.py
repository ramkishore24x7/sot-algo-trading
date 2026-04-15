import argparse
import datetime
import os
import subprocess
import sys
import time

import requests
from utils.clock import Clock
from utils.constants import Config

_ALGO_DIR = os.path.dirname(os.path.abspath(__file__))

bank_nifty  = "NSE:NIFTYBANK-INDEX"
nifty       = "NSE:NIFTY50-INDEX"
midcpnifty  = "NSE:MIDCPNIFTY-INDEX"
finnifty    = "NSE:FINNIFTY-INDEX"
bajfinance  = "NSE:BAJFINANCE-EQ"
sensex      = "BSE:SENSEX-INDEX"

unhealthy_timer = 15  # In seconds

# ── supported instruments ────────────────────────────────────────────────────
INSTRUMENTS = {
	"BANKNIFTY":  {"instrument": bank_nifty,  "ws_key": "BANKNIFTY",  "script": "ws_fyers_BANKNIFTY_v3.py",  "kill": lambda: Config.kill_banknifty_ws},
	"NIFTY":      {"instrument": nifty,       "ws_key": "NIFTY",      "script": "ws_fyers_NIFTY_v3.py",      "kill": lambda: Config.kill_nifty_ws},
	"MIDCPNIFTY": {"instrument": midcpnifty,  "ws_key": "MIDCPNIFTY", "script": "ws_fyers_MIDCPNIFTY_v3.py", "kill": lambda: Config.kill_midcpnifty_ws},
	"FINNIFTY":   {"instrument": finnifty,    "ws_key": "FINNIFTY",   "script": "ws_fyers_FINNIFTY_v3.py",   "kill": lambda: Config.kill_finnifty_ws},
	"BAJFINANCE": {"instrument": bajfinance,  "ws_key": "BAJFINANCE", "script": "ws_fyers_BAJFINANCE_v3.py", "kill": lambda: Config.kill_bajfinance_ws},
	"SENSEX":     {"instrument": sensex,      "ws_key": "SENSEX",     "script": "ws_fyers_SENSEX_v3.py",     "kill": lambda: Config.kill_sensex_ws},
}

startTime = datetime.time(9, 0, 0)
endTime   = datetime.time(15, 30, 0)


def getLTP(instrument_symbol, port_number):
	url  = f"http://localhost:{port_number}/ltp?instrument={instrument_symbol}"
	try:
		resp = requests.get(url)
		return resp.json()
	except Exception as e:
		print(datetime.datetime.now(), " Exception @getLTP:", e)
		return None


def _relaunch_ws(name: str):
	"""Restart a WS server as a detached background subprocess."""
	script = os.path.join(_ALGO_DIR, INSTRUMENTS[name]["script"])
	log_name = INSTRUMENTS[name]["script"].replace(".py", "") + ".out"
	log_path = os.path.join(Config.logger_path, log_name)
	os.makedirs(Config.logger_path, exist_ok=True)
	log_fh = open(log_path, "a")
	proc = subprocess.Popen(
		[sys.executable, script],
		stdout=log_fh,
		stderr=subprocess.STDOUT,
		start_new_session=True,   # detach from healthcheck's process group
	)
	print(f"{Clock.tictoc()} {name} WS relaunched in background (PID={proc.pid})")


def monitorHealth(watch: set):
	unhealthy = {k: 0 for k in watch}
	ltps      = {k: [] for k in watch}

	while Clock.time_in_range(startTime, endTime, datetime.datetime.now().time()):
		# ── poll watched instruments ──────────────────────────────────────────
		current = {}
		for name in watch:
			cfg  = INSTRUMENTS[name]
			port = Config.ws_map.get(cfg["ws_key"])
			assert port is not None, f"No port configured for {name}"
			current[name] = getLTP(cfg["instrument"], port)

		# ── health check each ────────────────────────────────────────────────
		for name in watch:
			buf = ltps[name]
			ltp = current[name]
			if ltp is None:
				# server not responding — don't pollute buffer with None
				print(f"\r{Clock.tictoc()} WARNING: {name} server not responding (getLTP=None)", flush=True)
				continue
			if len(buf) >= unhealthy_timer:
				buf.pop(0)
				buf.append(ltp)
				# only trigger if ALL values are identical non-None (frozen feed)
				if len(set(str(v) for v in buf)) == 1:
					unhealthy[name] += 1
					print(Clock.tictoc(), f"{name} WebSocket Unhealthy for {unhealthy[name]} times today. Rebooting...")
					os.system(INSTRUMENTS[name]["kill"]())
					time.sleep(5)
					_relaunch_ws(name)
					time.sleep(5)
					buf.clear()
			else:
				buf.append(ltp)

		# ── status line ──────────────────────────────────────────────────────
		parts = [f"{name}: @{ltps[name][-1]}" for name in watch if ltps[name]]
		if parts:
			print(f"{Clock.tictoc()} " + " || ".join(parts), end="\r", flush=True)

		time.sleep(2)

	# ── EOD: kill watched websockets ─────────────────────────────────────────
	for name in watch:
		os.system(INSTRUMENTS[name]["kill"]())
		time.sleep(5)
	exit()


def main():
	valid = set(INSTRUMENTS.keys())
	default = ["BANKNIFTY", "NIFTY", "FINNIFTY", "SENSEX"]

	parser = argparse.ArgumentParser(description="WebSocket health monitor")
	parser.add_argument(
		"--watch",
		nargs="+",
		metavar="INSTRUMENT",
		default=default,
		help=f"Instruments to monitor. Valid: {', '.join(sorted(valid))}. Default: {' '.join(default)}",
	)
	args = parser.parse_args()

	# validate manually so we get a clear error without argparse choices quirks
	unknown = [i for i in args.watch if i not in valid]
	if unknown:
		parser.error(f"Unknown instrument(s): {', '.join(unknown)}. Valid: {', '.join(sorted(valid))}")

	watch = set(args.watch)
	print(f"Monitoring: {', '.join(sorted(watch))}")

	Clock.wait_until(Config.ws_start_hour, Config.ws_start_min + 1, Config.ws_start_sec)
	monitorHealth(watch)


if __name__ == "__main__":
	main()
