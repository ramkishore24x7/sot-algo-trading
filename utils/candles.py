class Candles:

    def isGreenCandle(candle):
        return candle["close"] > candle["open"]
    
    def isRedCandle(candle):
        return candle["close"] < candle["open"]
    
    # Sample Response from history API: [1682493480, 42740.65, 42740.65, 42717.1, 42717.1, 0]
    def isAlertRedDoji(candle):
        # open - close > high - low * 0.1
        proper_candle = (candle["open"] - candle["close"]) > (candle["high"] - candle["low"])*0.1
        if not proper_candle:
            print("Doji Found.")
        return not proper_candle

    def isAlertGreenDoji(candle):
        # close - open > high - low * 0.1
        proper_candle = (candle["close"] - candle["open"]) > (candle["high"] - candle["low"])*0.1
        if not proper_candle:
            print("Doji Found.")
        return not proper_candle

    def isCandleUpby(candle,difference=10):
        return candle["close"] - candle["open"] >= difference
    
    def isCandleDownBy(candle,difference=10):
        return candle["open"] - candle["close"] >= difference
