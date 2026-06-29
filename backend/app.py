import os
import json
import time
from datetime import datetime, date
from flask import Flask, request, jsonify
from flask_cors import CORS
from database import init_db, add_or_update_stock, delete_stock, get_all_stocks
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

app = Flask(__name__)
# Enable CORS for all routes so the React frontend can talk to this API
CORS(app)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") # Fetch from environment (GitHub Secrets/App Runner Env)

# Initialize SQLite database
init_db()

# ══════════════════════════════════════════════════════════════
#  SIMPLE IN-PROCESS TTL CACHE
#  Most endpoints re-fetch slow external data (yfinance / NSE / Gemini /
#  Playwright) on every request even though that data barely changes minute
#  to minute. This decorator stores a result for `ttl_seconds` and serves it
#  instantly to everyone until it expires. No Redis needed.
#
#  Notes:
#   - Thread-safe (we run many Waitress threads).
#   - By default, empty/None/error results are NOT cached, so a transient
#     failure is retried on the next request instead of being stuck.
#   - Cache is per-process and clears on restart/redeploy (which is fine).
# ══════════════════════════════════════════════════════════════
import threading as _threading

_cache_store = {}
_cache_lock  = _threading.Lock()

def _is_empty_result(val):
    """Treat None, empty containers, {'error': ...}, and failed tuples as 'don't cache'."""
    if val is None:
        return True
    if isinstance(val, dict) and "error" in val:
        return True
    if isinstance(val, (list, dict, str)) and len(val) == 0:
        return True
    # Several fetchers return a tuple whose FIRST element is the payload
    # (e.g. (price, hi, lo) or (data, error)). A None first element = failure.
    if isinstance(val, tuple):
        if len(val) == 0 or val[0] is None:
            return True
    return False

def cached(ttl_seconds, cache_empty=False):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            key = (fn.__name__, args, tuple(sorted(kwargs.items())))
            now = time.time()
            with _cache_lock:
                entry = _cache_store.get(key)
                if entry is not None:
                    value, ts = entry
                    if now - ts < ttl_seconds:
                        return value
            # Compute outside the lock so slow fetches don't block other keys.
            result = fn(*args, **kwargs)
            if cache_empty or not _is_empty_result(result):
                with _cache_lock:
                    _cache_store[key] = (result, now)
            return result
        wrapper.__name__ = fn.__name__
        wrapper.__doc__  = fn.__doc__
        return wrapper
    return decorator

# ══════════════════════════════════════════════════════════════
#  HELPERS (Extracted directly from old app.py)
# ══════════════════════════════════════════════════════════════
@cached(120)            # live price: 2 min
def fetch_quote_nse(ticker):
    """Fast live price + 52-week high/low from NSE's own quote API (uses cached NSE
    session cookies). Returns (price, hi52, lo52) or (None, None, None)."""
    try:
        data = get_nse_data(f"/api/quote-equity?symbol={ticker}")
        if not data:
            return None, None, None
        pi = data.get("priceInfo", {}) or {}
        wk = pi.get("weekHighLow", {}) or {}
        price = pi.get("lastPrice")
        if price:
            price = round(float(price), 2)
            hi52  = round(float(wk.get("max")), 2) if wk.get("max") else price
            lo52  = round(float(wk.get("min")), 2) if wk.get("min") else price
            return price, hi52, lo52
    except Exception:
        pass
    return None, None, None


def fetch_live_price(ticker):
    # Fast path: NSE's official quote endpoint.
    p, hi, lo = fetch_quote_nse(ticker)
    if p:
        return p, hi, lo
    # Fallback: yfinance (.NS then .BO only — dropping the bare-symbol attempt that
    # rarely matches Indian tickers and just added a slow extra round trip).
    try:
        import yfinance as yf
        for suffix in [".NS", ".BO"]:
            try:
                t    = yf.Ticker(ticker + suffix)
                hist = t.history(period="5d")
                if hist.empty: continue
                h1y  = t.history(period="1y")
                price = round(float(hist["Close"].iloc[-1]), 2)
                hi52  = round(float(h1y["High"].max()), 2) if not h1y.empty else price
                lo52  = round(float(h1y["Low"].min()),  2) if not h1y.empty else price
                if price > 0:
                    return price, hi52, lo52
            except Exception:
                continue
    except Exception:
        pass
    return None, None, None


@cached(900)            # 6mo daily candles: 15 min
def fetch_ohlcv(ticker):
    """Fetch 6 months of daily OHLCV data from yfinance for candlestick chart."""
    try:
        import yfinance as yf
        for suffix in [".NS", ".BO"]:
            try:
                t    = yf.Ticker(ticker + suffix)
                df   = t.history(period="6mo", interval="1d")
                if len(df) < 10:
                    continue
                # Calculate 21 EMA
                closes   = df["Close"].tolist()
                k        = 2 / (21 + 1)
                ema      = [closes[0]]
                for c in closes[1:]:
                    ema.append(c * k + ema[-1] * (1 - k))
                records = []
                for i, (idx, row) in enumerate(df.iterrows()):
                    records.append({
                        "date":   idx.strftime("%Y-%m-%d"),
                        "open":   round(float(row["Open"]),   2),
                        "high":   round(float(row["High"]),   2),
                        "low":    round(float(row["Low"]),    2),
                        "close":  round(float(row["Close"]),  2),
                        "volume": int(row["Volume"]),
                        "ema21":  round(ema[i], 2),
                    })
                return records
            except Exception as e:
                continue
    except Exception as e:
        print(f"  OHLCV error: {e}")
    return []


# ── Screener.in parsing helpers (shared by the fast HTTP path and the Playwright fallback) ──
def _build_holdings(hdrs, rows):
    """Build the 'holdings' dict from an extracted shareholding table {hdrs, rows}."""
    if not hdrs or not rows:
        return None
    n   = len(hdrs)
    idx = list(range(max(0, n - 4), n))
    q4  = [hdrs[i] for i in idx]

    def get_row(keys):
        for k, v in rows.items():
            if any(key in k for key in keys):
                return [str(v[i]) if i < len(v) else 'N/A' for i in idx]
        return ['N/A'] * 4

    promoter = get_row(['promoter'])
    fii      = get_row(['fii', 'foreign'])
    dii      = get_row(['dii', 'domestic'])
    if q4 and promoter[0] != 'N/A':
        return {'quarters': q4, 'promoter': promoter, 'fii': fii, 'dii': dii}
    return None


def _build_quarterly(hdrs, rows):
    """Build the 'quarterly_results' dict from an extracted quarterly table {hdrs, rows}."""
    if not hdrs or not rows:
        return None
    n     = len(hdrs)
    num_q = min(3, n)
    ci    = list(range(n - num_q, n))
    yi    = [max(0, i - 4) for i in ci]
    qlbls = [hdrs[i] for i in ci]

    def get_qr(keys):
        for k, v in rows.items():
            if any(key in k for key in keys):
                return [str(v[i]).strip() + ' cr' if i < len(v) else 'N/A' for i in ci]
        return ['N/A'] * num_q

    def get_yoy(keys):
        for k, v in rows.items():
            if any(key in k for key in keys):
                out = []
                for c2, p2 in zip(ci, yi):
                    try:
                        cv2 = float(str(v[c2]).replace(',', ''))
                        pv2 = float(str(v[p2]).replace(',', ''))
                        if pv2 != 0 and c2 != p2:
                            pct = round((cv2 - pv2) / abs(pv2) * 100, 1)
                            out.append(f"+{pct}%" if pct >= 0 else f"{pct}%")
                        else:
                            out.append('N/A')
                    except Exception:
                        out.append('N/A')
                return out          # (fix: original dropped successful YoY rows)
        return ['N/A'] * num_q

    revenue = get_qr(['sales', 'revenue', 'net sales'])
    profit  = get_qr(['net profit', 'profit after tax', 'pat'])
    eps_v   = get_qr(['eps', 'earning'])
    if qlbls and revenue[0] != 'N/A':
        return {
            'quarters':    qlbls,
            'revenue':     revenue,
            'revenue_yoy': get_yoy(['sales', 'revenue', 'net sales']),
            'profit':      profit,
            'profit_yoy':  get_yoy(['net profit', 'profit after tax', 'pat']),
            'eps':         [e.replace(' cr', '') for e in eps_v],
            'eps_yoy':     get_yoy(['eps', 'earning']),
        }
    return None


def _extract_screener_table(table):
    """Extract (hdrs, rows) from a BeautifulSoup <table>, mirroring the JS extraction."""
    hdrs = []
    thead = table.find("thead")
    if thead:
        hdrs = [th.get_text(strip=True) for th in thead.find_all("th")]
    rows = {}
    tbody = table.find("tbody")
    if tbody:
        for tr in tbody.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) > 1:
                rows[cells[0].lower()] = cells[1:]
    return hdrs, rows


def fetch_screener_http(ticker):
    """FAST path: Screener.in shareholding + quarterly via a plain HTTP GET (no browser).
    Screener renders these tables server-side, so this is ~1-2s vs ~10s for a Chromium
    launch. Returns {} on any failure so the caller falls back to Playwright."""
    result = {}
    url     = f"https://www.screener.in/company/{ticker}/consolidated/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        res = cffi_requests.get(url, headers=headers, impersonate="chrome120", timeout=8)
        if res.status_code != 200:
            return {}
        soup = BeautifulSoup(res.content, "html.parser")

        # Shareholding
        try:
            sec = soup.find(id="shareholding")
            if sec is None:
                for s in soup.find_all("section"):
                    if "Shareholding Pattern" in (s.get_text() or ""):
                        sec = s
                        break
            if sec is not None:
                best, best_cols = None, 0
                for t in sec.find_all("table"):
                    cols = len(t.select("thead th"))
                    if cols > best_cols:
                        best_cols, best = cols, t
                if best is not None:
                    hdrs, rows = _extract_screener_table(best)
                    hdrs = [h for h in hdrs if h and len(h) > 2]
                    rows = {k: [c.replace('%', '') for c in v] for k, v in rows.items()}
                    holdings = _build_holdings(hdrs, rows)
                    if holdings:
                        result['holdings'] = holdings
        except Exception:
            pass

        # Quarterly results
        try:
            sec = soup.find(id="quarters")
            if sec is None:
                for s in soup.find_all("section"):
                    if (s.get_text() or "").strip().startswith("Quarterly"):
                        sec = s
                        break
            if sec is not None:
                table = sec.find("table")
                if table is not None:
                    hdrs, rows = _extract_screener_table(table)
                    hdrs = [h for h in hdrs if h and len(h) > 1]
                    rows = {k: [c.replace(',', '') for c in v] for k, v in rows.items()}
                    quarterly = _build_quarterly(hdrs, rows)
                    if quarterly:
                        result['quarterly_results'] = quarterly
        except Exception:
            pass

        return result
    except Exception:
        return {}


