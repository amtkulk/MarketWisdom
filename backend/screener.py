"""
Stock Screener Module
Scans Nifty 500 (India) or S&P 500 (US) stocks and filters by:
  - P/E ratio < 20
  - Volume spike > 2x the 20-day average
  - RSI (14-period) > 50
Returns ranked results by composite score.
"""

import concurrent.futures
import traceback

# ──────────────────────────────────────────────────────────────
#  TICKER LIST FETCHERS
# ──────────────────────────────────────────────────────────────

# Fallback list of major Nifty stocks if live fetch fails
NIFTY_FALLBACK = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
    "SBIN", "BHARTIARTL", "KOTAKBANK", "ITC", "LT", "AXISBANK",
    "BAJFINANCE", "MARUTI", "HCLTECH", "ASIANPAINT", "SUNPHARMA",
    "TITAN", "ULTRACEMCO", "WIPRO", "NESTLEIND", "TATAMOTORS",
    "POWERGRID", "NTPC", "TECHM", "JSWSTEEL", "TATASTEEL", "ONGC",
    "BAJAJFINSV", "ADANIENT", "ADANIPORTS", "DRREDDY", "COALINDIA",
    "GRASIM", "CIPLA", "DIVISLAB", "EICHERMOT", "HEROMOTOCO",
    "APOLLOHOSP", "BPCL", "TATACONSUM", "M&M", "BRITANNIA",
    "INDUSINDBK", "HINDALCO", "SBILIFE", "HDFCLIFE", "BAJAJ-AUTO",
    "DABUR", "GODREJCP", "PIDILITIND", "HAVELLS", "VOLTAS",
    "TRENT", "ZOMATO", "PAYTM", "DMART", "IRCTC", "HAL",
    "BEL", "BHEL", "NHPC", "PFC", "RECLTD", "IOC", "GAIL",
    "VEDL", "TATAPOWER", "CANBK", "PNB", "BANKBARODA",
    "IDFCFIRSTB", "FEDERALBNK", "MUTHOOTFIN", "CHOLAFIN",
    "SHRIRAMFIN", "LICHSGFIN", "MANAPPURAM", "JUBLFOOD",
    "PAGEIND", "COLPAL", "MARICO", "BERGEPAINT", "AMBUJACEM",
    "ACC", "SHREECEM", "RAMCOCEM", "DEEPAKNTR", "ATUL",
    "PIIND", "SYNGENE", "BIOCON", "AUROPHARMA", "LUPIN",
    "TORNTPHARM", "ALKEM", "LALPATHLAB", "METROPOLIS",
    "MAXHEALTH", "FORTIS", "PERSISTENT", "LTIM", "MPHASIS",
    "COFORGE", "TATAELXSI", "POLYCAB", "KEI", "Dixon",
    "AFFLE", "ZYDUSLIFE", "NAUKRI", "INDIGO", "CONCOR"
]

SP500_FALLBACK = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B",
    "UNH", "XOM", "JNJ", "JPM", "V", "PG", "MA", "HD", "CVX", "MRK",
    "ABBV", "LLY", "PEP", "KO", "COST", "AVGO", "TMO", "MCD", "WMT",
    "CSCO", "ACN", "ABT", "DHR", "NEE", "LIN", "TXN", "PM", "RTX",
    "UPS", "HON", "LOW", "QCOM", "UNP", "INTC", "ORCL", "AMD", "CRM",
    "GS", "MS", "BAC", "C", "BLK", "SCHW", "AXP", "CB", "CME",
]


def get_nifty500_tickers():
    """Fetch Nifty 500 tickers from NSE India. Falls back to curated list."""
    try:
        import pandas as pd
        import io
        import urllib.request

        url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            csv_data = resp.read().decode("utf-8")

        df = pd.read_csv(io.StringIO(csv_data))
        symbols = df["Symbol"].tolist()
        tickers = [s.strip() + ".NS" for s in symbols if isinstance(s, str) and len(s.strip()) > 0]
        if len(tickers) > 100:
            print(f"[Screener] Fetched {len(tickers)} Nifty 500 tickers from NSE")
            return tickers
    except Exception as e:
        print(f"[Screener] NSE fetch failed: {e}, using fallback list")

    return [t + ".NS" for t in NIFTY_FALLBACK]


def get_sp500_tickers():
    """Fetch S&P 500 tickers from Wikipedia. Falls back to curated list."""
    try:
        import pandas as pd

        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(url)
        df = tables[0]
        tickers = df["Symbol"].tolist()
        # Clean up tickers (e.g. BRK.B -> BRK-B for yfinance)
        cleaned = [t.strip().replace(".", "-") for t in tickers if isinstance(t, str)]
        if len(cleaned) > 100:
            print(f"[Screener] Fetched {len(cleaned)} S&P 500 tickers from Wikipedia")
            return cleaned
    except Exception as e:
        print(f"[Screener] Wikipedia fetch failed: {e}, using fallback list")

    return list(SP500_FALLBACK)


