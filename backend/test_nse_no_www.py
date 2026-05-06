from curl_cffi import requests
import time

def test_no_www():
    session = requests.Session(impersonate="chrome120")
    session.get("https://www.nseindia.com/", timeout=10)
    time.sleep(1)
    
    # Try without www (sometimes it helps or bypasses cache)
    url = "https://nseindia.com/api/option-chain-indices?symbol=NIFTY"
    session.headers.update({
        'Referer': 'https://www.nseindia.com/option-chain',
        'X-Requested-With': 'XMLHttpRequest',
    })
    r = session.get(url, timeout=10)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:200]}")

if __name__ == "__main__":
    test_no_www()
