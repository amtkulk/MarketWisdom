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

def run_screener(market="india"):
    """
    Main entry point. Scans all stocks for the given market,
    filters and ranks by composite score.
    Returns dict with results and metadata.
    """
    import time

    start = time.time()

    if market == "us":
        tickers = get_sp500_tickers()
        market_label = "S&P 500"
    else:
        tickers = get_nifty500_tickers()
        market_label = "Nifty 500"

    total = len(tickers)
    print(f"[Screener] Starting scan of {total} {market_label} stocks...")

    results = []
    scanned = 0
    errors = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_map = {executor.submit(scan_stock, t): t for t in tickers}
        for future in concurrent.futures.as_completed(future_map):
            scanned += 1
            try:
                result = future.result()
                if result is not None:
                    results.append(result)
            except Exception:
                errors += 1

            if scanned % 50 == 0:
                print(f"[Screener] Progress: {scanned}/{total} scanned, {len(results)} passed filters")

    # Sort by composite score descending
    results.sort(key=lambda x: x["score"], reverse=True)

    # Return top 25
    top_results = results[:25]

    elapsed = round(time.time() - start, 1)
    print(f"[Screener] Done in {elapsed}s — {len(results)} stocks passed filters, returning top {len(top_results)}")

    return {
        "market": market_label,
        "total_scanned": total,
        "total_passed": len(results),
        "results": top_results,
        "scan_time_seconds": elapsed,
    }
