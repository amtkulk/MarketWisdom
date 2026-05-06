from curl_cffi import requests
import time

def test_nifty_equity():
    session = requests.Session(impersonate="chrome120")
    session.get("https://www.nseindia.com/", timeout=10)
    time.sleep(1)
    
    # Try the equity URL for Nifty (sometimes it works)
    url = "https://www.nseindia.com/api/option-chain-equities?symbol=NIFTY"
    session.headers.update({
        'Referer': 'https://www.nseindia.com/option-chain',
        'X-Requested-With': 'XMLHttpRequest',
    })
    r = session.get(url, timeout=10)
    print(f"Equity API for NIFTY status: {r.status_code}")
    print(f"Response: {r.text[:200]}")

if __name__ == "__main__":
    test_nifty_equity()
