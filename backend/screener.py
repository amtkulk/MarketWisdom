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

    if market == "india_hidden_gems":
        return run_hidden_gems_screener()

    if market == "india_wyckoff":
        return run_wyckoff_screener()

    if market == "india_multibagger":
        return run_multibagger_screener()

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
    """Batched 1-y daily download returning
    {ticker: [(date, close, volume, high, low), ...]}.
    Index 4 (low) was added for the Wyckoff scanner; all earlier callers use
    only indices 0-3 (date, close, volume, high), so appending low is safe."""
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
            lows = sub["Low"]
            for idx in sub.index:
                c, v, h = closes.get(idx), vols.get(idx), highs.get(idx)
                lo = lows.get(idx)
                if c == c and v == v:          # NaN check
                    rows.append((idx.to_pydatetime(), float(c), float(v),
                                 float(h) if h == h else float(c),
                                 float(lo) if lo == lo else float(c)))
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
    for r in rows:                        # chronological → last close of week wins
        dt, c = r[0], r[1]                # rows may be 4- or 5-tuples (low added for Wyckoff)
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


# ══════════════════════════════════════════════════════════════
#  HIDDEN GEMS SCANNER  ("wonder stock" finder)
#  A pro-trader fusion scan over the FULL market that tags each stock with
#  which institutional setup it matches, then ranks by a Wonder Score:
#    🤫 STEALTH    — quality name grinding up on RISING volume, still NOT
#                    extended (10-25% below 52wH): the coil before the move.
#    🔄 TURNAROUND — reclaimed the 200-DMA recently with momentum turning up
#                    (MACD bullish, RSI rising through 50): bottom with proof.
#    🚀 BREAKOUT   — strong name pushing to new highs (<5% from 52wH) on
#                    above-average volume: established winner in motion.
#  Every candidate must clear a quality floor (liquidity + not a falling
#  knife) so results are tradeable, not junk.
# ══════════════════════════════════════════════════════════════

def _sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def _detect_gem_setups(closes, vols, d):
    """Given price/vol history and the technical_score dict d, return
    (setups:list, extra:dict) describing which wonder-stock patterns fire."""
    setups = []
    extra = {}
    if len(closes) < 60:
        return setups, extra

    price = closes[-1]
    sma200 = _sma(closes, 200)
    sma50  = _sma(closes, 50)

    # Volume: last 10 days vs the prior 50 (is money flowing in now?)
    v_recent = sum(vols[-10:]) / 10 if len(vols) >= 10 else None
    v_base   = sum(vols[-60:-10]) / 50 if len(vols) >= 60 else None
    vol_surge = (v_recent / v_base) if (v_recent and v_base) else None
    extra["vol_surge"] = round(vol_surge, 2) if vol_surge else None
    rising_vol = vol_surge is not None and vol_surge >= 1.2

    rsi_d = d.get("rsi_d")
    dist  = d.get("dist_52wh")          # % below 52-week high
    mcap_ok = True                      # (fundamental gate handled by caller)

    # 🤫 STEALTH ACCUMULATION
    #   uptrending (above 200DMA & 50DMA), still has room (10-25% below high),
    #   RSI in constructive 50-65 band, and volume quietly rising.
    if (d.get("above_200dma") and d.get("above_50dma") and dist is not None
            and 8 <= dist <= 25 and rsi_d is not None and 50 <= rsi_d <= 68
            and rising_vol):
        setups.append("stealth")

    # 🔄 TURNAROUND / REVERSAL
    #   price reclaimed the 200DMA within the last ~15 sessions (was below,
    #   now above), MACD bullish, RSI rising through 50.
    if sma200 is not None and price > sma200 and d.get("macd_bull"):
        # was it below the 200DMA recently?
        below_recently = False
        if len(closes) >= 215:
            for k in range(2, 16):
                past = closes[-k]
                past_sma = _sma(closes[:len(closes)-k+1], 200)
                if past_sma and past < past_sma:
                    below_recently = True
                    break
        if below_recently and rsi_d is not None and 48 <= rsi_d <= 65:
            setups.append("turnaround")

    # 🚀 BREAKOUT MOMENTUM
    #   near/at 52w high (<5% below), strong RSI, on above-average volume.
    if (dist is not None and dist <= 5 and rsi_d is not None and rsi_d >= 60
            and (vol_surge is None or vol_surge >= 1.1) and d.get("above_50dma")):
        setups.append("breakout")

    return setups, extra