# ──────────────────────────────────────────────────────────────
#  TECHNICAL INDICATORS
# ──────────────────────────────────────────────────────────────

def calculate_rsi(closes, period=14):
    """Calculate RSI using exponential moving average method."""
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    # Initial averages (SMA for first period)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # EMA smoothing for remaining
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)


def compute_composite_score(pe, vol_ratio, rsi):
    """
    Composite score (0-100):
      - P/E component (30%): lower PE = higher score, capped at PE=0
      - Volume spike component (40%): higher ratio = better, capped at 10x
      - RSI component (30%): higher RSI = stronger momentum
    """
    pe_score = max(0, min(1, (20 - pe) / 20)) * 30
    vol_score = min(vol_ratio, 10) / 10 * 40
    rsi_score = rsi / 100 * 30
    return round(pe_score + vol_score + rsi_score, 2)


# ──────────────────────────────────────────────────────────────
#  SINGLE STOCK SCANNER
# ──────────────────────────────────────────────────────────────

def scan_stock(ticker):
    """
    Scan a single stock. Returns a dict if it passes all filters, else None.
    Filters: P/E < 20, Volume > 2x 20-day avg, RSI > 50
    Works on weekends/holidays by using last available trading day data.
    """
    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)

        # Get 2 months of history to ensure enough data even on weekends
        hist = stock.history(period="2mo", interval="1d")
        if hist is None or len(hist) < 21:
            return None

        # Drop any rows with zero volume (non-trading days)
        hist = hist[hist["Volume"] > 0]
        if len(hist) < 21:
            return None

        closes = hist["Close"].tolist()
        volumes = hist["Volume"].tolist()
        current_price = round(float(closes[-1]), 2)

        # ── P/E Ratio ──
        try:
            info = stock.info
            pe = info.get("trailingPE") or info.get("forwardPE")
            if pe is None or pe <= 0 or pe >= 20:
                return None
            pe = round(float(pe), 2)
        except Exception:
            return None

        # ── Volume Spike (uses last trading day vs 20-day avg) ──
        if len(volumes) < 21:
            return None
        current_vol = volumes[-1]  # Last trading day volume
        avg_vol_20 = sum(volumes[-21:-1]) / 20  # 20-day avg excluding last day
        if avg_vol_20 <= 0:
            return None
        vol_ratio = round(current_vol / avg_vol_20, 2)
        if vol_ratio < 2.0:
            return None

        # ── RSI ──
        rsi = calculate_rsi(closes)
        if rsi is None or rsi <= 50:
            return None

        # ── All filters passed ──
        score = compute_composite_score(pe, vol_ratio, rsi)
        clean_ticker = ticker.replace(".NS", "").replace(".BO", "")

        return {
            "ticker": clean_ticker,
            "price": current_price,
            "pe": pe,
            "vol_ratio": vol_ratio,
            "avg_vol_20d": int(avg_vol_20),
            "current_vol": int(current_vol),
            "rsi": rsi,
            "score": score,
        }

    except Exception:
        return None


# ──────────────────────────────────────────────────────────────
#  ORCHESTRATOR
# ──────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────
#  NIFTY 501-1000 UNIVERSE  (stocks ranked beyond the top 500)
# ──────────────────────────────────────────────────────────────

# Fallback mid/small-cap names (outside the Nifty 500 megacaps)
NIFTY_NEXT_FALLBACK = [
    "CDSL", "BSE", "ANGELONE", "KFINTECH", "CAMS", "IEX", "KALYANKJIL",
    "RADICO", "CCL", "JYOTHYLAB", "BLUESTARCO", "AMBER", "KAYNES", "TATATECH",
    "JBCHEPHARM", "ERIS", "MANKIND", "GLAND", "NATCOPHARM", "SUVENPHAR",
    "APARINDS", "TRIVENI", "CAPLIPOINT", "REDINGTON", "RAILTEL", "RVNL",
    "IRFC", "IRCON", "NBCC", "ENGINERSIN", "HUDCO", "JWL", "TITAGARH",
    "CGCL", "SBFC", "FIVESTAR", "HOMEFIRST", "AAVAS", "APTUS", "CREDITACC",
    "POONAWALLA", "360ONE", "ANANDRATHI", "NUVAMA", "MCX", "CESC",
    "NLCINDIA", "JSWENERGY", "KEC", "ASTERDM", "RAINBOW", "KIMS", "MEDANTA",
    "GRANULES", "LAURUSLABS", "AJANTPHARM", "JKCEMENT", "BIRLACORPN",
]


def _fetch_nse_index_csv(filename):
    """Fetch a symbol list from an NSE index CSV (e.g. Total Market / Microcap)."""
    import pandas as pd, io, urllib.request
    url = f"https://archives.nseindia.com/content/indices/{filename}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        csv_data = resp.read().decode("utf-8")
    df = pd.read_csv(io.StringIO(csv_data))
    return [s.strip() for s in df["Symbol"].tolist() if isinstance(s, str) and s.strip()]


