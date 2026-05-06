from curl_cffi import requests
import time

def test_raw_v5():
    session = requests.Session(impersonate="chrome120")
    
    print("1. Hitting home page...")
    session.get("https://www.nseindia.com/", timeout=10)
    time.sleep(1)
    
    print("2. Hitting option chain page...")
    session.headers.update({
        'Referer': 'https://www.nseindia.com/',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    })
    session.get("https://www.nseindia.com/option-chain", timeout=10)
    time.sleep(2)
    
    print("3. Hitting API...")
    session.headers.update({
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://www.nseindia.com/option-chain',
        'X-Requested-With': 'XMLHttpRequest',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
    })
    r = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
    print(f"API status: {r.status_code}")
    print(f"API Response: {r.text[:200]}")

if __name__ == "__main__":
    test_raw_v5()
