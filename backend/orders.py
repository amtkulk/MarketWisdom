"""
Corporate Action New Orders
===========================
Pulls BSE's "Company Update → Award of Order / Receipt of Order" filings,
keeps a rolling store (last ~35 days, persisted in the DB), and adds a
pro-trader read on every company:

  1. WHAT KIND OF ORDER — many "orders" in this BSE category are actually
     tax demands, GST/ROC/court orders. We classify each filing as a
     business order (contract/LoI/PO), a tax/regulatory order, or unclear.
  2. HOW BIG — order value parsed from the headline, else from the PDF,
     compared with the company's trailing-12-month sales and market cap.
     An order worth 20% of annual sales re-rates a stock; 0.5% doesn't.
  3. HAS THE MARKET NOTICED — price move and volume since the filing.
     A big order the price hasn't reacted to yet is the edge
     (post-announcement drift).
  4. IS THE CHART READY — breakout / pressing base high / coiling, reusing
     the Multibagger Early Signal engine, plus profit acceleration.
  5. ORDER MOMENTUM — repeat winners (several orders in 30 days).

Everything that touches the network runs in a background job; the API
always answers immediately from the store.
"""

import concurrent.futures
import datetime as dt
import json
import re
import threading
import time
import traceback

BSE_API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
BSE_ANN_PAGE = "https://www.bseindia.com/corporates/ann.html"
BSE_PDF_BASES = ("https://www.bseindia.com/xml-data/corpfiling/AttachLive/",
                 "https://www.bseindia.com/xml-data/corpfiling/AttachHis/")
CATEGORY = "Company Update"
SUBCATEGORY = "Award of Order / Receipt of Order"
STORE_KEY = "bse_orders_store"          # row key in the screener_results table
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

REFRESH_TTL = 300          # re-pull the recent window at most every 5 min…
REFRESH_FLOOR = 60         # …or every 60 s when the user presses Refresh
MOMENTUM_TTL = 6 * 3600    # 30-day window (for repeat-winner counts) every 6 h
ENRICH_TTL = 3 * 3600      # price / fundamentals per company
KEEP_DAYS = 35

_HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/",
    "Sec-Fetch-Site": "same-site",
}


def _now_ist():
    return dt.datetime.now(IST)


# ══════════════════════════════════════════════════════════════
#  1. FETCH FROM BSE
# ══════════════════════════════════════════════════════════════

def _params(page, d_from, d_to):
    return {
        "pageno": page, "strCat": CATEGORY, "subcategory": SUBCATEGORY,
        "strPrevDate": d_from.strftime("%Y%m%d"), "strToDate": d_to.strftime("%Y%m%d"),
        "strSearch": "P", "strscrip": "", "strType": "C",
    }


def _bse_get_direct(params, timeout=10):
    """One API page via curl_cffi (browser TLS fingerprint). Returns dict or raises."""
    from curl_cffi import requests as cffi
    r = cffi.get(BSE_API, params=params, headers=_HDRS, impersonate="chrome120", timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"BSE API HTTP {r.status_code}")
    try:
        data = r.json()
    except Exception:
        raise RuntimeError("BSE API returned non-JSON (likely blocked)")
    if not isinstance(data, dict) or "Table" not in data:
        raise RuntimeError("BSE API returned an unexpected payload")
    return data


def _paginate(get_page, d_from, d_to, max_pages):
    rows, page, total_pages = [], 1, 1
    while page <= min(total_pages, max_pages):
        data = get_page(_params(page, d_from, d_to))
        tbl = data.get("Table") or []
        if not tbl:
            break
        rows.extend(tbl)
        try:
            total_pages = int(tbl[0].get("TotalPageCnt") or 1)
        except Exception:
            total_pages = 1
        page += 1
        if page <= total_pages:
            time.sleep(0.4)                        # be polite to BSE
    return rows


def _fetch_playwright(d_from, d_to, max_pages):
    """Fallback when BSE refuses direct calls: open the real announcements page
    in headless Chromium (gets BSE's cookies) and call the API from inside it."""
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlencode
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=_HDRS["User-Agent"])
            page = ctx.new_page()
            page.goto(BSE_ANN_PAGE, wait_until="domcontentloaded", timeout=45000)

            def get_page(params):
                url = BSE_API + "?" + urlencode(params)
                data = page.evaluate(
                    "async (u) => { const r = await fetch(u, {credentials: 'include', "
                    "headers: {'Accept': 'application/json, text/plain, */*'}}); "
                    "if (!r.ok) throw new Error('HTTP ' + r.status); return await r.json(); }", url)
                if not isinstance(data, dict) or "Table" not in data:
                    raise RuntimeError("unexpected payload via browser")
                return data

            return _paginate(get_page, d_from, d_to, max_pages)
        finally:
            browser.close()


def fetch_bse_orders(d_from, d_to, max_pages=30, allow_browser=True):
    """All 'Award of Order / Receipt of Order' filings between two dates.
    Returns (rows, transport). Raises if every transport fails."""
    try:
        return _paginate(_bse_get_direct, d_from, d_to, max_pages), "direct"
    except Exception as e:
        if not allow_browser:
            raise
        print(f"[Orders] direct BSE fetch failed ({e}); trying headless browser")
        return _fetch_playwright(d_from, d_to, max_pages), "browser"


