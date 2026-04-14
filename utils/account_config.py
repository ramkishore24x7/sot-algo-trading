# from utils.demat import Demat

class AccountConfig():
    def __init__(self,name,client_id,secret_key, access_token,quantity_nifty=None,quantity_midcpnifty=None,quantity_banknifty=None,quantity_finnifty=None,quantity_bajfinance=None,quantity_sensex=None,paper_trade=True,should_average=False,squareoff_at_first_target=True,trade_banknifty=True,trade_nifty=True,trade_midcpnifty=True,trade_finnifty=True,trade_bajfinance=True,trade_sensex=True,await_next_target=False,aggressive_trail=False,lazy_trail=False,config_type="default") -> None:
        self.name = name
        self.client_id = client_id
        self.secret_key = secret_key
        self.access_token = access_token
        self.quantity_nifty = quantity_nifty
        self.quantity_midcpnifty = quantity_midcpnifty
        self.quantity_banknifty = quantity_banknifty
        self.quantity_finnifty = quantity_finnifty
        self.quantity_bajfinance = quantity_bajfinance
        self.quantity_sensex = quantity_sensex
        # self.trade_banknifty = trade_banknifty if self.quantity_banknifty is None else False
        # self.trade_nifty = trade_nifty if self.quantity_nifty is None else False
        # self.trade_finnifty = trade_finnifty if self.quantity_finnifty is None else False
        # self.trade_bajfinance = trade_bajfinance  if self.quantity_bajfinance is None else False
        self.trade_banknifty = trade_banknifty
        self.trade_nifty = trade_nifty
        self.trade_midcpnifty = trade_midcpnifty
        self.trade_finnifty = trade_finnifty
        self.trade_bajfinance = trade_bajfinance
        self.trade_sensex = trade_sensex
        self.should_average = should_average
        self.paper_trade = paper_trade
        self.squareoff_at_first_target = squareoff_at_first_target
        self.await_next_target = await_next_target
        self.aggressive_trail = aggressive_trail
        self.lazy_trail = lazy_trail
        if self.lazy_trail:
            # print(f"{self.name}: Lazy Trail True: Updating await_next and square-off at target1 to False!")
            self.await_next_target = False
            self.squareoff_at_first_target = False
        if self.squareoff_at_first_target and self.await_next_target:
            raise Exception(f"{self.name}:Square off at first target and await next target cannot be true at the same time.")
        self.config_type = config_type