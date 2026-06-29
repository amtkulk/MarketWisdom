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