def get_nifty_next500_tickers():
    """Stocks ranked roughly 501-1000: the Nifty Total Market (~750) plus Microcap 250,
    minus the Nifty 500. Falls back to a curated mid/small-cap list."""
    try:
        nifty500 = set(s.replace(".NS", "") for s in get_nifty500_tickers())
        broad = []
        for fn in ("ind_niftytotalmarket_list.csv", "ind_niftymicrocap250_list.csv"):
            try:
                broad += _fetch_nse_index_csv(fn)
            except Exception as e:
                print(f"[Screener] {fn} fetch failed: {e}")
        seen, universe = set(), []
        for s in broad:
            if s in nifty500 or s in seen:
                continue
            seen.add(s)
            universe.append(s)
        if len(universe) > 50:
            print(f"[Screener] Next-500 universe: {len(universe)} tickers")
            return [s + ".NS" for s in universe]
    except Exception as e:
        print(f"[Screener] Next-500 build failed: {e}, using fallback")
    return [t + ".NS" for t in NIFTY_NEXT_FALLBACK]


# ──────────────────────────────────────────────────────────────
#  BULK PRICE DOWNLOAD  (one batched call for many tickers)
# ──────────────────────────────────────────────────────────────

def _bulk_download(tickers, period="3mo"):
    """Download daily OHLCV for many tickers in ONE batched yfinance request.
    Returns {ticker: {"closes": [...], "volumes": [...]}}. This replaces 1 history
    call per stock — the single biggest speedup for a bulk scan."""
    import yfinance as yf
    out = {}
    if not tickers:
        return out
    try:
        df = yf.download(tickers, period=period, interval="1d", group_by="ticker",
                         threads=True, progress=False, auto_adjust=False)
    except Exception:
        return out

    def extract(tk_df):
        try:
            closes = [float(x) for x in tk_df["Close"].tolist()  if x == x]   # x==x drops NaN
            vols   = [float(x) for x in tk_df["Volume"].tolist() if x == x]
            return closes, vols
        except Exception:
            return [], []

    if len(tickers) == 1:
        c, v = extract(df)
        if c:
            out[tickers[0]] = {"closes": c, "volumes": v}
    else:
        for t in tickers:
            try:
                sub = df[t]
            except Exception:
                continue
            c, v = extract(sub)
            if c:
                out[t] = {"closes": c, "volumes": v}
    return out


# ──────────────────────────────────────────────────────────────
#  ORCHESTRATOR  (bulk-first architecture)
# ──────────────────────────────────────────────────────────────

def run_screener(market="india"):
    """
    Scan a market and rank survivors by composite score.
    Architecture: bulk-download all prices → compute volume-spike + RSI locally →
    fetch the SLOW per-stock P/E only for the few that already pass. This avoids
    ~500 `stock.info` calls (the old bottleneck) and batches the price fetch.
    """
    import time
    start = time.time()

    if market == "india_master":
        return run_master_screener()

    if market == "india_smallmid_master":
        return run_smallmid_master_screener()

    if market == "india_microcap":
        return run_microcap_screener()

    if market == "us":
        tickers = get_sp500_tickers()
        label   = "S&P 500"
    elif market in ("india_next500", "india500_1000"):
        tickers = get_nifty_next500_tickers()
        label   = "Nifty 501-1000"
    else:
        tickers = get_nifty500_tickers()
        label   = "Nifty 500"

    total = len(tickers)
    print(f"[Screener] Scanning {total} {label} stocks (bulk mode)...")

    # 1) Bulk-download prices in parallel chunks
    price_data = {}
    CHUNK = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(_bulk_download, tickers[i:i + CHUNK], "3mo")
                for i in range(0, total, CHUNK)]
        for f in concurrent.futures.as_completed(futs):
            try:
                price_data.update(f.result() or {})
            except Exception:
                pass

    # 2) Cheap local filters: volume spike (>2x) + RSI (>50)
    candidates = []
    for t, d in price_data.items():
        pairs = [(c, v) for c, v in zip(d["closes"], d["volumes"]) if v > 0]
        if len(pairs) < 21:
            continue
        closes = [c for c, _ in pairs]
        vols   = [v for _, v in pairs]
        cur_vol = vols[-1]
        avg20   = sum(vols[-21:-1]) / 20
        if avg20 <= 0:
            continue
        vol_ratio = round(cur_vol / avg20, 2)
        if vol_ratio < 2.0:
            continue
        rsi = calculate_rsi(closes)
        if rsi is None or rsi <= 50:
            continue
        candidates.append({
            "ticker": t, "price": round(closes[-1], 2), "vol_ratio": vol_ratio,
            "avg_vol_20d": int(avg20), "current_vol": int(cur_vol), "rsi": rsi,
        })

    # 3) Fetch the slow P/E ONLY for survivors, then apply P/E < 20
    def add_pe(c):
        try:
            import yfinance as yf
            info = yf.Ticker(c["ticker"]).info
            pe = info.get("trailingPE") or info.get("forwardPE")
            if pe is None or pe <= 0 or pe >= 20:
                return None
            c["pe"] = round(float(pe), 2)
            return c
        except Exception:
            return None

    passed = []
    if candidates:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            for r in ex.map(add_pe, candidates):
                if r:
                    passed.append(r)

    for c in passed:
        c["ticker"] = c["ticker"].replace(".NS", "").replace(".BO", "")
        c["score"]  = compute_composite_score(c["pe"], c["vol_ratio"], c["rsi"])
    passed.sort(key=lambda x: x["score"], reverse=True)
    top = passed[:25]

    elapsed = round(time.time() - start, 1)
    print(f"[Screener] Done in {elapsed}s — {len(price_data)} priced, "
          f"{len(candidates)} candidates, {len(passed)} passed, returning {len(top)}")

    return {
        "market": label,
        "total_scanned": total,
        "total_passed": len(passed),
        "results": top,
        "scan_time_seconds": elapsed,
    }