def _parse_bse_dt(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d %b %Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(s[:26], fmt)
        except Exception:
            continue
    return None


def normalize(r):
    """BSE row → compact item. Times from BSE are already IST."""
    news_id = str(r.get("NEWSID") or "").strip()
    if not news_id:
        return None
    sub = " ".join(str(r.get("NEWSSUB") or "").split())
    code = str(r.get("SCRIP_CD") or "").strip()
    company = " ".join(str(r.get("SLONGNAME") or "").split()) or sub.split(" - ")[0]
    company = re.sub(r'[\s\-]*\$+$', '', company).strip()          # "TVS Srichakra Ltd-$"
    when = (_parse_bse_dt(r.get("DissemDT")) or _parse_bse_dt(r.get("NEWS_DT"))
            or _parse_bse_dt(r.get("DT_TM")))
    if not when:
        return None
    ns = str(r.get("NSURL") or "").strip()
    slug = ""
    m = re.search(r'/stock-share-price/[^/]+/([^/]+)/\d+/?$', ns)
    if m:
        slug = m.group(1).upper()
    size = r.get("Fld_Attachsize")
    return {
        "id": news_id, "code": code, "company": company, "subject": sub,
        "headline": " ".join(str(r.get("HEADLINE") or "").split()),
        "dt": when.strftime("%Y-%m-%dT%H:%M:%S"), "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M"),
        "attachment": str(r.get("ATTACHMENTNAME") or "").strip(),
        "size_kb": round(size / 1024) if isinstance(size, (int, float)) and size else None,
        "bse_url": ns, "bse_slug": slug,
    }


# ══════════════════════════════════════════════════════════════
#  2. WHAT KIND OF ORDER?
# ══════════════════════════════════════════════════════════════

_REG_PATTERNS = [
    r'\btax\s+officer', r'\bstate\s+tax', r'\bincome[\s-]*tax', r'\bgst\s+(?:demand|order|authorit|department|council)',
    r'\b(?:demand|penalty|penalties)\b', r'show[\s-]*cause', r'\bassessment\s+order', r'\bassessing\s+officer',
    r'registrar\s+of\s+(?:the\s+)?compan', r'\broc\b', r'\bnclt\b', r'\bnclat\b', r'\btribunal\b',
    r'\bcourt\b', r'\bsebi\b', r'\badjudicat',
    r'\b(?:assistant|deputy|joint|additional|principal)?\s*commissioner\b', r'\bcustoms\b', r'\bexcise\b',
    r'\bentry\s+of\s+goods\b', r'\bappellate\b', r'\bcompetition\s+commission\b', r'\bcci\b',
    r'\brbi\b.*\b(?:penalty|order)\b', r'\bdgft\b', r'\bnotice\s+u/s\b', r'\bgoods\s+and\s+services\s+tax\b',
    r'\bsection\s+\d+\s+of\s+the\s+(?:cgst|sgst|igst|income)', r'\binterest\s+(?:and|&)\s+penalty',
]
_BIZ_PATTERNS = [
    r'letter\s+of\s+(?:intent|award|acceptance)', r'\bloi\b', r'\bloa\b', r'\bwork\s+orders?\b',
    r'\bpurchase\s+orders?\b', r'\bsupply\b', r'\bcontracts?\b', r'\baward(?:ed|s)?\b', r'\bbagged\b',
    r'\bsecured\b', r'\bwins?\b', r'\bwon\b', r'\border\s+(?:worth|valued|value|of\s+(?:rs|inr|usd|₹))',
    r'\bepc\b', r'\bproject\b', r'\btender\b', r'\blowest\s+bidder\b', r'\bl-?1\b', r'\bexport\s+orders?\b',
    r'\bnew\s+orders?\b', r'\bfresh\s+orders?\b', r'\brepeat\s+orders?\b', r'\bmandate\b',
    r'\bagreement\b', r'\bempanel', r'\bdeliver', r'\binstallation\b', r'\bcommissioning\b',
]
_GST_NOISE = re.compile(r'(?:excluding|exclusive\s+of|inclusive\s+of|including|plus|\+)\s+(?:applicable\s+)?(?:gst|taxes?)', re.I)

_GOVT = re.compile(r'\b(?:nhai|railway|rvnl|ircon|dfccil|metro|ntpc|nhpc|bhel|pgcil|power\s*grid|indian\s+army|'
                   r'indian\s+navy|indian\s+air\s+force|ministry|mod\b|drdo|isro|coal\s+india|ongc|iocl|indian\s+oil|'
                   r'bpcl|hpcl|gail|sail|nmdc|municipal|government|govt|state\s+electricity|discom|'
                   r'jal\s+jeevan|smart\s+city|psu|bel\b|hal\b|bharat\s+electronics|cpwd|nbcc|nhidcl)\b', re.I)