def run_hidden_gems_screener():
    """Full-market 'wonder stock' scan. Tags each pick with its setup(s) and
    ranks by Wonder Score = technical score + fundamental score + setup bonus."""
    import time
    start = time.time()

    # FULL market = Nifty 1000 ∪ beyond-1000 universe
    try:
        u1 = get_nifty1000_tickers()
    except Exception:
        u1 = []
    try:
        u2 = get_small_mid_cap_tickers()
    except Exception:
        u2 = []
    seen, tickers = set(), []
    for t in list(u1) + list(u2):
        if t not in seen:
            seen.add(t); tickers.append(t)
    total = len(tickers)
    print(f"[HiddenGems] Full-market scan: {total} tickers")

    price_data = {}
    CHUNK = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(_bulk_download_ohlc, tickers[i:i+CHUNK], "1y")
                for i in range(0, total, CHUNK)]
        futs.append(ex.submit(_bulk_download_ohlc, ["^NSEI"], "1y"))
        for f in concurrent.futures.as_completed(futs):
            try:
                price_data.update(f.result() or {})
            except Exception:
                pass

    nifty_rows = price_data.pop("^NSEI", [])
    nifty_closes = [r[1] for r in nifty_rows]

    candidates = []
    for t, rows in price_data.items():
        res = technical_score(rows, nifty_closes)
        if not res:
            continue
        tech, d = res
        # Quality floor: must be liquid and not a falling knife (above 200DMA).
        if not (d.get("liquid") and d.get("above_200dma")):
            continue
        closes = [r[1] for r in rows]
        vols   = [r[2] for r in rows]
        setups, extra = _detect_gem_setups(closes, vols, d)
        if not setups:
            continue
        candidates.append({"ticker": t, "tech": tech, "d": d,
                           "setups": setups, "extra": extra})

    # Rank by technical first, take a generous finalist pool for fundamentals.
    candidates.sort(key=lambda c: (len(c["setups"]), c["tech"]), reverse=True)
    finalists = candidates[:50]
    print(f"[HiddenGems] {len(price_data)} priced, {len(candidates)} matched a setup, "
          f"enriching top {len(finalists)}")

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

    SETUP_BONUS = {"stealth": 8, "turnaround": 6, "breakout": 5}
    for c in finalists:
        bonus = sum(SETUP_BONUS.get(s, 0) for s in c["setups"])
        # multi-setup confluence is the strongest tell → extra points
        if len(c["setups"]) >= 2:
            bonus += 6
        c["wonder"] = round(c["tech"] + c["fund"] + bonus, 1)

    finalists.sort(key=lambda c: c["wonder"], reverse=True)

    LABELS = {"stealth": "🤫 Stealth", "turnaround": "🔄 Turnaround", "breakout": "🚀 Breakout"}
    results = []
    for i, c in enumerate(finalists[:15]):
        d, f = c["d"], c.get("f", {})
        results.append({
            "rank": i + 1, "ticker": c["sym"], "price": d.get("price"),
            "wonder_score": c["wonder"], "tech_score": c["tech"], "fund_score": c["fund"],
            "setups": [LABELS.get(s, s) for s in c["setups"]],
            "setup_keys": c["setups"],
            "rsi_d": d.get("rsi_d"), "rsi_w": d.get("rsi_w"),
            "rs_6m": d.get("rs_6m"), "dist_52wh": d.get("dist_52wh"),
            "vol_surge": c["extra"].get("vol_surge"),
            "macd_bull": d.get("macd_bull"), "golden_stack": d.get("golden_stack"),
            "roe": f.get("roe"), "roce": f.get("roce"),
            "sales_g": f.get("sales_g"), "profit_g": f.get("profit_g"),
            "pe": f.get("pe"), "mcap": f.get("mcap"),
        })

    elapsed = round(time.time() - start, 1)
    print(f"[HiddenGems] Done in {elapsed}s — {len(results)} gems")
    return {
        "market": "Hidden Gems",
        "total_scanned": total,
        "total_passed": len(candidates),
        "results": results,
        "scan_time_seconds": elapsed,
    }


# ══════════════════════════════════════════════════════════════
#  WYCKOFF MOMENTUM SCANNER  (Nifty 1000)
#  Operationalizes Wyckoff's phases from OHLCV:
#    ACCUMULATION — a multi-week TIGHT range on DECLINING volume after a prior
#      decline; big money quietly absorbing supply (the "cause").
#    SPRING       — a recent dip BELOW the base low that reclaimed it fast (the
#      shakeout of weak holders) — a high-probability pre-markup trigger.
#    EARLY MARKUP — price BREAKING OUT of the base on volume >= 1.5x average,
#      closing in the top of the day's range (demand > supply), and not yet
#      extended far past the breakout (so we catch the move near its start).
#  Laws applied: Supply/Demand (close position + volume), Cause/Effect (base
#  length), Effort vs Result (volume vs price progress).
#  Runs on BOTH daily (swing) and weekly (position) candles; each stock shows
#  which timeframe(s) fired.
# ══════════════════════════════════════════════════════════════

def _weekly_from_daily(rows):
    """Collapse daily (date,close,vol,high,low) into weekly bars by ISO week."""
    if not rows:
        return []
    weeks = {}
    order = []
    for (dt_, c, v, h, lo) in rows:
        key = (dt_.isocalendar()[0], dt_.isocalendar()[1])
        if key not in weeks:
            weeks[key] = {"o_close": c, "close": c, "vol": 0.0, "high": h, "low": lo, "dt": dt_}
            order.append(key)
        wk = weeks[key]
        wk["close"] = c
        wk["vol"] += v
        wk["high"] = max(wk["high"], h)
        wk["low"] = min(wk["low"], lo)
        wk["dt"] = dt_
    return [(weeks[k]["dt"], weeks[k]["close"], weeks[k]["vol"],
             weeks[k]["high"], weeks[k]["low"]) for k in order]


