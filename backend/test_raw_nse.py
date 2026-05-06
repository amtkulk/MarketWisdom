from curl_cffi import requests
import time

def test_raw():
    # Use a fixed UA
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    session = requests.Session(impersonate="chrome120")
    session.headers.update({
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
    })
    
    print("Hitting home page...")
    r1 = session.get("https://www.nseindia.com/", timeout=10)
    print(f"Home page status: {r1.status_code}")
    
    time.sleep(1)
    
    print("Hitting allIndices API (to warm up session)...")
    session.headers.update({'Referer': 'https://www.nseindia.com/'})
    r_indices = session.get("https://www.nseindia.com/api/allIndices", timeout=10)
    print(f"allIndices status: {r_indices.status_code}")
    
    time.sleep(1)
    
    print("Hitting option chain landing page...")
    session.headers.update({'Referer': 'https://www.nseindia.com/'})
    r2 = session.get("https://www.nseindia.com/option-chain", timeout=10)
    print(f"Option chain page status: {r2.status_code}")
    
    time.sleep(1)
    
    print("Hitting API...")
    session.headers.update({
        'Referer': 'https://www.nseindia.com/option-chain',
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json, text/plain, */*',
    })
    r3 = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
    print(f"API status: {r3.status_code}")
    print(f"API Response: {r3.text[:200]}")
    
    if r3.text == "{}":
        print("Still getting empty data. Trying with cookies from option-chain page directly...")
        # Sometimes you need to wait longer
        time.sleep(2)
        r4 = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
        print(f"API Attempt 2 status: {r4.status_code}")
        print(f"API Attempt 2 Response: {r4.text[:200]}")

if __name__ == "__main__":
    test_raw()