def classify_order(text):
    """('business' | 'regulatory' | 'unclear', tags[])"""
    t = _GST_NOISE.sub(" ", (text or "").lower())
    reg = sum(1 for p in _REG_PATTERNS if re.search(p, t))
    biz = sum(1 for p in _BIZ_PATTERNS if re.search(p, t))
    tags = []
    if re.search(r'\blowest\s+bidder\b|\bl-?1\b', t):
        tags.append("L1 bidder (not final)")
    if re.search(r'letter\s+of\s+(?:intent|award|acceptance)|\bloi\b|\bloa\b', t):
        tags.append("LoI/LoA")
    if re.search(r'\bexport', t):
        tags.append("Export")
    if _GOVT.search(t):
        tags.append("Govt/PSU")
    if reg and not biz:
        cls = "regulatory"
    elif biz and not reg:
        cls = "business"
    elif biz and reg:
        cls = "business" if biz > reg else "regulatory" if reg > biz else "unclear"
    else:
        cls = "unclear"
    if cls == "regulatory":
        tags = []                    # "Govt/PSU" etc. mean nothing on a tax/court order
    return cls, tags


# ══════════════════════════════════════════════════════════════
#  3. HOW BIG? — order value parser (₹ crore)
# ══════════════════════════════════════════════════════════════

_NUM = r'(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)'
_UNIT = r'(crores?|crs?\b\.?|cr\b\.?|lakhs?|lacs?|million|mn\b|mio\b|billion|bn\b|thousand)'
_CUR = r'(rs\.?|inr|₹|rupees|usd|us\s?\$|\$|eur(?:o|os)?|€|gbp|£)'
_MONEY_A = re.compile(r'(?<![A-Za-z])' + _CUR + r'\s*' + _NUM + r'\s*(?:/-)?\s*' + _UNIT + r'?', re.I)
_MONEY_B = re.compile(_NUM + r'\s*' + _UNIT, re.I)
_VALUE_KW = re.compile(r'order|contract|worth|valu|amount|aggregat|consideration|size|loi\b|loa\b|'
                       r'purchase|work|award|demand|penalty|tax|bid|tender|project', re.I)
_BAD_CTX = re.compile(r'turnover|net\s*worth|paid[\s-]*up|authori[sz]ed|share\s+capital|face\s+value|'
                      r'market\s+cap|revenue\s+of|sales\s+of|cin\b|per\s+share', re.I)
FX_DEFAULT = {"USD": 88.0, "EUR": 102.0, "GBP": 118.0}


def _unit_mult_inr_cr(unit):
    """Multiplier to convert an INR amount in `unit` to crore."""
    u = (unit or "").lower().rstrip(".")
    if u.startswith("cr"):
        return 1.0
    if u.startswith("lakh") or u.startswith("lac"):
        return 0.01
    if u in ("million", "mn", "mio"):
        return 0.1
    if u in ("billion", "bn"):
        return 100.0
    if u == "thousand":
        return 1e-4
    return None


def _abs_mult(unit):
    u = (unit or "").lower().rstrip(".")
    return {"million": 1e6, "mn": 1e6, "mio": 1e6, "billion": 1e9, "bn": 1e9, "thousand": 1e3}.get(u, 1.0) \
        if not (u.startswith("cr") or u.startswith("lakh") or u.startswith("lac")) else \
        (1e7 if u.startswith("cr") else 1e5)


def parse_order_value(text, fx=None, require_kw=True):
    """Largest plausible money amount near an order/value keyword, in ₹ crore.
    Returns {'cr': float, 'snippet': str} or None."""
    if not text:
        return None
    fx = fx or FX_DEFAULT
    cands = []

    def consider(start, end, cur, num, unit):
        try:
            n = float(num.replace(",", ""))
        except Exception:
            return
        cur = (cur or "").lower().replace(" ", "")
        foreign = None
        if cur in ("usd", "us$", "$"):
            foreign = "USD"
        elif cur.startswith("eur") or cur == "€":
            foreign = "EUR"
        elif cur in ("gbp", "£"):
            foreign = "GBP"
        if foreign:
            cr = n * _abs_mult(unit) * fx.get(foreign, FX_DEFAULT[foreign]) / 1e7
        else:
            m = _unit_mult_inr_cr(unit)
            if m is None:                      # raw rupees, e.g. "Rs. 3,45,67,890/-"
                if n < 1e5:
                    return
                cr = n / 1e7
            else:
                cr = n * m
        if not (0.01 <= cr <= 500000):
            return
        before = text[max(0, start - 110):start]
        if _BAD_CTX.search(before[-32:]):            # only the words right before the amount
            return
        near = bool(_VALUE_KW.search(before))
        snippet = " ".join(text[max(0, start - 40):min(len(text), end + 25)].split())
        cands.append({"cr": round(cr, 2), "near": near, "snippet": snippet, "has_unit": bool(unit)})

    for m in _MONEY_A.finditer(text):
        consider(m.start(), m.end(), m.group(1), m.group(2), m.group(3))
    taken = [(m.start(), m.end()) for m in _MONEY_A.finditer(text)]
    for m in _MONEY_B.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        consider(m.start(), m.end(), "", m.group(1), m.group(2))
    if not cands:
        return None
    near = [c for c in cands if c["near"]]
    if near:
        best = max(near, key=lambda c: c["cr"])
    elif not require_kw:
        pool = [c for c in cands if c["has_unit"]]
        if not pool:
            return None
        best = max(pool, key=lambda c: c["cr"])
    else:
        return None
    return {"cr": best["cr"], "snippet": best["snippet"][:160]}


