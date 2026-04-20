import sys
sys.path.append("backend")
from app import scrape_chartink

url1 = "https://chartink.com/screener/15-minute-stock-breakouts"
url2 = "https://chartink.com/screener/volume-shockers"

print("Scraping URL 1...")
list1, err1 = scrape_chartink(url1, max_pages=1)
print(f"URL 1 Return: {len(list1)} items. Err: {err1}")
if list1: print(f"Sample: {list1[:5]}")

print("\nScraping URL 2...")
list2, err2 = scrape_chartink(url2, max_pages=1)
print(f"URL 2 Return: {len(list2)} items. Err: {err2}")
if list2: print(f"Sample: {list2[:5]}")
