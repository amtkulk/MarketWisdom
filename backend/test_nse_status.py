from curl_cffi import requests
import time

def test_status():
    session = requests.Session(impersonate="chrome120")
    print("Hitting marketStatus...")
    r = session.get("https://www.nseindia.com/api/marketStatus", timeout=10)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text}")

if __name__ == "__main__":
    test_status()
