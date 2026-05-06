from curl_cffi import requests
import time
import json

def debug_nse():
    session = requests.Session(impersonate="chrome110")
    session.get("https://www.nseindia.com", timeout=10)
    time.sleep(1)
    
    url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
    r = session.get(url, timeout=10)
    print("Status:", r.status_code)
    try:
        data = r.json()
        print("Keys found:", list(data.keys()))
        if 'records' in data:
            print("Records keys:", list(data['records'].keys()))
        else:
            print("Full Data:", data)
    except Exception as e:
        print("Not JSON:", r.text[:200])

debug_nse()
