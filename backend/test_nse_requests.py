import time
import requests

def get_nse_data_requests(endpoint):
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive"
    }
    session.headers.update(headers)
    
    try:
        # First request to get cookies
        response0 = session.get("https://www.nseindia.com", timeout=10)
        print("First request status:", response0.status_code)
        
        # Second request to actual API
        url = f"https://www.nseindia.com{endpoint}"
        response = session.get(url, timeout=15)
        print("Second request status:", response.status_code)
        
        if response.status_code == 200:
            return response.json()
        else:
            return f"Error: {response.status_code}"
    except Exception as e:
        return str(e)

print(get_nse_data_requests("/api/fii-stats?type=equity"))
