import urllib.request
import json

url = "https://webapi.niftytrader.in/webapi/option/option-chain-data?symbol=nifty"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req) as r:
    data = json.loads(r.read().decode('utf-8'))
    op_totals = data.get("resultData", {}).get("opTotals", {})
    print("KEYS:", list(op_totals.keys()))
    print("itm_total_calls:", op_totals.get("itm_total_calls"))