def fetch_pdf_text(attachment, max_pages=3, max_bytes=4_000_000):
    """Download a BSE attachment (Live, then His) and return (text, base_used)."""
    if not attachment or not re.fullmatch(r'[A-Za-z0-9\-_.]+\.pdf', attachment, re.I):
        return "", None
    from curl_cffi import requests as cffi
    import io
    for base in BSE_PDF_BASES:
        try:
            r = cffi.get(base + attachment, headers={k: v for k, v in _HDRS.items() if k != "Accept"},
                         impersonate="chrome120", timeout=15)
        except Exception:
            continue
        if r.status_code != 200 or not r.content or len(r.content) > max_bytes:
            continue
        if not r.content[:5].startswith(b"%PDF"):
            continue
        try:
            from pypdf import PdfReader
            rd = PdfReader(io.BytesIO(r.content))
            parts = []
            for pg in rd.pages[:max_pages]:
                try:
                    parts.append(pg.extract_text() or "")
                except Exception:
                    pass
            return " ".join(" ".join(parts).split())[:20000], base
        except Exception:
            return "", base
    return "", None


# ══════════════════════════════════════════════════════════════
#  4. MARKET REACTION + COMPANY ENRICHMENT
# ══════════════════════════════════════════════════════════════

def reaction_since(rows, ann_dt):
    """Price/volume reaction to a filing. rows = [(date, close, vol, high, low), ...].
    Filings after 15:30 IST (or on holidays) react on the next trading day."""
    if not rows or not ann_dt:
        return None
    a = dt.datetime.strptime(ann_dt, "%Y-%m-%dT%H:%M:%S") if isinstance(ann_dt, str) else ann_dt
    after_close = (a.hour, a.minute) >= (15, 30)
    idx = None
    for i, r in enumerate(rows):
        d = r[0].date() if hasattr(r[0], "date") else r[0]
        if d > a.date() or (d == a.date() and not after_close):
            idx = i
            break
    if idx is None:
        return {"pending": True}
    if idx == 0:
        return None
    base = rows[idx - 1][1]
    last = rows[-1][1]
    prior = [r[2] for r in rows[max(0, idx - 20):idx]]
    avg = sum(prior) / len(prior) if prior else 0
    react_vol = rows[idx][2]
    return {
        "pending": False,
        "reaction_pct": round((last / base - 1) * 100, 1) if base else None,
        "day1_pct": round((rows[idx][1] / base - 1) * 100, 1) if base else None,
        "vol_x": round(react_vol / avg, 2) if avg else None,
        "react_date": (rows[idx][0].strftime("%Y-%m-%d") if hasattr(rows[idx][0], "strftime") else str(rows[idx][0])),
    }


def _resolve_symbol(code, company):
    """BSE code / company name → Screener.in slug (NSE symbol, or BSE code if BSE-only)."""
    try:
        from curl_cffi import requests as cffi
    except Exception:
        return ""
    queries = [q for q in (code, re.sub(r'\b(?:ltd|limited|pvt|private)\b\.?', '', company or '', flags=re.I).strip()) if q]
    for q in queries:
        try:
            r = cffi.get("https://www.screener.in/api/company/search/", params={"q": q},
                         headers={"User-Agent": _HDRS["User-Agent"]}, impersonate="chrome120", timeout=8)
            if r.status_code == 200:
                arr = r.json() or []
                if arr:
                    parts = [p for p in (arr[0].get("url") or "").split("/") if p]
                    if len(parts) >= 2 and parts[0] == "company":
                        return parts[1].upper()
        except Exception:
            continue
    return ""


def _ticker_for(sym, code):
    if sym and not sym.isdigit():
        return sym + ".NS"
    return (code or sym) + ".BO"


# ══════════════════════════════════════════════════════════════
#  5. ORDER CATALYST SCORE  (0-100, per company)
# ══════════════════════════════════════════════════════════════