# ══════════════════════════════════════════════════════════════
#  MASTER SCREENER  —  Nifty 1000, multi-factor (technical + fundamental)
#
#  Architecture (same bulk-first pattern as run_screener):
#   1. ONE-YEAR daily OHLCV for ~1000 stocks via batched downloads.
#   2. Technical score computed locally for every stock (daily + weekly):
#      trend alignment, weekly stage, RSI (D/W), MACD, relative strength
#      vs Nifty, proximity to 52-week high.
#   3. Hard gate: price > 200-DMA  AND  weekly close > 30-week MA.
#   4. Top 40 by technical score → fundamentals fetched from Screener.in
#      (ROE, ROCE, 5-yr sales & profit CAGR, P/E) for those 40 only.
#   5. Composite = Technical (60) + Fundamental (40) → ranked Top 10.
# ══════════════════════════════════════════════════════════════

def get_nifty1000_tickers():
    """Nifty 500 + the 501-1000 universe, deduplicated (with .NS suffix)."""
    seen, out = set(), []
    for t in (get_nifty500_tickers() + get_nifty_next500_tickers()):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _ema_list(vals, period):
    if not vals:
        return []
    k = 2.0 / (period + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _sma(vals, period):
    if len(vals) < period:
        return None
    return sum(vals[-period:]) / period


def _macd_bullish(closes):
    """True when MACD line is above its signal line."""
    if len(closes) < 40:
        return False
    e12, e26 = _ema_list(closes, 12), _ema_list(closes, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = _ema_list(macd, 9)
    return macd[-1] > sig[-1]


def _bulk_download_ohlc(tickers, period="1y"):
    """Batched 1-y daily download returning {ticker: [(date, close, volume, high), ...]}."""
    import yfinance as yf
    out = {}
    if not tickers:
        return out
    try:
        df = yf.download(tickers, period=period, interval="1d", group_by="ticker",
                         threads=True, progress=False, auto_adjust=False)
    except Exception:
        return out

    def extract(sub):
        try:
            rows = []
            closes = sub["Close"]
            vols = sub["Volume"]
            highs = sub["High"]
            for idx in sub.index:
                c, v, h = closes.get(idx), vols.get(idx), highs.get(idx)
                if c == c and v == v:          # NaN check
                    rows.append((idx.to_pydatetime(), float(c), float(v),
                                 float(h) if h == h else float(c)))
            return rows
        except Exception:
            return []

    if len(tickers) == 1:
        rows = extract(df)
        if rows:
            out[tickers[0]] = rows
    else:
        for t in tickers:
            try:
                sub = df[t]
            except Exception:
                continue
            rows = extract(sub)
            if len(rows) >= 60:
                out[t] = rows
    return out


def _weekly_closes(rows):
    """Collapse daily rows into calendar-week closing prices (ISO weeks)."""
    weeks = {}
    for dt, c, _v, _h in rows:            # chronological → last close of week wins
        iso = dt.isocalendar()
        weeks[(iso[0], iso[1])] = c
    return [weeks[k] for k in sorted(weeks.keys())]


def _pct_return(closes, days):
    if len(closes) <= days or closes[-days - 1] <= 0:
        return None
    return (closes[-1] / closes[-days - 1] - 1) * 100


def technical_score(rows, nifty_closes):
    """Score 0-60 from daily+weekly technicals. Returns (score, details) or None."""
    if not rows or len(rows) < 210:        # need ~1y for the 200-DMA
        return None
    closes = [r[1] for r in rows]
    vols = [r[2] for r in rows]
    highs = [r[3] for r in rows]
    price = closes[-1]
    sma50, sma200 = _sma(closes, 50), _sma(closes, 200)
    wk = _weekly_closes(rows)
    wma30 = _sma(wk, 30)
    if sma200 is None or wma30 is None:
        return None

    d = {"price": round(price, 2)}
    score = 0.0

    # 1) Trend alignment (max 16): price>200DMA(6), price>50DMA(5), 50>200(5)
    d["above_200dma"] = price > sma200
    d["above_50dma"] = sma50 is not None and price > sma50
    d["golden_stack"] = sma50 is not None and sma50 > sma200
    score += (6 if d["above_200dma"] else 0) + (5 if d["above_50dma"] else 0) \
           + (5 if d["golden_stack"] else 0)

    # 2) Weekly stage (max 8): close above the 30-week MA (classic stage-2 test)
    d["above_30wma"] = wk[-1] > wma30
    score += 8 if d["above_30wma"] else 0

    # 3) Daily RSI (max 8): 50-70 is the strong-but-not-stretched zone
    rsi_d = calculate_rsi(closes)
    d["rsi_d"] = rsi_d
    if rsi_d is not None:
        if 50 <= rsi_d <= 70:
            score += 8
        elif 45 <= rsi_d < 50 or 70 < rsi_d <= 75:
            score += 4

    # 4) Weekly RSI (max 6): momentum confirmed on the higher timeframe
    rsi_w = calculate_rsi(wk) if len(wk) >= 15 else None
    d["rsi_w"] = rsi_w
    if rsi_w is not None and rsi_w > 50:
        score += 6

    # 5) MACD daily bullish (max 6)
    d["macd_bull"] = _macd_bullish(closes)
    score += 6 if d["macd_bull"] else 0

    # 6) Relative strength vs Nifty, 6-month (max 8, scaled)
    rs = None
    r_stock = _pct_return(closes, 126)
    r_nifty = _pct_return(nifty_closes, 126) if nifty_closes else None
    if r_stock is not None and r_nifty is not None:
        rs = round(r_stock - r_nifty, 1)
        if rs > 0:
            score += min(8.0, 2 + rs / 5.0)   # +5pp outperf ≈ 3pts … caps at 8
    d["rs_6m"] = rs

    # 7) Proximity to 52-week high (max 8): closer = stronger
    hi52 = max(highs)
    dist = (hi52 - price) / hi52 * 100 if hi52 > 0 else 100
    d["dist_52wh"] = round(dist, 1)
    if dist <= 3:
        score += 8
    elif dist <= 8:
        score += 6
    elif dist <= 15:
        score += 3

    # Liquidity sanity: 20-day avg turnover > Rs.1 cr (skips untradeable names)
    avg_turnover = (sum(vols[-20:]) / 20) * price
    d["liquid"] = avg_turnover > 1e7
    return (round(score, 1), d)


def _fetch_fundamentals(symbol):
    """ROE, ROCE, 5-yr sales & profit CAGR, and P/E from Screener.in (one GET)."""
    try:
        import re
        from curl_cffi import requests as cffi
        from bs4 import BeautifulSoup
        url = f"https://www.screener.in/company/{symbol}/consolidated/"
        r = cffi.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }, impersonate="chrome120", timeout=8)
        if r.status_code != 200:
            return {}
        soup = BeautifulSoup(r.content, "html.parser")
        out = {}
        for li in soup.select("#top-ratios li") or []:
            t = " ".join((li.get_text() or "").split())
            low = t.lower()
            m = re.search(r'(-?\d[\d,]*(?:\.\d+)?)', t)
            if not m:
                continue
            val = float(m.group(1).replace(",", ""))
            if "roce" in low and "roce" not in out:
                out["roce"] = val
            elif ("roe" in low or "return on equity" in low) and "roe" not in out:
                out["roe"] = val
            elif "stock p/e" in low and "pe" not in out:
                out["pe"] = val
            elif "market cap" in low and "mcap" not in out:
                out["mcap"] = val          # Screener shows Market Cap in Rs Cr
        for tbl in soup.find_all("table"):
            txt = tbl.get_text() or ""
            key = "sales_g" if "Compounded Sales Growth" in txt else \
                  "profit_g" if "Compounded Profit Growth" in txt else None
            if not key or key in out:
                continue
            for tr in tbl.find_all("tr"):
                cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
                if len(cells) >= 2 and "5 year" in cells[0].lower():
                    m = re.search(r'(-?\d+(?:\.\d+)?)', cells[1])
                    if m:
                        out[key] = float(m.group(1))
        return out
    except Exception:
        return {}