@cached(1800)           # shareholding/quarterly: 30 min
def fetch_screener_data(ticker):
    """Dispatcher: try the fast HTTP path first; fall back to the Playwright scrape
    only if HTTP returns nothing. Result is cached for 30 min either way."""
    data = fetch_screener_http(ticker)
    if data.get("holdings") or data.get("quarterly_results"):
        return data
    return _fetch_screener_playwright(ticker)


def _fetch_screener_playwright(ticker):
    """Fallback: Screener.in shareholding + quarterly via Playwright (slow, launches Chromium)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {}

    result = {}
    url    = f"https://www.screener.in/company/{ticker}/consolidated/"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120")
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            # Shareholding
            try:
                sh = page.evaluate(
                    "() => {"
                    "  let sec = document.querySelector('#shareholding');"
                    "  if (!sec) { document.querySelectorAll('section').forEach(function(s) {"
                    "    if (!sec && s.innerText && s.innerText.includes('Shareholding Pattern')) sec = s;"
                    "  }); }"
                    "  if (!sec) return null;"
                    "  const tables = sec.querySelectorAll('table');"
                    "  let best = null, bestCols = 0;"
                    "  tables.forEach(function(t) {"
                    "    const c = t.querySelectorAll('thead th').length;"
                    "    if (c > bestCols) { bestCols = c; best = t; }"
                    "  });"
                    "  if (!best) return null;"
                    "  const hdrs = Array.from(best.querySelectorAll('thead th')).map(function(th) { return th.innerText.trim(); }).filter(function(h) { return h && h.length > 2; });"
                    "  const rows = {};"
                    "  best.querySelectorAll('tbody tr').forEach(function(tr) {"
                    "    const cells = Array.from(tr.querySelectorAll('td')).map(function(td) { return td.innerText.trim().replace('%',''); });"
                    "    if (cells.length > 1) rows[cells[0].toLowerCase()] = cells.slice(1);"
                    "  });"
                    "  return { hdrs: hdrs, rows: rows };"
                    "}"
                )
                if sh and sh.get('hdrs') and sh.get('rows'):
                    hdrs = sh['hdrs']
                    rows = sh['rows']
                    n    = len(hdrs)
                    idx  = list(range(max(0, n-4), n))
                    q4   = [hdrs[i] for i in idx]

                    def get_row(keys):
                        for k, v in rows.items():
                            if any(key in k for key in keys):
                                return [str(v[i]) if i < len(v) else 'N/A' for i in idx]
                        return ['N/A']*4

                    promoter = get_row(['promoter'])
                    fii      = get_row(['fii','foreign'])
                    dii      = get_row(['dii','domestic'])
                    if q4 and promoter[0] != 'N/A':
                        result['holdings'] = {'quarters': q4, 'promoter': promoter, 'fii': fii, 'dii': dii}
            except Exception:
                pass

            # Quarterly results
            try:
                qr = page.evaluate(
                    "() => {"
                    "  let sec = document.querySelector('#quarters');"
                    "  if (!sec) { document.querySelectorAll('section').forEach(function(s) {"
                    "    if (!sec && s.innerText && s.innerText.trim().startsWith('Quarterly')) sec = s;"
                    "  }); }"
                    "  if (!sec) return null;"
                    "  const table = sec.querySelector('table');"
                    "  if (!table) return null;"
                    "  const hdrs = Array.from(table.querySelectorAll('thead th')).map(function(th) { return th.innerText.trim(); }).filter(function(h) { return h && h.length > 1; });"
                    "  const rows = {};"
                    "  table.querySelectorAll('tbody tr').forEach(function(tr) {"
                    "    const cells = Array.from(tr.querySelectorAll('td')).map(function(td) { return td.innerText.trim().replace(/,/g,''); });"
                    "    if (cells.length > 1) rows[cells[0].toLowerCase()] = cells.slice(1);"
                    "  });"
                    "  return { hdrs: hdrs, rows: rows };"
                    "}"
                )
                if qr and qr.get('hdrs') and qr.get('rows'):
                    hdrs = qr['hdrs']
                    rows = qr['rows']
                    n    = len(hdrs)
                    num_q = min(3, n)
                    ci    = list(range(n - num_q, n))
                    yi    = [max(0, i - 4) for i in ci]
                    qlbls = [hdrs[i] for i in ci]

                    def get_qr(keys):
                        for k, v in rows.items():
                            if any(key in k for key in keys):
                                return [str(v[i]).strip() + ' cr' if i < len(v) else 'N/A' for i in ci]
                        return ['N/A'] * num_q

                    def get_yoy(keys):
                        for k, v in rows.items():
                            if any(key in k for key in keys):
                                out = []
                                for c2, p2 in zip(ci, yi):
                                    try:
                                        cv2 = float(str(v[c2]).replace(',',''))
                                        pv2 = float(str(v[p2]).replace(',',''))
                                        if pv2 != 0 and c2 != p2:
                                            pct = round((cv2-pv2)/abs(pv2)*100, 1)
                                            out.append(f"+{pct}%" if pct >= 0 else f"{pct}%")
                                        else:
                                            out.append('N/A')
                                    except Exception:
                                        out.append('N/A')
                                        return out
                        return ['N/A'] * num_q

                    revenue = get_qr(['sales','revenue','net sales'])
                    profit  = get_qr(['net profit','profit after tax','pat'])
                    eps_v   = get_qr(['eps','earning'])
                    if qlbls and revenue[0] != 'N/A':
                        result['quarterly_results'] = {
                            'quarters':    qlbls,
                            'revenue':     revenue,
                            'revenue_yoy': get_yoy(['sales','revenue','net sales']),
                            'profit':      profit,
                            'profit_yoy':  get_yoy(['net profit','profit after tax','pat']),
                            'eps':         [e.replace(' cr','') for e in eps_v],
                            'eps_yoy':     get_yoy(['eps','earning']),
                        }
            except Exception:
                pass

            browser.close()
    except Exception:
        pass
    return result


@cached(900)            # news: 15 min
def fetch_news_rss(company_name):
    try:
        import urllib.request, urllib.parse, xml.etree.ElementTree as ET
        q   = urllib.parse.quote(company_name + " stock india")
        url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            root = ET.fromstring(r.read())
        ch    = root.find("channel") or root
        items = []
        for item in ch.findall("item")[:6]:
            title    = (item.findtext("title") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            src_el   = item.find("source")
            src      = src_el.text if src_el is not None else "Google News"
            date_str = ""
            for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"]:
                try:
                    date_str = datetime.strptime(pub_date[:31].strip(), fmt).strftime("%d %b %Y").upper()
                    break
                except Exception:
                    pass
            if title and len(title) > 15:
                items.append({"headline": title, "date": date_str, "source": src})
        return items
    except Exception:
        return []


@cached(1800)           # company analysis (Gemini): 30 min; only successes cached
def fetch_gemini(company):
    if not GEMINI_API_KEY:
        return None, "GEMINI_API_KEY not set. Run: setx GEMINI_API_KEY your_key — then restart CMD."
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None, "google-genai not installed. Run: pip install google-genai"

    prompt = (
        "You are an Indian stock market analyst. "
        "Research this company: " + company + ". "
        "Return ONLY valid JSON — no markdown, no backticks, no explanation before or after. "
        "JSON structure: {"
        '"company_name":"full official name",'
        '"ticker":"correct NSE ticker e.g. RELIANCE not RIL",'
        '"sector":"sector name",'
        '"description":"2-3 lines about what company does",'
        '"current_price":"0",'
        '"week_52_high":"0",'
        '"week_52_low":"0",'
        '"cagr":{"ytd":"+/-XX%","3yr":"+/-XX%","5yr":"+/-XX%","10yr":"+/-XX%"},'
        '"candle_analysis":{'
        '"above_21_ema_daily":true,'
        '"ema_note":"price vs EMA note",'
        '"price_volume_breakout":false,'
        '"breakout_note":"breakout note",'
        '"volume_spurt":false,'
        '"volume_note":"volume note",'
        '"rsi_value":"XX.X",'
        '"rsi_signal":"Neutral",'
        '"rsi_note":"rsi note",'
        '"chart_patterns_last_quarter":["pattern name"],'
        '"pattern_detail":"2-3 lines on chart pattern"},'
        '"fundamental_checks":{'
        '"roe_above_20":true,"roe_value":"XX%","roe_note":"note",'
        '"roce_above_20":true,"roce_value":"XX%","roce_note":"note",'
        '"sales_cagr_15_to_20":true,"sales_cagr_value":"XX%","sales_cagr_note":"note"},'
        '"upcoming_meetings":[{"date":"DD MMM YYYY","event":"event description"}]'
        "}"
    )

    client   = genai.Client(api_key=GEMINI_API_KEY)
    # Target models for 2026. Updated to handle current versioning.
    # Fast models first (flash) — the big research JSON on Pro was a major chunk of the
    # 30-60s. Pro is kept as a last-resort fallback. Reorder if you prefer Pro's quality
    # over speed.
    MODELS   = ["gemini-3-flash-preview", "gemini-3.1-flash-lite-preview", "gemini-flash-latest", "gemini-3.1-pro-preview", "gemini-pro-latest"]
    last_err = "No models available"

    for model in MODELS:
        for attempt in range(3): # Try each model 3 times with backoff if 503
            try:
                cfg_args = {"temperature": 0.2, "max_output_tokens": 6000}
                if "2.5" in model or "3.0" in model: # Future proofing
                    try:
                        cfg_args["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
                    except Exception:
                        pass

                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(**cfg_args)
                )

                text = ""
                try: text = resp.text or ""
                except Exception: pass

                if not text:
                    try:
                        for cand in resp.candidates:
                            for part in cand.content.parts:
                                if hasattr(part, "text") and part.text and len(part.text) > 30:
                                    text += part.text
                    except Exception: pass

                if not text:
                    continue

                clean = text.replace("```json","").replace("```","").strip()
                s = clean.find("{")
                e = clean.rfind("}")
                if s == -1 or e == -1:
                    continue

                data = json.loads(clean[s:e+1])
                return data, None

            except Exception as ex:
                err = str(ex)
                last_err = err
                # 429 = Rate Limit, 503 = Overloaded, 500 = Internal Error
                if "429" in err or "quota" in err.lower() or "RESOURCE_EXHAUSTED" in err:
                    time.sleep(2 * (attempt + 1)) # Wait longer for quota
                    continue 
                if "503" in err or "unavailable" in err.lower() or "500" in err:
                    time.sleep(1 * (attempt + 1)) # Exponential backoff for demand
                    continue
                if "404" in err or "not found" in err.lower():
                    break # Skip to next model immediately for 404
                
                # For other errors, skip to next model
                break

    if "429" in last_err or "quota" in last_err.lower() or "RESOURCE_EXHAUSTED" in last_err:
        return None, "Gemini API Free Tier rate limit exceeded. Please wait a moment and try again."
    return None, f"All Gemini models failed. Error: {last_err[:200]}"


@cached(1800)           # P/E (slow .info call): 30 min
def fetch_pe(ticker):
    try:
        import yfinance as yf
        for suffix in [".NS", ".BO", ""]:
            try:
                t = yf.Ticker(ticker + suffix)
                pe = t.info.get("trailingPE") or t.info.get("forwardPE")
                if pe:
                    return round(float(pe), 2)
            except Exception:
                continue
    except Exception:
        pass
    return "N/A"

def fetch_stock_action_gemini(company):
    if not GEMINI_API_KEY:
        return "GEMINI_API_KEY not set. Cannot fetch summary."
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return "google-genai not installed."

    prompt = (
        f"You are a short, concise financial analyst. "
        f"Briefly summarize the most recent block deals, bulk deals, or significant change of hands for {company}. "
        "Return just a simple 2-3 sentence paragraph of facts. Do not use markdown, arrays, or JSON. Just plain text."
    )

    client = genai.Client(api_key=GEMINI_API_KEY)
    MODELS = ["gemini-3.1-flash-lite-preview", "gemini-3-flash-preview", "gemini-flash-latest", "gemini-pro-latest"]
    for model in MODELS:
        try:
            cfg_args = {"temperature": 0.2, "max_output_tokens": 1000}
            if "2.5" in model:
                try: cfg_args["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
                except Exception: pass
            resp = client.models.generate_content(
                model=model, contents=prompt, config=types.GenerateContentConfig(**cfg_args)
            )
            text = ""
            try: text = resp.text or ""
            except Exception: pass
            if not text:
                try:
                    for cand in resp.candidates:
                        for part in cand.content.parts:
                            if hasattr(part, "text") and part.text: text += part.text
                except Exception: pass
            if text:
                return text.strip()
        except Exception:
            continue
    return "No recent block deal information could be found or fetched."

def resolve_ticker_gemini(company):
    if not GEMINI_API_KEY:
        return ""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return ""

    prompt = f"Return ONLY the exact NSE (National Stock Exchange of India) ticker symbol for the company '{company}'. Example: For Reliance Industries return RELIANCE. Return strictly the uppercase ticker text and absolutely nothing else."
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    MODELS = ["gemini-3.1-flash-lite-preview", "gemini-3-flash-preview", "gemini-flash-latest", "gemini-pro-latest"]
    for model in MODELS:
        try:
            cfg_args = {"temperature": 0.0, "max_output_tokens": 10}
            resp = client.models.generate_content(
                model=model, contents=prompt, config=types.GenerateContentConfig(**cfg_args)
            )
            text = ""
            try: text = resp.text or ""
            except Exception: pass
            if not text:
                try:
                    for cand in resp.candidates:
                        for part in cand.content.parts:
                            if hasattr(part, "text") and part.text: text += part.text
                except Exception: pass
            if text:
                return text.strip().upper()
        except Exception: pass
    return ""


def _scrape_chartink_playwright(url, max_pages=3):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], "Playwright not installed"

    all_names = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120",
                viewport={"width": 1280, "height": 900}
            )
            page = context.new_page()
            page.goto(url, timeout=60000)
            try:
                page.wait_for_selector("table.scan-results-table", timeout=30000)
            except Exception:
                pass
            
            # Click the Run Scan button to run the scan with live data
            try:
                btn = page.locator("text='Run Scan'")
                if btn.count() > 0:
                    btn.first.click()
                    time.sleep(4)  # Wait for results to refresh
            except Exception:
                pass
            
            time.sleep(2)

            for page_num in range(1, max_pages + 1):
                rows_data = page.evaluate(
                    "() => {"
                    "  const table = document.querySelector('table.scan-results-table');"
                    "  if (!table) return [];"
                    "  const result = [];"
                    "  table.querySelectorAll('tbody tr').forEach(function(row) {"
                    "    const cells = Array.from(row.querySelectorAll('td')).map(function(c) { return c.innerText.trim(); });"
                    "    result.push(cells);"
                    "  });"
                    "  return result;"
                    "}"
                )
                names = []
                for cells in (rows_data or []):
                    if len(cells) >= 2:
                        name = cells[1].strip()
                        if name and len(name) > 1:
                            try: float(name)
                            except ValueError: names.append(name)

                if not names:
                    break
                all_names.extend(names)

                if page_num >= max_pages:
                    break

                clicked = page.evaluate(
                    "() => {"
                    "  const sels = ['button[aria-label=\"next page\"]','.footer__navigation__page-btn--next','.next-page button'];"
                    "  for (const s of sels) {"
                    "    const b = document.querySelector(s);"
                    "    if (b && !b.disabled) { b.click(); return true; }"
                    "  }"
                    "  const btns = document.querySelectorAll('button, a');"
                    "  for (const b of btns) {"
                    "    const t = b.innerText.trim();"
                    "    if ((t === '>' || t === '›' || t === 'Next') && !b.disabled) { b.click(); return true; }"
                    "  }"
                    "  return false;"
                    "}"
                )
                if not clicked:
                    break
                time.sleep(4)

            browser.close()
    except Exception as e:
        return [], str(e)

    return list(dict.fromkeys(all_names)), None


def scrape_chartink_http(url):
    """FAST path: run a saved Chartink screener via its JSON 'process' endpoint — no browser.
    Returns (names, None) on success, or ([], reason) so the caller can fall back."""
    try:
        import re
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = cffi_requests.get(url, headers=headers, impersonate="chrome120", timeout=10)
        if r.status_code != 200:
            return [], f"page status {r.status_code}"
        html = r.text

        m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
        if not m:
            return [], "no csrf token"
        csrf = m.group(1)

        clause = None
        for pat in (
            r'name="scan_clause"[^>]*value="([^"]*)"',
            r'"scan_clause"\s*:\s*"((?:[^"\\]|\\.)*)"',
            r'scan_clause\\?":\\?"((?:[^"\\]|\\.)*)"',
        ):
            mm = re.search(pat, html)
            if mm:
                clause = mm.group(1)
                break
        if not clause:
            return [], "no scan clause"
        # Unescape HTML entities and backslash escapes
        clause = clause.replace("&quot;", '"').replace("&amp;", "&")
        try:
            clause = clause.encode().decode("unicode_escape")
        except Exception:
            pass

        post_headers = {
            **headers,
            "x-csrf-token":     csrf,
            "x-requested-with": "XMLHttpRequest",
            "Referer":          url,
            "Origin":           "https://chartink.com",
            "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
        }
        resp = cffi_requests.post(
            "https://chartink.com/screener/process",
            data={"scan_clause": clause}, headers=post_headers,
            cookies=r.cookies, impersonate="chrome120", timeout=15,
        )
        if resp.status_code != 200:
            return [], f"process status {resp.status_code}"
        rows = (resp.json() or {}).get("data", []) or []
        names = []
        for row in rows:
            sym = row.get("nsecode") or row.get("name") or row.get("bsecode")
            if sym:
                names.append(str(sym).strip())
        return list(dict.fromkeys(names)), None
    except Exception as e:
        return [], str(e)


def scrape_chartink(url, max_pages=3):
    """Dispatcher: try the fast HTTP endpoint first; fall back to the Playwright scrape
    only if HTTP returns nothing."""
    names, err = scrape_chartink_http(url)
    if names:
        return names, None
    return _scrape_chartink_playwright(url, max_pages=max_pages)


_nse_cookie_cache = {"cookie": None, "ts": 0}
_nse_cookie_lock  = _threading.Lock()

def _get_nse_cookies(session_headers):
    """Fetch NSE session cookies, reusing them for up to 3 min across requests."""
    now = time.time()
    with _nse_cookie_lock:
        if _nse_cookie_cache["cookie"] and now - _nse_cookie_cache["ts"] < 180:
            return _nse_cookie_cache["cookie"]
    import urllib.request
    req0 = urllib.request.Request("https://www.nseindia.com", headers=session_headers)
    with urllib.request.urlopen(req0, timeout=3) as r:
        raw_cookies = r.headers.get_all("Set-Cookie") or []
        cookie_str  = "; ".join([c.split(";")[0] for c in raw_cookies])
    with _nse_cookie_lock:
        _nse_cookie_cache["cookie"] = cookie_str
        _nse_cookie_cache["ts"]     = now
    return cookie_str

def get_nse_data(endpoint):
    import urllib.request, urllib.error
    session_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120",
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer":         "https://www.nseindia.com/",
        "Connection":      "keep-alive",
    }
    try:
        session_headers["Cookie"] = _get_nse_cookies(session_headers)
        req = urllib.request.Request(
            f"https://www.nseindia.com{endpoint}",
            headers=session_headers
        )
        import gzip, io
        with urllib.request.urlopen(req, timeout=3) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
    except Exception as e:
        # On failure, drop cached cookies so the next call re-handshakes.
        with _nse_cookie_lock:
            _nse_cookie_cache["cookie"] = None
        return None

@cached(180)            # option PCR: 3 min
def fetch_pcr_data(symbol="NIFTY"):
    try:
        url = f"https://webapi.niftytrader.in/webapi/option/option-chain-data?symbol={symbol.lower()}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
            "Referer": "https://www.niftytrader.in/"
        }
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode('utf-8'))
            res = data.get("resultData", {})
            op_data = res.get("opDatas", [])
            
            if not op_data: 
                return None
            
            # Aggregate OI per expiry
            exp_aggregates = {}
            for item in op_data:
                exp = item.get("expiry_date", "")
                if "T" in exp: exp = exp.split("T")[0]
                if not exp: continue
                
                if exp not in exp_aggregates:
                    exp_aggregates[exp] = {"ce_oi": 0, "pe_oi": 0}
                    
                exp_aggregates[exp]["ce_oi"] += item.get("calls_oi", 0)
                exp_aggregates[exp]["pe_oi"] += item.get("puts_oi", 0)

            expiries = sorted(list(exp_aggregates.keys()))
            if not expiries:
                return None
                
            def calc_pcr(exp):
                totals = exp_aggregates.get(exp, {})
                ce_oi = totals.get("ce_oi", 0)
                pe_oi = totals.get("pe_oi", 0)
                pcr = round(pe_oi / ce_oi, 2) if ce_oi > 0 else 0
                return {
                    "expiry": exp,
                    "ce_oi": ce_oi,
                    "pe_oi": pe_oi,
                    "pcr": pcr,
                    "signal": "Bullish" if pcr > 1.2 else "Bearish" if pcr < 0.8 else "Neutral",
                }

            weekly = calc_pcr(expiries[0]) if len(expiries) > 0 else None
            monthly = calc_pcr(expiries[1]) if len(expiries) > 1 else None

            return {"weekly": weekly, "monthly": monthly, "symbol": symbol}
            
    except Exception as e:
        print(f"PCR error for {symbol}: {e}")
        return None


@cached(300)            # nifty 1y chart: 5 min
def fetch_nifty_chart_data():
    try:
        import yfinance as yf
        ticker = yf.Ticker("^NSEI")
        df     = ticker.history(period="1y", interval="1d")
        if len(df) < 10: return None

        df.index = df.index.tz_localize(None)
        closes   = df["Close"].tolist()

        k21  = 2 / 22
        ema21 = [closes[0]]
        for c in closes[1:]:
            ema21.append(c * k21 + ema21[-1] * (1 - k21))

        dma200 = []
        for i in range(len(closes)):
            if i < 199:
                dma200.append(None)
            else:
                dma200.append(round(sum(closes[i-199:i+1]) / 200, 2))

        current_200dma = dma200[-1]
        current_price  = round(closes[-1], 2)
        above_200dma   = current_price > current_200dma if current_200dma else None

        records = []
        for i, (idx, row) in enumerate(df.iterrows()):
            records.append({
                "date":   idx.strftime("%Y-%m-%d"),
                "open":   round(float(row["Open"]),   2),
                "high":   round(float(row["High"]),   2),
                "low":    round(float(row["Low"]),    2),
                "close":  round(float(row["Close"]),  2),
                "volume": int(row["Volume"]),
                "ema21":  round(ema21[i], 2),
                "dma200": dma200[i],
            })

        return {
            "ohlcv":         records,
            "current_price": current_price,
            "current_ema21": round(ema21[-1], 2),
            "current_200dma":current_200dma,
            "above_200dma":  above_200dma,
            "days":          len(records),
        }
    except Exception as e:
        return None

@cached(600)            # FII stats: 10 min
def fetch_fii_data():
    try:
        data = get_nse_data("/api/fii-stats?type=equity")
        if not data:
            data = get_nse_data("/api/fii-stats")
        if not data:
            return None

        result = {}
        if isinstance(data, list):
            for item in data:
                name = str(item.get("name","") or item.get("type","")).lower()
                if "index" in name or "future" in name:
                    result["futures"] = {
                        "long":       item.get("longContracts",  item.get("longOI", 0)),
                        "short":      item.get("shortContracts", item.get("shortOI", 0)),
                        "net":        item.get("netContracts",   item.get("netOI", 0)),
                        "long_val":   item.get("longValue",  0),
                        "short_val":  item.get("shortValue", 0),
                        "label":      "Index Futures",
                    }
                elif "option" in name:
                    result["options"] = {
                        "long":  item.get("longContracts",  0),
                        "short": item.get("shortContracts", 0),
                        "net":   item.get("netContracts",   0),
                        "label": "Index Options",
                    }
        elif isinstance(data, dict):
            result = data

        return result if result else None
    except Exception as e:
        return None

@cached(600)            # FII derivatives: 10 min
def fetch_fii_derivative_stats():
    try:
        data = get_nse_data("/api/participant-wise-trading-date-wise?tradeDate=&category=FIIS")
        if not data:
            data = get_nse_data("/api/participant-wise-open-interest?type=FIIS")
        if not data:
            return None
        return data
    except Exception as e:
        return None


# (group, display name, yfinance symbol, unit)
GLOBAL_ASSETS = [
    # Commodities
    ("commodities", "Gold",          "GC=F", "$/oz"),
    ("commodities", "Silver",        "SI=F", "$/oz"),
    ("commodities", "Crude (WTI)",   "CL=F", "$/bbl"),
    ("commodities", "Crude (Brent)", "BZ=F", "$/bbl"),
    ("commodities", "Natural Gas",   "NG=F", "$/MMBtu"),
    ("commodities", "Copper",        "HG=F", "$/lb"),
    # Asia
    ("asia", "Nikkei 225 (Japan)",     "^N225",     ""),
    ("asia", "Hang Seng (HK)",         "^HSI",      ""),   # real index, not the India ETF
    ("asia", "Shanghai Composite",     "000001.SS", ""),
    ("asia", "KOSPI (Korea)",          "^KS11",     ""),
    ("asia", "Taiwan Weighted",        "^TWII",     ""),
    ("asia", "ASX 200 (Australia)",    "^AXJO",     ""),
    ("asia", "Sensex (India)",         "^BSESN",    ""),
    ("asia", "Nifty 50 (India)",       "^NSEI",     ""),
    # Europe
    ("europe", "DAX (Germany)",        "^GDAXI",    ""),
    ("europe", "FTSE 100 (UK)",        "^FTSE",     ""),
    ("europe", "CAC 40 (France)",      "^FCHI",     ""),
    ("europe", "Euro Stoxx 50",        "^STOXX50E", ""),
    ("europe", "FTSE MIB (Italy)",     "FTSEMIB.MI",""),
    ("europe", "IBEX 35 (Spain)",      "^IBEX",     ""),
    # US
    ("us", "Dow Jones",        "^DJI",  ""),
    ("us", "S&P 500",          "^GSPC", ""),
    ("us", "Nasdaq",           "^IXIC", ""),
    ("us", "Russell 2000",     "^RUT",  ""),
    ("us", "S&P Futures",      "ES=F",  ""),
    ("us", "Nasdaq Futures",   "NQ=F",  ""),
    ("us", "Dow Futures",      "YM=F",  ""),
    ("us", "VIX (Volatility)", "^VIX",  ""),
    # Currencies
    ("currencies", "USD / INR", "USDINR=X", ""),
    ("currencies", "EUR / USD", "EURUSD=X", ""),
    ("currencies", "USD / JPY", "JPY=X",    ""),
]

@cached(60)            # fresh values, but batch makes even a cache miss fast
def fetch_global_market_data():
    """All indices/commodities/FX in ONE batched yfinance call (was 16 separate calls)."""
    import yfinance as yf
    from datetime import datetime, timezone
    symbols = [a[2] for a in GLOBAL_ASSETS]
    df = None
    try:
        df = yf.download(symbols, period="5d", interval="1d", group_by="ticker",
                         threads=True, progress=False, auto_adjust=False)
    except Exception:
        df = None

    def last_two(sym):
        try:
            sub = df[sym]
            closes = [float(x) for x in sub["Close"].tolist() if x == x]
            if closes:
                return closes[-1], (closes[-2] if len(closes) > 1 else closes[-1])
        except Exception:
            pass
        return None, None

    groups = {"commodities": [], "asia": [], "europe": [], "us": [], "currencies": []}
    movers = []
    for group, name, sym, unit in GLOBAL_ASSETS:
        curr, prev = last_two(sym)
        if curr is None:
            groups[group].append({"name": name, "price": "N/A", "change": 0, "pct": 0, "unit": unit})
            continue
        change = curr - prev
        pct = (change / prev * 100) if prev else 0
        groups[group].append({
            "name": name, "price": round(curr, 2), "change": round(change, 2),
            "pct": round(pct, 2), "unit": unit,
        })
        if group in ("asia", "europe", "us") and prev:
            movers.append({"name": name, "pct": round(pct, 2)})

    movers.sort(key=lambda x: abs(x["pct"]), reverse=True)
    return {
        **groups,
        "movers":  movers[:5],
        "updated": datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
    }


@cached(300)           # live market news: 5 min
def fetch_global_market_news():
    """Live, market-only headlines from Google News RSS — newest first, top 15."""
    import concurrent.futures
    queries = [
        "stock market today when:2d",
        "world stock markets when:2d",
        "Wall Street Nasdaq S&P when:2d",
        "Sensex Nifty India market when:2d",
    ]
    all_items = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(queries)) as pool:
        futs = [pool.submit(_fetch_google_news, q, 10) for q in queries]
        for f in concurrent.futures.as_completed(futs):
            try:
                all_items += f.result() or []
            except Exception:
                pass
    seen, out = set(), []
    for it in sorted(all_items, key=lambda x: x.get("timestamp", 0), reverse=True):
        h = it["headline"].strip().lower()
        if h in seen:
            continue
        seen.add(h)
        out.append(it)
        if len(out) >= 15:
            break
    return out


@cached(21600)         # economic events: 6h
def fetch_econ_events_grounded():
    """This week's key macro events via Gemini + Google Search grounding (live, not memory).
    Returns [] if grounding/keys unavailable so the UI simply hides the section."""
    if not GEMINI_API_KEY:
        return []
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = (
            "Use web search to find the most important SCHEDULED macroeconomic events for the "
            "current week across US, Eurozone, UK, Japan, China and India — central-bank rate "
            "decisions, CPI/inflation, PMI, GDP and jobs reports. "
            "Return ONLY JSON (no markdown): "
            "[{\"date\":\"Mon DD\",\"region\":\"US|EU|UK|Japan|China|India\","
            "\"title\":\"short event name\",\"importance\":\"high|medium\"}]. "
            "Max 10 items, sorted by date."
        )
        try:
            tools = [types.Tool(google_search=types.GoogleSearch())]
        except Exception:
            tools = None
        for model in ["gemini-3-flash-preview", "gemini-flash-latest"]:
            try:
                cfg = types.GenerateContentConfig(temperature=0.2,
                                                  tools=tools) if tools else types.GenerateContentConfig(temperature=0.2)
                resp = client.models.generate_content(model=model, contents=prompt, config=cfg)
                text = resp.text or ""
                s, e = text.find("["), text.rfind("]")
                if s != -1 and e != -1:
                    return json.loads(text[s:e + 1])
            except Exception:
                continue
    except Exception:
        pass
    return []


@cached(900)            # global news (Gemini): 15 min
def fetch_global_news_gemini():
    if not GEMINI_API_KEY:
        return []
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = (
            "You are a global market news aggregator. "
            "Identify the top 7 most important global market news stories from the last 24 hours. "
            "Focus on US markets, European markets, and major commodities/currencies. "
            "Return ONLY valid JSON — no markdown, no backticks. "
            "JSON structure: [{\"headline\": \"...\", \"summary\": \"...\", \"source\": \"...\"}]"
        )
        MODELS = ["gemini-3.1-flash-lite-preview", "gemini-3-flash-preview", "gemini-flash-latest"]
        for model in MODELS:
            try:
                resp = client.models.generate_content(model=model, contents=prompt)
                text = resp.text or ""
                clean = text.replace("```json","").replace("```","").strip()
                s = clean.find("[")
                e = clean.rfind("]")
                if s != -1 and e != -1:
                    return json.loads(clean[s:e+1])
            except Exception:
                continue
    except Exception:
        pass
    return []


@cached(900)            # war news (Gemini): 15 min
def fetch_war_news_gemini():
    if not GEMINI_API_KEY:
        return []
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = (
            "You are a global war and geopolitical news aggregator. "
            "Identify the most important recent war-related news stories from the last 24-48 hours. "
            "Provide news from exactly these 4 perspectives: US, Iran, Crude Oil, and Hormuz. "
            "Return a maximum of 15 news items in total. "
            "Return ONLY valid JSON \u2014 no markdown, no backticks. "
            "JSON structure: [{\"headline\": \"...\", \"summary\": \"...\", \"perspective\": \"US\"|\"Iran\"|\"Crude Oil\"|\"Hormuz\", \"source\": \"...\"}]"
        )
        MODELS = ["gemini-3.1-flash-lite-preview", "gemini-3-flash-preview", "gemini-flash-latest"]
        for model in MODELS:
            try:
                resp = client.models.generate_content(model=model, contents=prompt)
                text = resp.text or ""
                clean = text.replace("```json","").replace("```","").strip()
                s = clean.find("[")
                e = clean.rfind("]")
                if s != -1 and e != -1:
                    return json.loads(clean[s:e+1])
            except Exception:
                continue
    except Exception:
        pass
    return []


def _parse_rss_dt(pub_date):
    from datetime import datetime, timezone
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            dt = datetime.strptime(pub_date[:31].strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
    return None

def _time_ago(dt):
    if not dt:
        return ""
    from datetime import datetime, timezone
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    secs = max(0, secs)
    if secs < 120:    return "just now"
    if secs < 3600:   return f"{int(secs//60)}m ago"
    if secs < 86400:  return f"{int(secs//3600)}h ago"
    return f"{int(secs//86400)}d ago"

def _fetch_google_news(query, limit=14):
    """Live headlines from Google News RSS for a query, newest first."""
    import urllib.request, urllib.parse, xml.etree.ElementTree as ET
    items = []
    try:
        q   = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            root = ET.fromstring(r.read())
        ch = root.find("channel") or root
        for item in ch.findall("item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            pub   = (item.findtext("pubDate") or "").strip()
            src_el = item.find("source")
            src = (src_el.text if (src_el is not None and src_el.text) else "")
            # Google News titles read "Headline - Source"; split the source out.
            headline = title
            if " - " in title:
                head, tail = title.rsplit(" - ", 1)
                if head and tail:
                    headline = head
                    if not src:
                        src = tail.strip()
            if not src:
                src = "Google News"
            dt = _parse_rss_dt(pub)
            if headline and len(headline) > 12:
                items.append({
                    "headline":  headline.strip(),
                    "source":    src,
                    "date":      dt.strftime("%d %b %Y").upper() if dt else "",
                    "time_ago":  _time_ago(dt),
                    "timestamp": dt.timestamp() if dt else 0,
                    "link":      link,
                })
        items.sort(key=lambda x: x["timestamp"], reverse=True)
    except Exception:
        pass
    return items[:limit]

# Two tracked conflicts. `when:7d` keeps Google News to the last week for freshness.
WAR_CONFLICTS = [
    {"key": "iran",    "flag": "🛢️", "title": "US · Iran · Middle East", "query": "Iran Israel US war strike when:7d", "color": "#ef4444"},
    {"key": "ukraine", "flag": "🇺🇦", "title": "Russia · Ukraine",        "query": "Russia Ukraine war when:7d",          "color": "#3b82f6"},
]

def fetch_war_news_rss():
    """Live war news from Google News RSS — both conflicts fetched in parallel."""
    import concurrent.futures
    from datetime import datetime, timezone
    conflicts = [dict(c) for c in WAR_CONFLICTS]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(conflicts)) as pool:
        futs = {c["key"]: pool.submit(_fetch_google_news, c["query"], 14) for c in conflicts}
        for c in conflicts:
            try:
                c["items"] = futs[c["key"]].result()
            except Exception:
                c["items"] = []
    return {
        "conflicts": conflicts,
        "updated":   datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
    }


# ══════════════════════════════════════════════════════════════
#  API ROUTES
# ══════════════════════════════════════════════════════════════

@app.route('/api/war_news', methods=['GET'])
def get_war_news():
    # No server-side cache: the user wants the latest headlines on every open.
    # Two parallel RSS pulls keep it fast (~1-2s).
    return jsonify(fetch_war_news_rss())

def fetch_telegram_messages(channel_name="marketwisdom_official"):
    """Scrapes the public preview page of a Telegram channel."""
    url = f"https://t.me/s/{channel_name}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        res = cffi_requests.get(url, headers=headers, impersonate="chrome110", timeout=10)
        if res.status_code != 200:
            return {"error": f"Failed to fetch Telegram channel (Status {res.status_code})"}
            
        soup = BeautifulSoup(res.content, "html.parser")
        messages = []
        
        # Telegram preview page uses 'tgme_widget_message' class for each post
        post_elements = soup.find_all("div", class_="tgme_widget_message")
        
        for post in post_elements:
            # Extract text
            text_el = post.find("div", class_="tgme_widget_message_text")
            # Convert <br> to newlines for better JSON formatting
            if text_el:
                for br in text_el.find_all("br"):
                    br.replace_with("\\n")
                text = text_el.get_text().replace("\\n", "\n").strip()
            else:
                text = ""
                
            # Extract timestamp
            time_el = post.find("time")
            timestamp = time_el.get("datetime") if time_el else ""
            
            # Extract post link
            link_el = post.find("a", class_="tgme_widget_message_date")
            link = link_el.get("href") if link_el else ""
            
            # Skip empty messages without text (like pure image posts if we don't handle them)
            # Or we can include them. Let's include them.
            messages.append({
                "text": text,
                "timestamp": timestamp,
                "link": link
            })
            
        # Return the last 20 messages in reverse chronological order (newest first)
        messages.reverse()
        return {
            "channel": channel_name,
            "messages": messages[:20],
            "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        return {"error": str(e)}

@app.route('/api/telegram_feed', methods=['GET'])
def telegram_feed():
    data = fetch_telegram_messages("marketwisdom_official")
    return jsonify(data)



@app.route("/api/global_market")
def api_global_market():
    try:
        import concurrent.futures
        from datetime import datetime
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            f_mkt  = pool.submit(fetch_global_market_data)
            f_news = pool.submit(fetch_global_market_news)
            f_evt  = pool.submit(fetch_econ_events_grounded)

            def safe(f, d):
                try:
                    return f.result()
                except Exception:
                    return d

            market = safe(f_mkt, {}) or {}
            market["news"]      = safe(f_news, [])
            market["events"]    = safe(f_evt, [])
            market["timestamp"] = datetime.now().strftime("%d %b %Y  %H:%M:%S")
        return jsonify(market)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/stock", methods=["POST"])
def api_stock():
    try:
        body    = request.get_json()
        company = (body.get("company","") or "").strip()
        ticker  = (body.get("ticker","")  or "").strip().upper()

        if not company:
            return jsonify({"error": "Company name is required"}), 400

        # ── Resolve the ticker cheaply FIRST so we don't have to wait for the big
        # Gemini analysis before starting the data fetches. If the client didn't pass
        # a ticker, a tiny 10-token Gemini call (~1s) gets it. ──
        if not ticker:
            ticker = resolve_ticker_gemini(company) or ""
        t = ticker

        # ── Now fire EVERYTHING at once: the heavy Gemini research call runs alongside
        # price / screener / candles / news instead of blocking them. Total time becomes
        # ~the single slowest call rather than the sum of all of them. ──
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            tasks = {"gemini": pool.submit(fetch_gemini, company)}
            if t:
                tasks["price"]    = pool.submit(fetch_live_price, t)
                tasks["screener"] = pool.submit(fetch_screener_data, t)
                tasks["ohlcv"]    = pool.submit(fetch_ohlcv, t)
            tasks["news"] = pool.submit(fetch_news_rss, company)

            def _safe(key, default):
                fut = tasks.get(key)
                if not fut:
                    return default
                try:
                    return fut.result()
                except Exception:
                    return default

            data, err = _safe("gemini", (None, "Gemini analysis failed"))
            if err or not data:
                return jsonify({"error": err or "No analysis available"}), 400

            # Prefer a real ticker from Gemini's analysis if the client gave none and
            # the cheap resolver came back empty (data fetches just won't have run).
            if not t and data.get("ticker"):
                t = data["ticker"]

            if t and "price" not in tasks:
                # Rare path: ticker only became known from the Gemini analysis, so the
                # data fetches weren't started above. Run them now (in parallel).
                tasks["price"]    = pool.submit(fetch_live_price, t)
                tasks["screener"] = pool.submit(fetch_screener_data, t)
                tasks["ohlcv"]    = pool.submit(fetch_ohlcv, t)

            if t:
                price, hi52, lo52 = _safe("price", (None, None, None))
                if price:
                    data["current_price"] = str(price)
                    data["week_52_high"]  = str(hi52)
                    data["week_52_low"]   = str(lo52)

                sc = _safe("screener", {}) or {}
                if sc.get("holdings"):
                    data["holdings"] = sc["holdings"]
                if sc.get("quarterly_results"):
                    data["quarterly_results"] = sc["quarterly_results"]

                ohlcv = _safe("ohlcv", [])
                if ohlcv:
                    data["ohlcv"] = ohlcv

            news = _safe("news", [])
            if news:
                data["news"] = news

        today    = date.today()
        live_mtg = []
        for m in data.get("upcoming_meetings", []):
            ds = m.get("date","")
            for fmt in ["%d %b %Y", "%d %B %Y"]:
                try:
                    if datetime.strptime(ds.strip(), fmt).date() >= today:
                        live_mtg.append(m)
                    break
                except Exception:
                    pass
        data["upcoming_meetings"] = live_mtg

        return jsonify(data)

    except Exception as ex:
        import traceback
        tb = traceback.format_exc()
        print(f"Server Error: {ex}\n{tb}")
        return jsonify({"error": f"Server error: {str(ex)}"}), 400


# ════════════════════════════════════════════════════════════════════════
#  STOCK OVERVIEW  —  same content/criteria as Stock Research, but sourced the
#  "best-in-class" way: numbers are COMPUTED from price history or pulled from
#  real sources (NSE quote + Screener.in) instead of being generated by the LLM.
#  The LLM is used only for the qualitative description. Much faster + accurate.
# ════════════════════════════════════════════════════════════════════════

def _ema_series(values, period):
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def _rsi(closes, period=14):
    """Wilder's 14-period RSI."""
    if len(closes) < period + 1:
        return None
    gains  = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)