def catalyst_score(c):
    """c = per-company aggregate. Returns (score, parts[], tags[])."""
    parts, tags = [], []
    earned = avail = 0.0

    def add(key, label, pts, mx, note):
        nonlocal earned, avail
        parts.append({"key": key, "label": label, "pts": None if pts is None else round(pts, 1),
                      "max": mx, "note": note})
        if pts is not None:
            earned += pts
            avail += mx

    # A · order materiality vs annual sales (30)
    mat = c.get("order_pct_sales")
    if mat is not None:
        p = 30 if mat >= 25 else 22 if mat >= 10 else 15 if mat >= 5 else 8 if mat >= 2 else 3
        add("materiality", "Order size vs annual sales", p, 30, f"{mat:.1f}% of TTM sales")
        if mat >= 10:
            tags.append(f"🎯 Big order: {mat:.0f}% of sales")
    else:
        add("materiality", "Order size vs annual sales", None, 30,
            "value not disclosed" if c.get("order_cr") is None else "sales data missing")

    # B · order momentum, 30 days (10)
    n30 = c.get("orders_30d") or 0
    p = 10 if n30 >= 3 else 6 if n30 == 2 else 2 if n30 == 1 else 0
    add("momentum", "Order momentum (30 days)", p, 10, f"{n30} business order{'s' if n30 != 1 else ''}")
    if n30 >= 2:
        tags.append(f"🔁 Repeat winner: {n30} orders / 30d")

    # C · chart setup (25)
    st = c.get("breakout_state")
    if st is not None:
        p = {"breakout": 25, "at_resistance": 20, "coiling": 12, "extended_breakout": 8}.get(st, 0)
        if c.get("above_200dma"):
            p = min(25, p + 3)
        add("chart", "Chart setup", p, 25, {
            "breakout": "fresh breakout from base", "at_resistance": "pressing base high",
            "coiling": "coiling in upper half of base", "extended_breakout": "broke out, already extended",
            "none": "no base / no setup"}.get(st, st))
        if st == "breakout":
            tags.append("🧱 Fresh breakout")
        elif st == "at_resistance":
            tags.append("⏳ At base high")
    else:
        add("chart", "Chart setup", None, 25, "price history unavailable")

    # D · market reaction (15)
    rx, vx = c.get("reaction_pct"), c.get("vol_x")
    if rx is not None or vx is not None:
        p = 0
        if vx is not None:
            p += 9 if vx >= 2 else 6 if vx >= 1.5 else 3 if vx >= 1.2 else 0
        if rx is not None:
            p += 6 if rx >= 2 else 3 if rx >= 0 else 0
        add("reaction", "Market reaction since filing", p, 15,
            (f"{rx:+.1f}% " if rx is not None else "") + (f"on {vx:.1f}× volume" if vx is not None else ""))
        if rx is not None and vx is not None and rx >= 3 and vx >= 1.5:
            tags.append("📈 Market agrees")
    else:
        add("reaction", "Market reaction since filing", None, 15,
            "reacts next session" if c.get("pending") else "n/a")

    # E · business momentum (20) — revenue/EBITDA/PAT acceleration from the Early Signal engine
    bm = c.get("biz_accel_pts")
    if bm is not None:
        add("business", "Profit & sales acceleration", bm / 27.0 * 20, 20, c.get("biz_note") or "")
    else:
        add("business", "Profit & sales acceleration", None, 20, "financials unavailable")

    # Under-reaction: big order, price hasn't moved — the classic drift setup
    if (mat is not None and mat >= 10 and (rx is None or rx < 3) and (vx is None or vx < 1.5)
            and not c.get("pending")):
        tags.append("😴 Not priced in yet")

    penalty = 0
    ran = c.get("ran_pct")
    if ran is not None and ran > 70:
        penalty += 15; tags.append(f"⏰ Already up {ran:.0f}% from 6M low")
    elif ran is not None and ran > 45:
        penalty += 5
    if c.get("turnover_cr") is not None and c["turnover_cr"] < 0.5:
        penalty += 10; tags.append("💧 Thinly traded")

    # Rescale over what we know, but if BOTH order size and financials are unknown
    # score pessimistically (missing = 0) so thin data can't look like a 90.
    known_core = (mat is not None) or (bm is not None)
    denom = avail if (known_core and avail >= 50) else 100.0
    score = max(0.0, (earned - penalty) / denom * 100) if denom else 0.0
    if mat is None:
        score = min(score, 75.0)               # can't be top-tier without knowing order size
    return round(score, 1), parts, tags


# ══════════════════════════════════════════════════════════════
#  6. STORE, BACKGROUND JOB, VIEW
# ══════════════════════════════════════════════════════════════

_lock = threading.Lock()
_S = {"loaded": False, "store": None, "last_fetch": 0.0, "last_momentum": 0.0,
      "job": {"running": False, "stage": "", "started": 0.0, "error": None}}


def _empty_store():
    return {"news": {}, "scrips": {}, "updated_at": None, "source": None, "last_error": None,
            "momentum_from": None}


def _load_store():
    if _S["loaded"]:
        return
    try:
        from database import get_screener_results
        data, _ts = get_screener_results(STORE_KEY)
    except Exception:
        data = None
    st = data if isinstance(data, dict) and "news" in data else _empty_store()
    _S["store"], _S["loaded"] = st, True


def _save_store():
    try:
        from database import save_screener_results
        with _lock:
            snap = json.loads(json.dumps(_S["store"]))
        save_screener_results(STORE_KEY, snap)
    except Exception as e:
        print(f"[Orders] save failed: {e}")


