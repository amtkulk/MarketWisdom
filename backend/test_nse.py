import json
import urllib.request
import yfinance as yf

# 1. Test yfinance PE
def get_pe(ticker):
    try:
        t = yf.Ticker(ticker + ".NS")
        info = t.info
        return info.get("trailingPE") or info.get("forwardPE")
    except Exception as e:
        return str(e)

print("PE for RELIANCE:", get_pe("RELIANCE"))

# 2. Test NSE API for block deals
def get_nse_data(endpoint):
    session_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/"
    }
    try:
        req0 = urllib.request.Request("https://www.nseindia.com", headers=session_headers)
        with urllib.request.urlopen(req0, timeout=3) as r:
            raw_cookies = r.headers.get_all("Set-Cookie") or []
            cookie_str  = "; ".join([c.split(";")[0] for c in raw_cookies])

        session_headers["Cookie"] = cookie_str
        req = urllib.request.Request(
            f"https://www.nseindia.com{endpoint}",
            headers=session_headers
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return str(e)

print("NSE Block Deals:", get_nse_data("/api/historical/block-deals"))
