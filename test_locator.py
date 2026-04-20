from playwright.sync_api import sync_playwright

url = "https://chartink.com/screener/15-minute-stock-breakouts"

def test():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=30000)
        
        loc = page.locator("text='Run Scan'")
        count = loc.count()
        print("Count:", count)
        for i in range(count):
            el = loc.nth(i)
            print(f"Element {i} visible: {el.is_visible()}, tag: {el.evaluate('e => e.tagName')}")
            try:
                el.click(timeout=1000)
                print(f"Clicked {i} successfully")
            except Exception as e:
                print(f"Failed to click {i} - {str(e)[:50]}")
                
        browser.close()

test()