def fundamental_score(f):
    """Score 0-40 from Screener.in fundamentals. Missing data simply scores 0."""
    s = 0.0

    def tier(val, hi, mid, lo, p_hi, p_mid, p_lo):
        if val is None:
            return 0
        if val >= hi:
            return p_hi
        if val >= mid:
            return p_mid
        if val >= lo:
            return p_lo
        return 0

    s += tier(f.get("roe"),      20, 15, 10, 10, 6, 3)
    s += tier(f.get("roce"),     20, 15, 10, 10, 6, 3)
    s += tier(f.get("sales_g"),  15, 10, 5,   8, 5, 2)
    s += tier(f.get("profit_g"), 15, 10, 5,   8, 5, 2)
    pe = f.get("pe")
    if pe is not None and pe > 0:
        s += 4 if pe <= 25 else (2 if pe <= 40 else 0)
    return round(s, 1)


def run_master_screener():
    """Nifty-1000 multi-factor scan → Top 10 by composite score."""
    import time
    start = time.time()
    tickers = get_nifty1000_tickers()
    total = len(tickers)
    print(f"[Master] Scanning {total} Nifty-1000 stocks (1y daily, bulk mode)...")

    price_data = {}
    CHUNK = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(_bulk_download_ohlc, tickers[i:i + CHUNK], "1y")
                for i in range(0, total, CHUNK)]
        futs.append(ex.submit(_bulk_download_ohlc, ["^NSEI"], "1y"))
        for f in concurrent.futures.as_completed(futs):
            try:
                price_data.update(f.result() or {})
            except Exception:
                pass

    nifty_rows = price_data.pop("^NSEI", [])
    nifty_closes = [r[1] for r in nifty_rows]

    scored = []
    for t, rows in price_data.items():
        res = technical_score(rows, nifty_closes)
        if not res:
            continue
        tech, d = res
        # Hard gate: long-term uptrend on BOTH timeframes + tradeable liquidity
        if not (d["above_200dma"] and d["above_30wma"] and d["liquid"]):
            continue
        scored.append({"ticker": t, "tech": tech, "d": d})

    scored.sort(key=lambda x: x["tech"], reverse=True)
    finalists = scored[:40]
    print(f"[Master] {len(price_data)} priced, {len(scored)} passed the gate, "
          f"fetching fundamentals for top {len(finalists)}...")

    def enrich(c):
        sym = c["ticker"].replace(".NS", "").replace(".BO", "")
        f = _fetch_fundamentals(sym)
        c["f"] = f
        c["fund"] = fundamental_score(f)
        c["sym"] = sym
        return c

    if finalists:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            finalists = list(ex.map(enrich, finalists))

    for c in finalists:
        c["score"] = round(c["tech"] + c["fund"], 1)
    finalists.sort(key=lambda x: x["score"], reverse=True)

    results = []
    for i, c in enumerate(finalists[:10]):
        d, f = c["d"], c.get("f", {})
        results.append({
            "rank": i + 1,
            "ticker": c["sym"],
            "price": d["price"],
            "score": c["score"],
            "tech_score": c["tech"],
            "fund_score": c["fund"],
            "rsi_d": d.get("rsi_d"),
            "rsi_w": d.get("rsi_w"),
            "rs_6m": d.get("rs_6m"),
            "dist_52wh": d.get("dist_52wh"),
            "macd_bull": d.get("macd_bull"),
            "golden_stack": d.get("golden_stack"),
            "roe": f.get("roe"), "roce": f.get("roce"),
            "sales_g": f.get("sales_g"), "profit_g": f.get("profit_g"),
            "pe": f.get("pe"),
        })

    elapsed = round(time.time() - start, 1)
    print(f"[Master] Done in {elapsed}s — returning top {len(results)}")
    return {
        "market": "Nifty 1000 Master",
        "total_scanned": total,
        "total_passed": len(scored),
        "results": results,
        "scan_time_seconds": elapsed,
    }


