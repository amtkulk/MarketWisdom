import requests

def test_simple():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    session = requests.Session()
    session.headers.update(headers)
    
    print("1. Hitting home page...")
    session.get("https://www.nseindia.com/", timeout=10)
    
    print("2. Hitting API...")
    session.headers.update({
        'Referer': 'https://www.nseindia.com/option-chain',
    })
    r = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
    print(f"API status: {r.status_code}")
    print(f"API Response: {r.text[:200]}")

if __name__ == "__main__":
    test_simple()
