#!/usr/bin/env python
# coding: utf-8

# pip install fyers-apiv3
import base64
import os
import pyotp
import pprint
import requests
import yaml

from datetime import date, datetime
from time import sleep
from urllib.parse import parse_qs,urlparse
from fyers_apiv3 import fyersModel
from utils.credentials import HIGHEST_MIDCPNIFTY_OPTION_PRICE, HIGHEST_NIFTY_OPTION_PRICE,HIGHEST_BANKNIFTY_OPTION_PRICE,HIGHEST_BAJFINANCE_OPTION_PRICE,HIGHEST_FINNIFTY_OPTION_PRICE,HIGHEST_SENSEX_OPTION_PRICE,LOT_SIZE_BANKNIFTY, LOT_SIZE_MIDCPNIFTY, LOT_SIZE_NIFTY,LOT_SIZE_FINNIFTY,LOT_SIZE_BAJFINANCE,LOT_SIZE_SENSEX, DEMATS_FOR_LOGIN
from utils.deco import retry

# Get the directory containing the main script
script_directory = os.path.dirname(os.path.abspath(__file__))
static_qty = True

# Navigate up to the root directory by using os.path.dirname() repeatedly
root_directory = script_directory
while not os.path.exists(os.path.join(root_directory, 'README.md')):
    root_directory = os.path.dirname(root_directory)
logger_path = os.path.join(root_directory,"Trades",date.today().strftime("%Y-%m-%d")) #os.path.join("/Users/ramkishore.gollakota/Documents/algo/Fyers/Trades",date.today().strftime("%Y-%m-%d"))
current_day_yml = os.path.join(logger_path, "config_"+date.today().strftime("%Y-%m-%d")+".yml")
current_day_override = os.path.join(logger_path, "override_"+date.today().strftime("%Y-%m-%d")+".txt")
os.makedirs(logger_path, exist_ok=True)

"""
In order to get started with Fyers API we would like you to do the following things first.
1. Checkout our API docs :   https://myapi.fyers.in/docsv3
2. Create an APP using our API dashboard :   https://myapi.fyers.in/dashboard/
Once you have created an APP you can start using the below SDK 
"""

# redirect_uri = "https://myapi.fyers.in/"
# client_id='Z810ARS2UD-100'
# secret_key = 'D7BWGJLWVW'
# FY_ID = "XG11497"  # Your fyers ID
# TOTP_KEY = "LO3R3NOGOQLVE5EFXTQQBUNY3YP5NCLC"  # TOTP secret is generated when we enable 2Factor TOTP from myaccount portal
# PIN = "1606"  # User pin for fyers account

# redirect_uri = "https://myapi.fyers.in/"
# client_id='XPCPS6P7YO-100'
# secret_key = 'VALSDCIM3Q'
# FY_ID = "XS66776"  # Your fyers ID
# TOTP_KEY = "G5FGG7DH4EM7VSYYGUZS7A6WBWE35BFO"  # TOTP secret is generated when we enable 2Factor TOTP from myaccount portal
# PIN = "1606"  # User pin for fyers account