# ══════════════════════════════════════════════════════════════
#  SMALL / MID CAP MASTER SCREENER
#  Same 12-factor engine as run_master_screener, but on the universe
#  EXCLUDING the Nifty 1000 — i.e. genuine small & mid-caps NSE recognizes.
#
#  Universe: NSE Total Market (~750) + Microcap 250, minus Nifty 500 and
#  the 501-1000 set. Falls back to a curated small/mid list if the NSE
#  CSVs can't be fetched.
# ══════════════════════════════════════════════════════════════

SMALL_MID_FALLBACK = [
    # A conservative fallback of well-known small/mid caps outside the Nifty 1000
    "MASTEK", "TANLA", "ROUTE", "SONATSOFTW", "CYIENT", "ZENSARTECH",
    "HAPPYFORGE", "SANSERA", "RATNAMANI", "SHAILY", "HAPPSTMNDS",
    "ANANDRATHI", "KFINTECH", "PRUDENT", "MOTILALOFS", "CDSL", "BSE",
    "VGUARD", "SYMPHONY", "CROMPTON", "FINEORG", "GRSE", "MIDHANI",
    "APOLLOPIPE", "PRINCEPIPE", "ASTRAL", "SUPREMEIND", "FINPIPE",
    "LTFOODS", "KRBL", "MARKSANS", "GRANULES", "LAURUSLABS",
    "AJANTPHARM", "CAPLIPOINT", "SUVENPHAR", "NATCOPHARM",
    "SYRMA", "KAYNES", "AMBER", "DIXON", "ORIENTELEC", "BUTTERFLY",
    "CENTURYPLY", "GREENPLY", "GREENLAM", "ARCHIES", "REPCOHOME",
    "AAVAS", "APTUS", "HOMEFIRST", "FIVESTAR", "CGCL", "SBFC",
    "POONAWALLA", "MASFIN", "CREDITACC", "MANAPPURAM", "MUTHOOTCAP",
    "JMFINANCIL", "CENTRALBK", "IOB", "UCOBANK", "MAHABANK",
    "PSB", "J&KBANK", "KTKBANK", "DCBBANK", "CSBBANK",
    "SOUTHBANK", "KARURVYSYA", "TMB", "UJJIVAN", "EQUITASBNK",
    "JYOTHYLAB", "RADICO", "CCL", "TASTYBITE", "MRSFOODS",
    "ELECTCAST", "TECHNOE", "GRAPHITE", "HEG", "VAIBHAVGBL",
    "REDINGTON", "RAILTEL", "IRCON", "NBCC", "ENGINERSIN",
    "HUDCO", "JWL", "TITAGARH", "TEXRAIL", "CENTRALTX",
    "BIRLACORPN", "JKCEMENT", "HEIDELBERG", "RAMCOCEM",
    "STARCEMENT", "SAGCEM", "PRISMJOHNSN", "ORIENTCEM",
]


