from curl_cffi import requests
import time

def get_option_chain(symbol="NIFTY"):
    session = requests.Session(impersonate="chrome110")
    session.headers.update({
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    
    try:
        # Get cookies
        session.get("https://www.nseindia.com", timeout=10)
        time.sleep(0.5)
        
        # Get data
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        r = session.get(url, timeout=10)
        
        if r.status_code != 200:
            return {"error": f"NSE API returned {r.status_code}"}
            
        data = r.json()
        records = data.get("records", {})
        if not records:
            return {"error": "Invalid data format from NSE"}
            
        underlying_val = records.get("underlyingValue", 0)
        timestamp = records.get("timestamp", "")
        data_list = records.get("data", [])
        
        strikes = sorted(list(set([d["strikePrice"] for d in data_list])))
        max_pain_strike = 0
        min_loss = float('inf')
        
        total_call_oi = sum(d.get("CE", {}).get("openInterest", 0) for d in data_list)
        total_put_oi = sum(d.get("PE", {}).get("openInterest", 0) for d in data_list)
        
        pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0
        
        highest_call_oi = 0
        highest_call_strike = 0
        highest_put_oi = 0
        highest_put_strike = 0
        
        atm_strike = min(strikes, key=lambda x: abs(x - underlying_val)) if strikes else 0
        
        for d in data_list:
            ce_oi = d.get("CE", {}).get("openInterest", 0)
            pe_oi = d.get("PE", {}).get("openInterest", 0)
            strike = d["strikePrice"]
            
            if ce_oi > highest_call_oi:
                highest_call_oi = ce_oi
                highest_call_strike = strike
                
            if pe_oi > highest_put_oi:
                highest_put_oi = pe_oi
                highest_put_strike = strike
                
        # Fast Max Pain (±15 strikes from ATM)
        if strikes and atm_strike:
            idx = strikes.index(atm_strike)
            test_strikes = strikes[max(0, idx-15):min(len(strikes), idx+16)]
            
            for test_strike in test_strikes:
                total_loss = 0
                for d in data_list:
                    s = d["strikePrice"]
                    ce_oi = d.get("CE", {}).get("openInterest", 0)
                    pe_oi = d.get("PE", {}).get("openInterest", 0)
                    
                    if s < test_strike:
                        total_loss += ce_oi * (test_strike - s)
                    if s > test_strike:
                        total_loss += pe_oi * (s - test_strike)
                
                if total_loss < min_loss:
                    min_loss = total_loss
                    max_pain_strike = test_strike

        # Format chain for frontend (only ATM ± 10 strikes)
        filtered_chain = []
        if atm_strike:
            idx = strikes.index(atm_strike)
            show_strikes = set(strikes[max(0, idx-10):min(len(strikes), idx+11)])
            for d in data_list:
                if d["strikePrice"] in show_strikes:
                    filtered_chain.append({
                        "strike": d["strikePrice"],
                        "ce_oi": d.get("CE", {}).get("openInterest", 0),
                        "ce_price": d.get("CE", {}).get("lastPrice", 0),
                        "pe_oi": d.get("PE", {}).get("openInterest", 0),
                        "pe_price": d.get("PE", {}).get("lastPrice", 0),
                    })
                    
        return {
            "symbol": symbol,
            "timestamp": timestamp,
            "underlying": underlying_val,
            "pcr": round(pcr, 2),
            "max_pain": max_pain_strike,
            "resistance_strike": highest_call_strike,
            "support_strike": highest_put_strike,
            "chain": sorted(filtered_chain, key=lambda x: x["strike"])
        }
    except Exception as e:
        return {"error": str(e)}
