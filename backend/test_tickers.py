import yfinance as yf

tickers = {
    "Dow": "^DJI",
    "Nasdaq": "^IXIC",
    "S&P 500": "^GSPC",
    "Dow Futures": "YM=F",
    "Nasdaq Futures": "NQ=F",
    "S&P Futures": "ES=F",
    "DAX Futures": "DAX=F",
    "Gift Nifty": "IN1!", # Some sources use this for SGX/Gift Nifty on yfinance
    "Crude Oil": "CL=F",
    "Silver": "SI=F",
    "Gold": "GC=F",
    "USDINR": "USDINR=X",
    "India VIX": "^INDIAVIX",
    "US VIX": "^VIX",
    "Nikkei 225": "^N225",
    "Hang Seng": "^HSI"
}

for name, ticker in tickers.items():
    try:
        t = yf.Ticker(ticker)
        data = t.history(period="1d")
        if not data.empty:
            print(f"{name} ({ticker}): {data['Close'].iloc[-1]}")
        else:
            print(f"{name} ({ticker}): NO DATA")
    except Exception as e:
        print(f"{name} ({ticker}): ERROR {e}")