def get_small_mid_cap_tickers():
    """Small/mid-caps = NSE Total Market ∪ Microcap 250, minus Nifty 1000.
    Falls back to a curated list if NSE CSV fetches fail."""
    try:
        excluded = set(t.replace(".NS", "") for t in get_nifty1000_tickers())
        broad = []
        for fn in ("ind_niftytotalmarket_list.csv", "ind_niftymicrocap250_list.csv"):
            try:
                broad += _fetch_nse_index_csv(fn)
            except Exception as e:
                print(f"[SmallMid] {fn} fetch failed: {e}")
        seen, universe = set(), []
        for s in broad:
            if s in excluded or s in seen:
                continue
            seen.add(s)
            universe.append(s)
        if len(universe) > 40:
            print(f"[SmallMid] Universe: {len(universe)} tickers (excluding Nifty 1000)")
            return [s + ".NS" for s in universe]
    except Exception as e:
        print(f"[SmallMid] Build failed: {e}, using fallback")
    excluded_fb = set(t.replace(".NS", "") for t in get_nifty1000_tickers())
    return [t + ".NS" for t in SMALL_MID_FALLBACK if t not in excluded_fb]


def run_smallmid_master_screener():
    """Same multi-factor scoring as run_master_screener, applied to the
    genuine small/mid-cap universe outside the Nifty 1000."""
    import time
    start = time.time()
    tickers = get_small_mid_cap_tickers()
    total = len(tickers)
    print(f"[SmallMid Master] Scanning {total} small/mid-cap stocks (1y daily, bulk)...")

    price_data = {}
    CHUNK = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(_bulk_download_ohlc, tickers[i:i + CHUNK], "1y")
                for i in range(0, total, CHUNK)]
        futs.append(ex.submit(_bulk_download_ohlc, ["^NSEI"], "1y"))
        for f in concurrent.futures.as_completed(futs):
            try:
                price_data.update(f.result() or {})
            except Exception:
                pass

    nifty_rows = price_data.pop("^NSEI", [])
    nifty_closes = [r[1] for r in nifty_rows]

    scored = []
    for t, rows in price_data.items():
        res = technical_score(rows, nifty_closes)
        if not res:
            continue
        tech, d = res
        # Same hard gate: long-term uptrend on both timeframes + tradeable
        if not (d["above_200dma"] and d["above_30wma"] and d["liquid"]):
            continue
        scored.append({"ticker": t, "tech": tech, "d": d})

    scored.sort(key=lambda x: x["tech"], reverse=True)
    finalists = scored[:40]
    print(f"[SmallMid Master] {len(price_data)} priced, {len(scored)} passed gate, "
          f"fetching fundamentals for top {len(finalists)}...")

    def enrich(c):
        sym = c["ticker"].replace(".NS", "").replace(".BO", "")
        f = _fetch_fundamentals(sym)
        c["f"] = f
        c["fund"] = fundamental_score(f)
        c["sym"] = sym
        return c

    if finalists:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            finalists = list(ex.map(enrich, finalists))

    for c in finalists:
        c["score"] = round(c["tech"] + c["fund"], 1)
    finalists.sort(key=lambda x: x["score"], reverse=True)

    results = []
    for i, c in enumerate(finalists[:10]):
        d, f = c["d"], c.get("f", {})
        results.append({
            "rank": i + 1, "ticker": c["sym"], "price": d["price"],
            "score": c["score"], "tech_score": c["tech"], "fund_score": c["fund"],
            "rsi_d": d.get("rsi_d"), "rsi_w": d.get("rsi_w"),
            "rs_6m": d.get("rs_6m"), "dist_52wh": d.get("dist_52wh"),
            "macd_bull": d.get("macd_bull"), "golden_stack": d.get("golden_stack"),
            "roe": f.get("roe"), "roce": f.get("roce"),
            "sales_g": f.get("sales_g"), "profit_g": f.get("profit_g"),
            "pe": f.get("pe"),
        })

    elapsed = round(time.time() - start, 1)
    print(f"[SmallMid Master] Done in {elapsed}s — returning top {len(results)}")
    return {
        "market": "Small & Mid Cap Master",
        "total_scanned": total,
        "total_passed": len(scored),
        "results": results,
        "scan_time_seconds": elapsed,
    }