def _wyckoff_analyze(bars, base_len, min_bars):
    """Detect Wyckoff phase on a bar series (daily or weekly).
    bars: [(date, close, vol, high, low), ...]  oldest->newest.
    Returns dict {phase, score, detail...} or None if nothing fires."""
    n = len(bars)
    if n < min_bars:
        return None
    closes = [b[1] for b in bars]
    vols   = [b[2] for b in bars]
    highs  = [b[3] for b in bars]
    lows   = [b[4] for b in bars]
    price  = closes[-1]

    # ---- define the "base" as the window just BEFORE the latest few bars ----
    # base window: bars [-(base_len+3) : -3]   (leave last 3 for breakout read)
    if n < base_len + 6:
        return None
    base = slice(n - base_len - 3, n - 3)
    base_highs = highs[base]; base_lows = lows[base]; base_vols = vols[base]
    base_closes = closes[base]
    base_hi = max(base_highs); base_lo = min(base_lows)
    if base_hi <= 0 or base_lo <= 0:
        return None

    # Range tightness: (hi-lo)/lo over the base. Tight = good accumulation.
    rng_pct = (base_hi - base_lo) / base_lo * 100
    # Prior trend: compare base midpoint to the 20 bars before the base (want
    # a prior decline or sideways, i.e. not already extended up).
    pre = slice(max(0, n - base_len - 23), n - base_len - 3)
    pre_closes = closes[pre] if (n - base_len - 23) >= 0 else base_closes
    prior_move = (base_closes[0] - (pre_closes[0] if pre_closes else base_closes[0]))

    # Volume dry-up in the base: base avg vol vs the pre-base avg vol.
    base_avg_v = sum(base_vols) / len(base_vols) if base_vols else 0
    pre_vols = vols[pre] if (n - base_len - 23) >= 0 else base_vols
    pre_avg_v = sum(pre_vols) / len(pre_vols) if pre_vols else base_avg_v
    vol_dryup = (base_avg_v < pre_avg_v) if pre_avg_v else False

    last = bars[-1]
    last_close, last_vol, last_high, last_low = last[1], last[2], last[3], last[4]
    vol20 = sum(vols[-20:]) / min(20, len(vols))
    # Close position within the last bar's range (1 = closed at high).
    rng = (last_high - last_low) or 1e-9
    close_pos = (last_close - last_low) / rng

    phase = None
    score = 0
    detail = {
        "base_len": base_len, "range_pct": round(rng_pct, 1),
        "base_hi": round(base_hi, 2), "base_lo": round(base_lo, 2),
        "vol_vs_avg": round(last_vol / vol20, 2) if vol20 else None,
        "vol_dryup": vol_dryup,
    }

    tight = rng_pct <= 22          # base no wider than ~22%
    # ---- EARLY MARKUP: breakout above base high on effort, not extended ----
    if (tight and last_close > base_hi and close_pos >= 0.55
            and vol20 and last_vol >= 1.5 * vol20):
        ext = (last_close - base_hi) / base_hi * 100      # how far past breakout
        if ext <= 12:                                     # still early
            phase = "markup"
            score = 55
            score += 12 if vol_dryup else 0               # textbook dry-up then surge
            score += min(15, (last_vol / vol20 - 1.5) * 10)  # extra volume thrust
            score += 8 if close_pos >= 0.8 else 0
            score += max(0, 8 - ext)                      # earlier = better
            detail["breakout_ext_pct"] = round(ext, 1)

    # ---- SPRING: recent dip below base low that reclaimed it ----
    if phase is None and tight:
        recent_low = min(lows[-4:])
        if recent_low < base_lo and last_close > base_lo and close_pos >= 0.5:
            phase = "spring"
            score = 48
            score += 12 if vol_dryup else 0
            score += 10 if close_pos >= 0.75 else 0
            score += max(0, 10 - rng_pct * 0.3)
            detail["spring_undercut_pct"] = round((base_lo - recent_low) / base_lo * 100, 1)

    # ---- ACCUMULATION: still inside a tight, dried-up base ----
    if phase is None and tight and vol_dryup and prior_move <= 0:
        # price sitting in the upper half of the base = coiling toward breakout
        pos_in_base = (last_close - base_lo) / ((base_hi - base_lo) or 1e-9)
        if 0.35 <= pos_in_base <= 1.02:
            phase = "accumulation"
            score = 38
            score += max(0, 12 - rng_pct * 0.4)           # tighter = better cause
            score += 8 * pos_in_base                       # nearer the top = readier
            detail["pos_in_base"] = round(pos_in_base, 2)

    if phase is None:
        return None
    detail["phase"] = phase
    detail["score"] = round(min(score, 100), 1)
    return detail


def run_wyckoff_screener():
    """Wyckoff momentum scan over Nifty 1000 on daily + weekly timeframes."""
    import time
    start = time.time()
    try:
        tickers = get_nifty1000_tickers()
    except Exception:
        tickers = []
    total = len(tickers)
    print(f"[Wyckoff] Scanning {total} Nifty-1000 stocks (daily + weekly)")

    price_data = {}
    CHUNK = 100
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(_bulk_download_ohlc, tickers[i:i+CHUNK], "2y")
                for i in range(0, total, CHUNK)]
        for f in concurrent.futures.as_completed(futs):
            try:
                price_data.update(f.result() or {})
            except Exception:
                pass

    PHASE_LABEL = {"markup": "🚀 Early Markup", "spring": "🪤 Spring",
                   "accumulation": "🏗️ Accumulation"}
    PHASE_RANK  = {"markup": 3, "spring": 2, "accumulation": 1}

    candidates = []
    for t, rows in price_data.items():
        if len(rows) < 120:
            continue
        closes = [r[1] for r in rows]
        price = closes[-1]
        # Liquidity floor (turnover > 1 Cr), same spirit as other scanners
        vol20 = sum(r[2] for r in rows[-20:]) / 20
        if vol20 * price < 1e7:
            continue

        daily = _wyckoff_analyze(rows, base_len=30, min_bars=90)          # ~6-week base
        weekly_bars = _weekly_from_daily(rows)
        weekly = _wyckoff_analyze(weekly_bars, base_len=12, min_bars=30)  # ~12-week base

        if not daily and not weekly:
            continue

        # Combined: prefer the stronger phase; confluence (both fire) is gold.
        tfs = {}
        if daily:  tfs["daily"] = daily
        if weekly: tfs["weekly"] = weekly
        # pick the "headline" phase = highest PHASE_RANK across timeframes
        headline = max(tfs.values(), key=lambda d: (PHASE_RANK[d["phase"]], d["score"]))
        combo = headline["score"]
        if daily and weekly:
            combo += 10                                   # multi-timeframe confluence
        if daily and weekly and daily["phase"] == weekly["phase"]:
            combo += 6                                    # same phase both = strong
        candidates.append({
            "ticker": t, "sym": t.replace(".NS", "").replace(".BO", ""),
            "price": round(price, 2),
            "headline_phase": headline["phase"],
            "daily": daily, "weekly": weekly,
            "wyckoff_score": round(min(combo, 100), 1),
            "tf": "+".join(tfs.keys()),
        })

    candidates.sort(key=lambda c: (PHASE_RANK[c["headline_phase"]], c["wyckoff_score"]),
                    reverse=True)
    top = candidates[:20]

    results = []
    for i, c in enumerate(top):
        d = c["daily"] or {}; w = c["weekly"] or {}
        results.append({
            "rank": i + 1, "ticker": c["sym"], "price": c["price"],
            "wyckoff_score": c["wyckoff_score"],
            "phase": PHASE_LABEL[c["headline_phase"]],
            "phase_key": c["headline_phase"],
            "timeframes": c["tf"],
            "daily_phase": PHASE_LABEL.get(d.get("phase")) if d else None,
            "weekly_phase": PHASE_LABEL.get(w.get("phase")) if w else None,
            "range_pct": (d or w).get("range_pct"),
            "vol_vs_avg": (d or w).get("vol_vs_avg"),
            "vol_dryup": (d or w).get("vol_dryup"),
            "base_hi": (d or w).get("base_hi"),
            "base_lo": (d or w).get("base_lo"),
            "breakout_ext_pct": (d or w).get("breakout_ext_pct"),
            "spring_undercut_pct": (d or w).get("spring_undercut_pct"),
        })

    elapsed = round(time.time() - start, 1)
    print(f"[Wyckoff] {len(price_data)} priced, {len(candidates)} matched, "
          f"top {len(results)} in {elapsed}s")
    return {
        "market": "Wyckoff Momentum",
        "total_scanned": total,
        "total_passed": len(candidates),
        "results": results,
        "scan_time_seconds": elapsed,
    }