class SmartLogin():

    def __init__(self) -> None:
        self.count = 0
        pass

    def getEncodedString(self, string):
        string = str(string)
        base64_bytes = base64.b64encode(string.encode("ascii"))
        return base64_bytes.decode("ascii")
            
    
    def activateAccount(self,redirect_uri,client_id,secret_key,FY_ID=None,TOTP_KEY=None,PIN=None):
        #### Generate an authcode and then make a request to generate an accessToken (Login Flow)
        ## app_secret key which you got after creating the app 
        grant_type = "authorization_code"                  ## The grant_type always has to be "authorization_code"
        response_type = "code"                             ## The response_type always has to be "code"
        state = "sample"                                   ##  The state field here acts as a session manager. you will be sent with the state field after successfull generation of auth_code 

        ### Connect to the sessionModel object here with the required input parameters
        appSession = fyersModel.SessionModel(client_id = client_id, redirect_uri = redirect_uri,response_type=response_type,state=state,secret_key=secret_key,grant_type=grant_type)

        # ## Make  a request to generate_authcode object this will return a login url which you need to open in your browser from where you can get the generated auth_code 
        generateTokenUrl = appSession.generate_authcode()
        generateTokenUrl
    
    def generateTokenAndQuantity(self,options):
        # print(options)
        redirect_uri = options['redirect_uri']
        client_id = options['client_id']
        secret_key = options['secret_key']
        FY_ID = options['FY_ID']
        TOTP_KEY = options['TOTP_KEY']
        PIN = options['PIN']

        URL_SEND_LOGIN_OTP="https://api-t2.fyers.in/vagator/v2/send_login_otp_v2"
        URL_SEND_LOGIN_OTP_RESPONSE = requests.post(url=URL_SEND_LOGIN_OTP, json={"fy_id": self.getEncodedString(FY_ID),"app_id":"2"}).json()
        print(f"[{options['account_name']}] send_login_otp: {URL_SEND_LOGIN_OTP_RESPONSE}")

        if datetime.now().second % 30 > 27 : sleep(5)
        URL_VERIFY_OTP="https://api-t2.fyers.in/vagator/v2/verify_otp"
        URL_VERIFY_OTP_RESPONSE = requests.post(url=URL_VERIFY_OTP, json= {"request_key":URL_SEND_LOGIN_OTP_RESPONSE["request_key"],"otp":pyotp.TOTP(TOTP_KEY).now()}).json()
        print(f"[{options['account_name']}] verify_otp: {URL_VERIFY_OTP_RESPONSE}")

        ses = requests.Session()
        URL_VERIFY_OTP2="https://api-t2.fyers.in/vagator/v2/verify_pin_v2"
        payload2 = {"request_key": URL_VERIFY_OTP_RESPONSE["request_key"],"identity_type":"pin","identifier":self.getEncodedString(PIN)}
        URL_VERIFY_OTP2_RESPONSE = ses.post(url=URL_VERIFY_OTP2, json= payload2).json()
        print(f"[{options['account_name']}] verify_pin: {URL_VERIFY_OTP2_RESPONSE}")

        if 'data' not in URL_VERIFY_OTP2_RESPONSE or 'access_token' not in URL_VERIFY_OTP2_RESPONSE['data']:
            raise RuntimeError(f"[{options['account_name']}] PIN verification failed. Full response: {URL_VERIFY_OTP2_RESPONSE}")

        ses.headers.update({
            'authorization': f"Bearer {URL_VERIFY_OTP2_RESPONSE['data']['access_token']}"
        })

        TOKENURL="https://api-t1.fyers.in/api/v3/token"
        payload3 = {"fyers_id":FY_ID,
                "app_id":client_id[:-4],
                "redirect_uri":redirect_uri,
                "appType":"100","code_challenge":"",
                "state":"None","scope":"","nonce":"","response_type":"code","create_cookie":True}

        TOKENURL_RESPONSE = ses.post(url=TOKENURL, json= payload3).json()  
        # print(TOKENURL_RESPONSE)

        url = TOKENURL_RESPONSE['Url']
        # print(url)
        parsed = urlparse(url)
        auth_code = parse_qs(parsed.query)['auth_code'][0]
        # auth_code

        grant_type = "authorization_code" 
        response_type = "code"  

        session = fyersModel.SessionModel(
            client_id=client_id,
            secret_key=secret_key, 
            redirect_uri=redirect_uri, 
            response_type=response_type, 
            grant_type=grant_type
        )

        # Set the authorization code in the session object
        session.set_token(auth_code)

        # Generate the access token using the authorization code
        response = session.generate_token()

        # Print the response, which should contain the access token and other details
        #print(response)

        access_token = response['access_token']
        # print("access_token: ", access_token)

        # Initialize the FyersModel instance with your client_id, access_token, and enable async mode
        # fyers = fyersModel.FyersModel(client_id=client_id, is_async=False, token=access_token, log_path=Config.logger_path)

        # Make a request to get the user profile information
        # print(fyers.get_profile())

        # Get Max Quantities
        fyers = fyersModel.FyersModel(token=access_token, is_async=False, client_id=client_id,log_path=logger_path)
        response = fyers.funds()
        # print("funds:\n",response)
        funds = response["fund_limit"]
        available_funds = [item for item in funds if "Available Balance" in item.values()][0]["equityAmount"]
        # print(f"Available Balance: {available_funds}/-")
        # available_funds = 100000  # Replace with your actual available funds

        # Calculate for BankNifty
        max_quantity_bank_nifty = self.calculate_max_quantity(available_funds=available_funds,highest_price=HIGHEST_BANKNIFTY_OPTION_PRICE,min_lot_size=LOT_SIZE_BANKNIFTY) if not static_qty else 45
        # print(f"SAI_BANKNIFTY_QTY = {max_quantity_bank_nifty}")

        # Calculate for Nifty
        max_quantity_nifty = self.calculate_max_quantity(available_funds=available_funds,highest_price=HIGHEST_NIFTY_OPTION_PRICE,min_lot_size=LOT_SIZE_NIFTY) if not static_qty else 150
        # print(f"SAI_NIFTY_QTY = {max_quantity_nifty}")

        # Calculate for MidCPNifty
        max_quantity_midcpnifty = self.calculate_max_quantity(available_funds=available_funds,highest_price=HIGHEST_MIDCPNIFTY_OPTION_PRICE,min_lot_size=LOT_SIZE_MIDCPNIFTY) if not static_qty else 150
        # print(f"SAI_NIFTY_QTY = {max_quantity_nifty}")

        max_quantity_finnifty = self.calculate_max_quantity(available_funds=available_funds,highest_price=HIGHEST_FINNIFTY_OPTION_PRICE,min_lot_size=LOT_SIZE_FINNIFTY) if not static_qty else 120
        # print(f"SAI_NIFTY_QTY = {max_quantity_nifty}")

        max_quantity_bajfinance = self.calculate_max_quantity(available_funds=available_funds,highest_price=HIGHEST_BAJFINANCE_OPTION_PRICE,min_lot_size=LOT_SIZE_BAJFINANCE) if not static_qty else 125

        # Calculate for SENSEX (BSE)
        max_quantity_sensex = self.calculate_max_quantity(available_funds=available_funds,highest_price=HIGHEST_SENSEX_OPTION_PRICE,min_lot_size=LOT_SIZE_SENSEX) if not static_qty else 20

        return {"access_token": access_token, "max_quantity_bank_nifty": max_quantity_bank_nifty, "max_quantity_nifty": max_quantity_nifty, "max_quantity_midcpnifty": max_quantity_midcpnifty,"max_quantity_finnifty": max_quantity_finnifty,"max_quantity_bajfinance": max_quantity_bajfinance,"max_quantity_sensex": max_quantity_sensex, "created_date" : str(datetime.now())}
    
    def calculate_max_quantity(self,available_funds, highest_price, min_lot_size):
        # Set aside 25% of the funds
        funds_to_use = available_funds * 0.75

        # Calculate the maximum quantity that can be purchased
        max_quantity = funds_to_use // highest_price

        # Adjust the quantity to be a multiple of the minimum lot size
        max_quantity = (max_quantity // min_lot_size) * min_lot_size

        return int(max_quantity)

smartlogin = SmartLogin()

yml_data = {}
for demat in DEMATS_FOR_LOGIN:
    yml_data[demat['account_name']] = smartlogin.generateTokenAndQuantity(demat)

# Write content to the file
with open(current_day_override, 'w') as file:
    file.write("TRUE")

# Write the dictionary to a YAML file
with open(current_day_yml, 'w') as outfile:
    yaml.dump(yml_data, outfile, default_flow_style=False)

def print_yaml_file(filename):
    with open(filename, 'r') as stream:
        try:
            data = yaml.safe_load(stream)
            print(pprint.pformat(data))
        except yaml.YAMLError as exc:
            print(exc)

print_yaml_file(current_day_yml)
print("Successfully LoggedIn for all accounts!")