"""Put/Call ratio from NSE's official end-of-day F&O file ("UDiFF bhavcopy").

The live option-chain APIs (NSE's www site, niftytrader) refuse our cloud server, which
left the Nifty Analysis PCR cards permanently empty. NSE publishes every strike's closing
open interest each trading evening (~6 pm IST) on nsearchives.nseindia.com — the same
archive host our server already reads corporate filings from — so we compute PCR from it:

    PCR = total put open interest ÷ total call open interest   (per expiry)

It's end-of-day: during market hours it shows the previous session's close.
"""
import csv
import datetime as dt
import io
import threading
import time
import zipfile

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip"
SYMBOLS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")
STORE_KEY = "fo_bhav_pcr"
RECHECK_SECS = 20 * 60          # look for a newer file at most this often
PUBLISH_HOUR = 17               # file normally appears ~17:30–19:00 IST
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

_lock = threading.Lock()
_mem = {"data": None, "checked": 0.0}


def now_ist():
    return dt.datetime.now(IST)


def _parse_date(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_bhavcopy(raw_zip):
    """zip bytes → {'trade_date': iso, 'symbols': {SYM: {expiry_iso: {'ce','pe','ce_chg','pe_chg'}}}, 'spot': {SYM: px}}."""
    zf = zipfile.ZipFile(io.BytesIO(raw_zip))
    name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
    text = io.TextIOWrapper(zf.open(name), encoding="utf-8-sig", newline="")
    rd = csv.DictReader(text)
    out, spot, tdate = {}, {}, None
    for r in rd:
        sym = (r.get("TckrSymb") or "").strip().upper()
        if sym not in SYMBOLS:
            continue
        ot = (r.get("OptnTp") or "").strip().upper()
        if ot not in ("CE", "PE"):
            continue
        exp = _parse_date(r.get("XpryDt") or r.get("FininstrmActlXpryDt"))
        if not exp:
            continue
        tdate = tdate or _parse_date(r.get("TradDt") or r.get("BizDt"))
        try:
            oi = float(r.get("OpnIntrst") or 0)
            chg = float(r.get("ChngInOpnIntrst") or 0)
        except ValueError:
            continue
        a = out.setdefault(sym, {}).setdefault(exp.isoformat(), {"ce": 0.0, "pe": 0.0, "ce_chg": 0.0, "pe_chg": 0.0})
        k = "ce" if ot == "CE" else "pe"
        a[k] += oi
        a[k + "_chg"] += chg
        try:
            px = float(r.get("UndrlygPric") or 0)
            if px > 0:
                spot[sym] = px
        except ValueError:
            pass
    if not out:
        raise ValueError("no index options found in the file")
    return {"trade_date": tdate.isoformat() if tdate else None, "symbols": out, "spot": spot}


def _candidate_dates(now=None):
    """Trading-day guesses, newest first (today only once it's likely published)."""
    now = now or now_ist()
    d = now.date() if now.hour >= PUBLISH_HOUR else now.date() - dt.timedelta(days=1)
    out = []
    while len(out) < 8:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return out


def _download(d):
    from curl_cffi import requests as cffi
    r = cffi.get(URL.format(ymd=d.strftime("%Y%m%d")), headers={"User-Agent": UA, "Accept": "*/*"},
                 impersonate="chrome120", timeout=40)
    if r.status_code == 200 and r.content[:2] == b"PK":
        return r.content
    return None                                   # 404 / holiday / not published yet


def _load_saved():
    try:
        from database import get_screener_results
        data, _ = get_screener_results(STORE_KEY)
        return data if isinstance(data, dict) and data.get("symbols") else None
    except Exception:
        return None


def _save(data):
    try:
        from database import save_screener_results
        save_screener_results(STORE_KEY, data)
    except Exception as e:
        print(f"[FO bhav] save failed: {e}")


def latest(download=None, now=None):
    """Newest parsed bhavcopy (memory → DB → NSE). Never raises; returns None if nothing."""
    download = download or _download
    with _lock:
        data = _mem["data"] or _load_saved()
        _mem["data"] = data
        newest_possible = _candidate_dates(now)[0].isoformat()
        fresh = data and data.get("trade_date") == newest_possible
        if fresh or time.time() - _mem["checked"] < RECHECK_SECS:
            return data
        _mem["checked"] = time.time()
        have = (data or {}).get("trade_date") or ""
        for d in _candidate_dates(now):
            if d.isoformat() <= have:
                break                                # nothing newer than what we hold
            try:
                raw = download(d)
            except Exception as e:
                print(f"[FO bhav] {d}: {e}")
                continue
            if not raw:
                continue
            try:
                parsed = parse_bhavcopy(raw)
            except Exception as e:
                print(f"[FO bhav] parse {d}: {e}")
                continue
            parsed["trade_date"] = parsed.get("trade_date") or d.isoformat()
            parsed["fetched_at"] = now_ist().strftime("%d %b %Y %H:%M IST")
            _mem["data"] = parsed
            _save(parsed)
            return parsed
        return data


def pick_expiries(expiries, today=None):
    """(weekly, monthly) from expiry ISO dates: nearest live expiry, and the last expiry in
    that expiry's month (the monthly contract). For indices with only monthly options
    (BANKNIFTY since Nov 2024) both are the same contract."""
    today = (today or now_ist().date()).isoformat()
    live = sorted(e for e in expiries if e >= today) or sorted(expiries)
    if not live:
        return None, None
    wk = live[0]
    monthly = max(e for e in live if e[:7] == wk[:7])
    return wk, monthly


def pcr_from_bhav(symbol, data, today=None):
    """Same shape as app.fetch_pcr_data's result, from the end-of-day file."""
    exps = (data or {}).get("symbols", {}).get(symbol.upper())
    if not exps:
        return None
    # expiries still running today (a Tuesday-evening file read on Wednesday skips Tuesday's expiry)
    wk, mo = pick_expiries(list(exps), today)
    def fmt(e):
        a = exps[e]
        ce, pe = a["ce"], a["pe"]
        pcr = round(pe / ce, 2) if ce > 0 else 0
        return {"expiry": dt.date.fromisoformat(e).strftime("%d-%b-%Y"), "ce_oi": int(ce), "pe_oi": int(pe),
                "ce_oi_chg": int(a["ce_chg"]), "pe_oi_chg": int(a["pe_chg"]), "pcr": pcr,
                "signal": "Bullish" if pcr > 1.2 else "Bearish" if pcr < 0.8 else "Neutral"}
    return {"weekly": fmt(wk), "monthly": fmt(mo), "symbol": symbol.upper(), "source": "nse_eod",
            "as_of": data.get("trade_date"), "same_contract": wk == mo,
            "spot": (data.get("spot") or {}).get(symbol.upper())}
