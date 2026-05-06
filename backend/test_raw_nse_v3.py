import requests
import time

def test_raw_v3():
    headers = {
        'authority': 'www.nseindia.com',
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    
    session = requests.Session()
    session.headers.update(headers)
    
    print("1. Hitting home page...")
    session.get("https://www.nseindia.com/", timeout=10)
    
    print("2. Hitting option chain page...")
    session.get("https://www.nseindia.com/option-chain", timeout=10)
    time.sleep(1)
    
    print("3. Hitting API...")
    session.headers.update({
        'referer': 'https://www.nseindia.com/option-chain',
        'x-requested-with': 'XMLHttpRequest',
    })
    r = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
    print(f"API status: {r.status_code}")
    print(f"API Response: {r.text[:200]}")

if __name__ == "__main__":
    test_raw_v3()
