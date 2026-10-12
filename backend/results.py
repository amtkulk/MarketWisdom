"""Nifty50 Result Analyzer.

Last 4 quarters of results for every Nifty 50 company, side by side.

Sources
  • Quarterly figures ........ Screener.in company page (consolidated; banks whose
                                consolidated page has no NPA data use standalone,
                                which is what banks headline anyway)
  • Result dates ............. BSE result calendar (Corpforthresults); for results
                                already out, the first BSE "Result" filing after
                                quarter end
  • Dividends ................ BSE forthcoming corporate actions (upcoming ex-dates)
                                + Yahoo Finance dividend history (past ex-dates)
  • Nifty 50 list ............ NSE archives index CSV (fallback: built-in list)

The full refresh runs in a background thread (~2-3 min for 50 companies, polite
to Screener.in) and is saved in the database, so the page answers instantly from
the last saved copy while the refresh fills in.
"""
import csv
import datetime as dt
import io
import re
import threading
import time
import urllib.request

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
STORE_KEY = "nifty50_results"
STALE_SECS = 3 * 3600            # auto-refresh when the saved copy is older than this
MIN_REFRESH_GAP = 10 * 60        # a manual full refresh at most every 10 min
JOB_TIMEOUT = 20 * 60            # a "running" job older than this is treated as dead
SCREENER_GAP = 1.5               # seconds between Screener.in requests

BSE_API = "https://api.bseindia.com/BseIndiaAPI/api/"
_HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/",
}

# Used only if the NSE index CSV can't be fetched.
NIFTY50_FALLBACK = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO",
    "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL",
    "KOTAKBANK", "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA",
    "TATACONSUM", "TMPV", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT",
    "ULTRACEMCO", "WIPRO",
]

_lock = threading.RLock()
_mem = {"store": None, "loaded_at": 0}


# ══════════════════════════════════════════════════════════════
#  Quarter helpers
# ══════════════════════════════════════════════════════════════

_MON = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _today():
    return dt.datetime.now(IST).date()


def _month_end(y, m):
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)


def latest_quarter_end(today):
    """Most recent quarter end strictly before today (30 Sep counts from 1 Oct)."""
    d = today - dt.timedelta(days=1)
    ends = [dt.date(d.year - 1, 12, 31)] + [_month_end(d.year, m) for m in (3, 6, 9, 12)]
    return max(e for e in ends if e <= d)


def prev_quarter_end(qe, n=1):
    y, m = qe.year, qe.month
    for _ in range(n):
        m -= 3
        if m <= 0:
            m += 12
            y -= 1
    return _month_end(y, m)


def season_quarters(today=None):
    """The 4 quarter ends shown as columns (oldest → newest)."""
    last = latest_quarter_end(today or _today())
    return [prev_quarter_end(last, 3), prev_quarter_end(last, 2), prev_quarter_end(last, 1), last]


def q_label(qe):
    return qe.strftime("%b %Y")


def fy_label(qe):
    """Indian financial year label: Sep 2026 → 'Q2 FY27'."""
    q = {6: 1, 9: 2, 12: 3, 3: 4}[qe.month]
    fy = qe.year + 1 if qe.month > 3 else qe.year
    return f"Q{q} FY{fy % 100:02d}"


def sebi_deadline(qe):
    """SEBI LODR Reg. 33: 45 days after quarter end; 60 days for the March quarter."""
    return qe + dt.timedelta(days=60 if qe.month == 3 else 45)


def _parse_header_date(txt):
    m = re.match(r"\s*([A-Za-z]{3})[a-z]*\s+(\d{4})", txt or "")
    if not m or m.group(1).lower() not in _MON:
        return None
    return _month_end(int(m.group(2)), _MON[m.group(1).lower()])


def _parse_date(s):
    """BSE / misc date strings → date."""
    if not s:
        return None
    s = str(s).strip()
    if re.match(r"\d{4}-\d{2}-\d{2}T", s):
        s = s[:10]                                  # ISO timestamp → date part
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except Exception:
            continue
    m = re.search(r"(\d{1,2})\s*[-/ ]\s*([A-Za-z]{3})[a-z]*\s*[-/ ]\s*(\d{4})", s)
    if m and m.group(2).lower() in _MON:
        try:
            return dt.date(int(m.group(3)), _MON[m.group(2).lower()], int(m.group(1)))
        except Exception:
            return None
    return None


