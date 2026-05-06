from curl_cffi import requests
import time

def test_equity():
    session = requests.Session(impersonate="chrome120")
    session.get("https://www.nseindia.com/", timeout=10)
    time.sleep(1)
    session.headers.update({'Referer': 'https://www.nseindia.com/'})
    session.get("https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE", timeout=10)
    time.sleep(1)
    session.headers.update({
        'Referer': 'https://www.nseindia.com/get-quotes/equity?symbol=RELIANCE',
        'X-Requested-With': 'XMLHttpRequest',
    })
    r = session.get("https://www.nseindia.com/api/option-chain-equities?symbol=RELIANCE", timeout=10)
    print(f"Equity API status: {r.status_code}")
    print(f"Equity API Response: {r.text[:200]}")

if __name__ == "__main__":
    test_equity()
