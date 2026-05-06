from curl_cffi import requests
import time

def test_quote_derivative():
    session = requests.Session(impersonate="chrome120")
    session.get("https://www.nseindia.com/", timeout=10)
    
    print("Hitting quote-derivative...")
    r = session.get("https://www.nseindia.com/api/quote-derivative?symbol=NIFTY", timeout=10)
    print(f"Status: {r.status_code}")
    print(f"Cookies: {session.cookies.get_dict().keys()}")
    
    time.sleep(1)
    session.headers.update({'Referer': 'https://www.nseindia.com/option-chain'})
    r_api = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
    print(f"API Response: {r_api.text[:200]}")

if __name__ == "__main__":
    test_quote_derivative()
