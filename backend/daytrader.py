"""
Day Trader Scanner — Nifty 500 setups with entry, stop-loss and targets.

Two scans, both run in the background through the shared /api/screener/* endpoints:
  india_daytrader      daily setups (20-day breakout, bull flag, reclaimed 20-day
                       average, oversold, oversold bounce) with levels for the NEXT
                       session: buy above the prior-day high, stop at the prior-day
                       low (kept within 0.5-1.5 x ATR14), targets at 1R and 2R.
  india_daytrader_orb  15-minute opening-range breakouts on 5-minute bars with
                       intraday levels: entry at the breakout close, stop at the
                       range midpoint, targets at 1R and 2R, exit by 3:10 PM.

Scan rules come from trader-scanner in github.com/Jakeschincariol/day-trader-skill
(MIT); the NSE adaptation and the daily-setup levels are in daytrader_core.py.
Prices come from yfinance in batches, like the other screeners. Never places orders.
"""

import concurrent.futures
import datetime
import time

import daytrader_core as core

core.EXCHANGE = "nse"

UNIVERSE_LABEL = "Nifty 500"
CHUNK = 100
MIN_PRICE = 20.0
MIN_TURNOVER = 5 * core.CRORE       # daily scan: last session's turnover >= Rs 5 crore
LATE_ENTRY = datetime.time(14, 30)   # ORB: breakouts after this are flagged late
EXIT_BY = datetime.time(15, 10)      # ORB: be out before brokers square off
EPOCH = datetime.datetime(1970, 1, 1)
SESSION_OPEN_UTC = datetime.datetime.combine(datetime.date(2000, 1, 1), core.OPEN) - core.IST


def _universe():
    from screener import get_nifty500_tickers
    syms = []
    for t in get_nifty500_tickers():
        s = t.upper().replace(".NS", "").strip()
        if s and s not in syms:
            syms.append(s)
    return syms


def _ts(idx, daily):
    """pandas Timestamp -> unix seconds. Daily bars are stamped at the 9:15 IST open."""
    if daily:
        d = idx.date() if hasattr(idx, "date") else idx
        return int((datetime.datetime.combine(d, SESSION_OPEN_UTC.time()) - EPOCH).total_seconds())
    if getattr(idx, "tzinfo", None) is None:          # naive intraday index: yfinance gives exchange time
        return int((idx.to_pydatetime() - core.IST - EPOCH).total_seconds())
    return int(idx.timestamp())


def _series(sub, daily):
    """One ticker's yfinance frame -> the core's series dict."""
    t, o, h, l, c, v = [], [], [], [], [], []
    for idx, row in sub.iterrows():
        close = row.get("Close")
        if close is None or close != close:        # NaN
            continue

        def f(k):
            x = row.get(k)
            return None if x is None or x != x else float(x)
        t.append(_ts(idx, daily))
        o.append(f("Open")); h.append(f("High")); l.append(f("Low")); c.append(float(close))
        v.append(f("Volume") or 0.0)
    if not t:
        return None
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v,
            "meta": {"regularMarketPrice": c[-1], "regularMarketVolume": v[-1]}}


def _download(symbols, period, interval):
    """{SYMBOL: series} for many NSE symbols, batched like the other screeners."""
    import yfinance as yf
    daily = interval == "1d"

    def chunk(syms):
        out = {}
        tick = [s + ".NS" for s in syms]
        try:
            df = yf.download(tick, period=period, interval=interval, group_by="ticker",
                             threads=True, progress=False, auto_adjust=False)
        except Exception as e:
            print(f"[DayTrader] download failed for a chunk: {e}")
            return out
        if df is None or df.empty:
            return out
        for s, t in zip(syms, tick):
            try:
                cols = df.columns
                if getattr(cols, "nlevels", 1) > 1:
                    sub = df[t] if t in cols.get_level_values(0) else df.xs(t, axis=1, level=1)
                else:
                    sub = df
                ser = _series(sub, daily)
                if ser:
                    out[s] = ser
            except Exception:
                continue
        return out

    data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(chunk, symbols[i:i + CHUNK]) for i in range(0, len(symbols), CHUNK)]
        for f in concurrent.futures.as_completed(futs):
            try:
                data.update(f.result() or {})
            except Exception:
                pass
    return data


def _label(tags):
    return [core.SHORT[t] for t in tags]