# ══════════════════════════════════════════════════════════════
#  AMIT MULTIBAGGER EARLY SIGNAL SCANNER  (full market)
#  Built from three real winning calls:
#    Wheels India  ~₹950  (17 Feb)  → ₹2,200+  (+132%)
#    Spectrum Elec ₹2,063 (22 Jul)  → ₹3,839   (+86%)
#    Fermenta      ~₹388            → ~₹560    (+44%)
#  Common DNA at the START of each move:
#    business change (earnings ACCELERATING, PAT faster than sales)
#    + abnormal volume + breakout from a base + supportive ownership
#    + not yet re-rated.  This scanner scores every stock 0-100 on the
#  auto-computable subset of Amit's 20 parameters. Catalyst/news signals
#  (8-promoter open-market buys, 12 capex, 13 approvals, 14 order wins,
#  15 investor meets, 16 exchange clarifications, 18 delivery %) cannot
#  be fetched reliably, so they are OMITTED and the score is rescaled.
#
#  Pipeline:
#   1. Full-market 1y price download (Nifty 1000 ∪ beyond-1000).
#   2. Price/volume signals for every stock; drop illiquid, penny and
#      ALREADY-RUN names (up >70% from 6-month low) — we want Wheels at
#      ₹950, not Wheels at ₹2,000.
#   3. Top ~120 by price signal → Screener.in deep fetch (quarterly P&L,
#      ROCE trend, borrowings, operating cash flow, shareholding).
#   4. Score = points earned / points available × 100.
# ══════════════════════════════════════════════════════════════

# key → (label, weight, group). Weights sum to 100.
MB_PARAMS = [
    ("rev_accel",   "1 · Revenue acceleration",          7,  "Business"),
    ("ebitda_accel","2 · EBITDA acceleration",            8,  "Business"),
    ("pat_accel",   "3 · PAT / EPS acceleration",        12,  "Business"),
    ("roce_up",     "4 · ROCE improving",                 5,  "Business"),
    ("debt_down",   "5 · Debt reduction",                 5,  "Business"),
    ("ocf",         "6 · Operating cash flow",            5,  "Business"),
    ("prom_hold",   "7 · Promoter holding",               3,  "Ownership"),
    ("prom_up",     "8 · Promoter stake rising",          4,  "Ownership"),
    ("fii_up",      "9 · FII holding change",             3,  "Ownership"),
    ("dii_up",      "10 · DII / MF holding change",       3,  "Ownership"),
    ("inst_entry",  "11 · New institutional entry",       2,  "Ownership"),
    ("vol_ratio",   "17 · Volume vs 20-day average",     12,  "Price"),
    ("breakout",    "19 · Breakout from consolidation",  17,  "Price"),
    ("trend_rs",    "Trend + relative strength",          8,  "Price"),
    ("valuation",   "20 · Valuation vs growth (PEG)",     6,  "Valuation"),
]
MB_WEIGHT = {k: w for k, _l, w, _g in MB_PARAMS}
MB_OMITTED = [
    "8 · Promoter open-market buying (bulk/insider deals) — proxied by stake change",
    "12 · Capex announcement", "13 · New product / approval",
    "14 · Order book / order wins", "15 · Investor / analyst meetings",
    "16 · Exchange price/volume clarification", "18 · Delivery % of volume",
]
MB_MIN_PRICE = 40            # skip penny names
MB_MIN_TURNOVER = 5e6        # 20-day avg turnover ≥ ₹50 lakh
MB_MAX_RUN_PCT = 70          # up >70% from 6-month low = already ran → excluded


