class OptionCalculator:
    def __init__(self,spot, strike, cmp, proximal_line, distil_line, delta, gamma, option_type, trade_type, spot_target1, spot_target2, spot_target3,instrument=None,itm=None):
        self.instrument = instrument
        self.itm = itm
        self.spot = spot
        self.strike = strike
        self.cmp = cmp
        self.proximal_line = proximal_line
        self.distil_line = distil_line
        self.delta = delta
        self.gamma = gamma
        self.option_type = option_type
        self.trade_type = trade_type
        self.spot_target1 = spot_target1
        self.spot_target2 = spot_target2
        self.spot_target3 = spot_target3

    def calculate(self):
        if self.trade_type == 'RANGE':
            if self.option_type == 'CE':
                return self.calculate_ce()
            elif self.option_type == 'PE':
                return self.calculate_pe()
        elif self.trade_type == 'BREAKOUT':
            if self.option_type == 'CE' and self.proximal_line > self.spot:
                return self.calculate_breakout_ce()
            elif self.option_type == 'PE' and self.proximal_line < self.spot:
                return self.calculate_breakout_pe()
            else:
                return {"error": "Invalid proximal line for breakout trade."}
        else:
            return {"error": "Invalid trade type or option type."}

    def calculate_ce(self):
        if self.proximal_line < self.spot and self.distil_line < self.proximal_line and self.spot < self.spot_target1 < self.spot_target2 < self.spot_target3:
            differential_value = (self.spot - self.proximal_line) * self.delta
            entry = self.cmp - differential_value
            stop_loss = entry - ((self.proximal_line - self.distil_line) * self.delta)
            # updated_delta = self.delta - (self.gamma * (self.spot - self.proximal_line))
            updated_delta = self.delta
            strike_target1 = ((self.spot_target1 - self.proximal_line) * updated_delta) + entry
            strike_target2 = ((self.spot_target2 - self.proximal_line) * updated_delta) + entry
            strike_target3 = ((self.spot_target3 - self.proximal_line) * updated_delta) + entry
            return {"entry": entry, "stop_loss": stop_loss, "strike_target1": strike_target1, "strike_target2": strike_target2, "strike_target3": strike_target3}
        else:
            return {"error": f"Invalid input: For CE, proximal line ({self.proximal_line}) should be below spot ({self.spot}), distil line ({self.distil_line}) should be below proximal line ({self.proximal_line}), and spot targets should be in ascending order and greater than spot."}

    def calculate_pe(self):
        if self.proximal_line > self.spot and self.distil_line > self.proximal_line and self.proximal_line > self.spot_target1 > self.spot_target2 > self.spot_target3:
            differential_value = (self.proximal_line - self.spot) * self.delta
            entry = self.cmp - differential_value
            stop_loss = entry - ((self.distil_line - self.proximal_line) * self.delta)
            strike_target1 = ((self.proximal_line - self.spot_target1) * self.delta) + entry
            strike_target2 = ((self.proximal_line - self.spot_target2) * self.delta) + entry
            strike_target3 = ((self.proximal_line - self.spot_target3) * self.delta) + entry
            return {"entry": entry, "stop_loss": stop_loss, "strike_target1": strike_target1, "strike_target2": strike_target2, "strike_target3": strike_target3}
        else:
            return {"error": f"Invalid input: For PE, proximal line ({self.proximal_line}) should be above spot ({self.spot}), distil line ({self.distil_line}) should be above proximal line ({self.proximal_line}), and spot targets should be in descending order and less than spot."}
        
    def calculate_breakout_ce(self):
        differential_value = (self.proximal_line - self.spot) * self.delta
        entry = self.cmp + differential_value
        stop_loss = entry - ((self.proximal_line - self.distil_line) * self.delta)
        strike_target1 = ((self.spot_target1 - self.proximal_line) * self.delta) + entry
        strike_target2 = ((self.spot_target2 - self.proximal_line) * self.delta) + entry
        strike_target3 = ((self.spot_target3 - self.proximal_line) * self.delta) + entry
        return {"entry": entry, "stop_loss": stop_loss, "strike_target1": strike_target1, "strike_target2": strike_target2, "strike_target3": strike_target3}

    def calculate_breakout_pe(self):
        differential_value = (self.spot - self.proximal_line) * self.delta
        entry = self.cmp + differential_value
        stop_loss = entry - ((self.distil_line - self.proximal_line) * self.delta)
        strike_target1 = ((self.proximal_line - self.spot_target1) * self.delta) + entry
        strike_target2 = ((self.proximal_line - self.spot_target2) * self.delta) + entry
        strike_target3 = ((self.proximal_line - self.spot_target3) * self.delta) + entry
        return {"entry": entry, "stop_loss": stop_loss, "strike_target1": strike_target1, "strike_target2": strike_target2, "strike_target3": strike_target3}


