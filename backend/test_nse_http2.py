from curl_cffi import requests
import time

def test_http2():
    session = requests.Session(impersonate="chrome120")
    print("1. Hitting home page...")
    session.get("https://www.nseindia.com/", timeout=10)
    time.sleep(1)
    
    print("2. Hitting API with HTTP/2...")
    session.headers.update({
        'Referer': 'https://www.nseindia.com/option-chain',
        'X-Requested-With': 'XMLHttpRequest',
    })
    r = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
    print(f"API status: {r.status_code}")
    print(f"API Response: {r.text[:200]}")

if __name__ == "__main__":
    test_http2()
