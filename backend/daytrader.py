"""
Day Trader Scanner — every NSE stock (or the Nifty 500): setups with entry, stop-loss and
targets, and a Time Machine that shows how often the same trade worked on that stock before.

Two scans, run in the background (daytrader_routes.py), results saved per mode:
  daily   setups on daily closes (20-day breakout, bull flag, reclaimed 20-day average,
          oversold, oversold bounce) with levels for the NEXT session: buy above the
          prior-day high, stop at the prior-day low (kept within 0.5–1.5 × ATR14),
          targets at 1R and 2R.
  orb     15-minute opening-range breakouts on 5-minute bars with intraday levels:
          entry at the breakout close, stop at the range midpoint, targets 1R / 2R,
          exit by 3:10 PM.

For each hit (most liquid first, up to a cap) it then adds:
  * Time Machine (daytrader_tm.py): the same rule re-run on the stock's own past
    (5 years of daily bars / 60 days of 5-minute bars) + 5,000 simulated replays,
    after Indian trading costs, with a plain verdict
  * a report card pooling every hit's past trades by setup (and by Nifty trend)
  * events and news: results board meeting coming up, the company's NSE filings this
    week, and recent headlines (Google News)
  * the Nifty 50 trend, since breakouts behave differently in up and down markets

Scan rules come from trader-scanner in github.com/Jakeschincariol/day-trader-skill (MIT); NSE
adaptation in daytrader_core.py. Ideas from the Indian ports github.com/ankurgoels/trader-skills-india
(time machine method) and github.com/sanT-0/nse-day-trader-skills (costs, NSE + Google News).
Prices come from yfinance in batches, like the other screeners. Never places orders.
"""

import concurrent.futures
import datetime
import time

import daytrader_core as core
import daytrader_tm as tm

core.EXCHANGE = "nse"

UNIVERSES = {"all": "all NSE stocks", "nifty500": "Nifty 500"}
CHUNK = 100
GROUP = 500                          # ORB stage 1 is processed this many symbols at a time (memory)
MIN_PRICE = 20.0
MIN_TURNOVER = 5 * core.CRORE        # daily scan: last session's turnover >= Rs 5 crore
LATE_ENTRY = datetime.time(14, 30)   # ORB: breakouts after this are flagged late
EXIT_BY = datetime.time(15, 10)      # ORB: be out before brokers square off
MAX_TM_DAILY = 300                   # Time Machine for this many daily hits (most liquid first)
MAX_TM_ORB = 80
MAX_NEWS = 60                        # headlines for this many top hits
EPOCH = datetime.datetime(1970, 1, 1)
SESSION_OPEN_UTC = datetime.datetime.combine(datetime.date(2000, 1, 1), core.OPEN) - core.IST
UNIVERSE_KEY = "daytrader_universe_all"


def _noop(*a, **k):
    pass


# ── universe ──────────────────────────────────────────────────────────────

def _fetch_equity_list():
    """NSE's list of every listed equity (EQUITY_L.csv) → [(SYMBOL, name)], EQ series only."""
    text = None
    try:
        from curl_cffi import requests as cffi
        for u in core.EQUITY_URLS:
            try:
                r = cffi.get(u, headers={"User-Agent": core.UA_BROWSER}, impersonate="chrome120", timeout=40)
                if r.status_code == 200 and b"SYMBOL" in r.content[:200]:
                    text = r.content.decode("utf-8", "replace")
                    break
            except Exception:
                continue
    except Exception:
        pass
    if text is None:
        text, _ = core.fetch_first(core.EQUITY_URLS)
    rows, _ = core.parse_listing_csv(text, {"EQ"})
    return rows


def _all_nse():
    """[(SYMBOL, name)] for every EQ-series NSE stock, cached for a day in the DB."""
    from database import get_screener_results, save_screener_results
    try:
        saved, _ = get_screener_results(UNIVERSE_KEY)
    except Exception:
        saved = None
    today = core.to_ist(datetime.datetime.utcnow()).date().isoformat()
    if isinstance(saved, dict) and saved.get("date") == today and len(saved.get("rows") or []) > 1000:
        return [tuple(r) for r in saved["rows"]]
    try:
        rows = _fetch_equity_list()
        if len(rows) > 1000:
            save_screener_results(UNIVERSE_KEY, {"date": today, "rows": rows})
            return rows
    except Exception as e:
        print(f"[DayTrader] NSE equity list failed: {e}")
    if isinstance(saved, dict) and saved.get("rows"):
        return [tuple(r) for r in saved["rows"]]               # yesterday's list beats nothing
    return []


