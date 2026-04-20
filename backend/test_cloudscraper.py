import json
try:
    import cloudscraper
    scraper = cloudscraper.create_scraper()
    res = scraper.get("https://www.nseindia.com/api/fii-stats?type=equity", timeout=15)
    print("Status:", res.status_code)
    try:
        print(res.json())
    except Exception as je:
        print("JSON Error:", je)
except ImportError:
    print("cloudscraper not installed.")
