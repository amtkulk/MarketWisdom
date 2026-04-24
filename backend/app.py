import os
import json
import time
from datetime import datetime, date
from flask import Flask, request, jsonify
from flask_cors import CORS
from database import init_db, add_or_update_stock, delete_stock, get_all_stocks

app = Flask(__name__)
# Enable CORS for all routes so the React frontend can talk to this API
CORS(app)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") # Fetch from environment (GitHub Secrets/App Runner Env)

# Initialize SQLite database
init_db()

# ══════════════════════════════════════════════════════════════
#  HELPERS (Extracted directly from old app.py)
# ══════════════════════════════════════════════════════════════
def fetch_live_price(ticker):
    try:
        import yfinance as yf
        for suffix in [".NS", ".BO", ""]:
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


def fetch_ohlcv(ticker):
    """Fetch 6 months of daily OHLCV data from yfinance for candlestick chart."""
    try:
        import yfinance as yf
        for suffix in [".NS", ".BO", ""]:
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


def fetch_screener_data(ticker):
    """Fetch live shareholding + quarterly results from Screener.in via Playwright."""
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
    MODELS   = ["gemini-2.0-flash-lite", "gemini-2.0-flash", "gemini-2.5-flash"]
    last_err = "No models available"

    for model in MODELS:
        try:
            cfg_args = {"temperature": 0.2, "max_output_tokens": 6000}
            if "2.5" in model:
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
            if "429" in err or "quota" in err.lower() or "RESOURCE_EXHAUSTED" in err:
                continue
            if "404" in err or "not found" in err.lower():
                continue
            if "503" in err or "unavailable" in err.lower() or "500" in err:
                import time
                time.sleep(1) # tiny delay before fallback
                continue
            return None, f"Gemini error: {err[:200]}"

    if "429" in last_err or "quota" in last_err.lower() or "RESOURCE_EXHAUSTED" in last_err:
        return None, "Gemini API Free Tier rate limit exceeded (15 requests/minute). Please wait 60 seconds and try again."
    return None, f"All Gemini models failed. Error: {last_err[:200]}"


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
    MODELS = ["gemini-2.0-flash-lite", "gemini-2.0-flash", "gemini-2.5-flash"]
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


def scrape_chartink(url, max_pages=3):
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
        req0 = urllib.request.Request("https://www.nseindia.com", headers=session_headers)
        with urllib.request.urlopen(req0, timeout=3) as r:
            raw_cookies = r.headers.get_all("Set-Cookie") or []
            cookie_str  = "; ".join([c.split(";")[0] for c in raw_cookies])

        session_headers["Cookie"] = cookie_str
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
        return None

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


# ══════════════════════════════════════════════════════════════
#  API ROUTES
# ══════════════════════════════════════════════════════════════

@app.route("/api/stock", methods=["POST"])
def api_stock():
    try:
        body    = request.get_json()
        company = (body.get("company","") or "").strip()
        ticker  = (body.get("ticker","")  or "").strip().upper()

        if not company:
            return jsonify({"error": "Company name is required"}), 400

        data, err = fetch_gemini(company)
        if err:
            return jsonify({"error": err}), 500

        t = ticker or data.get("ticker","")
        if t:
            price, hi52, lo52 = fetch_live_price(t)
            if price:
                data["current_price"] = str(price)
                data["week_52_high"]  = str(hi52)
                data["week_52_low"]   = str(lo52)
                
            sc = fetch_screener_data(t)
            if sc.get("holdings"):
                data["holdings"] = sc["holdings"]
            if sc.get("quarterly_results"):
                data["quarterly_results"] = sc["quarterly_results"]

            ohlcv = fetch_ohlcv(t)
            if ohlcv:
                data["ohlcv"] = ohlcv

        news = fetch_news_rss(data.get("company_name", company))
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
        return jsonify({"error": f"Server error: {str(ex)}"}), 500


@app.route("/api/stock_action", methods=["POST"])
def api_stock_action():
    try:
        body = request.get_json()
        company = (body.get("company","") or "").strip()
        ticker = (body.get("ticker","") or "").strip().upper()

        if not company or not ticker:
            return jsonify({"error": "Company name and ticker are required"}), 400

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
        return jsonify({"error": str(ex)}), 500


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
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/watchlist/delete", methods=["POST"])
def api_watchlist_delete():
    try:
        ticker = (request.get_json().get("ticker","") or "").strip().upper()
        if ticker:
            delete_stock(ticker)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/watchlist/data")
def api_watchlist_data():
    try:
        stocks = get_all_stocks()
        return jsonify(stocks)
    except Exception as e:
         return jsonify({"error": str(e)}), 500

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

@app.route("/api/nifty")
def api_nifty():
    try:
        result = {}
        chart = fetch_nifty_chart_data()
        if chart: 
            result["chart"] = chart
            vix_levels = fetch_vix_support_resistance(chart["current_price"])
            if vix_levels:
                result["vix_levels"] = vix_levels
        
        nifty_pcr = fetch_pcr_data("NIFTY")
        if nifty_pcr: result["nifty_pcr"] = nifty_pcr
        
        bnf_pcr = fetch_pcr_data("BANKNIFTY")
        if bnf_pcr: result["banknifty_pcr"] = bnf_pcr
        
        fii = fetch_fii_derivative_stats()
        if not fii:
            fii = fetch_fii_data()
        if fii: result["fii"] = fii

        if not result:
            return jsonify({"error": "Could not fetch any data from NSE. Check internet connection."}), 500

        result["timestamp"] = datetime.now().strftime("%d %b %Y  %H:%M:%S")
        return jsonify(result)

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route("/api/chartink", methods=["POST"])
def api_chartink():
    body   = request.get_json()
    url1   = body.get("url1","").strip()
    url2   = body.get("url2","").strip()
    label1 = body.get("label1","Screener 1")
    label2 = body.get("label2","Screener 2")

    if not url1 or not url2:
        return jsonify({"error": "Both URLs are required"}), 400

    list1, err1 = scrape_chartink(url1, max_pages=3)
    if err1: return jsonify({"error": f"Screener 1 error: {err1}"}), 500

    list2, err2 = scrape_chartink(url2, max_pages=3)
    if err2: return jsonify({"error": f"Screener 2 error: {err2}"}), 500

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

from flask import send_from_directory

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
