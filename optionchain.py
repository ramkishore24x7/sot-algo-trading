import numpy as np
import pandas as pd
import requests
from py_vollib_vectorized import get_all_greeks, vectorized_implied_volatility
from datetime import datetime, timedelta
from fyers_apiv3 import fyersModel
from utils.constants import Config

fyers = fyersModel.FyersModel(token=Config.RAM_ACCESS_TOKEN, is_async=False, client_id=Config.RAM_CLIENT_ID, log_path=Config.fyers_log_path)
api_mode = True

class OptionCalculator:
    def __init__(self, index_type):
        self.index_type = index_type
        self.instrument_expiry = Config.expiry_map.get(index_type.upper(), None)
        self.full_expiry_data = Config.upstox_expiry_info[index_type]
        self.port_number = Config.ws_map.get(index_type.upper(), None)
        self.spot = self.getSpot()
        self.expiry_datetime = self.getExpiryDateTime()
        self.risk_free_rate = 0.1
        self.step_size = self._get_step_size()
        self.option_chain = None

    def getSpot(self):
        instrument_dict = {
            'banknifty': "NSE:NIFTYBANK-INDEX",
            'nifty': "NSE:NIFTY50-INDEX",
            'midcpnifty': "NSE:MIDCPNIFTY-INDEX",
            'finnifty': "NSE:FINNIFTY-INDEX",
            'bajfinance': "NSE:BAJFINANCE-EQ"
        }
        instrument = instrument_dict[self.index_type]
        data = {"symbols":instrument}
        url = f"http://localhost:{self.port_number}/spot_price"
        response = fyers.quotes(data) if api_mode else requests.get(url)
        # print("response: ", response.json())
        return (response)['d'][0]['v']['lp'] if api_mode else response.json()

    def getExpiryDateTime(self):
        # year,month,day = int("20"+self.instrument_expiry["year"]),int(self.instrument_expiry["month"]),int(self.instrument_expiry["day"]) if isinstance(int(self.instrument_expiry["month"]), int) else int("20"+self.full_expiry_data["expiry_year"]),self.full_expiry_data["expiry_month"],int(self.full_expiry_data["expiry_day"])

        year = int("20"+self.instrument_expiry["year"])
        month = int(self.full_expiry_data["expiry_month"]) if isinstance(self.instrument_expiry["month"],str) else int(self.instrument_expiry["month"])
        day = int(self.instrument_expiry["day"]) if self.instrument_expiry["day"] else int(self.full_expiry_data["expiry_day"])

        # Add your function to get expiry date here
        # return datetime(2024, 6, 13, 15, 30, 0)
        return datetime(year, month, day, 15, 30, 0)

    def _get_step_size(self):
        if self.index_type == 'banknifty':
            return 100
        elif self.index_type in ['nifty', 'finnifty', 'bajfinance']:
            return 50
        elif self.index_type == 'midcpnifty':
            return 25
        else:
            raise ValueError("Invalid index type")

    def _generate_strike_prices(self, option_type):
        atm_strike = round(self.spot / self.step_size) * self.step_size
        if option_type == 'c':
            return [atm_strike - i * self.step_size for i in range(8)]  # ATM, ATM-1, ATM-2, ATM-3 ...ATM-8 for call options
        else:
            return [atm_strike + i * self.step_size for i in range(8)]  # ATM, ATM+1, ATM+2, ATM+3 ...ATM+8for put options

    def _calculate_time_to_expiry(self):
        return (self.expiry_datetime - datetime.now()) / timedelta(days=1) / 365

    def getLTP(self, instrument):
        # print("instrument:\n", instrument)
        data = {"symbols":instrument}
        url = f"http://localhost:{self.port_number}/ltp?instrument={instrument}"
        response = fyers.quotes(data) if api_mode else requests.get(url)
        # print(f"{instrument}:", response) if api_mode else print(f"{instrument}:", response.json())
        return (response)['d'][0]['v']['lp'] if api_mode else response.json()

    def calculate_iv_and_greeks(self, option_type):
        self.time_to_expiry = self._calculate_time_to_expiry()  # Update time to expiry
        option_type_full = 'CE' if option_type == 'c' else 'PE'

        ltp_list = []
        for strike in self.strike_list:
            # ltp_list = np.array([self.getLTP(f'NSE:{self.index_type.upper()}{self.instrument_expiry["year"]}{self.instrument_expiry["month"]}{self.instrument_expiry["day"]}{strike}{option_type_full}') for strike in self.strike_list])
            ltp = self.getLTP(f'NSE:{self.index_type.upper()}{self.instrument_expiry["year"]}{self.instrument_expiry["month"]}{self.instrument_expiry["day"]}{strike}{option_type_full}')
            ltp_list.append(ltp)
        ltp_list = np.array(ltp_list)

        iv_list = vectorized_implied_volatility(
            ltp_list, 
            np.full_like(ltp_list, self.spot, dtype=np.float64), 
            np.array(self.strike_list, dtype=np.float64), 
            np.full_like(ltp_list, self.time_to_expiry, dtype=np.float64), 
            self.risk_free_rate, 
            np.repeat(option_type, len(ltp_list))
        )
        greeks = get_all_greeks(
            np.repeat(option_type, len(ltp_list)), 
            np.full_like(ltp_list, self.spot, dtype=np.float64), 
            np.array(self.strike_list, dtype=np.float64), 
            np.full_like(ltp_list, self.time_to_expiry, dtype=np.float64), 
            self.risk_free_rate, 
            iv_list
        )

        self.spot = self.getSpot() 
        return iv_list, greeks, ltp_list

    def generate_option_chain(self):
        # Generate strike prices for call options
        strike_list_call = self._generate_strike_prices('c')
        self.strike_list = strike_list_call
        iv_list_call, greeks_call, ltp_list_call = self.calculate_iv_and_greeks('c')

        # Generate strike prices for put options
        strike_list_put = self._generate_strike_prices('p')
        self.strike_list = strike_list_put
        iv_list_put, greeks_put, ltp_list_put = self.calculate_iv_and_greeks('p')

        results_call = pd.DataFrame({
            'Index': [self.index_type.upper()] * len(strike_list_call),
            'Strike': strike_list_call,
            'Option_Type': ['CE'] * len(strike_list_call),
            'LTP': ltp_list_call,
            'Delta': np.ravel(greeks_call['delta']),
            'IV': np.ravel(iv_list_call),
            'Gamma': np.ravel(greeks_call['gamma']),
            'Rho': np.ravel(greeks_call['rho']),
            'Theta': np.ravel(greeks_call['theta']),
            'Spot': [self.spot]* len(strike_list_call),
        })

        results_put = pd.DataFrame({
            'Index': [self.index_type.upper()] * len(strike_list_put),
            'Strike': strike_list_put,
            'Option_Type': ['PE'] * len(strike_list_put),
            'LTP': ltp_list_put,
            'Delta': np.ravel(greeks_put['delta']),
            'IV': np.ravel(iv_list_put),
            'Gamma': np.ravel(greeks_put['gamma']),
            'Rho': np.ravel(greeks_put['rho']),
            'Theta': np.ravel(greeks_put['theta']),
            'Spot': [self.spot]* len(strike_list_put)
        })

        self.option_chain = pd.concat([results_call, results_put], ignore_index=True)
        return self.option_chain
    
    def get_closest_strike(self, target_delta, option_type):
        if self.option_chain.empty:
            return None, None, None
        
        delta_column = 'Delta'
        ltp_column = 'LTP'
        gamma_column = 'Gamma'
        
        self.option_chain['delta_diff'] = abs(self.option_chain[delta_column] - target_delta)
        closest_row = self.option_chain.loc[self.option_chain['delta_diff'].idxmin()]
        
        strike = int(closest_row['Strike'])
        ltp = closest_row[ltp_column]
        delta = closest_row[delta_column]
        gamma = closest_row[gamma_column]
        
        self.option_chain.drop(columns=['delta_diff'], inplace=True)
        
        return str(int(strike)) + option_type.upper(), ltp, delta, gamma


# Example usage:
# index_type = 'nifty'  # Options: 'banknifty', 'nifty', 'finnifty', 'midcapnifty'
index_type = 'banknifty'  # Options: 'banknifty', 'nifty', 'finnifty', 'midcapnifty'
# index_type = 'finnifty'  # Options: 'banknifty', 'nifty', 'finnifty', 'midcapnifty'
# index_type = 'midcpnifty'  # Options: 'banknifty', 'nifty', 'finnifty', 'midcapnifty'
option_calculator = OptionCalculator(index_type)
option_chain = option_calculator.generate_option_chain()
# print("option_chain: \n", option_chain)
print("\n", option_chain,"\n")



# # Retrieve a specific strike price for a call option
# strike_price = 52100
# option_type = 'CE'
# filtered_option = option_chain[(option_chain['Strike'] == strike_price) & (option_chain['Option_Type'] == option_type)]

# print("Filtered option: \n", filtered_option)


# delta = filtered_option['Delta'].values[0]
# print("Delta: ", delta)

strike,ltp,delta,gamma = option_calculator.get_closest_strike(0.85,"CE")
print(f"Strike: {strike}, LTP: {ltp}, Delta: {delta} | Gamma: {gamma}")