# ══════════════════════════════════════════════════════════════
#  Nifty 50 list
# ══════════════════════════════════════════════════════════════

def nifty50_list():
    """[{sym, name, industry}] from NSE archives; falls back to the built-in list."""
    try:
        req = urllib.request.Request(
            "https://archives.nseindia.com/content/indices/ind_nifty50list.csv",
            headers={"User-Agent": _HDRS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8", "replace")
        out = []
        for r in csv.DictReader(io.StringIO(text)):
            r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
            if r.get("Symbol"):
                out.append({"sym": r["Symbol"], "name": r.get("Company Name") or r["Symbol"],
                            "industry": r.get("Industry") or ""})
        if len(out) >= 45:
            return out, "nse"
    except Exception as e:
        print(f"[Results] Nifty 50 list fetch failed: {e}")
    return [{"sym": s, "name": s, "industry": ""} for s in NIFTY50_FALLBACK], "fallback"


# ══════════════════════════════════════════════════════════════
#  Screener.in quarterly table
# ══════════════════════════════════════════════════════════════

def _num(txt):
    if txt is None:
        return None
    t = str(txt).replace(",", "").replace("%", "").replace("−", "-").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    return float(m.group(0)) if m else None


def parse_quarters(html):
    """Screener.in company page → {name, bse_code, headers[], dates[], rows{label: [..]}, pdf[]}.
    Pure function (testable with saved HTML)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = {"rows": {}, "headers": [], "dates": [], "pdf": []}
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        out["name"] = " ".join(h1.get_text(" ", strip=True).split())[:80]
    head_txt = " ".join(soup.get_text(" ", strip=True).split())[:6000]
    m = re.search(r"BSE:\s*(\d{6})", head_txt)
    out["bse_code"] = m.group(1) if m else None
    sec = soup.select_one("section#quarters")
    tbl = sec.find("table") if sec else None
    if not tbl:
        return out
    for tr in tbl.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        label = re.sub(r"[\s+ ]+$", "", cells[0].get_text(" ", strip=True)).strip()
        label = re.sub(r"\s+", " ", label)
        if not label:
            if not out["headers"]:
                out["headers"] = [c.get_text(" ", strip=True) for c in cells[1:]]
            continue
        if label.lower().startswith("raw pdf"):
            out["pdf"] = [(c.find("a").get("href") if c.find("a") else None) for c in cells[1:]]
            continue
        out["rows"][label] = [_num(c.get_text(strip=True)) for c in cells[1:]]
    out["dates"] = [_parse_header_date(h) for h in out["headers"]]
    return out


def _pick(rows, *names):
    for nm in names:
        for k, v in rows.items():
            if k.lower() == nm.lower():
                return v
    for nm in names:
        for k, v in rows.items():
            if k.lower().startswith(nm.lower()):
                return v
    return None


def _has_values(series):
    return bool(series) and any(x is not None for x in series)


def _is_financing(page):
    return _pick(page.get("rows") or {}, "Financing Profit") is not None


def _screener_get(path):
    from curl_cffi import requests as cffi
    url = f"https://www.screener.in/company/{path}"
    hdr = {"User-Agent": _HDRS["User-Agent"]}
    for attempt in range(3):
        try:
            r = cffi.get(url, headers=hdr, impersonate="chrome120", timeout=12)
        except Exception:
            r = None
        if r is not None and r.status_code in (429, 503) and attempt < 2:
            time.sleep(4.0 * (attempt + 1))        # polite back-off
            continue
        break
    if r is None or r.status_code != 200:
        return None
    return r.content


def fetch_company_page(sym):
    """Consolidated page; standalone when consolidated is empty, or for lenders whose
    consolidated page carries no NPA data (banks headline standalone numbers)."""
    from urllib.parse import quote
    s = quote(sym, safe="-")
    cons = None
    html = _screener_get(f"{s}/consolidated/")
    if html:
        try:
            cons = parse_quarters(html)
            cons["basis"] = "Consolidated"
        except Exception as e:
            print(f"[Results] parse failed (cons) {sym}: {e}")
            cons = None
    need_standalone = (not cons or not _has_values(_pick(cons["rows"], "Net Profit"))
                       or (_is_financing(cons) and not _has_values(_pick(cons["rows"], "Gross NPA %"))))
    if not need_standalone:
        return cons
    time.sleep(SCREENER_GAP)
    html = _screener_get(f"{s}/")
    if html:
        try:
            st = parse_quarters(html)
            st["basis"] = "Standalone"
            if _has_values(_pick(st["rows"], "Net Profit")):
                if cons and _has_values(_pick(cons["rows"], "Net Profit")) and not _is_financing(st):
                    return cons
                return st
        except Exception as e:
            print(f"[Results] parse failed (standalone) {sym}: {e}")
    return cons if cons and _has_values(_pick(cons["rows"], "Net Profit")) else None


# ══════════════════════════════════════════════════════════════
#  BSE: result calendar, upcoming corporate actions, result filing date
# ══════════════════════════════════════════════════════════════

def _bse_json(path, params=None, timeout=12):
    from curl_cffi import requests as cffi
    r = cffi.get(BSE_API + path, params=params or {}, headers=_HDRS,
                 impersonate="chrome120", timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"BSE HTTP {r.status_code}")
    data = r.json()
    if isinstance(data, dict):                     # some BSE APIs wrap the list in {"Table": [...]}
        for k in ("Table", "table", "data"):
            if isinstance(data.get(k), list):
                return data[k]
    return data if isinstance(data, list) else []


def _key(d, *pats):
    for p in pats:
        for k in d.keys():
            if re.search(p, k, re.I):
                return d.get(k)
    return None


def fetch_result_calendar(qe, today):
    """{bse_code: [dates]} of result board meetings from BSE (quarter end → +90 days)."""
    out = {}
    rows = []
    try:
        rows = _bse_json("Corpforthresults/w", {
            "fromdate": (qe + dt.timedelta(days=1)).strftime("%Y%m%d"),
            "todate": (today + dt.timedelta(days=90)).strftime("%Y%m%d")})
    except Exception as e:
        print(f"[Results] BSE calendar (range) failed: {e}")
    if not rows:
        try:
            rows = _bse_json("Corpforthresults/w")             # all forthcoming
        except Exception as e:
            print(f"[Results] BSE calendar failed: {e}")
            return None
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = str(_key(r, r"^scrip_?code$", r"scrip") or "").strip()
        d = _parse_date(_key(r, r"meeting_?date", r"date"))
        if code and d and d > qe:
            out.setdefault(code, []).append(d)
    return out


def _parse_div_purpose(p):
    """'Interim Dividend - Rs. - 11.0000' → ('Interim', 11.0); None if not a dividend."""
    p = " ".join(str(p or "").split())
    if "dividend" not in p.lower():
        return None
    kind = "Final" if re.search(r"\bfinal\b", p, re.I) else \
           "Special" if re.search(r"\bspecial\b", p, re.I) else \
           "Interim" if re.search(r"\binterim\b", p, re.I) else "Dividend"
    m = re.search(r"(?:Rs\.?|₹|INR)\s*-?\s*(\d+(?:\.\d+)?)", p, re.I)
    amt = float(m.group(1)) if m else None
    return kind, amt


def fetch_upcoming_actions():
    """{bse_code: [{ex_date, amount, kind}]} — dividends with an upcoming ex-date."""
    try:
        rows = _bse_json("DefaultData/w", {"ddlcategorys": "E", "ddlindustrys": "",
                                          "segment": "0", "strSearch": "D"})
    except Exception as e:
        print(f"[Results] BSE corporate actions failed: {e}")
        return None
    out = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = str(_key(r, r"^scrip_?code$", r"scrip") or "").strip()
        dv = _parse_div_purpose(_key(r, r"purpose"))
        exd = _parse_date(_key(r, r"^ex_?date$", r"ex.?date"))
        if code and dv and exd:
            out.setdefault(code, []).append({"ex_date": exd.isoformat(), "amount": dv[1],
                                             "kind": dv[0], "src": "BSE"})
    return out


def fetch_result_filing_date(bse_code, qe, today):
    """Date of the first BSE 'Result' filing after quarter end (= results announced)."""
    try:
        from orders import BseClient, fetch_bse, normalize
    except Exception:
        return None
    c = BseClient(allow_browser=False)
    try:
        rows = fetch_bse(c, "Result", "-1", qe + dt.timedelta(days=1), today, max_pages=2, scrip=bse_code)
    except Exception as e:
        print(f"[Results] BSE result filings {bse_code} failed: {e}")
        return None
    finally:
        c.close()
    ds = []
    for r in rows:
        it = normalize(r)
        if it and it.get("date"):
            d = dt.date.fromisoformat(it["date"])
            if qe < d <= today:
                ds.append(d)
    return min(ds) if ds else None


def fetch_dividend_history(sym):
    """[{ex_date, amount}] from Yahoo Finance (last ~18 months)."""
    try:
        import yfinance as yf
        s = yf.Ticker(f"{sym}.BO" if sym.isdigit() else f"{sym}.NS").dividends
    except Exception as e:
        print(f"[Results] dividends {sym} failed: {e}")
        return []
    out = []
    cutoff = _today() - dt.timedelta(days=560)
    try:
        for ts, amt in s.items():
            d = ts.date() if hasattr(ts, "date") else _parse_date(ts)
            if d and d >= cutoff and amt:
                out.append({"ex_date": d.isoformat(), "amount": round(float(amt), 2),
                            "kind": None, "src": "Yahoo"})
    except Exception:
        pass
    return out


def dividend_quarter(ex_date, kind=None):
    """Which results quarter a dividend belongs to. A final dividend goes with the
    March quarter; others with the latest quarter that ended ≥12 days before the
    ex-date (results come at least ~2 weeks after quarter end)."""
    d = ex_date if isinstance(ex_date, dt.date) else dt.date.fromisoformat(ex_date)
    if kind == "Final":
        y = d.year if d.month > 3 or (d.month == 3 and d.day == 31) else d.year - 1
        return dt.date(y, 3, 31)
    qe = latest_quarter_end(d + dt.timedelta(days=1))
    if (d - qe).days < 12:
        qe = prev_quarter_end(qe)
    return qe


# ══════════════════════════════════════════════════════════════
#  Build one company's comparison
# ══════════════════════════════════════════════════════════════

def _pct(a, b):
    try:
        if a is None or b in (None, 0):
            return None
        return round(a / b * 100, 1)
    except Exception:
        return None


def _add(a, b):
    return None if a is None or b is None else round(a + b, 2)


def _sub(a, b):
    return None if a is None or b is None else round(a - b, 2)


def build_company(meta, page, cols, today, cal_dates=None, divs=None, filed_on=None):
    """Assemble the 4-quarter comparison for one company. Pure (testable)."""
    rows = (page or {}).get("rows") or {}
    dates = (page or {}).get("dates") or []
    pdfs = (page or {}).get("pdf") or []
    fin = _is_financing(page or {})

    def series(*names):
        return _pick(rows, *names) or []

    raw = {
        "sales": series("Sales", "Revenue"), "op": series("Operating Profit", "Financing Profit"),
        "other": series("Other Income"), "interest": series("Interest"),
        "dep": series("Depreciation"), "pbt": series("Profit before tax"),
        "np": series("Net Profit"), "eps": series("EPS in Rs", "EPS"),
        "gnpa": series("Gross NPA %", "Gross NPA"), "nnpa": series("Net NPA %", "Net NPA"),
    }

    def at(key, qe):
        """Value of a raw series for quarter end qe (None if absent)."""
        if qe in dates:
            i = dates.index(qe)
            s = raw[key]
            return s[i] if i < len(s) else None
        return None

    def metric_at(key, qe):
        if key == "revenue": return at("sales", qe)
        if key == "ebitda":  return at("op", qe)
        if key == "opm":     return _pct(at("op", qe), at("sales", qe))
        if key == "ebit":
            pbt, it = at("pbt", qe), at("interest", qe)
            return _add(pbt, it) if pbt is not None else None
        if key == "pbt":     return at("pbt", qe)
        if key == "np":      return at("np", qe)
        if key == "npm":     return _pct(at("np", qe), at("sales", qe))
        if key == "eps":     return at("eps", qe)
        if key == "nii":     return _sub(at("sales", qe), at("interest", qe))
        if key == "other":   return at("other", qe)
        if key == "gnpa":    return at("gnpa", qe)
        if key == "nnpa":    return at("nnpa", qe)
        return None

    if fin:
        spec = [
            ("revenue", "Revenue (interest earned)", "cr"),
            ("nii", "Net Interest Income", "cr"),
            ("ebitda", "Operating profit (financing profit)", "cr"),
            ("opm", "Operating margin", "pct"),
            ("other", "Other income", "cr"),
            ("pbt", "Profit before tax", "cr"),
            ("np", "Net profit", "cr"),
            ("eps", "EPS (₹)", "rs"),
        ]
        if _has_values(raw["gnpa"]):
            spec.append(("gnpa", "Gross NPA", "pct"))
        if _has_values(raw["nnpa"]):
            spec.append(("nnpa", "Net NPA", "pct"))
    else:
        spec = [
            ("revenue", "Revenue", "cr"),
            ("ebitda", "EBITDA", "cr"),
            ("opm", "Operating margin (EBITDA %)", "pct"),
            ("ebit", "EBIT", "cr"),
            ("pbt", "Profit before tax", "cr"),
            ("np", "Net profit", "cr"),
            ("npm", "Net profit margin", "pct"),
            ("eps", "EPS (₹)", "rs"),
        ]

    metrics = []
    for key, label, unit in spec:
        vals = [metric_at(key, qe) for qe in cols]
        prev = [metric_at(key, prev_quarter_end(qe)) for qe in cols]          # QoQ base
        yago = [metric_at(key, dt.date(qe.year - 1, qe.month, qe.day)) for qe in cols]  # YoY base
        metrics.append({"key": key, "label": label, "unit": unit,
                        "vals": vals, "qoq_base": prev, "yoy_base": yago})

    # Dividends → quarter columns
    div_cols = [[] for _ in cols]
    seen = set()
    for dv in sorted(divs or [], key=lambda x: (x.get("src") != "BSE", x["ex_date"])):
        if dv["ex_date"] in seen:
            continue
        seen.add(dv["ex_date"])
        q = dividend_quarter(dv["ex_date"], dv.get("kind"))
        if q in cols:
            div_cols[cols.index(q)].append(dv)
    metrics.append({"key": "div", "label": "Dividend / share", "unit": "div",
                    "vals": [round(sum(d["amount"] or 0 for d in c), 2) if c else None for c in div_cols],
                    "items": div_cols})

    # Status of each column (announced or not) + the latest quarter's dates
    have = [qe in dates and at("np", qe) is not None for qe in cols]
    latest = cols[-1]
    status = {"announced": have[-1]}
    future = sorted(d for d in (cal_dates or []) if d > latest)
    upcoming = [d for d in future if d >= today]
    past = [d for d in future if d < today]
    if have[-1]:
        on = filed_on or (max(past) if past else None) or (today if today in future else None)
        status["announced_on"] = on.isoformat() if on else None
    else:
        if today in future:
            status["expected_on"] = today.isoformat()
            status["note"] = "Board meeting today"
        elif upcoming:
            status["expected_on"] = upcoming[0].isoformat()
            status["note"] = "Board meeting date (BSE)"
        elif past:
            status["expected_on"] = max(past).isoformat()
            status["note"] = "Results out; figures updating"
            status["out_pending"] = True
        else:
            status["expected_on"] = None
            status["deadline"] = sebi_deadline(latest).isoformat()
            status["note"] = "Date not announced yet"

    pdf_cols = []
    for qe in cols:
        href = None
        if qe in dates:
            i = dates.index(qe)
            href = pdfs[i] if i < len(pdfs) else None
        if href and href.startswith("/"):
            href = "https://www.screener.in" + href
        pdf_cols.append(href)

    return {
        "sym": meta["sym"],
        "name": (meta.get("name") if meta.get("name") and meta.get("name") != meta["sym"] else None)
                or (page or {}).get("name") or meta["sym"],
        "industry": meta.get("industry") or "", "bse_code": (page or {}).get("bse_code"),
        "basis": (page or {}).get("basis"), "lender": fin,
        "have": have, "status": status, "metrics": metrics, "pdf": pdf_cols,
        "ok": bool(page and dates),
    }


# ══════════════════════════════════════════════════════════════
#  Store + background job
# ══════════════════════════════════════════════════════════════

def _now_ts():
    return time.time()


def _load():
    try:
        from database import get_screener_results
        data, _ = get_screener_results(STORE_KEY)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[Results] load failed: {e}")
    return {}


def _save(store):
    try:
        from database import save_screener_results
        save_screener_results(STORE_KEY, store)
    except Exception as e:
        print(f"[Results] save failed: {e}")


def _cols_meta(cols):
    return [{"qe": qe.isoformat(), "label": q_label(qe), "fy": fy_label(qe)} for qe in cols]


def _process_one(meta, cols, today, cal, actions, with_filing=True):
    page = fetch_company_page(meta["sym"])
    code = (page or {}).get("bse_code")
    divs = list((actions or {}).get(code, [])) if code else []
    divs += fetch_dividend_history(meta["sym"])
    cal_dates = (cal or {}).get(code, []) if code else []
    filed = None
    rec = build_company(meta, page, cols, today, cal_dates, divs)
    if with_filing and rec["status"].get("announced") and not rec["status"].get("announced_on") and code:
        filed = fetch_result_filing_date(code, cols[-1], today)
        if filed:
            rec["status"]["announced_on"] = filed.isoformat()
    rec["fetched_at"] = dt.datetime.now(IST).strftime("%d %b %Y %H:%M")
    return rec


def _run_job():
    today = _today()
    cols = season_quarters(today)
    store = _load()
    try:
        universe, src = nifty50_list()
        cal = fetch_result_calendar(cols[-1], today)
        actions = fetch_upcoming_actions()
        companies = dict(store.get("companies") or {}) if store.get("season") == q_label(cols[-1]) else {}
        store.update({"season": q_label(cols[-1]), "cols": _cols_meta(cols),
                      "universe": universe, "universe_src": src,
                      "calendar_ok": cal is not None, "actions_ok": actions is not None})
        failed = []
        for i, meta in enumerate(universe):
            try:
                rec = _process_one(meta, cols, today, cal, actions)
                if not rec.get("ok"):
                    failed.append(meta["sym"])
                if rec.get("ok") or meta["sym"] not in companies:
                    companies[meta["sym"]] = rec
                else:
                    companies[meta["sym"]]["stale"] = True
            except Exception as e:
                print(f"[Results] {meta['sym']} failed: {e}")
                failed.append(meta["sym"])
            store["companies"] = companies
            store["job"] = {"running": True, "started": (store.get("job") or {}).get("started") or _now_ts(),
                            "done": i + 1, "total": len(universe)}
            if (i + 1) % 5 == 0:
                _save(store)                         # the page fills in while the job runs
            time.sleep(SCREENER_GAP)
        store["failed"] = failed
        store["updated_at"] = dt.datetime.now(IST).strftime("%d %b %Y %H:%M")
        store["updated_ts"] = _now_ts()
        store["job"] = {"running": False, "finished": _now_ts(), "done": len(universe), "total": len(universe)}
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        store["job"] = {"running": False, "finished": _now_ts(), "error": str(e)}
    _save(store)
    with _lock:
        _mem["store"], _mem["loaded_at"] = store, _now_ts()


def _maybe_start(store, force):
    job = store.get("job") or {}
    running = job.get("running") and _now_ts() - (job.get("started") or 0) < JOB_TIMEOUT
    if running:
        return False
    age = _now_ts() - (store.get("updated_ts") or 0)
    season_changed = store.get("season") != q_label(latest_quarter_end(_today()))
    if not (season_changed or age > STALE_SECS or (force and age > MIN_REFRESH_GAP)):
        return False
    store["job"] = {"running": True, "started": _now_ts(), "done": 0, "total": 50}
    _save(store)
    threading.Thread(target=_run_job, daemon=True).start()
    return True


def get_view(force=False):
    """Whole page payload: columns, every company's comparison, job status."""
    with _lock:
        store = _load()
        started = _maybe_start(store, force)
    cols = season_quarters()
    comps = store.get("companies") or {}
    order = [m["sym"] for m in (store.get("universe") or [])] or list(comps.keys())
    job = store.get("job") or {}
    return {
        "title": "Nifty50 Result Analyzer",
        "cols": store.get("cols") or _cols_meta(cols),
        "season": store.get("season") or q_label(cols[-1]),
        "season_fy": fy_label(cols[-1]),
        "deadline": sebi_deadline(cols[-1]).isoformat(),
        "companies": [comps[s] for s in order if s in comps],
        "pending": [m for m in (store.get("universe") or []) if m["sym"] not in comps],
        "updated_at": store.get("updated_at"),
        "fetching": bool(job.get("running")) or started,
        "progress": {"done": job.get("done", 0), "total": job.get("total", 50)},
        "calendar_ok": store.get("calendar_ok"),
        "error": job.get("error"),
        "failed": store.get("failed") or [],
        "today": _today().isoformat(),
    }


def refresh_company(sym):
    """Fetch one company now (used by the per-company Update button)."""
    sym = (sym or "").upper().strip()
    today = _today()
    cols = season_quarters(today)
    store = _load()
    universe = store.get("universe") or nifty50_list()[0]
    meta = next((m for m in universe if m["sym"] == sym), None)
    if not meta:
        raise ValueError(f"{sym} is not in the Nifty 50 list")
    cal = fetch_result_calendar(cols[-1], today)
    actions = fetch_upcoming_actions()
    rec = _process_one(meta, cols, today, cal, actions)
    if not rec.get("ok"):
        raise RuntimeError("Couldn't read results from Screener.in right now; try again in a minute")
    with _lock:
        store = _load()
        if store.get("season") == q_label(cols[-1]):
            store.setdefault("companies", {})[sym] = rec
            _save(store)
    return rec


# ══════════════════════════════════════════════════════════════
#  Any company (search box) — same table, not limited to Nifty 50
# ══════════════════════════════════════════════════════════════

AUX_TTL = 30 * 60                # BSE calendar / corporate actions reused for 30 min
LOOKUP_TTL = 30 * 60             # one company's comparison reused for 30 min
_aux = {}
_lookups = {}


def _aux_get(key, fn):
    now = _now_ts()
    with _lock:
        hit = _aux.get(key)
        if hit and now - hit[0] < AUX_TTL:
            return hit[1]
    val = fn()
    if val is not None:
        with _lock:
            _aux[key] = (now, val)
    return val


def lookup_company(sym, refresh=False):
    """4-quarter comparison for any listed company (NSE symbol or BSE code as used
    by Screener.in). Returns the company record, or None if Screener has no results."""
    sym = (sym or "").upper().strip()
    now = _now_ts()
    with _lock:
        hit = _lookups.get(sym)
    if hit and not refresh and now - hit[0] < LOOKUP_TTL:
        return hit[1]
    today = _today()
    cols = season_quarters(today)
    cal = _aux_get(("cal", cols[-1].isoformat()), lambda: fetch_result_calendar(cols[-1], today))
    actions = _aux_get(("act", today.isoformat()), fetch_upcoming_actions)
    store = _load()
    nifty = {m["sym"] for m in (store.get("universe") or [])}
    meta = next((m for m in (store.get("universe") or []) if m["sym"] == sym), None) \
        or {"sym": sym, "name": sym, "industry": ""}
    rec = _process_one(meta, cols, today, cal, actions)
    if not rec.get("ok"):
        return None
    rec["in_nifty50"] = sym in nifty
    rec["cols"] = _cols_meta(cols)
    with _lock:
        _lookups[sym] = (now, rec)
        if len(_lookups) > 300:                     # keep memory bounded
            for k, _ in sorted(_lookups.items(), key=lambda kv: kv[1][0])[:100]:
                _lookups.pop(k, None)
    return rec