# ══════════════════════════════════════════════════════════════
#  MICRO CAP SCANNER
#  Same 12-factor engine, on the beyond-Nifty-1000 universe, with two
#  EXTRA hard gates on top of the standard one:
#    • Distance from 52-week high ≤ 7%  (price coiling right at highs —
#      the strongest momentum posture; laggards are cut before scoring)
#    • Market cap < Rs 2,000 Cr, VERIFIED from Screener.in. Anything at or
#      above the cap — or whose market cap cannot be confirmed — is dropped.
#  Pipeline: technical gates first (cheap, incl. the 7% rule) → wider
#  finalist pool (60) → fundamentals fetched → mcap filter → composite → Top 10.
# ══════════════════════════════════════════════════════════════

MICROCAP_MAX_MCAP_CR   = 2000.0   # Rs Cr
MICROCAP_MAX_DIST_52WH = 7.0      # percent below 52-week high


def run_microcap_screener():
    import time
    start = time.time()
    tickers = get_small_mid_cap_tickers()      # beyond-Nifty-1000 universe
    total = len(tickers)
    print(f"[MicroCap] Scanning {total} beyond-Nifty-1000 stocks (1y daily, bulk)...")

    price_data = {}
    CHUNK = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(_bulk_download_ohlc, tickers[i:i + CHUNK], "1y")
                for i in range(0, total, CHUNK)]
        futs.append(ex.submit(_bulk_download_ohlc, ["^NSEI"], "1y"))
        for f in concurrent.futures.as_completed(futs):
            try:
                price_data.update(f.result() or {})
            except Exception:
                pass

    nifty_rows = price_data.pop("^NSEI", [])
    nifty_closes = [r[1] for r in nifty_rows]

    scored = []
    for t, rows in price_data.items():
        res = technical_score(rows, nifty_closes)
        if not res:
            continue
        tech, d = res
        # Standard gate + the micro-cap 7%-from-high momentum gate
        if not (d["above_200dma"] and d["above_30wma"] and d["liquid"]):
            continue
        if d.get("dist_52wh") is None or d["dist_52wh"] > MICROCAP_MAX_DIST_52WH:
            continue
        scored.append({"ticker": t, "tech": tech, "d": d})

    scored.sort(key=lambda x: x["tech"], reverse=True)
    # Wider finalist pool than the master (60 vs 40): the mcap filter will
    # cut an unknown share of them, so we need headroom to still fill a Top 10.
    finalists = scored[:60]
    print(f"[MicroCap] {len(price_data)} priced, {len(scored)} passed gates "
          f"(incl. <= {MICROCAP_MAX_DIST_52WH}% from 52WH), "
          f"checking fundamentals + mcap for top {len(finalists)}...")

    def enrich(c):
        sym = c["ticker"].replace(".NS", "").replace(".BO", "")
        f = _fetch_fundamentals(sym)
        c["f"] = f
        c["fund"] = fundamental_score(f)
        c["sym"] = sym
        return c

    if finalists:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            finalists = list(ex.map(enrich, finalists))

    # HARD micro-cap filter: must have a confirmed market cap below the limit.
    dropped_big, dropped_unknown = 0, 0
    kept = []
    for c in finalists:
        mcap = (c.get("f") or {}).get("mcap")
        if mcap is None:
            dropped_unknown += 1
            continue
        if mcap >= MICROCAP_MAX_MCAP_CR:
            dropped_big += 1
            continue
        kept.append(c)
    print(f"[MicroCap] mcap filter: kept {len(kept)}, dropped {dropped_big} too-big, "
          f"{dropped_unknown} unverifiable")

    for c in kept:
        c["score"] = round(c["tech"] + c["fund"], 1)
    kept.sort(key=lambda x: x["score"], reverse=True)

    results = []
    for i, c in enumerate(kept[:10]):
        d, f = c["d"], c.get("f", {})
        results.append({
            "rank": i + 1, "ticker": c["sym"], "price": d["price"],
            "mcap": f.get("mcap"),
            "score": c["score"], "tech_score": c["tech"], "fund_score": c["fund"],
            "rsi_d": d.get("rsi_d"), "rsi_w": d.get("rsi_w"),
            "rs_6m": d.get("rs_6m"), "dist_52wh": d.get("dist_52wh"),
            "macd_bull": d.get("macd_bull"), "golden_stack": d.get("golden_stack"),
            "roe": f.get("roe"), "roce": f.get("roce"),
            "sales_g": f.get("sales_g"), "profit_g": f.get("profit_g"),
            "pe": f.get("pe"),
        })

    elapsed = round(time.time() - start, 1)
    print(f"[MicroCap] Done in {elapsed}s — returning top {len(results)}")
    return {
        "market": "Micro Cap (< Rs 2000 Cr)",
        "total_scanned": total,
        "total_passed": len(scored),
        "results": results,
        "scan_time_seconds": elapsed,
    }