def _mb_price_signals(rows, nifty_closes):
    """Price/volume part of the score. rows = [(date, close, vol, high, low), ...].
    Returns dict (with 'excluded' reason if it fails a gate) or None if too little data."""
    if not rows or len(rows) < 130:
        return None
    closes = [r[1] for r in rows]
    vols   = [r[2] for r in rows]
    highs  = [r[3] for r in rows]
    lows   = [r[4] if len(r) > 4 else r[1] for r in rows]
    price  = closes[-1]
    n = len(closes)
    out = {"price": round(price, 2)}

    # ---- gates ---------------------------------------------------------
    vol20 = sum(vols[-20:]) / 20
    out["turnover_cr"] = round(vol20 * price / 1e7, 2)
    if price < MB_MIN_PRICE:
        out["excluded"] = "penny"; return out
    if vol20 * price < MB_MIN_TURNOVER:
        out["excluded"] = "illiquid"; return out
    low6m = min(lows[-126:]) if n >= 126 else min(lows)
    ran = (price / low6m - 1) * 100 if low6m > 0 else 0
    out["ran_pct"] = round(ran, 1)
    if ran > MB_MAX_RUN_PCT:
        out["excluded"] = "already_ran"; return out

    pts = {}
    notes = {}

    # ---- 17 · Volume vs 20-day average (abnormal participation) --------
    base_v = sum(vols[-23:-3]) / 20 if n >= 23 else vol20
    if base_v > 0:
        r1 = vols[-1] / base_v
        r3 = (sum(vols[-3:]) / 3) / base_v
        peak5 = max(vols[-5:]) / base_v
        vr = max(r1, r3, peak5 * 0.85)          # a spike in the last week still counts
    else:
        vr = 0
    v10 = sum(vols[-10:]) / 10
    v50 = sum(vols[-60:-10]) / 50 if n >= 60 else base_v
    sustained = (v10 / v50) if v50 else 0
    out["vol_ratio"] = round(vr, 2)
    out["vol_sustained"] = round(sustained, 2)
    w = MB_WEIGHT["vol_ratio"]
    p = w if vr >= 3 else 0.75 * w if vr >= 2 else 0.5 * w if vr >= 1.5 else 0.25 * w if vr >= 1.2 else 0
    if sustained >= 1.3:
        p = min(w, p + 0.2 * w)                  # steady accumulation, not a one-day blip
    pts["vol_ratio"] = p
    notes["vol_ratio"] = f"{vr:.1f}× avg (10d/50d {sustained:.1f}×)"

    # ---- 19 · Breakout from consolidation -----------------------------
    # base = 40 sessions ending 3 sessions ago; breakout read on the last 3.
    BL = 40
    bh = highs[n - BL - 3: n - 3]; bl = lows[n - BL - 3: n - 3]
    base_hi, base_lo = max(bh), min(bl)
    rng = (base_hi - base_lo) / base_lo * 100 if base_lo > 0 else 999
    ext = (price - base_hi) / base_hi * 100 if base_hi > 0 else 0
    pos = (price - base_lo) / ((base_hi - base_lo) or 1e-9)
    out.update({"base_hi": round(base_hi, 2), "base_lo": round(base_lo, 2),
                "base_range_pct": round(rng, 1), "ext_pct": round(ext, 1)})
    w = MB_WEIGHT["breakout"]
    state, p = "none", 0
    if 4 <= rng <= 35:                           # a genuine consolidation (not a dead-flat / circuit-locked line)
        tight_bonus = 0.15 * w if rng <= 18 else 0.08 * w if rng <= 25 else 0
        if 0 < ext <= 12:
            state, p = "breakout", 0.8 * w + tight_bonus
        elif 12 < ext <= 20:
            state, p = "extended_breakout", 0.45 * w
        elif -4 <= ext <= 0:
            state, p = "at_resistance", 0.55 * w + tight_bonus
        elif pos >= 0.6:
            state, p = "coiling", 0.3 * w + tight_bonus
    p = min(w, p)
    out["breakout_state"] = state
    pts["breakout"] = p
    lbl = {"breakout": f"broke out of {rng:.0f}% base, {ext:+.1f}% past",
           "extended_breakout": f"broke out, already {ext:+.1f}% past base",
           "at_resistance": f"pressing base high ({ext:+.1f}%)",
           "coiling": f"upper half of {rng:.0f}% base",
           "none": (f"range too flat ({rng:.1f}%)" if rng < 4 else f"no tight base ({rng:.0f}% range)")}
    notes["breakout"] = lbl[state]

    # ---- Trend + relative strength (supports the breakout) ------------
    sma50, sma200 = _sma(closes, 50), _sma(closes, 200)
    hi52 = max(highs[-250:])
    dist = (hi52 - price) / hi52 * 100 if hi52 else 100
    rs = None
    r_s = _pct_return(closes, 126)
    r_n = _pct_return(nifty_closes, 126) if nifty_closes else None
    if r_s is not None and r_n is not None:
        rs = round(r_s - r_n, 1)
    t = 0
    if sma200 and price > sma200: t += 2
    if sma50 and price > sma50: t += 1
    if rs is not None and rs > 0: t += 2
    if rs is not None and rs > 15: t += 1
    if dist <= 10: t += 2
    pts["trend_rs"] = min(MB_WEIGHT["trend_rs"], t)
    out.update({"rs_6m": rs, "dist_52wh": round(dist, 1),
                "above_200dma": bool(sma200 and price > sma200)})
    notes["trend_rs"] = (f"RS {rs:+.0f}pp vs Nifty, " if rs is not None else "") + f"{dist:.0f}% below 52wH"

    # "Already moving" penalty — earlier is better
    penalty = 8 if ran > 45 else 3 if ran > 30 else 0
    out["late_penalty"] = penalty
    out["pts"], out["notes"] = pts, notes
    out["price_pts"] = round(sum(pts.values()) - penalty, 2)
    return out


def _mb_num(txt):
    import re
    if txt is None:
        return None
    t = txt.replace(",", "").replace("%", "").strip()
    m = re.search(r'-?\d+(?:\.\d+)?', t)
    return float(m.group(0)) if m else None


def _mb_parse_screener_html(html):
    """Parse a Screener.in company page into raw series. Pure function (testable)."""
    from bs4 import BeautifulSoup
    import re
    soup = BeautifulSoup(html, "html.parser")
    raw = {}
    for li in soup.select("#top-ratios li") or []:
        t = " ".join((li.get_text() or "").split()).lower()
        v = _mb_num(li.select_one(".number").get_text() if li.select_one(".number") else t)
        if v is None:
            continue
        if "market cap" in t: raw.setdefault("mcap", v)
        elif "stock p/e" in t: raw.setdefault("pe", v)
        elif "roce" in t: raw.setdefault("roce", v)
        elif "roe" in t: raw.setdefault("roe", v)

    def table_series(section_sel, inner_sel=None):
        sec = soup.select_one(section_sel)
        if not sec:
            return {}
        tbl = (sec.select_one(inner_sel + " table") if inner_sel else None) or sec.find("table")
        if not tbl:
            return {}
        rows = {}
        for tr in tbl.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label = re.sub(r'[\s\+ ]+$', '', cells[0].get_text(" ", strip=True)).strip()
            label = re.sub(r'\s+', ' ', label)
            if not label:
                continue
            vals = [_mb_num(c.get_text(strip=True)) for c in cells[1:]]
            rows[label] = vals
        return rows

    def pick(rows, *names):
        for nm in names:
            for k, v in rows.items():
                if k.lower() == nm.lower():
                    return v
        for nm in names:
            for k, v in rows.items():
                if k.lower().startswith(nm.lower()):
                    return v
        return None

    q = table_series("section#quarters")
    raw["q_sales"]  = pick(q, "Sales", "Revenue")
    raw["q_ebitda"] = pick(q, "Operating Profit", "Financing Profit")
    raw["q_pat"]    = pick(q, "Net Profit")
    raw["q_eps"]    = pick(q, "EPS in Rs", "EPS")
    raw["q_opm"]    = pick(q, "OPM %", "OPM", "Financing Margin %")
    r = table_series("section#ratios")
    raw["roce_hist"] = pick(r, "ROCE %", "ROCE", "ROE %")
    b = table_series("section#balance-sheet")
    raw["borrowings"] = pick(b, "Borrowings")
    c = table_series("section#cash-flow")
    raw["ocf"] = pick(c, "Cash from Operating Activity", "Cash from Operating")
    s = table_series("section#shareholding", "#quarterly-shp")
    raw["sh_prom"] = pick(s, "Promoters")
    raw["sh_fii"]  = pick(s, "FIIs")
    raw["sh_dii"]  = pick(s, "DIIs")
    # drop trailing None columns / empty series
    for k in list(raw.keys()):
        v = raw[k]
        if isinstance(v, list):
            v = [x for x in v]
            while v and v[-1] is None:
                v.pop()
            raw[k] = v if any(x is not None for x in v) else None
    return raw