def _universe(kind="all"):
    """-> (symbols, {symbol: company name}, label)."""
    rows = []
    try:
        rows = _all_nse()
    except Exception as e:
        print(f"[DayTrader] universe: {e}")
    names = {s: n for s, n in rows}
    if kind == "all" and len(rows) > 1000:
        return [s for s, _ in rows], names, UNIVERSES["all"]
    from screener import get_nifty500_tickers, get_nifty1000_tickers
    if kind == "all":                                           # NSE list unreachable: widest list we have
        src, label = get_nifty1000_tickers(), "Nifty 1000 (NSE's full list was unavailable)"
    else:
        src, label = get_nifty500_tickers(), UNIVERSES["nifty500"]
    syms = []
    for t in src:
        s = t.upper().replace(".NS", "").strip()
        if s and s not in syms:
            syms.append(s)
    return syms, names, label


# ── prices ────────────────────────────────────────────────────────────────

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


def _download(symbols, period, interval, progress=None, label="", suffix=".NS"):
    """{SYMBOL: series} for many NSE symbols, batched like the other screeners."""
    import yfinance as yf
    daily = interval == "1d"
    progress = progress or _noop

    def chunk(syms):
        out = {}
        tick = [s + suffix for s in syms]
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

    data, done = {}, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(chunk, symbols[i:i + CHUNK]): len(symbols[i:i + CHUNK]) for i in range(0, len(symbols), CHUNK)}
        for f in concurrent.futures.as_completed(futs):
            try:
                data.update(f.result() or {})
            except Exception:
                pass
            done += futs[f]
            progress(label, done, len(symbols))
    return data


def _nifty_bars(now, period="5y"):
    """Nifty 50 daily bars (complete sessions) for the trend / regime."""
    try:
        d = _download(["^NSEI"], period, "1d", suffix="")
        s = d.get("^NSEI")
        return core.daily_bars(s, now) if s else []
    except Exception as e:
        print(f"[DayTrader] Nifty download failed: {e}")
        return []


# ── events + news ─────────────────────────────────────────────────────────

def _google_news(query, days=7, limit=3):
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query + " when:%dd" % days, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=8) as r:
        root = ET.fromstring(r.read())
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if not title or len(title) < 15:
            continue
        src = it.find("source")
        try:
            when = parsedate_to_datetime(it.findtext("pubDate") or "")
            when_s = core.to_ist(when.astimezone(datetime.timezone.utc).replace(tzinfo=None)).strftime("%d %b")
        except Exception:
            when_s = ""
        out.append({"title": title, "source": src.text if src is not None else "", "when": when_s,
                    "link": (it.findtext("link") or "").strip()})
        if len(out) >= limit:
            break
    return out


def _events(results, names, today, with_news):
    """Adds r['events'] = {results_on, filings, news} to each result (news for the first `with_news`)."""
    try:
        import nse_feeds
        store = nse_feeds._load()
        rm = nse_feeds.result_meetings(store)
    except Exception as e:
        print(f"[DayTrader] NSE store: {e}")
        store, rm = {}, {"by_symbol": {}, "by_name": {}}
    for r in results:
        sym = r["ticker"]
        ev = {}
        upcoming = sorted(d for d in (rm["by_symbol"].get(sym) or []) if today <= d <= today + datetime.timedelta(days=14))
        if upcoming:
            ev["results_on"] = upcoming[0].isoformat()
        try:
            fil = nse_feeds.symbol_filings(sym, store) if store else []
        except Exception:
            fil = []
        if fil:
            ev["filings"] = fil[:3]
        r["events"] = ev

    def news(r):
        q = names.get(r["ticker"]) or r["ticker"]
        q = q.replace("Limited", "").replace("Ltd", "").strip()
        try:
            return r, _google_news('"%s" share' % q)
        except Exception:
            return r, None
    top = results[:with_news]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for r, items in ex.map(news, top):
            if items:
                r["events"]["news"] = items


def _label(tags):
    return [core.SHORT[t] for t in tags]


def _sort_by_record(results):
    """Best past record first (average R after costs, shrunk for small samples), the rest by liquidity."""
    def key(r):
        p = ((r.get("tm") or {}).get("primary_summary")) or {}
        return (0 if p.get("trades") else 1, -tm.rank_score(p) if p.get("trades") else 0, -(r.get("turnover_cr") or 0))
    results.sort(key=key)
    for i, r in enumerate(results, 1):
        r["rank"] = i


# ── daily scan ────────────────────────────────────────────────────────────