template = """
instrument: {instrument}
spot: {spot}
strike: {strike}
cmp: {cmp}
proximal_line: {proximal_line}
distil_line: {distil_line}
delta: {delta}
gamma: {gamma}
option_type: {option_type}
trade_type: {trade_type}
spot_target1: {spot_target1}
spot_target2: {spot_target2}
spot_target3: {spot_target3}
itm: {itm}
"""

# # Example usage:
# params = template.format(
#     instrument='banknifty',
#     spot=46040,
#     strike=45800,
#     cmp=535,
#     proximal_line=45920,
#     distil_line=45900,
#     delta=0.65,
#     gamma=.004,
#     option_type='CE',
#     trade_type='Range',
#     spot_target1=46100,
#     spot_target2=46200,
#     spot_target3=46300,
#     itm=-2
# )


# Example usage:
params = template.format(
    instrument='banknifty',
    spot=52590,
    strike=45800,
    cmp=465,
    proximal_line=52765,
    distil_line=52805,
    delta=0.64,
    gamma=.004,
    option_type='PE',
    trade_type='Range',
    spot_target1=52580,
    spot_target2=52579,
    spot_target3=52578,
    itm=-2
)

# Parse the parameters:
params = {line.split(": ")[0]: line.split(": ")[1] for line in params.split("\n") if line}

# Convert the necessary parameters to float:
for param in ['cmp', 'spot', 'proximal_line', 'distil_line', 'delta', 'gamma', 'spot_target1', 'spot_target2', 'spot_target3']:
    params[param] = float(params[param])

# Calculate the step:
step = 50 if params['instrument'] in ['nifty', 'finnifty', 'bajfinance'] else 100 if params['instrument'] == 'banknifty' else 25

# Calculate the ATM strike:
atm_strike = round(params['proximal_line'] / step) * step

# Adjust the strike based on the ITM and option type:
if params['option_type'] == 'CE':
    params['strike'] = atm_strike + step * int(params['itm'])
elif params['option_type'] == 'PE':
    params['strike'] = atm_strike - step * int(params['itm'])

# Now you can pass the parameters to the OptionCalculator:
# option_calculator = OptionCalculator(**params)
# result = option_calculator.calculate()
# print(result)

# # CE Range Usage
# option_calculator = OptionCalculator(spot=46040, strike=45800, cmp=535, proximal_line=45920, distil_line=45900, delta=0.65, gamma=.004, option_type='CE', trade_type='Range', spot_target1=46100,spot_target2= 46200,spot_target3= 46300)
# result = option_calculator.calculate()
# if "error" in result:
#     print(result["error"])
# else:
#     print("Range CE Entry: ", result["entry"])
#     print("Range CE Stop Loss: ", result["stop_loss"])
#     print("Range CE Strike Target 1: ", result["strike_target1"])
#     print("Range Ce Strike Target 2: ", result["strike_target2"])
#     print("Range CE Strike Target 3: ", result["strike_target3"])
#     print("\n")


# # PE Range Usage
# option_calculator = OptionCalculator(spot=46040, strike=45600, cmp=535, proximal_line=46230, distil_line=46250, delta=0.65, gamma=.004, option_type='PE', trade_type='Range', spot_target1=46130,spot_target2= 46100,spot_target3=46050)
# result = option_calculator.calculate()
# if "error" in result:
#     print(result["error"])
# else:
#     print("Range PE Entry: ", result["entry"])
#     print("Range PE Stop Loss: ", result["stop_loss"])
#     print("Range PE Strike Target 1: ", result["strike_target1"])
#     print("Range PE Strike Target 2: ", result["strike_target2"])
#     print("Range PE Strike Target 3: ", result["strike_target3"])
#     print("\n")


# # Breakout CE Usage
# option_calculator = OptionCalculator(spot=46040, strike=45600, cmp=535, proximal_line=46100, distil_line=46080, delta=0.65, gamma=.004, option_type='CE', trade_type='Breakout', spot_target1=46200,spot_target2= 46300,spot_target3=46400)
# result = option_calculator.calculate()
# if "error" in result:
#     print(result["error"])
# else:
#     print("CE Entry: ", result["entry"])
#     print("CE Stop Loss: ", result["stop_loss"])
#     print("CE Strike Target 1: ", result["strike_target1"])
#     print("CE Strike Target 2: ", result["strike_target2"])
#     print("CE Strike Target 3: ", result["strike_target3"])
#     print("\n")


# # Breakout PE Usage
# option_calculator = OptionCalculator(spot=46020, strike=45600, cmp=535, proximal_line=45920, distil_line=45900, delta=0.65, gamma=.004, option_type='PE', trade_type='Breakout', spot_target1=45820,spot_target2= 45720,spot_target3=45620)
# result = option_calculator.calculate()
# if "error" in result:
#     print(result["error"])
# else:
#     print("PE Entry: ", result["entry"])
#     print("PE Stop Loss: ", result["stop_loss"])
#     print("PE Strike Target 1: ", result["strike_target1"])
#     print("PE Strike Target 2: ", result["strike_target2"])
#     print("PE Strike Target 3: ", result["strike_target3"])
#     print("\n")