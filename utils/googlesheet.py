import gspread
import pandas as pd
from gspread_dataframe import set_with_dataframe
from utils.credentials import GSHEET_CREDS

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

creds = GSHEET_CREDS

# client = gspread.service_account_from_dict(creds)
# spreadsheet = client.open("SOT_BOT_DATA")
