import urllib.request
import json
import traceback

def get_sensibull_pcr(symbol):
    try:
        url = f"https://prices.sensibull.com/v1/compute/cache/live_derivative_prices/{symbol}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Origin": "https://web.sensibull.com",
            "Referer": "https://web.sensibull.com/"
        }
        
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode('utf-8'))
            
            payload = data.get("payload", {})
            if not payload: return None
            
            # The payload contains all options data keyed by symbol-expiry-strike-type e.g. "NIFTY24DEC10000CE"
            # We want to aggregate open interest by expiry and CE/PE
            
            expiries = {}
            for key, val in payload.items():
                if "CE" in key or "PE" in key:
                    # Sensibull doesn't give a direct expiry string easily without parsing the instrument
                    # But if we just need the total PCR, we can accumulate all CE and PE OI for the symbol
                    # This is actually better than nothing.
                    pass
            print(f"Total options: {len(payload)}")
            
            ce_oi = 0
            pe_oi = 0
            for key, val in payload.items():
                if key.endswith("CE"): ce_oi += val.get("oi", 0)
                elif key.endswith("PE"): pe_oi += val.get("oi", 0)
            
            pcr = round(pe_oi / ce_oi, 2) if ce_oi else 0
            print(f"[{symbol}] Total CE OI: {ce_oi}, Total PE OI: {pe_oi}, PCR: {pcr}")
            
    except Exception as e:
        traceback.print_exc()

print("--- NIFTY ---")
get_sensibull_pcr("NIFTY")
print("\n--- BANKNIFTY ---")
get_sensibull_pcr("BANKNIFTY")