def _fetch_screener_deep(symbol):
    """One Screener.in GET (consolidated, falling back to standalone) → raw series."""
    import time as _t
    try:
        from curl_cffi import requests as cffi
    except Exception:
        return {}
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    for path in (f"{symbol}/consolidated/", f"{symbol}/"):
        url = f"https://www.screener.in/company/{path}"
        for attempt in range(2):
            try:
                r = cffi.get(url, headers=hdr, impersonate="chrome120", timeout=10)
            except Exception:
                r = None
            if r is not None and r.status_code in (429, 503) and attempt == 0:
                _t.sleep(2.0)                      # polite back-off, one retry
                continue
            break
        if r is None or r.status_code != 200:
            continue
        try:
            raw = _mb_parse_screener_html(r.content)
        except Exception:
            raw = {}
        # Consolidated pages of companies without subsidiaries are often empty →
        # try standalone before giving up.
        if raw.get("q_sales") or raw.get("q_pat"):
            return raw
    return {}


def _mb_yoy(series, back=1):
    """YoY growth % for the quarter `back` from the end (1 = latest) vs 4 quarters
    earlier. Returns (growth or None, turnaround_flag)."""
    if not series:
        return None, False
    i = len(series) - back
    j = i - 4
    if j < 0 or i >= len(series):
        return None, False
    a, b = series[i], series[j]
    if a is None or b is None:
        return None, False
    if b <= 0:
        return None, (a > 0)                    # loss → profit = turnaround
    return (a / b - 1) * 100, False


def _mb_accel_pts(series, w, hi, mid):
    """Points for an accelerating growth line (latest YoY vs previous quarter's YoY)."""
    g0, turn = _mb_yoy(series, 1)
    g1, _ = _mb_yoy(series, 2)
    if g0 is None:
        if turn:
            return 0.8 * w, "turned profitable vs year-ago loss", g0
        return None, "n/a", None
    acc = g1 is not None and g0 > g1
    if g0 >= hi and acc:   p = w
    elif g0 >= hi:         p = 0.6 * w
    elif g0 >= mid and acc: p = 0.6 * w
    elif g0 >= mid:        p = 0.35 * w
    elif g0 > 0 and acc:   p = 0.2 * w
    else:                  p = 0
    note = f"YoY {g0:+.0f}%" + (f" (prev qtr {g1:+.0f}%)" if g1 is not None else "")
    return p, note, g0


def _mb_rs(x):
    """₹ amount for notes: '₹1,234' / '−₹10'."""
    return ("−₹" if x < 0 else "₹") + f"{abs(x):,.0f}"