def run_daytrader_daily(now_utc=None, symbols=None, data=None, universe="all", progress=None,
                        names=None, history=None, nifty=None, with_news=MAX_NEWS):
    start = time.time()
    progress = progress or _noop
    now = now_utc or core.parse_now(None)
    label = "custom list"
    if symbols is None:
        progress("Getting the list of stocks", 0, 0)
        symbols, names, label = _universe(universe)
    names = names or {}
    print(f"[DayTrader] Daily scan over {len(symbols)} {label}")
    if data is None:
        data = _download(symbols, "6mo", "1d", progress, "Reading daily charts")
    hits, counts, last_date = core.scan_daily(data, symbols, now, MIN_PRICE, MIN_TURNOVER)
    results = []
    for h in hits:
        lv = core.daily_levels(core.daily_bars(data[h["symbol"]], now))
        if not lv:
            continue
        results.append({
            "ticker": h["symbol"], "name": names.get(h["symbol"]), "setups": h["setups"],
            "setup_labels": _label(h["setups"]), "price": h["price"], "rsi": h["rsi"],
            "turnover_cr": h["turnover_cr"], "levels": lv,
        })
    data = None                                                 # free stage-1 prices

    # ── Time Machine on the most liquid hits ──
    tm_syms = [r["ticker"] for r in results[:MAX_TM_DAILY]]     # results are most-traded first
    if history is None and tm_syms:
        history = _download(tm_syms, "5y", "1d", progress, "Time Machine: reading 5 years of history")
    history = history or {}
    if nifty is None:
        progress("Reading the Nifty 50 trend", 0, 0)
        nifty = _nifty_bars(now)
    regime = tm.regime_map(nifty)
    pooled = []
    for i, r in enumerate(results[:MAX_TM_DAILY]):
        s = history.get(r["ticker"])
        if not s:
            continue
        try:
            bars = core.daily_bars(s, now)
            if len(bars) < 120:
                continue
            hist = tm.daily_history(bars, r["setups"], regime)
            per = {k: v["summary"] for k, v in hist.items()}
            primary = max(r["setups"], key=lambda k: (per[k].get("trades") or 0))
            lv = r["levels"]
            rep = tm.daily_replays(bars, lv["entry"], lv["stop"], lv["target2"])
            r["tm"] = {"setups": per, "primary": primary, "primary_summary": per[primary], "replays": rep,
                       "since": bars[0]["d"].isoformat(), "years": round(len(bars) / 248, 1),
                       "hold_days": tm.HOLD_DAYS}
            for k, v in hist.items():
                for t in v["trades"]:
                    pooled.append({"setup": k, "out": t["out"], "r": t["r"], "t1": t["t1"], "regime": t.get("regime")})
        except Exception as e:
            print(f"[DayTrader] time machine {r['ticker']}: {e}")
        if i % 25 == 0:
            progress("Time Machine: replaying past trades", i, min(len(results), MAX_TM_DAILY))
    history = None

    _sort_by_record(results)
    local = core.to_ist(now)
    if results:
        progress("Checking news and results dates", 0, 0)
        _events(results, names, local.date(), with_news)
    return {
        "mode": "daily", "universe": label, "universe_key": universe, "scanned": counts["scanned"],
        "fetched": counts["fetched"], "requested": counts["requested"],
        "closes_through": last_date.isoformat() if last_date else None,
        "levels_for": "the next session", "scanned_at_ist": local.strftime("%d %b %Y %I:%M %p"),
        "calendar_warning": core.calendar_warning(local.date()),
        "filters": {"min_price": MIN_PRICE, "min_turnover_cr": MIN_TURNOVER / core.CRORE},
        "regime": tm.regime_now(nifty), "report_card": tm.report_card(pooled, "setup"),
        "tm_info": {"hold_days": tm.HOLD_DAYS, "cost_pct": tm.DELIVERY_COST * 100, "replays": tm.REPLAYS,
                    "seed": tm.SEED, "covered": sum(1 for r in results if r.get("tm")), "cap": MAX_TM_DAILY},
        "results": results, "elapsed_s": round(time.time() - start, 1),
    }


# ── opening-range breakouts ───────────────────────────────────────────────

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


