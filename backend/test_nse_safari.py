from curl_cffi import requests
import time

def test_safari():
    session = requests.Session(impersonate="safari15_5")
    session.get("https://www.nseindia.com/", timeout=10)
    time.sleep(1)
    
    session.headers.update({
        'Referer': 'https://www.nseindia.com/option-chain',
        'X-Requested-With': 'XMLHttpRequest',
    })
    r = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:200]}")

if __name__ == "__main__":
    test_safari()