def run_daytrader_daily(now_utc=None, symbols=None, data=None):
    start = time.time()
    now = now_utc or core.parse_now(None)
    symbols = symbols or _universe()
    print(f"[DayTrader] Daily scan over {len(symbols)} {UNIVERSE_LABEL} stocks")
    if data is None:
        data = _download(symbols, "6mo", "1d")
    hits, counts, last_date = core.scan_daily(data, symbols, now, MIN_PRICE, MIN_TURNOVER)
    results = []
    for h in hits:
        lv = core.daily_levels(core.daily_bars(data[h["symbol"]], now))
        if not lv:
            continue
        results.append({
            "ticker": h["symbol"], "setups": h["setups"], "setup_labels": _label(h["setups"]),
            "price": h["price"], "rsi": h["rsi"], "turnover_cr": h["turnover_cr"], "levels": lv,
        })
    for i, r in enumerate(results, 1):
        r["rank"] = i
    local = core.to_ist(now)
    return {
        "mode": "daily", "universe": UNIVERSE_LABEL, "scanned": counts["scanned"],
        "fetched": counts["fetched"], "requested": counts["requested"],
        "closes_through": last_date.isoformat() if last_date else None,
        "levels_for": "the next session", "scanned_at_ist": local.strftime("%d %b %Y %I:%M %p"),
        "calendar_warning": core.calendar_warning(local.date()),
        "filters": {"min_price": MIN_PRICE, "min_turnover_cr": MIN_TURNOVER / core.CRORE},
        "results": results, "elapsed_s": round(time.time() - start, 1),
    }


def _pick_session(data, now_utc):
    """The session to read: today once the first bar after the range has closed,
    otherwise the latest earlier session in the data. -> (day, cutoff naive IST)."""
    now = core.to_ist(now_utc)
    days = set()
    for s in data.values():
        for ts in s["t"][-200:]:
            days.add(core.ts_to_ist(ts).date())
    first_close = lambda d: core.slot(d, 3) + datetime.timedelta(minutes=core.BAR_MIN)
    for d in sorted(days, reverse=True):
        if not core.trading_day(d)[0]:
            continue
        if d == now.date():
            if now < first_close(d):
                continue
            close_dt = datetime.datetime.combine(d, core.CLOSE)
            return d, min(now.replace(second=0, microsecond=0), close_dt)
        if d < now.date():
            return d, datetime.datetime.combine(d, core.CLOSE)
    return None, None


def run_daytrader_orb(now_utc=None, symbols=None, data=None):
    start = time.time()
    now = now_utc or core.parse_now(None)
    symbols = symbols or _universe()
    print(f"[DayTrader] ORB scan over {len(symbols)} {UNIVERSE_LABEL} stocks")
    if data is None:
        data = _download(symbols, "5d", "5m")
    day, cutoff = _pick_session(data, now)
    if day is None:
        raise RuntimeError("No completed opening range to scan yet: the 9:15-9:30 AM range needs one more "
                           "5-minute candle, so run after 9:35 AM IST. If it is later than that, the price "
                           "feed sent no 5-minute data; try again in a few minutes.")
    hits, counts, _ = core.scan_orb(data, symbols, day, cutoff, 15, MIN_PRICE, 0.0, exit_by=EXIT_BY)
    results = []
    for h in hits:
        lv = h["levels"]
        trig = datetime.datetime.strptime(h["at"], "%I:%M %p").time()
        late = trig > LATE_ENTRY
        if late and not lv["status"].startswith("STOPPED"):
            lv["status"] = "late breakout (after 2:30 PM): " + lv["status"]
        results.append({
            "ticker": h["symbol"], "setups": ["orb"], "setup_labels": ["ORB"], "price": lv["now"],
            "breakout_at": h["at"], "or_high": h["or_high"], "or_low": h["or_low"], "pct_over": h["pct_over"],
            "turnover_cr": h["turnover_cr"], "late": late, "levels": lv,
        })
    for i, r in enumerate(results, 1):
        r["rank"] = i
    is_live = day == core.to_ist(now).date() and cutoff < datetime.datetime.combine(day, core.CLOSE)
    return {
        "mode": "orb", "universe": UNIVERSE_LABEL, "scanned": counts["scanned"],
        "fetched": counts["fetched"], "requested": counts["requested"], "unusable": counts["unusable"],
        "session": core.day_label(day), "cutoff_ist": core.clock_label(cutoff), "live": is_live,
        "scanned_at_ist": core.to_ist(now).strftime("%d %b %Y %I:%M %p"),
        "calendar_warning": core.calendar_warning(day),
        "results": results, "elapsed_s": round(time.time() - start, 1),
    }