def _merge(rows):
    """Normalize + classify new BSE rows into the store (under lock). Returns #new."""
    added = 0
    cutoff = (_now_ist() - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    news = _S["store"]["news"]
    for r in rows:
        it = normalize(r)
        if not it or it["date"] < cutoff:
            continue
        if it["id"] in news:
            continue
        # Headline only: the subject just repeats the BSE category + company name
        # (a company called "... Projects Ltd" would otherwise look like an order).
        cls, tags = classify_order(it["headline"])
        it.update({"cls": cls, "tags": tags, "cls_src": "headline"})
        v = parse_order_value(it["headline"], require_kw=False)
        if v:
            it.update({"value_cr": v["cr"], "value_snippet": v["snippet"], "value_src": "headline"})
        news[it["id"]] = it
        added += 1
    for k in [k for k, v in news.items() if v.get("date", "") < cutoff]:
        del news[k]
    return added


def _window_dates(news, n=3):
    dates = sorted({v["date"] for v in news.values()}, reverse=True)
    return dates[:n]


def _job_stage(s):
    with _lock:
        _S["job"]["stage"] = s


def _run_job(need_fetch, need_momentum):
    try:
        today = _now_ist().date()
        # -- (a) BSE fetches that may need the slow browser fallback --------
        if need_fetch:
            _job_stage("Fetching latest filings from BSE")
            try:
                rows, how = fetch_bse_orders(today - dt.timedelta(days=7), today, max_pages=10)
                with _lock:
                    _merge(rows)
                    _S["store"]["source"] = f"live ({how})"
                    _S["store"]["last_error"] = None
                    _S["store"]["updated_at"] = _now_ist().strftime("%d %b %Y %H:%M IST")
                    _S["last_fetch"] = time.time()
            except Exception as e:
                with _lock:
                    _S["store"]["last_error"] = f"BSE did not respond: {e}"
        if need_momentum:
            _job_stage("Loading 30 days of orders (for repeat winners)")
            try:
                rows, _how = fetch_bse_orders(today - dt.timedelta(days=30), today, max_pages=40)
                with _lock:
                    _merge(rows)
                    _S["store"]["momentum_from"] = (today - dt.timedelta(days=30)).strftime("%Y-%m-%d")
                    _S["last_momentum"] = time.time()
            except Exception as e:
                print(f"[Orders] 30-day fetch failed: {e}")

        with _lock:
            news = dict(_S["store"]["news"])
        win = set(_window_dates(news))
        win_items = [v for v in news.values() if v["date"] in win]

        # -- (b) PDFs: order value (and class when the headline is vague) -----
        todo = [v for v in win_items if v.get("attachment") and not v.get("pdf_done")
                and (v.get("value_cr") is None or v["cls"] == "unclear")]
        if todo:
            done = [0]

            def do_pdf(item):
                text, base = fetch_pdf_text(item["attachment"])
                upd = {"pdf_done": True, "pdf_base": base}
                if text:
                    if item["cls"] == "unclear":
                        cls, tags = classify_order(text[:3000])
                        if cls != "unclear":
                            upd.update({"cls": cls, "tags": tags, "cls_src": "pdf"})
                    if item.get("value_cr") is None:
                        v = parse_order_value(text, require_kw=True)
                        if v:
                            upd.update({"value_cr": v["cr"], "value_snippet": v["snippet"], "value_src": "pdf"})
                with _lock:
                    if item["id"] in _S["store"]["news"]:
                        _S["store"]["news"][item["id"]].update(upd)
                    done[0] += 1
                    _S["job"]["stage"] = f"Reading order PDFs {done[0]}/{len(todo)}"

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
                list(ex.map(do_pdf, todo))

        # -- (c) per-company: symbol, prices, fundamentals --------------------
        import screener as S
        with _lock:
            scrips = _S["store"]["scrips"]
            codes = {}
            for v in win_items:
                codes.setdefault(v["code"], v["company"])
            stale = [c for c in codes if time.time() - (scrips.get(c, {}).get("enriched_at") or 0) > ENRICH_TTL]
        if stale:
            _job_stage(f"Matching {len(stale)} companies to NSE symbols")
            need_sym = [c for c in stale if not scrips.get(c, {}).get("sym")]

            def do_sym(code):
                sym = _resolve_symbol(code, codes[code]) or ""
                with _lock:
                    _S["store"]["scrips"].setdefault(code, {})["sym"] = sym
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                list(ex.map(do_sym, need_sym))

            _job_stage("Fetching price history")
            with _lock:
                tick = {c: _ticker_for(_S["store"]["scrips"].get(c, {}).get("sym"), c) for c in stale}
            price = {}
            tl = list(set(tick.values()))
            for i in range(0, len(tl), 100):
                try:
                    price.update(S._bulk_download_ohlc(tl[i:i + 100], "1y") or {})
                except Exception:
                    pass
            # retry missing NSE tickers on BSE
            retry = {c: f"{c}.BO" for c, t in tick.items() if t not in price and t.endswith(".NS")}
            if retry:
                try:
                    price.update(S._bulk_download_ohlc(list(retry.values()), "1y") or {})
                except Exception:
                    pass
                for c, t in retry.items():
                    if t in price:
                        tick[c] = t
            try:
                nifty = [r[1] for r in (S._bulk_download_ohlc(["^NSEI"], "1y") or {}).get("^NSEI", [])]
            except Exception:
                nifty = []

            done = [0]

            def do_company(code):
                info = {"ticker": tick[code], "enriched_at": time.time()}
                rows = price.get(tick[code])
                if rows:
                    info["rows_tail"] = [(r[0].strftime("%Y-%m-%d"), round(r[1], 2), r[2]) for r in rows[-60:]]
                    sig = S._mb_price_signals(rows, nifty, strict=False)
                    if sig:
                        info.update({k: sig.get(k) for k in ("price", "breakout_state", "base_hi", "ext_pct",
                                                              "ran_pct", "vol_ratio", "rs_6m", "dist_52wh",
                                                              "above_200dma", "turnover_cr")})
                        info["sig"] = {"pts": sig["pts"], "notes": sig["notes"], "late_penalty": sig["late_penalty"]}
                    else:
                        info["price"] = round(rows[-1][1], 2)
                sym = _S["store"]["scrips"].get(code, {}).get("sym") or code
                raw = S._fetch_screener_deep(sym) if sym else {}
                if raw:
                    fp, fn, fv = S._mb_fund_signals(raw)
                    qs = [x for x in (raw.get("q_sales") or []) if x is not None]
                    info["ttm_sales"] = round(sum(qs[-4:]), 1) if len(qs) >= 4 else None
                    info["mcap"] = fv.get("mcap")
                    info["name"] = raw.get("name")
                    acc = [fp.get(k) for k in ("rev_accel", "ebitda_accel", "pat_accel")]
                    info["biz_accel_pts"] = round(sum(a for a in acc if a is not None), 2) if any(a is not None for a in acc) else None
                    info["biz_note"] = "; ".join(f"{lab} {fn.get(k, '')}" for k, lab in
                                                 (("rev_accel", "Sales"), ("pat_accel", "PAT")) if fp.get(k) is not None)
                    info["pat_yoy"], info["rev_yoy"] = fv.get("pat_yoy"), fv.get("rev_yoy")
                    if "sig" in info:
                        s2 = {"pts": info["sig"]["pts"], "late_penalty": info["sig"]["late_penalty"]}
                        score, _cov, price_only, hits, n_avail = S._mb_score(s2, fp)
                        info["early_score"] = score
                with _lock:
                    _S["store"]["scrips"].setdefault(code, {}).update(info)
                    done[0] += 1
                    _S["job"]["stage"] = f"Reading company financials {done[0]}/{len(stale)}"

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                list(ex.map(do_company, stale))

        # prune companies no longer referenced
        with _lock:
            live_codes = {v["code"] for v in _S["store"]["news"].values()}
            for c in [c for c in _S["store"]["scrips"] if c not in live_codes]:
                del _S["store"]["scrips"][c]
        _save_store()
    except Exception as e:
        print("[Orders] job failed:", traceback.format_exc())
        with _lock:
            _S["job"]["error"] = str(e)
    finally:
        with _lock:
            _S["job"]["running"] = False
            _S["job"]["stage"] = ""


def _start_job(need_fetch, need_momentum):
    with _lock:
        if _S["job"]["running"]:
            return False
        _S["job"].update({"running": True, "started": time.time(), "error": None, "stage": "Starting"})
    threading.Thread(target=_run_job, args=(need_fetch, need_momentum), daemon=True).start()
    return True


def _company_rollup(code, items_win, news_all, scrip):
    """Aggregate a company's filings in the 3-day window + 30-day order count."""
    biz = [v for v in items_win if v["cls"] == "business"]
    vals = [v["value_cr"] for v in biz if v.get("value_cr") is not None]
    order_cr = round(sum(vals), 2) if vals else None
    ttm = scrip.get("ttm_sales")
    c = {
        "code": code, "company": items_win[0]["company"], "sym": scrip.get("sym") or code,
        "name": scrip.get("name"), "orders_3d": len(biz), "order_cr": order_cr,
        "order_pct_sales": round(order_cr / ttm * 100, 1) if (order_cr and ttm and ttm > 0) else None,
        "order_pct_mcap": round(order_cr / scrip["mcap"] * 100, 2) if (order_cr and scrip.get("mcap")) else None,
        "orders_30d": sum(1 for v in news_all if v["code"] == code and v["cls"] == "business"
                          and v["date"] >= (_now_ist().date() - dt.timedelta(days=30)).strftime("%Y-%m-%d")),
        "first_dt": min(v["dt"] for v in biz) if biz else None,
        "latest_dt": max(v["dt"] for v in biz) if biz else None,
        "tags": sorted({t for v in biz for t in v.get("tags", [])}),
    }
    for k in ("price", "breakout_state", "base_hi", "ext_pct", "ran_pct", "vol_ratio", "rs_6m",
              "dist_52wh", "above_200dma", "turnover_cr", "mcap", "ttm_sales", "early_score",
              "biz_accel_pts", "biz_note", "pat_yoy", "rev_yoy"):
        c[k] = scrip.get(k)
    # reaction measured from the FIRST business order in the window
    tail = scrip.get("rows_tail")
    if tail and c["first_dt"]:
        rows = [(dt.datetime.strptime(d, "%Y-%m-%d"), cl, vo) for d, cl, vo in tail]
        rx = reaction_since(rows, c["first_dt"])
        if rx:
            c.update({"reaction_pct": rx.get("reaction_pct"), "vol_x": rx.get("vol_x"),
                      "pending": rx.get("pending", False), "day1_pct": rx.get("day1_pct")})
    score, parts, tags = catalyst_score(c)
    c.update({"score": score, "parts": parts, "signal_tags": tags})
    return c


def build_view():
    with _lock:
        st = json.loads(json.dumps(_S["store"]))
        job = dict(_S["job"])
    news = st["news"]
    dates = _window_dates(news)
    win = [v for v in news.values() if v["date"] in dates]
    by_code = {}
    for v in win:
        by_code.setdefault(v["code"], []).append(v)
    companies = {code: _company_rollup(code, items, list(news.values()), st["scrips"].get(code, {}))
                 for code, items in by_code.items()}

    today = _now_ist().date()
    days = []
    for d in dates:
        dd = dt.datetime.strptime(d, "%Y-%m-%d").date()
        rel = "Today" if dd == today else "Yesterday" if dd == today - dt.timedelta(days=1) else None
        items = sorted([v for v in win if v["date"] == d], key=lambda v: v["dt"], reverse=True)
        out = []
        for v in items:
            c = companies.get(v["code"], {})
            sc = st["scrips"].get(v["code"], {})
            ttm, mcap = sc.get("ttm_sales"), sc.get("mcap")
            row = {k: v.get(k) for k in ("id", "code", "company", "subject", "headline", "time", "dt",
                                          "attachment", "size_kb", "bse_url", "cls", "tags", "cls_src",
                                          "value_cr", "value_snippet", "value_src", "pdf_base")}
            row["sym"] = sc.get("sym") or ""
            row["value_pct_sales"] = round(v["value_cr"] / ttm * 100, 1) if (v.get("value_cr") and ttm) else None
            row["value_pct_mcap"] = round(v["value_cr"] / mcap * 100, 2) if (v.get("value_cr") and mcap) else None
            row["score"] = c.get("score") if v["cls"] == "business" else None
            row["reaction_pct"], row["vol_x"], row["pending"] = c.get("reaction_pct"), c.get("vol_x"), c.get("pending")
            row["price"] = sc.get("price")
            out.append(row)
        days.append({"date": d, "label": dd.strftime("%a %d %b %Y"), "rel": rel, "items": out})

    watch = sorted([c for c in companies.values() if c["orders_3d"] > 0],
                   key=lambda c: c["score"], reverse=True)[:12]
    stats = {"total": len(win), "business": sum(1 for v in win if v["cls"] == "business"),
             "regulatory": sum(1 for v in win if v["cls"] == "regulatory"),
             "unclear": sum(1 for v in win if v["cls"] == "unclear"),
             "companies": len(by_code)}
    pend_pdf = sum(1 for v in win if v.get("attachment") and not v.get("pdf_done")
                   and (v.get("value_cr") is None or v["cls"] == "unclear"))
    return {"days": days, "watch": watch, "stats": stats, "updated_at": st.get("updated_at"),
            "source": st.get("source"), "error": st.get("last_error"),
            "enriching": job["running"], "stage": job["stage"], "pending_pdfs": pend_pdf,
            "momentum_from": st.get("momentum_from"), "empty": not news}


def get_orders_view(force=False):
    """Main entry for the API. Answers from the store immediately; kicks off a
    background refresh/enrichment when anything is stale."""
    with _lock:
        _load_store()
        since = time.time() - _S["last_fetch"]
        need_fetch = since > (REFRESH_FLOOR if force else REFRESH_TTL)
        need_momentum = time.time() - _S["last_momentum"] > MOMENTUM_TTL
    if need_fetch:
        # Fast path: try a direct BSE call inline (a few seconds) so a page visit
        # shows today's filings straight away. The browser fallback runs in the job.
        today = _now_ist().date()
        try:
            rows, how = fetch_bse_orders(today - dt.timedelta(days=7), today, max_pages=10, allow_browser=False)
            with _lock:
                _merge(rows)
                _S["store"]["source"] = f"live ({how})"
                _S["store"]["last_error"] = None
                _S["store"]["updated_at"] = _now_ist().strftime("%d %b %Y %H:%M IST")
                _S["last_fetch"] = time.time()
            need_fetch = False
        except Exception as e:
            with _lock:
                _S["store"]["last_error"] = f"BSE did not respond: {e}"
    view = build_view()
    stale_enrich = view["pending_pdfs"] > 0
    with _lock:
        scrips = _S["store"]["scrips"]
        codes = {it["code"] for d in view["days"] for it in d["items"]}
        stale_enrich = stale_enrich or any(time.time() - (scrips.get(c, {}).get("enriched_at") or 0) > ENRICH_TTL
                                           for c in codes)
    if need_fetch or need_momentum or stale_enrich:
        if _start_job(need_fetch, need_momentum):
            view["enriching"] = True
            view["stage"] = "Starting"
    return view


def resolve_pdf_url(attachment):
    """Find which BSE folder (Live/His) holds an attachment; default to His."""
    if not attachment or not re.fullmatch(r'[A-Za-z0-9\-_.]+\.pdf', attachment, re.I):
        return None
    with _lock:
        _load_store()
        for v in _S["store"]["news"].values():
            if v.get("attachment") == attachment and v.get("pdf_base"):
                return v["pdf_base"] + attachment
    try:
        from curl_cffi import requests as cffi
        for base in BSE_PDF_BASES:
            r = cffi.head(base + attachment, headers={"User-Agent": _HDRS["User-Agent"],
                                                       "Referer": "https://www.bseindia.com/"},
                          impersonate="chrome120", timeout=6)
            if r.status_code == 200:
                return base + attachment
    except Exception:
        pass
    return BSE_PDF_BASES[1] + attachment