def _mb_fund_signals(raw):
    """Business, ownership and valuation points from parsed Screener.in data.
    Each entry is None when the data isn't available (then it's excluded from
    the denominator instead of counted as a zero)."""
    pts, notes, vals = {}, {}, {}

    p, n, g_rev = _mb_accel_pts(raw.get("q_sales"), MB_WEIGHT["rev_accel"], 25, 15)
    pts["rev_accel"], notes["rev_accel"] = p, n
    p, n, g_ebit = _mb_accel_pts(raw.get("q_ebitda"), MB_WEIGHT["ebitda_accel"], 30, 20)
    pts["ebitda_accel"], notes["ebitda_accel"] = p, n
    pat_series = raw.get("q_pat") or raw.get("q_eps")
    p, n, g_pat = _mb_accel_pts(pat_series, MB_WEIGHT["pat_accel"], 35, 20)
    if p is not None and g_pat is not None and g_rev is not None and g_pat > g_rev and g_pat >= 15:
        p = min(MB_WEIGHT["pat_accel"], p + 0.2 * MB_WEIGHT["pat_accel"])
        n += " · profit outgrowing sales"
    pts["pat_accel"], notes["pat_accel"] = p, n
    _r = lambda x: round(x, 1) if x is not None else None
    vals.update({"rev_yoy": _r(g_rev), "ebitda_yoy": _r(g_ebit), "pat_yoy": _r(g_pat)})

    # 4 · ROCE improving (yearly)
    rh = [x for x in (raw.get("roce_hist") or []) if x is not None]
    w = MB_WEIGHT["roce_up"]
    if len(rh) >= 2:
        d = rh[-1] - rh[-2]
        p = w if d >= 3 else 0.7 * w if d >= 1 else 0.4 * w if d > 0 else 0
        if rh[-1] >= 18 and d > -1:
            p = max(p, 0.5 * w)
        if rh[-1] < 8:
            p = min(p, 0.3 * w)
        pts["roce_up"], notes["roce_up"] = p, f"{rh[-2]:.0f}% → {rh[-1]:.0f}%"
        vals["roce"] = rh[-1]
    else:
        pts["roce_up"], notes["roce_up"] = None, "n/a"

    # 5 · Debt reduction (yearly borrowings)
    br = [x for x in (raw.get("borrowings") or []) if x is not None]
    w = MB_WEIGHT["debt_down"]
    if len(br) >= 2:
        a, b = br[-1], br[-2]
        if a <= 1:
            p, n = w, "debt-free"
        elif b > 0 and a <= b * 0.85:
            p, n = w, f"{_mb_rs(b)} → {_mb_rs(a)} Cr"
        elif b > 0 and a <= b * 0.95:
            p, n = 0.7 * w, f"{_mb_rs(b)} → {_mb_rs(a)} Cr"
        elif b > 0 and a <= b * 1.05:
            p, n = 0.3 * w, "flat"
        else:
            p, n = 0, f"rising {_mb_rs(b)} → {_mb_rs(a)} Cr"
        pts["debt_down"], notes["debt_down"] = p, n
    else:
        pts["debt_down"], notes["debt_down"] = None, "n/a"

    # 6 · Operating cash flow
    oc = [x for x in (raw.get("ocf") or []) if x is not None]
    w = MB_WEIGHT["ocf"]
    if len(oc) >= 2:
        a, b = oc[-1], oc[-2]
        if a > 0 and (b <= 0 or a >= b):
            p = w
        elif a > 0:
            p = 0.6 * w
        else:
            p = 0
        pts["ocf"], notes["ocf"] = p, f"{_mb_rs(b)} → {_mb_rs(a)} Cr"
    else:
        pts["ocf"], notes["ocf"] = None, "n/a"

    # 7-11 · Shareholding (quarterly %)
    pr = [x for x in (raw.get("sh_prom") or []) if x is not None]
    fi = [x for x in (raw.get("sh_fii") or []) if x is not None]
    di = [x for x in (raw.get("sh_dii") or []) if x is not None]
    if pr:
        w = MB_WEIGHT["prom_hold"]
        lvl = pr[-1]
        fell = len(pr) >= 5 and pr[-1] < pr[-5] - 2
        p = 0 if fell else w if lvl >= 50 else 0.7 * w if lvl >= 35 else 0.35 * w if lvl >= 25 else 0
        pts["prom_hold"], notes["prom_hold"] = p, f"{lvl:.1f}%" + (" (falling)" if fell else "")
        vals["promoter"] = lvl
    else:
        pts["prom_hold"], notes["prom_hold"] = None, "n/a"
    if len(pr) >= 3:
        w = MB_WEIGHT["prom_up"]
        d = pr[-1] - pr[-3]
        p = w if d >= 0.5 else 0.6 * w if d > 0.1 else 0.25 * w if d >= -0.1 else 0
        pts["prom_up"], notes["prom_up"] = p, f"{d:+.2f}pp over 2 qtrs"
    else:
        pts["prom_up"], notes["prom_up"] = None, "n/a"
    for key, ser, nm in (("fii_up", fi, "FII"), ("dii_up", di, "DII")):
        w = MB_WEIGHT[key]
        if len(ser) >= 3:
            d = ser[-1] - ser[-3]
            p = w if d >= 1.0 else 0.65 * w if d >= 0.3 else 0.3 * w if d > 0 else 0
            pts[key], notes[key] = p, f"{ser[-1]:.1f}% ({d:+.2f}pp / 2 qtrs)"
        else:
            pts[key], notes[key] = None, "n/a"
    if len(fi) >= 5 or len(di) >= 5:
        w = MB_WEIGHT["inst_entry"]
        hit = []
        for ser, nm in ((fi, "FII"), (di, "DII")):
            if len(ser) >= 5 and ser[-5] <= 1.0 and ser[-1] >= ser[-5] + 1.0:
                hit.append(nm)
        pts["inst_entry"] = w if hit else 0
        notes["inst_entry"] = (" & ".join(hit) + " newly above 1%") if hit else "no new entry"
    else:
        pts["inst_entry"], notes["inst_entry"] = None, "n/a"

    # 20 · Valuation vs growth (PEG on trailing-4-quarter profit growth)
    pe = raw.get("pe")
    w = MB_WEIGHT["valuation"]
    ps = [x for x in (pat_series or [])]
    g_ttm = None
    if len(ps) >= 8 and all(x is not None for x in ps[-8:]):
        a, b = sum(ps[-4:]), sum(ps[-8:-4])
        if b > 0:
            g_ttm = (a / b - 1) * 100
    gs = [g for g in (g_ttm, g_pat) if g is not None]
    g_exp = min(100.0, sum(gs) / len(gs)) if gs else None
    if pe is not None and pe > 0 and g_exp is not None:
        if g_exp <= 0:
            p, n = 0, f"P/E {pe:.0f}, profit not growing"
        else:
            peg = pe / g_exp
            p = w if peg <= 0.8 else 0.8 * w if peg <= 1.2 else 0.5 * w if peg <= 1.8 else 0.25 * w if peg <= 2.5 else 0
            n = f"P/E {pe:.0f} ÷ growth {g_exp:.0f}% = PEG {peg:.2f}"
            vals["peg"] = round(peg, 2)
        pts["valuation"], notes["valuation"] = p, n
    else:
        pts["valuation"], notes["valuation"] = None, ("loss-making / no P/E" if pe is None or pe <= 0 else "n/a")
    vals.update({"pe": pe, "mcap": raw.get("mcap")})
    return pts, notes, vals


def _mb_score(price_sig, fund_pts):
    """Combine into the 0-100 Early Signal Score with rescaling for missing data."""
    pts = dict(price_sig["pts"])
    pts.update(fund_pts or {})
    avail = {k: v for k, v in pts.items() if v is not None}
    avail_max = sum(MB_WEIGHT[k] for k in avail)
    earned = sum(avail.values()) - price_sig.get("late_penalty", 0)
    non_price_max = sum(MB_WEIGHT[k] for k in avail if k not in ("vol_ratio", "breakout", "trend_rs"))
    coverage = avail_max / 100.0
    # Rescale only when we have at least half of the business/ownership/valuation
    # signals; otherwise score pessimistically (missing = 0) so a stock with only
    # price data can't look like a perfect 100.
    price_only = non_price_max < 0.5 * 63
    denom = 100.0 if price_only else avail_max
    score = max(0.0, earned / denom * 100) if denom else 0.0
    hits = sum(1 for k, v in avail.items() if v >= 0.6 * MB_WEIGHT[k])
    return round(min(score, 100), 1), round(coverage * 100), price_only, hits, len(avail)


