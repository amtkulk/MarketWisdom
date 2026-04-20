from playwright.sync_api import sync_playwright

url = "https://chartink.com/screener/15-minute-stock-breakouts"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(url, timeout=30000)
    
    # Wait a bit for page load
    page.wait_for_timeout(3000)
    
    # Dump all buttons
    buttons = page.locator("button, a.btn").all_inner_texts()
    print("All buttons on page:")
    for b in buttons:
        if b.strip():
            print(" -", b.strip())
            
    browser.close()
