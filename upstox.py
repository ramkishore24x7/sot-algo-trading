
from playwright.sync_api import Playwright, sync_playwright
from urllib.parse import parse_qs,urlparse,quote
from utils.constants import Config

import pyotp
import requests



# python -m playwright install
# python -m  playwright codegen demo.playwright.dev/todomvc


API_KEY = '59854fe5-4c70-4c05-99fe-6b9224ee7fd4'
SECRET_KEY = 'wojeaj96z7'
RURL = 'https://127.0.0.1:5000/'

TOTP_KEY = 'PPXNAEH7WOLZKS4ILOGS6CKQ357HXFGL'
MOBILE_NO = '9550288173'
PIN   =    '548248'


rurlEncode = quote(RURL,safe="")

AUTH_URL = f'https://api-v2.upstox.com/login/authorization/dialog?response_type=code&client_id={API_KEY}&redirect_uri={rurlEncode}'


def getAccessToken(code):
    url = 'https://api-v2.upstox.com/login/authorization/token'

    headers = {
        'accept': 'application/json',
        'Api-Version': '2.0',
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    data = {
        'code': code,
        'client_id': API_KEY,
        'client_secret': SECRET_KEY,
        'redirect_uri': RURL,
        'grant_type': 'authorization_code'
    }

    response = requests.post(url, headers=headers, data=data)
    json_response = response.json()
    # Access the response data
    # print(f"access_token:  {json_response['access_token']}")
    
    return json_response['access_token']
    

def run(playwright: Playwright) -> str:
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()   
    with page.expect_request(f"*{RURL}?code*") as request:
        page.goto(AUTH_URL)
        page.locator("#mobileNum").click()
        page.locator("#mobileNum").fill(MOBILE_NO)
        page.get_by_role("button", name="Get OTP").click()
        page.locator("#otpNum").click()
        otp = pyotp.TOTP(TOTP_KEY).now()
        page.locator("#otpNum").fill(otp)
        page.get_by_role("button", name="Continue").click()
        page.get_by_label("Enter 6-digit PIN").click()
        page.get_by_label("Enter 6-digit PIN").fill(PIN)
        res = page.get_by_role("button", name="Continue").click()
        page.wait_for_load_state()

    url =    request.value.url 
    # print(f"Redirect Url with code : {url}")
    parsed = urlparse(url)
    code = parse_qs(parsed.query)['code'][0]
    context.close()
    browser.close()
    return code


with sync_playwright() as playwright:
    code = run(playwright)

access_token = getAccessToken(code)
print(f"access_token:  {access_token}")
with open(Config.upstox_token, 'w') as file:
        file.write(access_token)
# access_token = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI2NUJLRlYiLCJqdGkiOiI2NjY3YzA3NTk5MjEzNjQ5ODk5NjIxZWYiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaWF0IjoxNzE4MDc1NTA5LCJpc3MiOiJ1ZGFwaS1nYXRld2F5LXNlcnZpY2UiLCJleHAiOjE3MTgxNDMyMDB9.0zkoum_QMCHFacNZ2bmewSjG9IJlHRJgH3rQSX5tYBM"