def run_daytrader_orb(now_utc=None, symbols=None, data=None, universe="all", progress=None,
                      names=None, history=None, nifty=None, with_news=40):
    start = time.time()
    progress = progress or _noop
    now = now_utc or core.parse_now(None)
    label = "custom list"
    if symbols is None:
        progress("Getting the list of stocks", 0, 0)
        symbols, names, label = _universe(universe)
    names = names or {}
    print(f"[DayTrader] ORB scan over {len(symbols)} {label}")

    # Stage 1 in groups so 2,000+ five-minute charts are never all in memory at once.
    groups = [symbols] if data is not None else [symbols[i:i + GROUP] for i in range(0, len(symbols), GROUP)]
    day = cutoff = None
    hits, kept = [], {}
    tot = {"requested": len(symbols), "fetched": 0, "scanned": 0, "unusable": 0}
    for gi, grp in enumerate(groups):
        gdata = data if data is not None else _download(
            grp, "5d", "5m", lambda _l, d, _t, gi=gi: progress("Reading 5-minute charts", gi * GROUP + d, len(symbols)),
            "Reading 5-minute charts")
        if day is None:
            day, cutoff = _pick_session(gdata, now)
            if day is None:
                continue
        h, counts, _ = core.scan_orb(gdata, grp, day, cutoff, 15, MIN_PRICE, 0.0, exit_by=EXIT_BY)
        for k in tot:
            if k != "requested":
                tot[k] += counts.get(k, 0)
        hits.extend(h)
        gdata = None
    if day is None:
        raise RuntimeError("No completed opening range to scan yet: the 9:15-9:30 AM range needs one more "
                           "5-minute candle, so run after 9:35 AM IST. If it is later than that, the price "
                           "feed sent no 5-minute data; try again in a few minutes.")
    results = []
    for h in hits:
        lv = h["levels"]
        trig = datetime.datetime.strptime(h["at"], "%I:%M %p").time()
        late = trig > LATE_ENTRY
        if late and not lv["status"].startswith("STOPPED"):
            lv["status"] = "late breakout (after 2:30 PM): " + lv["status"]
        results.append({
            "ticker": h["symbol"], "name": names.get(h["symbol"]), "setups": ["orb"], "setup_labels": ["ORB"],
            "price": lv["now"], "breakout_at": h["at"], "bar_start": h.get("bar_start"),
            "or_high": h["or_high"], "or_low": h["or_low"], "pct_over": h["pct_over"],
            "turnover_cr": h["turnover_cr"], "late": late, "levels": lv,
        })

    # ── Time Machine: 60 days of 5-minute bars for the most traded breakouts ──
    by_liq = sorted(results, key=lambda r: -(r["turnover_cr"] or 0))[:MAX_TM_ORB]
    if history is None and by_liq:
        history = _download([r["ticker"] for r in by_liq], "60d", "5m", progress,
                            "Time Machine: reading 60 days of 5-minute bars")
    history = history or {}
    pooled = []
    for r in by_liq:
        s = history.get(r["ticker"])
        if not s:
            continue
        try:
            sessions = tm.full_sessions(s, day)
            if len(sessions) < 10:
                continue
            hist = tm.orb_history(sessions, EXIT_BY)
            lv = r["levels"]
            hh, mm = map(int, (r.get("bar_start") or "09:30").split(":"))
            rep = tm.orb_replays(sessions, lv["entry"], lv["stop"], lv["target2"],
                                 tm._slot_of(datetime.time(hh, mm)), EXIT_BY)
            r["tm"] = {"setups": {"orb": hist["summary"]}, "primary": "orb", "primary_summary": hist["summary"],
                       "replays": rep, "sessions": len(sessions)}
            for t in hist["trades"]:
                pooled.append({"bucket": tm.orb_time_bucket(t["at"]), "out": t["out"], "r": t["r"], "t1": t["t1"]})
        except Exception as e:
            print(f"[DayTrader] ORB time machine {r['ticker']}: {e}")
    history = None
    _sort_by_record(results)
    if nifty is None:
        nifty = _nifty_bars(now, "6mo")
    if results:
        progress("Checking news and results dates", 0, 0)
        _events(results, names, day, with_news)
    is_live = day == core.to_ist(now).date() and cutoff < datetime.datetime.combine(day, core.CLOSE)
    return {
        "mode": "orb", "universe": label, "universe_key": universe, "scanned": tot["scanned"],
        "fetched": tot["fetched"], "requested": tot["requested"], "unusable": tot["unusable"],
        "session": core.day_label(day), "cutoff_ist": core.clock_label(cutoff), "live": is_live,
        "scanned_at_ist": core.to_ist(now).strftime("%d %b %Y %I:%M %p"),
        "calendar_warning": core.calendar_warning(day),
        "regime": tm.regime_now(nifty), "report_card": tm.report_card(pooled, "bucket"),
        "tm_info": {"cost_pct": tm.INTRADAY_COST * 100, "replays": tm.REPLAYS, "seed": tm.SEED,
                    "covered": sum(1 for r in results if r.get("tm")), "cap": MAX_TM_ORB},
        "results": results, "elapsed_s": round(time.time() - start, 1),
    }