def _pct(p):
    if p is None:
        return "N/A"
    return f"+{p}%" if p >= 0 else f"{p}%"

def fetch_price_history(ticker, period="10y"):
    """Daily OHLCV history from yfinance (.NS then .BO). Returns a list of dicts."""
    try:
        import yfinance as yf
        for suffix in [".NS", ".BO"]:
            try:
                df = yf.Ticker(ticker + suffix).history(period=period, interval="1d")
                if len(df) < 30:
                    continue
                df.index = df.index.tz_localize(None)
                rows = []
                for idx, r in df.iterrows():
                    rows.append({
                        "date":   idx.strftime("%Y-%m-%d"),
                        "open":   float(r["Open"]),  "high":  float(r["High"]),
                        "low":    float(r["Low"]),   "close": float(r["Close"]),
                        "volume": int(r["Volume"]),
                    })
                return rows
            except Exception:
                continue
    except Exception:
        pass
    return []

def compute_cagr(history):
    """YTD total return + 3/5/10-yr CAGR from a daily close series."""
    if not history or len(history) < 2:
        return None
    closes = [h["close"] for h in history]
    last   = closes[-1]
    out    = {}
    cur_year = history[-1]["date"][:4]
    ytd_start = next((h["close"] for h in history if h["date"][:4] == cur_year and h["close"] > 0), None)
    if ytd_start:
        out["ytd"] = _pct(round((last / ytd_start - 1) * 100, 1))
    for years, key in [(3, "3yr"), (5, "5yr"), (10, "10yr")]:
        back = years * 252
        if len(closes) > back and closes[-back-1] > 0:
            out[key] = _pct(round(((last / closes[-back-1]) ** (1 / years) - 1) * 100, 1))
        else:
            out[key] = "N/A"
    return out

