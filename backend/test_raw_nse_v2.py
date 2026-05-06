from curl_cffi import requests
import time
import json

def test_raw():
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    session = requests.Session(impersonate="chrome120")
    
    base_headers = {
        'User-Agent': ua,
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
    }
    session.headers.update(base_headers)
    
    print("1. Hitting home page...")
    session.get("https://www.nseindia.com/", timeout=10)
    time.sleep(1)
    
    print("2. Hitting marketStatus...")
    session.headers.update({'Referer': 'https://www.nseindia.com/'})
    session.get("https://www.nseindia.com/api/marketStatus", timeout=10)
    time.sleep(1)
    
    print("3. Hitting allIndices...")
    session.get("https://www.nseindia.com/api/allIndices", timeout=10)
    time.sleep(1)
    
    print("4. Hitting API...")
    session.headers.update({
        'Referer': 'https://www.nseindia.com/option-chain',
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json, text/plain, */*',
    })
    r = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
    print(f"API status: {r.status_code}")
    print(f"API Response: {r.text[:200]}")

if __name__ == "__main__":
    test_raw()
