from curl_cffi import requests

def fetch_nse_cffi():
    session = requests.Session(impersonate="chrome110")
    
    print("Getting base...")
    r1 = session.get("https://www.nseindia.com", timeout=10)
    print("Base status:", r1.status_code)
    
    import time
    time.sleep(1)
    
    print("Getting chain...")
    url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
    r2 = session.get(url, timeout=10)
    print("Chain status:", r2.status_code)
    
    if r2.status_code == 200:
        data = r2.json()
        print("Success! Underlying:", data.get('records', {}).get('underlyingValue'))
    else:
        print(r2.text[:200])

fetch_nse_cffi()