def compute_technicals(history):
    """RSI, 21-EMA position, breakout and volume-spurt flags (all computed)."""
    if not history or len(history) < 30:
        return None
    closes = [h["close"]  for h in history]
    vols   = [h["volume"] for h in history]
    highs  = [h["high"]   for h in history]
    ema21  = _ema_series(closes, 21)
    rsi    = _rsi(closes, 14)
    last_c, last_e, last_v = closes[-1], ema21[-1], vols[-1]
    look      = highs[-61:-1] if len(highs) > 61 else highs[:-1]
    prior_hi  = max(look) if look else last_c
    vwin      = vols[-21:-1] if len(vols) > 21 else vols[:-1]
    avg_vol   = (sum(vwin) / len(vwin)) if vwin else 0
    vmult     = round(last_v / avg_vol, 1) if avg_vol > 0 else None
    breakout  = (last_c >= prior_hi) and (avg_vol > 0 and last_v > 1.2 * avg_vol)
    vol_spurt = (avg_vol > 0 and last_v > 2.0 * avg_vol)
    sig = "Overbought" if (rsi or 0) >= 70 else "Oversold" if (rsi is not None and rsi <= 30) else "Neutral"
    return {
        "rsi_value":  str(rsi) if rsi is not None else "N/A",
        "rsi_signal": sig,
        "rsi_note":   f"14-period Wilder RSI from daily closes — {sig.lower()} momentum.",
        "above_21_ema_daily": bool(last_c > last_e),
        "ema_note":   f"Last close Rs.{round(last_c,2)} vs 21-EMA Rs.{round(last_e,2)}.",
        "price_volume_breakout": bool(breakout),
        "breakout_note": f"60-day high Rs.{round(prior_hi,2)}; volume {vmult if vmult is not None else 'N/A'}x average.",
        "volume_spurt": bool(vol_spurt),
        "volume_note":  f"Latest volume {vmult if vmult is not None else 'N/A'}x the 20-day average.",
    }

