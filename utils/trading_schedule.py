# from datetime import datetime

# def get_account_config(name):
#     # Replace with your actual account configurations
#     if name == "RAM":
#         return {
#             "client_id": "RAM_CLIENT_ID",
#             "secret_key": "RAM_SECRET_KEY",
#             "access_token": "RAM_ACCESS_TOKEN",
#             "quantity_banknifty": "RAM_BANKNIFTY_QTY",
#             "quantity_nifty": "RAM_NIFTY_QTY",
#             "quantity_midcpnifty": "RAM_MIDCPNIFTY_QTY",
#             "quantity_finnifty": "RAM_FINNIFTY_QTY",
#             "quantity_bajfinance": "RAM_BAJFINANCE_QTY"
#         }
#     elif name == "SAI":
#         return {
#             "client_id": "SAI_CLIENT_ID",
#             "secret_key": "SAI_SECRET_KEY",
#             "access_token": "SAI_ACCESS_TOKEN",
#             "quantity_banknifty": "SAI_BANKNIFTY_QTY",
#             "quantity_nifty": "SAI_NIFTY_QTY",
#             "quantity_midcpnifty": "SAI_MIDCPNIFTY_QTY",
#             "quantity_finnifty": "SAI_FINNIFTY_QTY",
#             "quantity_bajfinance": "SAI_BAJFINANCE_QTY"
#         }
#     else:
#         raise ValueError("Invalid name provided. Must be either 'RAM' or 'SAI'.")

# def get_trading_config(name):
#     today = datetime.now().strftime('%a')  # Get the current day as a string (Mon, Tue, etc.)
    
#     range_schedule = {
#         "Mon": "RAM's Demat1",
#         "Tue": None,
#         "Wed": "RAM's Demat1_Aggressive",
#         "Thu": None,
#         "Fri": "RAM's Demat1"
#     }
    
#     breakout_schedule = {
#         "Mon": "RAM's Demat1_Aggressive",
#         "Tue": "RAM's Demat3_Aggressive",
#         "Wed": "RAM's Demat3_Aggressive",
#         "Thu": "RAM's Demat1",
#         "Fri": "RAM's Demat3_Aggressive"
#     }

#     config = get_account_config(name)
    
#     # Determine which strategy to use
#     if today in range_schedule and range_schedule[today]:
#         config["strategy"] = "range"
#         config["squareoff_at_first_target"] = True
#         config["should_average"] = "Aggressive" in range_schedule[today]
#     elif today in breakout_schedule and breakout_schedule[today]:
#         config["strategy"] = "breakout"
#         config["squareoff_at_first_target"] = True
#         config["should_average"] = "Aggressive" in breakout_schedule[today]
#     else:
#         config["paper_trade"] = True

#     return config

# # Example usage:
# name = "RAM"  # or "SAI"
# trading_config = get_trading_config(name)
# print(trading_config)


from datetime import datetime

def get_account_config(name):
    # config = {
    #     "client_id": getattr(globals(), f"{name}_CLIENT_ID"),
    #     "secret_key": getattr(globals(), f"{name}_SECRET_KEY"),
    #     "access_token": getattr(globals(), f"{name}_ACCESS_TOKEN"),
    #     "quantity_banknifty": getattr(globals(), f"{name}_BANKNIFTY_QTY"),
    #     "quantity_nifty": getattr(globals(), f"{name}_NIFTY_QTY"),
    #     "quantity_midcpnifty": getattr(globals(), f"{name}_MIDCPNIFTY_QTY"),
    #     "quantity_finnifty": getattr(globals(), f"{name}_FINNIFTY_QTY"),
    #     "quantity_bajfinance": getattr(globals(), f"{name}_BAJFINANCE_QTY")
    # }
    return {
        "client_id": "RAM_CLIENT_ID",
        "secret_key": "RAM_SECRET_KEY",
        "access_token": "RAM_ACCESS_TOKEN",
        "quantity_banknifty": "RAM_BANKNIFTY_QTY",
        "quantity_nifty": "RAM_NIFTY_QTY",
        "quantity_midcpnifty": "RAM_MIDCPNIFTY_QTY",
        "quantity_finnifty": "RAM_FINNIFTY_QTY",
        "quantity_bajfinance": "RAM_BAJFINANCE_QTY"
    }
    return config

def get_trading_config(name, day, strategy):
    range_schedule = {
        "Mon": "RAM's Demat1",
        "Tue": None,
        "Wed": "RAM's Demat1_Aggressive",
        "Thu": None,
        "Fri": "RAM's Demat1"
    }
    
    breakout_schedule = {
        "Mon": "RAM's Demat1_Aggressive",
        "Tue": "RAM's Demat3_Aggressive",
        "Wed": "RAM's Demat3_Aggressive",
        "Thu": "RAM's Demat1",
        "Fri": "RAM's Demat3_Aggressive"
    }

    config = get_account_config(name)
    
    # Determine which strategy to use
    if strategy == "range" and day in range_schedule and range_schedule[day]:
        config["strategy"] = "range"
        config["squareoff_at_first_target"] = True
        config["should_average"] = "Aggressive" in range_schedule[day]
    elif strategy == "breakout" and day in breakout_schedule and breakout_schedule[day]:
        config["strategy"] = "breakout"
        config["squareoff_at_first_target"] = True
        config["should_average"] = "Aggressive" in breakout_schedule[day]
    else:
        config["paper_trade"] = True

    return config

# Function to print configurations for all days of the week for a given strategy
def print_all_week_configs(name, strategy):
    days_of_week = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for day in days_of_week:
        trading_config = get_trading_config(name, day, strategy)
        print(f"Day: {day}")
        print(trading_config)
        print("-" * 40)

# Example usage:
name = "RAM"  # or "SAI"
strategy = "breakout"  # or "range"
print_all_week_configs(name, strategy)