def run_multibagger_screener():
    """Full-market Early Signal scan → top ranked stocks with a per-signal breakdown."""
    import time
    start = time.time()
    try:
        u1 = get_nifty1000_tickers()
    except Exception:
        u1 = []
    try:
        u2 = get_small_mid_cap_tickers()
    except Exception:
        u2 = []
    seen, tickers = set(), []
    for t in list(u1) + list(u2):
        if t not in seen:
            seen.add(t); tickers.append(t)
    total = len(tickers)
    print(f"[Multibagger] Full-market scan: {total} tickers")

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

    excluded = {"penny": 0, "illiquid": 0, "already_ran": 0}
    cands = []
    for t, rows in price_data.items():
        sig = _mb_price_signals(rows, nifty_closes)
        if not sig:
            continue
        if sig.get("excluded"):
            excluded[sig["excluded"]] = excluded.get(sig["excluded"], 0) + 1
            continue
        # Stage-1 trigger: SOMETHING must be happening on the chart — abnormal
        # volume or a base breakout/pressing resistance. Quiet stocks wait.
        if sig["vol_ratio"] < 1.3 and sig["breakout_state"] not in ("breakout", "at_resistance"):
            continue
        if sig["breakout_state"] == "none" and not sig["above_200dma"]:
            continue                                   # spike inside a downtrend
        sig["ticker"] = t
        cands.append(sig)

    cands.sort(key=lambda s: s["price_pts"], reverse=True)
    finalists = cands[:120]
    print(f"[Multibagger] {len(price_data)} priced, excluded {excluded}, "
          f"{len(cands)} with a chart trigger → deep-fetching {len(finalists)}")

    def enrich(sig):
        sym = sig["ticker"].replace(".NS", "").replace(".BO", "")
        sig["sym"] = sym
        raw = _fetch_screener_deep(sym)
        sig["fund_ok"] = bool(raw)
        if raw:
            fp, fn, fv = _mb_fund_signals(raw)
        else:
            fp, fn, fv = {}, {}, {}
        sig["fpts"], sig["fnotes"], sig["fvals"] = fp, fn, fv
        return sig

    if finalists:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            finalists = list(ex.map(enrich, finalists))

    fund_fetched = sum(1 for s in finalists if s.get("fund_ok"))
    scored = []
    for s in finalists:
        mc = s["fvals"].get("mcap")
        if mc is not None and mc < 300:                # sub-₹300 Cr: too thin to trust
            continue
        score, cov, price_only, hits, n_avail = _mb_score(s, s["fpts"])
        s.update({"score": score, "coverage": cov, "price_only": price_only,
                  "hits": hits, "n_avail": n_avail})
        scored.append(s)
    scored.sort(key=lambda s: (not s["price_only"], s["score"]), reverse=True)

    results = []
    for i, s in enumerate(scored[:25]):
        allp = dict(s["pts"]); allp.update(s["fpts"])
        alln = dict(s["notes"]); alln.update(s["fnotes"])
        breakdown = []
        for k, label, w, grp in MB_PARAMS:
            v = allp.get(k)
            breakdown.append({"key": k, "label": label, "group": grp, "max": w,
                              "pts": None if v is None else round(v, 1),
                              "hit": v is not None and v >= 0.6 * w,
                              "note": alln.get(k, "n/a")})
        sc = s["score"]
        # The thesis is "business change + chart confirmation": a stock can only be
        # STRONG if profit/EBITDA is actually accelerating, and only BUILDING if
        # at least one of revenue/EBITDA/PAT is improving.
        biz_hit = any(b["hit"] for b in breakdown if b["key"] in ("pat_accel", "ebitda_accel"))
        biz_any = any((b["pts"] or 0) > 0 for b in breakdown
                      if b["key"] in ("rev_accel", "ebitda_accel", "pat_accel"))
        tier = ("strong" if sc >= 70 and s["hits"] >= 0.6 * s["n_avail"]
                and not s["price_only"] and biz_hit
                else "building" if sc >= 55 and biz_any else "watch")
        fv = s["fvals"]
        results.append({
            "rank": i + 1, "ticker": s["sym"], "price": s["price"],
            "score": sc, "tier": tier, "coverage": s["coverage"],
            "price_only": s["price_only"], "hits": s["hits"], "n_avail": s["n_avail"],
            "breakout_state": s["breakout_state"], "base_hi": s["base_hi"],
            "base_lo": s["base_lo"], "base_range_pct": s["base_range_pct"],
            "ext_pct": s["ext_pct"], "ran_pct": s["ran_pct"],
            "vol_ratio": s["vol_ratio"], "rs_6m": s["rs_6m"], "dist_52wh": s["dist_52wh"],
            "late_penalty": s["late_penalty"],
            "rev_yoy": fv.get("rev_yoy"), "ebitda_yoy": fv.get("ebitda_yoy"),
            "pat_yoy": fv.get("pat_yoy"), "roce": fv.get("roce"),
            "promoter": fv.get("promoter"), "pe": fv.get("pe"), "peg": fv.get("peg"),
            "mcap": fv.get("mcap"),
            "breakdown": breakdown,
        })

    elapsed = round(time.time() - start, 1)
    print(f"[Multibagger] Done in {elapsed}s — {len(results)} results, "
          f"fundamentals for {fund_fetched}/{len(finalists)}")
    return {
        "market": "Multibagger Early Signal",
        "total_scanned": total,
        "total_priced": len(price_data),
        "total_passed": len(cands),
        "excluded": excluded,
        "deep_fetched": len(finalists),
        "fund_fetched": fund_fetched,
        "omitted_signals": MB_OMITTED,
        "params": [{"key": k, "label": l, "max": w, "group": g} for k, l, w, g in MB_PARAMS],
        "results": results,
        "scan_time_seconds": elapsed,
    }