def build_chart_ohlcv(history, days=126):
    """Last ~6 months of OHLCV with 21-EMA and 200-DMA for the candlestick chart."""
    if not history:
        return []
    closes_all = [h["close"] for h in history]
    ema_all = _ema_series(closes_all, 21)
    dma = []
    for i in range(len(closes_all)):
        dma.append(round(sum(closes_all[i-199:i+1]) / 200, 2) if i >= 199 else None)
    out, start = [], max(0, len(history) - days)
    for i in range(start, len(history)):
        h = history[i]
        out.append({
            "date":  h["date"],
            "open":  round(h["open"], 2),  "high":  round(h["high"], 2),
            "low":   round(h["low"], 2),   "close": round(h["close"], 2),
            "volume": h["volume"], "ema21": round(ema_all[i], 2), "dma200": dma[i],
        })
    return out

def _parse_screener_tables(soup):
    """Extract shareholding + quarterly tables from a parsed Screener page."""
    result = {}
    try:
        sec = soup.find(id="shareholding")
        if sec is None:
            for s in soup.find_all("section"):
                if "Shareholding Pattern" in (s.get_text() or ""):
                    sec = s
                    break
        if sec is not None:
            best, best_cols = None, 0
            for t in sec.find_all("table"):
                cols = len(t.select("thead th"))
                if cols > best_cols:
                    best_cols, best = cols, t
            if best is not None:
                hdrs, rows = _extract_screener_table(best)
                hdrs = [h for h in hdrs if h and len(h) > 2]
                rows = {k: [c.replace('%', '') for c in v] for k, v in rows.items()}
                holdings = _build_holdings(hdrs, rows)
                if holdings:
                    result['holdings'] = holdings
    except Exception:
        pass
    try:
        sec = soup.find(id="quarters")
        if sec is None:
            for s in soup.find_all("section"):
                if (s.get_text() or "").strip().startswith("Quarterly"):
                    sec = s
                    break
        if sec is not None:
            table = sec.find("table")
            if table is not None:
                hdrs, rows = _extract_screener_table(table)
                hdrs = [h for h in hdrs if h and len(h) > 1]
                rows = {k: [c.replace(',', '') for c in v] for k, v in rows.items()}
                quarterly = _build_quarterly(hdrs, rows)
                if quarterly:
                    result['quarterly_results'] = quarterly
    except Exception:
        pass
    return result

