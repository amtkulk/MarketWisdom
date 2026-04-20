import sys
sys.path.append("backend")
import json
from playwright.sync_api import sync_playwright

def get_nse_data_playwright(endpoint):
    url = f"https://www.nseindia.com{endpoint}"
    print("Fetching via playwright:", url)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120")
            # Navigate to nseindia first to get cookies
            page.goto("https://www.nseindia.com/option-chain", timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            
            # Fetch the API endpoint using page.evaluate to ensure cookies are sent
            json_data = page.evaluate("""(url) => {
                return fetch(url, {
                    headers: {
                        'accept': 'application/json, text/plain, */*',
                        'accept-language': 'en-US,en;q=0.9',
                    }
                }).then(r => r.json()).catch(e => null);
            }""", url)
            
            browser.close()
            return json_data
    except Exception as e:
        print("Playwright error:", e)
        return None

data = get_nse_data_playwright("/api/option-chain-indices?symbol=NIFTY")
if data: 
    records = data.get("records", {})
    print("Success, records:", len(records.get("data",[])))
    print("Expiry dates:", records.get("expiryDates", [])[:2])
else:
    print("Failed to get data.")
