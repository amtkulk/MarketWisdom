import requests

def test_no_compression():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Encoding': 'identity',
    }
    session = requests.Session()
    session.headers.update(headers)
    session.get("https://www.nseindia.com/", timeout=10)
    
    session.headers.update({'Referer': 'https://www.nseindia.com/option-chain'})
    r = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=10)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:200]}")

if __name__ == "__main__":
    test_no_compression()
