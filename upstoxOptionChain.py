import requests
import datetime
import pandas as pd
from utils.constants import Config, ExpiryDateExtractor

class UpstoxOptionChain:
    SYMBOL_MAPPING = {
        'nifty': 'NSE_INDEX|Nifty 50',
        'banknifty': 'NSE_INDEX|Nifty Bank',
        'midcpnifty': 'NSE_INDEX|NIFTY MID SELECT',
        'finnifty': 'NSE_INDEX|Nifty Fin Service',
        'bajfinance': 'NSE_EQ|INE296A01024'
    }
    
    def __init__(self, symbol):
        self.symbol = self.SYMBOL_MAPPING[symbol]
        self.expiry_data = Config.upstox_expiry_info[symbol]
        self.expiry = f"20{self.expiry_data['expiry_year']}-{self.expiry_data['expiry_month']}-{self.expiry_data['expiry_day']}"
        # self.expiry =  '2024-6-27'
        # Endpoint for Upstox API v2
        self.base_url = 'https://api.upstox.com/v2'
        self.option_chain_endpoint = '/option/chain'
        self.api_key = '59854fe5-4c70-4c05-99fe-6b9224ee7fd4'
        self.api_secret = 'wojeaj96z7'
        self.redirect_uri = 'https://127.0.0.1:5000/'
        self.access_token = open(Config.upstox_token, 'r').read()
        self.df = None

    def get_option_chain(self):
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Accept':'application/json'
        }
        params = {
            'instrument_key': self.symbol,
            'expiry_date': self.expiry
        }
        try:
            response = requests.get(self.base_url + self.option_chain_endpoint, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error occurred: {http_err}")
        except Exception as err:
            print(f"An error occurred: {err}")
        return None

    def fetch_option_data(self):
        data = self.get_option_chain()
        options = data['data']
        # print(options)
        if not data:
            return None
        market_data_criteria = ['ltp']
        greeks_data_criteria = ['delta','gamma']
        df_data = []
        
        for option in options:
            spot_price = round(option['underlying_spot_price'])
            strike_price = round(option['strike_price'])
            step = 50 if option['underlying_key'] in [self.SYMBOL_MAPPING['nifty'], self.SYMBOL_MAPPING['finnifty'], self.SYMBOL_MAPPING['bajfinance']] else 100 if option['underlying_key'] == self.SYMBOL_MAPPING['banknifty'] else 25
            if spot_price - 6*step <= strike_price <= spot_price + 6*step:
                row = {
                    'Timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'Index': option['underlying_key'],
                    'Spot Price': spot_price,
                    'Strike Price': strike_price,
                    **{'Call ' + key: value for key, value in option['call_options']['market_data'].items() if key in market_data_criteria},
                    **{'Call ' + key: value for key, value in option['call_options']['option_greeks'].items() if key in greeks_data_criteria},
                    **{'Put ' + key: value for key, value in option['put_options']['market_data'].items() if key in market_data_criteria},
                    **{'Put ' + key: value for key, value in option['put_options']['option_greeks'].items() if key in greeks_data_criteria}
                }
                df_data.append(row)
        self.df = pd.DataFrame(df_data)
        self.df.to_csv('options_data.csv', index=False)
        return self.df

    def get_closest_strike(self, target_delta, option_type):
        if self.df.empty:
            return None, None, None
        if option_type.lower() == 'ce':
            delta_column = 'Call delta'
            ltp_column = 'Call ltp'
            gamma_column = 'Call gamma'
        elif option_type.lower() == 'pe':
            delta_column = 'Put delta'
            ltp_column = 'Put ltp'
            gamma_column = 'Put gamma'
        else:
            raise ValueError("Invalid option type. Use 'ce' for call or 'pe' for put.")
        self.df['delta_diff'] = abs(self.df[delta_column] - target_delta)
        row = self.df.loc[self.df['delta_diff'].idxmin()]
        strike_price = int(row['Strike Price'])
        ltp = row[ltp_column]
        delta = row[delta_column]
        gamma = row[gamma_column]
        self.df.drop(columns=['delta_diff'], inplace=True)
        return str(int(strike_price))+option_type.upper(), ltp, delta, gamma

    def get_ltp_and_delta(self, strike_price, option_type):
        if self.df.empty:
            return None, None, None
        row = self.df.loc[self.df['Strike Price'] == strike_price]
        if row.empty:
            raise ValueError(f"No options found with strike price {strike_price}")
        if option_type.lower() == 'ce':
            ltp = row['Call ltp'].values[0]
            delta = row['Call delta'].values[0]
            gamma = row['Call gamma'].values[0]
        elif option_type.lower() == 'pe':
            ltp = row['Put ltp'].values[0]
            delta = row['Put delta'].values[0]
            gamma = row['Put gamma'].values[0]
        else:
            raise ValueError("Invalid option type. Use 'ce' for call or 'pe' for put.")
        # return str(int(strike_price))+option_type.upper(), ltp, delta
        return str(int(strike_price)) + option_type.upper(), ltp, delta, gamma
    

# expiry = '2024-6-20'
option_chain = UpstoxOptionChain('banknifty')
option_chain_data = option_chain.fetch_option_data()
# print(option_chain_data)

# strike_price = 53200
# option_type = 'pe'
# strike, ltp, delta, gamma = option_chain.get_ltp_and_delta(strike_price, option_type)
# print(f"{strike} LTP: {ltp}, Delta: {delta} | Gamma: {gamma}")

target_delta = 0.75
option_type = 'ce'
strike_price, ltp, delta, gamma = option_chain.get_closest_strike(target_delta, option_type)
print(f"Strike Price: {strike_price}, LTP: {ltp}, Delta: {delta} | Gamma: {gamma}")