from playwright.sync_api import sync_playwright
import time

url = "https://chartink.com/screener/15-minute-stock-breakouts"

def test_click_run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=30000)
        
        # Click "Run Scan"
        try:
            # Try multiple selectors that might match
            locators_to_try = [
                "button:has-text('Run Scan')", 
                "text='Run Scan'", 
                ".btn-primary:has-text('Run Scan')",
                "a:has-text('Run Scan')"
            ]
            
            clicked = False
            for loc in locators_to_try:
                btn = page.locator(loc)
                if btn.count() > 0:
                    print(f"Found Run Scan button using locator: '{loc}'")
                    btn.first.click()
                    time.sleep(5)
                    rows2 = page.locator("table.scan-results-table tbody tr").count()
                    print(f"Rows after clicking Run Scan: {rows2}")
                    clicked = True
                    break
                    
            if not clicked:
                print("Run Scan button not found.")
        except Exception as e:
            print("Error clicking:", e)
            
        browser.close()

test_click_run()