def _parse_screener_ratios(soup):
    """Best-effort: ROE, ROCE (top ratios) and 5-yr compounded sales growth."""
    import re
    out = {}
    try:
        lis = soup.select("#top-ratios li") or soup.find_all("li")
        for li in lis:
            t   = " ".join((li.get_text() or "").split())
            low = t.lower()
            m   = re.search(r'(-?\d+(?:\.\d+)?)\s*%', t) or re.search(r'(-?\d+(?:\.\d+)?)', t)
            if not m:
                continue
            if 'roce' in low and 'roce' not in out:
                out['roce'] = m.group(1)
            elif ('roe' in low or 'return on equity' in low) and 'roe' not in out:
                out['roe'] = m.group(1)
    except Exception:
        pass
    try:
        for tbl in soup.find_all("table"):
            if "Compounded Sales Growth" in (tbl.get_text() or ""):
                for tr in tbl.find_all("tr"):
                    cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                    if len(cells) >= 2 and "5 year" in cells[0].lower():
                        m = re.search(r'(-?\d+(?:\.\d+)?)', cells[1])
                        if m:
                            out['sales_growth_5y'] = m.group(1)
                break
    except Exception:
        pass
    return out

@cached(1800)           # fundamentals change slowly: 30 min
def fetch_screener_full(ticker):
    """One Screener.in GET → shareholding, quarterly, ROE/ROCE, 5-yr sales growth."""
    url = f"https://www.screener.in/company/{ticker}/consolidated/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        res = cffi_requests.get(url, headers=headers, impersonate="chrome120", timeout=8)
        if res.status_code != 200:
            return {}
        soup = BeautifulSoup(res.content, "html.parser")
        out  = _parse_screener_tables(soup)
        ratios = _parse_screener_ratios(soup)
        if ratios:
            out["ratios"] = ratios
        return out
    except Exception:
        return {}

