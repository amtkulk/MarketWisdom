from curl_cffi import requests
import time

def test_long_wait():
    session = requests.Session(impersonate="chrome120")
    print("1. Hitting home page...")
    session.get("https://www.nseindia.com/", timeout=10)
    time.sleep(2)
    
    print("2. Hitting option chain page...")
    session.get("https://www.nseindia.com/option-chain", timeout=10)
    print("Waiting 5 seconds for NSE to 'bake' the cookies...")
    time.sleep(5)
    
    print("3. Hitting API...")
    session.headers.update({
        'Referer': 'https://www.nseindia.com/option-chain',
        'X-Requested-With': 'XMLHttpRequest',
    })
    r = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
    print(f"API status: {r.status_code}")
    print(f"API Response: {r.text[:200]}")

if __name__ == "__main__":
    test_long_wait()
