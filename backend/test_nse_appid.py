from curl_cffi import requests
import time

def test_nseappid():
    session = requests.Session(impersonate="chrome120")
    print("1. Hitting home page...")
    session.get("https://www.nseindia.com/", timeout=10)
    
    print("2. Hitting item-description API (to get nseappid?)...")
    # This API is often used by the frontend to get basic info
    r_item = session.get("https://www.nseindia.com/api/common/item-description?symbol=NIFTY", timeout=10)
    print(f"Item Description status: {r_item.status_code}")
    print(f"Cookies: {session.cookies.get_dict().keys()}")
    
    time.sleep(1)
    
    print("3. Hitting API...")
    session.headers.update({
        'Referer': 'https://www.nseindia.com/option-chain',
        'X-Requested-With': 'XMLHttpRequest',
    })
    r = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
    print(f"API status: {r.status_code}")
    print(f"API Response: {r.text[:200]}")

if __name__ == "__main__":
    test_nseappid()