def _fundamentals_from_ratios(ratios):
    def num(x):
        try:
            return float(str(x).replace('%', '').strip())
        except Exception:
            return None
    roe, roce, sg = num(ratios.get('roe')), num(ratios.get('roce')), num(ratios.get('sales_growth_5y'))
    return {
        "roe_above_20":        (roe > 20) if roe is not None else None,
        "roe_value":           (f"{roe}%" if roe is not None else "N/A"),
        "roe_note":            "Return on equity (Screener.in).",
        "roce_above_20":       (roce > 20) if roce is not None else None,
        "roce_value":          (f"{roce}%" if roce is not None else "N/A"),
        "roce_note":           "Return on capital employed (Screener.in).",
        "sales_cagr_15_to_20": (15 <= sg <= 20) if sg is not None else None,
        "sales_cagr_value":    (f"{sg}%" if sg is not None else "N/A"),
        "sales_cagr_note":     "5-year compounded sales growth (Screener.in).",
    }

@cached(1800)
def fetch_overview_meta_gemini(company):
    """Small, fast LLM call for qualitative fields ONLY (no numbers)."""
    if not GEMINI_API_KEY:
        return {}
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return {}
    prompt = ('Return ONLY JSON, no markdown: '
              '{"company_name":"full official name","ticker":"NSE ticker",'
              '"sector":"sector","description":"2 concise lines on what the company does"} '
              'for the Indian listed company: ' + company)
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        for model in ["gemini-3-flash-preview", "gemini-3.1-flash-lite-preview", "gemini-flash-latest"]:
            try:
                resp = client.models.generate_content(
                    model=model, contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=400))
                text = resp.text or ""
                s, e = text.find("{"), text.rfind("}")
                if s != -1 and e != -1:
                    return json.loads(text[s:e+1])
            except Exception:
                continue
    except Exception:
        pass
    return {}

def resolve_ticker_screener(query):
    """Resolve a company name → NSE ticker using Screener's free search API."""
    try:
        res = cffi_requests.get(
            "https://www.screener.in/api/company/search/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"},
            impersonate="chrome120", timeout=6)
        if res.status_code == 200:
            arr = res.json()
            if arr:
                slug = [p for p in (arr[0].get("url", "") or "").split("/") if p]
                if len(slug) >= 2 and slug[0] == "company":
                    return slug[1].upper()
    except Exception:
        pass
    return ""

@cached(300)            # whole assembled overview: 5 min
def _build_overview(ticker, company):
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        f_quote = pool.submit(fetch_quote_nse, ticker)
        f_hist  = pool.submit(fetch_price_history, ticker, "10y")
        f_scr   = pool.submit(fetch_screener_full, ticker)
        f_meta  = pool.submit(fetch_overview_meta_gemini, company)
        f_news  = pool.submit(fetch_news_rss, company)

        def safe(f, d):
            try:
                return f.result()
            except Exception:
                return d

        price, hi52, lo52 = safe(f_quote, (None, None, None))
        history = safe(f_hist, [])
        scr     = safe(f_scr, {}) or {}
        meta    = safe(f_meta, {}) or {}
        news    = safe(f_news, [])

    data = {
        "company_name": meta.get("company_name") or company,
        "ticker":       ticker,
        "sector":       meta.get("sector", ""),
        "description":  meta.get("description", ""),
    }
    if price:
        data["current_price"] = str(price)
        data["week_52_high"]  = str(hi52)
        data["week_52_low"]   = str(lo52)
    elif history:
        recent = history[-252:]
        data["current_price"] = str(round(history[-1]["close"], 2))
        data["week_52_high"]  = str(round(max(h["high"] for h in recent), 2))
        data["week_52_low"]   = str(round(min(h["low"]  for h in recent), 2))

    cagr = compute_cagr(history)
    if cagr:
        data["cagr"] = cagr
    tech = compute_technicals(history)
    if tech:
        data["candle_analysis"] = tech
    if scr.get("ratios"):
        data["fundamental_checks"] = _fundamentals_from_ratios(scr["ratios"])
    if scr.get("holdings"):
        data["holdings"] = scr["holdings"]
    if scr.get("quarterly_results"):
        data["quarterly_results"] = scr["quarterly_results"]
    chart = build_chart_ohlcv(history)
    if chart:
        data["ohlcv"] = chart
    if news:
        data["news"] = news
    return data

@app.route("/api/stock_overview", methods=["POST"])
def api_stock_overview():
    try:
        body    = request.get_json() or {}
        company = (body.get("company", "") or "").strip()
        ticker  = (body.get("ticker", "")  or "").strip().upper()
        if not company and not ticker:
            return jsonify({"error": "Company name or ticker is required"}), 400

        if not ticker:
            ticker = resolve_ticker_screener(company) or resolve_ticker_gemini(company) or ""
        if not ticker:
            return jsonify({"error": f"Could not resolve an NSE ticker for '{company}'. Try the exact ticker."}), 400

        return jsonify(_build_overview(ticker, company or ticker))
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 400


@app.route("/api/stock_action", methods=["POST"])
def api_stock_action():
    try:
        body = request.get_json()
        company = (body.get("company","") or "").strip()
        ticker = (body.get("ticker","") or "").strip().upper()

        if not company:
            return jsonify({"error": "Company name is required"}), 400

        if not ticker:
            ticker = resolve_ticker_gemini(company)
            if not ticker:
                return jsonify({"error": f"Could not automatically find NSE ticker for {company}. Please try a more specific name."}), 400

        pe = fetch_pe(ticker)
        action_summary = fetch_stock_action_gemini(company)
        news = fetch_news_rss(company)

        return jsonify({
            "pe": pe,
            "action_summary": action_summary,
            "news": news
        })
    except Exception as ex:
        import traceback
        print(traceback.format_exc())
        return jsonify({"error": str(ex)}), 400


@app.route("/api/rate", methods=["POST"])
def api_rate():
    try:
        body    = request.get_json()
        ticker  = (body.get("ticker","") or "").strip().upper()
        name    = (body.get("company_name","") or "").strip()
        sector  = (body.get("sector","") or "").strip()
        price   = (body.get("price","") or "").strip()
        rating  = (body.get("rating","") or "").strip().lower()

        if not ticker or rating not in ["good","average","bad"]:
            return jsonify({"ok": False, "error": "Invalid input"})

        add_or_update_stock(ticker, name, sector, price, rating)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/watchlist/delete", methods=["POST"])
def api_watchlist_delete():
    try:
        ticker = (request.get_json().get("ticker","") or "").strip().upper()
        if ticker:
            delete_stock(ticker)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/watchlist/data")
def api_watchlist_data():
    try:
        stocks = get_all_stocks()
        
        import concurrent.futures
        def enrich(stock):
            live_p, _, _ = fetch_live_price(stock["ticker"])
            stock["live_price"] = live_p if live_p else "N/A"
            return stock
            
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            stocks = list(executor.map(enrich, stocks))
            
        return jsonify(stocks)
    except Exception as e:
         return jsonify({"error": str(e)}), 400

def fetch_vix_support_resistance(nifty_price):
    try:
        import yfinance as yf
        import math
        vix = yf.Ticker("^INDIAVIX")
        hist = vix.history(period="5d")
        if len(hist) < 2: return None
        
        current_vix = float(hist["Close"].iloc[-1])
        prev_close_vix = float(hist["Close"].iloc[-2])
        
        def calc_levels(v_price, v_vix):
            daily_move = v_price * (v_vix / 100) / math.sqrt(252)
            return {
                "vix": round(v_vix, 2),
                "r2": round(v_price + 2 * daily_move, 2),
                "r1": round(v_price + daily_move, 2),
                "s1": round(v_price - daily_move, 2),
                "s2": round(v_price - 2 * daily_move, 2),
            }
            
        return {
            "current": calc_levels(nifty_price, current_vix),
            "close": calc_levels(nifty_price, prev_close_vix)
        }
    except Exception as e:
        import traceback
        print("VIX error:", traceback.format_exc())
        return None

def _india_vix_pair():
    """Return (current_vix, prev_close_vix) so VIX can be fetched in parallel."""
    try:
        import yfinance as yf
        hist = yf.Ticker("^INDIAVIX").history(period="5d")
        if len(hist) >= 2:
            return float(hist["Close"].iloc[-1]), float(hist["Close"].iloc[-2])
        if len(hist) == 1:
            v = float(hist["Close"].iloc[-1])
            return v, v
    except Exception:
        pass
    return None, None

def _vix_levels_from(nifty_price, cur_vix, prev_vix):
    """Compute the VIX-implied expected range from already-fetched VIX values."""
    import math
    if not nifty_price or cur_vix is None:
        return None
    def calc(v_price, v_vix):
        daily = v_price * (v_vix / 100) / math.sqrt(252)
        return {
            "vix": round(v_vix, 2),
            "r2": round(v_price + 2 * daily, 2), "r1": round(v_price + daily, 2),
            "s1": round(v_price - daily, 2),     "s2": round(v_price - 2 * daily, 2),
        }
    return {
        "current": calc(nifty_price, cur_vix),
        "close":   calc(nifty_price, prev_vix if prev_vix is not None else cur_vix),
    }

def _nifty_extra_metrics(chart):
    """Day move, RSI(14) and distance from the 200-DMA — all computed from the chart series."""
    out = {}
    try:
        closes = [r["close"] for r in chart.get("ohlcv", [])]
        if len(closes) >= 2 and closes[-2]:
            chg = closes[-1] - closes[-2]
            out["day_change"]     = round(chg, 2)
            out["day_change_pct"] = round(chg / closes[-2] * 100, 2)
        rsi = _rsi(closes, 14)
        if rsi is not None:
            out["rsi"] = rsi
        cp, d200 = chart.get("current_price"), chart.get("current_200dma")
        if cp and d200:
            out["pct_from_200dma"] = round((cp - d200) / d200 * 100, 2)
    except Exception:
        pass
    return out


@app.route("/api/nifty")
def api_nifty():
    try:
        import concurrent.futures
        # All five sources are independent — fetch them at once instead of one-by-one.
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            f_chart = pool.submit(fetch_nifty_chart_data)
            f_vix   = pool.submit(_india_vix_pair)
            f_npcr  = pool.submit(fetch_pcr_data, "NIFTY")
            f_bpcr  = pool.submit(fetch_pcr_data, "BANKNIFTY")
            f_fii   = pool.submit(fetch_fii_derivative_stats)

            def safe(f, d):
                try:
                    return f.result()
                except Exception:
                    return d

            chart            = safe(f_chart, None)
            cur_vix, prv_vix = safe(f_vix, (None, None))
            nifty_pcr        = safe(f_npcr, None)
            bnf_pcr          = safe(f_bpcr, None)
            fii              = safe(f_fii, None)

        result = {}
        if chart:
            chart.update(_nifty_extra_metrics(chart))
            result["chart"] = chart
            lv = _vix_levels_from(chart.get("current_price"), cur_vix, prv_vix)
            if lv:
                result["vix_levels"] = lv
        if nifty_pcr:
            result["nifty_pcr"] = nifty_pcr
        if bnf_pcr:
            result["banknifty_pcr"] = bnf_pcr
        if not fii:
            fii = fetch_fii_data()       # fallback only if the derivative feed was empty
        if fii:
            result["fii"] = fii

        if not result:
            return jsonify({"error": "Could not fetch any data from NSE. Check internet connection."}), 400

        result["timestamp"] = datetime.now().strftime("%d %b %Y  %H:%M:%S")
        return jsonify(result)

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 400


@app.route("/api/chartink", methods=["POST"])
def api_chartink():
    body   = request.get_json()
    url1   = body.get("url1","").strip()
    url2   = body.get("url2","").strip()
    label1 = body.get("label1","Screener 1")
    label2 = body.get("label2","Screener 2")

    if not url1 or not url2:
        return jsonify({"error": "Both URLs are required"}), 400

    # Fetch both screeners at once instead of one after the other.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(scrape_chartink, url1, 3)
        f2 = pool.submit(scrape_chartink, url2, 3)
        try:
            list1, err1 = f1.result()
        except Exception as e:
            list1, err1 = [], str(e)
        try:
            list2, err2 = f2.result()
        except Exception as e:
            list2, err2 = [], str(e)

    if err1: return jsonify({"error": f"Screener 1 error: {err1}"}), 400
    if err2: return jsonify({"error": f"Screener 2 error: {err2}"}), 400

    set1   = set(list1)
    set2   = set(list2)
    common = sorted(set1 & set2)

    return jsonify({
        "list1":        sorted(list1),
        "list2":        sorted(list2),
        "common":       common,
        "only_in_1":    sorted(set1 - set2),
        "only_in_2":    sorted(set2 - set1),
        "count1":       len(list1),
        "count2":       len(list2),
        "common_count": len(common),
    })

from nse_api import get_option_chain
@app.route("/api/nse/option_chain")
def api_nse_option_chain():
    symbol = request.args.get("symbol", "NIFTY")
    data = get_option_chain(symbol)
    if "error" in data:
        return jsonify(data), 400
    return jsonify(data)


from screener import run_screener
from database import save_screener_results, get_screener_results
import threading

# In-memory state for background scans
_screener_state = {}  # { "india": {"status": "running"/"done"/"error", "error": "..."} }
_screener_lock = threading.Lock()


def _run_screener_background(market):
    """Run the screener in a background thread and save results to DB."""
    try:
        data = run_screener(market)
        save_screener_results(market, data)
        with _screener_lock:
            _screener_state[market] = {"status": "done"}
        print(f"[Screener] Background scan for {market} completed and saved.")
    except Exception as e:
        import traceback
        print(f"[Screener] Background scan error: {traceback.format_exc()}")
        with _screener_lock:
            _screener_state[market] = {"status": "error", "error": str(e)}


@app.route("/api/screener/start", methods=["POST"])
def api_screener_start():
    """Start a background scan. Returns immediately."""
    market = request.args.get("market", "india").lower()
    if market not in ("india", "us", "india_next500"):
        return jsonify({"error": "Invalid market."}), 400

    with _screener_lock:
        current = _screener_state.get(market, {})
        if current.get("status") == "running":
            return jsonify({"status": "already_running", "message": "A scan is already in progress."})

        _screener_state[market] = {"status": "running"}

    t = threading.Thread(target=_run_screener_background, args=(market,), daemon=True)
    t.start()

    return jsonify({"status": "started", "message": f"Scan started for {market}."})


@app.route("/api/screener/status")
def api_screener_status():
    """Check if a background scan is running or done."""
    market = request.args.get("market", "india").lower()
    with _screener_lock:
        state = _screener_state.get(market, {"status": "idle"})
    return jsonify(state)


@app.route("/api/screener/results")
def api_screener_results():
    """Get last saved results from the database."""
    market = request.args.get("market", "india").lower()
    if market not in ("india", "us", "india_next500"):
        return jsonify({"error": "Invalid market."}), 400

    data, updated_at = get_screener_results(market)
    if data is None:
        return jsonify({"empty": True, "message": "No previous scan results found. Click 'Scan Now' to run your first scan."})

    data["timestamp"] = updated_at
    data["empty"] = False
    return jsonify(data)


from flask import send_from_directory

def fetch_telegram_messages(channel_name="marketwisdom_official"):
    """Scrapes the public preview page of a Telegram channel."""
    url = f"https://t.me/s/{channel_name}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        res = cffi_requests.get(url, headers=headers, impersonate="chrome110", timeout=10, verify=False)
        if res.status_code != 200:
            return {"error": f"Failed to fetch Telegram channel (Status {res.status_code})"}
            
        soup = BeautifulSoup(res.content, "html.parser")
        messages = []
        
        # Telegram preview page uses 'tgme_widget_message' class for each post
        post_elements = soup.find_all("div", class_="tgme_widget_message")
        
        for post in post_elements:
            # Extract text
            text_el = post.find("div", class_="tgme_widget_message_text")
            # Convert <br> to newlines for better JSON formatting
            if text_el:
                for br in text_el.find_all("br"):
                    br.replace_with("\\n")
                text = text_el.get_text().replace("\\n", "\n").strip()
            else:
                text = ""
                
            # Extract timestamp
            time_el = post.find("time")
            timestamp = time_el.get("datetime") if time_el else ""
            
            # Extract post link
            link_el = post.find("a", class_="tgme_widget_message_date")
            link = link_el.get("href") if link_el else ""
            
            # Skip empty messages without text
            if not text:
                continue
                
            messages.append({
                "text": text,
                "timestamp": timestamp,
                "link": link
            })
            
        # Return the last 20 messages in reverse chronological order (newest first)
        messages.reverse()
        return {
            "channel": channel_name,
            "messages": messages[:20],
            "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        return {"error": str(e)}

@app.route("/api/telegram_feed")
def api_telegram_feed():
    # Use the public channel provided by the user
    channel = "marketwisdom_official"
    data = fetch_telegram_messages(channel)
    return jsonify(data)

@app.route("/")
def index():
    return send_from_directory("../frontend", "index.html")

@app.route("/<path:path>")
def serve_frontend(path):
    # Serve static file if it exists, otherwise fallback to index.html
    static_path = os.path.join(os.path.dirname(__file__), "..", "frontend", path)
    if os.path.exists(static_path) and os.path.isfile(static_path):
        return send_from_directory(os.path.join("..", "frontend"), path)
    return send_from_directory(os.path.join("..", "frontend"), "index.html")

if __name__ == "__main__":
    app.run(debug=True, port=5000)
