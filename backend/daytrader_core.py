#!/usr/bin/env python3
"""Day Trader Scanner core (NSE): setups with entry, stop-loss and target levels. Standard library only.
Used by daytrader.py for the Market Wisdom page; also runs on its own as a CLI.
Adapted for India from trader-scanner (github.com/Jakeschincariol/day-trader-skill, MIT).
It never places an order.

    python3 scan_india.py universe                         # all NSE EQ-series stocks -> trader/universe.txt
    python3 scan_india.py universe --index nifty500        # or a Nifty index (nifty50, nifty100, nifty200 ...)
    python3 scan_india.py daily                            # after 3:30 PM / before 9:15 AM IST: setups + levels
    python3 scan_india.py daily --symbols RELIANCE,TCS,M&M --risk-rs 500
    python3 scan_india.py orb                              # after 9:35 AM IST: opening-range breakouts + levels
    python3 scan_india.py orb --at 10:00 --stop or-low     # cut the clock at 10:00 IST

universe  NSE's list of securities available for trading (EQUITY_L.csv), EQ
          series only by default (--series EQ,BE to add trade-for-trade), or
          a Nifty index constituent list with --index NAME.

daily     Stage 1: daily closes (Yahoo spark, 20 symbols a request, 3 months).
          Liquidity: last price >= --min-price (Rs 20) and last session's
          turnover (price x volume) >= --min-turnover-cr (Rs 5 crore).
          Setups on closes:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10 % over the 10 sessions before the
                             last 5, then the last 5 closes inside a 5 % range
            reclaim_ma20     closed under the 20-day average, then closed above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 was under 30 yesterday and today closed up
          Stage 2 (levels): the daily chart with highs and lows for the
          --max-levels (100) most traded hits. For the NEXT session:
            entry    one tick above the last session's high (a buy-stop:
                     only triggers if price trades above it)
            stop     the last session's low minus one tick, but never more
                     than 1.5 x ATR(14) under the entry and never closer
                     than 0.5 x ATR(14)
            T1, T2   entry + 1R and entry + 2R (R = entry - stop)
          These are swing levels (daily bars). Skip a stock that opens far
          above its entry: the risk is no longer the risk planned.

orb       Opening-range breakouts on 5-minute bars, 9:15 AM to 3:30 PM IST.
          The range is the first --or-minutes (15: the 9:15, 9:20 and 9:25
          bars). A hit = the first completed bar to CLOSE above the range high.
            entry    the trigger bar's close
            stop     --stop or-mid (default: the range midpoint) or or-low
            T1, T2   entry + 1R and entry + 2R
            exit by  --exit-by (15:10), before brokers square off intraday positions
          "now" is the last completed bar: below the stop, past T1 or past T2
          is said, so a stale hit is not chased.

Prices are rounded to NSE's tick size for the price (Apr 2025 table: under
250 -> 0.01, 250-1,000 -> 0.05, 1,000-5,000 -> 0.10, 5,000-10,000 -> 0.50,
10,000-20,000 -> 1, above 20,000 -> 5). A stop rounds DOWN, an entry UP,
targets to the nearest tick. NSE reviews tick sizes monthly on last month's
close, so a stock near a band edge can differ by one tick.

--risk-rs N (and optional --capital-rs C) adds a quantity: floor(N / risk per
share), capped at floor(C / entry). Nothing else is sized.

Times are IST (UTC+5:30, no daylight saving). NSE equity holidays for 2026
are built in; for a year not in the list only weekends are known, and the
scanner says so. Muhurat and other special weekend sessions are not scanned.

Data: Yahoo Finance's free, unofficial chart endpoints with the .NS suffix
(--exchange bse uses .BO). They can lag and can stop working. Nothing here is
real time. --offline PATH reads saved files instead (see --help).

A scan is a list of charts that match a rule, not a list of stocks to buy.
Exit code 0 when the scan found something, 1 when it did not or could not run.
"""

import argparse
import csv
import datetime
import gzip
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

UA_YAHOO = "Mozilla/5.0 (compatible; trader-scanner-india/1.0)"
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
EQUITY_URLS = ("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
               "https://archives.nseindia.com/content/equities/EQUITY_L.csv")
INDEX_URLS = ("https://niftyindices.com/IndexConstituent/ind_%slist.csv",
              "https://www.niftyindices.com/IndexConstituent/ind_%slist.csv",
              "https://nsearchives.nseindia.com/content/indices/ind_%slist.csv",
              "https://archives.nseindia.com/content/indices/ind_%slist.csv")
SUFFIX = {"nse": ".NS", "bse": ".BO"}
SPARK_MAX = 20      # Yahoo's spark answers 400 above 20 symbols
PAUSE = 0.3         # seconds between requests: be polite to free endpoints
BACKOFF = 5.0       # seconds before the one retry after a 429
SLEEP = time.sleep  # tests swap this out
BAR_MIN = 5
MAX_CONFIRM = 200
MAX_LEVELS = 100
MIN_DAILY_BARS = 30
ATR_N = 14
CRORE = 10_000_000
DEFAULT_UNIVERSE = "trader/universe.txt"
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."
STOP_NOTE = "A stop-loss is a plan, not a guarantee: a gap or a fast market can fill past it."

IST = datetime.timedelta(hours=5, minutes=30)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)

# NSE equity segment trading holidays on weekdays (NSE circular; Upstox / TrueData lists, read 2026-11-19).
NSE_HOLIDAYS = {
    "2026-01-15": "Maharashtra municipal corporation elections",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
CALENDAR_YEARS = (2026,)

SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
]
LABEL = dict(SETUPS)
SHORT = {"breakout_20d": "20D BREAKOUT", "bull_flag": "BULL FLAG", "reclaim_ma20": "RECLAIM MA20",
         "oversold": "OVERSOLD", "oversold_bounce": "OVERSOLD BOUNCE", "orb": "ORB"}


class ScanError(Exception):
    pass


# ---------------------------------------------------------------- time (IST)

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    return local - IST


def ts_to_ist(ts):
    """Unix seconds -> naive IST. Never uses the machine's time zone."""
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)) + IST


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ScanError('--now wants a time like "2026-11-18 10:00" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, flag="--at"):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", text or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ScanError("%s wants an IST time like 10:00" % flag)
    return datetime.time(int(m.group(1)), int(m.group(2)))


def trading_day(d):
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def calendar_warning(d):
    if d.year not in CALENDAR_YEARS:
        return ("Note: the NSE holiday list for %d is not built in yet; only weekends are known. "
                "Add the year's holidays to NSE_HOLIDAYS in scan_india.py." % d.year)
    return None


def session(d):
    ok, _ = trading_day(d)
    return (OPEN, CLOSE) if ok else None


def clock_label(t):
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    return "%s %s" % (d.strftime("%a"), d.isoformat())


# ---------------------------------------------------------------- money and ticks

def tick_for(price):
    """NSE cash-segment tick size by price (from 15 Apr 2025)."""
    p = Decimal(str(price))
    if p < 250:
        return Decimal("0.01")
    if p <= 1000:
        return Decimal("0.05")
    if p <= 5000:
        return Decimal("0.10")
    if p <= 10000:
        return Decimal("0.50")
    if p <= 20000:
        return Decimal("1")
    return Decimal("5")


def to_tick(x, mode, ref=None):
    """Round x to the tick of `ref` (default x). mode: up, down, near."""
    t = tick_for(ref if ref is not None else x)
    q = Decimal(str(x)) / t
    rnd = {"up": ROUND_CEILING, "down": ROUND_FLOOR, "near": ROUND_HALF_UP}[mode]
    return float(q.quantize(Decimal(1), rounding=rnd) * t)


def inr(x):
    """Rs with Indian digit grouping: 12,34,567.80."""
    neg = x < 0
    s = "%.2f" % abs(x)
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + "₹" + whole + "." + frac


def px(x):
    return inr(x)[1:]  # without the rupee sign, for tables


def crore(x):
    return "%s Cr" % ("{:,.1f}".format(x / CRORE))


# ---------------------------------------------------------------- network

def http_get(url, timeout=20, ua=UA_YAHOO):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*",
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def yahoo_get(path):
    """GET a Yahoo Finance path. query2 first, query1 on a 429 or 5xx, one more
    round after BACKOFF seconds if a 429 was seen."""
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            try:
                return http_get(host + path)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                try:
                    body = e.read().decode("utf-8", "replace") if 400 <= e.code < 500 and e.code != 429 else ""
                except Exception:
                    body = ""
                finally:
                    e.close()
                if e.code == 429:
                    saw_429 = True
                    continue
                if e.code >= 500:
                    continue
                if body.strip().startswith("{"):
                    return body
                raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                continue
        if attempt == 0 and saw_429:
            SLEEP(BACKOFF)
            continue
        break
    raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))


EXCHANGE = "nse"   # set by main()


def to_yahoo(sym):
    s = sym.upper()
    if s.endswith((".NS", ".BO")) or s.startswith("^"):
        return s
    return s + SUFFIX[EXCHANGE]


def from_yahoo(ysym):
    s = (ysym or "").upper()
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            return s[:-len(suf)]
    return s


def q(sym):
    return urllib.parse.quote(to_yahoo(sym), safe=".-^")


def spark_path(symbols, rng, interval):
    return "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (",".join(q(s) for s in symbols), rng, interval)


def chart_path(sym, rng, interval):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (q(sym), rng, interval)


# ---------------------------------------------------------------- parsing

def series_from_result(res):
    qd = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = qd.get(k)
        return list(v) + [None] * (n - len(v)) if isinstance(v, list) else None
    return {"t": list(t), "c": col("close") or [None] * n, "o": col("open"), "h": col("high"),
            "l": col("low"), "v": col("volume"), "meta": res.get("meta") or {}}


def parse_spark(text_or_doc, wanted=None):
    doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
    sp = (doc or {}).get("spark") if isinstance(doc, dict) else None
    if not isinstance(sp, dict):
        raise ScanError("Yahoo's spark answer is not the shape this tool knows")
    err = sp.get("error")
    out = {}
    for item in sp.get("result") or []:
        resp = item.get("response") or []
        if not resp:
            continue
        ysym = item.get("symbol") or (resp[0].get("meta") or {}).get("symbol") or ""
        sym = (wanted or {}).get(ysym.upper(), from_yahoo(ysym))
        out[sym] = series_from_result(resp[0])
    if err and not out:
        raise ScanError("Yahoo spark: %s" % (err.get("description") or err.get("code") or err))
    return out


def parse_chart(text_or_doc, sym=""):
    try:
        doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise ScanError("Yahoo returned something unexpected for %s" % sym)
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise ScanError("no data for %s: %s" % (sym, err.get("description", "unknown ticker")))
    return series_from_result(res[0])


def _read_maybe_gz(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def _stem(name):
    for ext in (".json.gz", ".json"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _absorb(doc, name_hint, data, errors):
    if isinstance(doc, dict) and "spark" in doc:
        data.update(parse_spark(doc))
    elif isinstance(doc, dict) and "chart" in doc:
        sym = name_hint
        if not sym:
            try:
                sym = (doc["chart"].get("result") or [{}])[0]["meta"]["symbol"]
            except (AttributeError, KeyError, TypeError, IndexError):
                raise ScanError("an offline chart without a symbol: name the file <SYMBOL>.json")
        sym = from_yahoo(sym)
        try:
            data[sym] = parse_chart(doc, sym)
        except ScanError as e:
            errors[sym] = str(e)
    elif isinstance(doc, dict):
        for k, v in doc.items():
            _absorb(v, k, data, errors)
    else:
        raise ScanError("offline file %s is not a spark answer, a chart or a bundle" % name_hint)


def load_offline(path):
    data, errors = {}, {}
    if os.path.isdir(path):
        names = sorted(f for f in os.listdir(path) if f.endswith((".json", ".json.gz")))
        if not names:
            raise ScanError("no .json files in %s" % path)
        for f in names:
            try:
                doc = json.loads(_read_maybe_gz(os.path.join(path, f)))
            except ValueError as e:
                raise ScanError("%s is not valid JSON: %s" % (f, e))
            if isinstance(doc, dict) and ("chart" in doc or "spark" in doc):
                _absorb(doc, "" if "spark" in doc else _stem(f), data, errors)
            else:
                _absorb(doc, "", data, errors)
    elif os.path.isfile(path):
        try:
            doc = json.loads(_read_maybe_gz(path))
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        _absorb(doc, "", data, errors)
    else:
        raise ScanError("no offline file or folder at %s" % path)
    return data, errors


# ---------------------------------------------------------------- universe

def parse_listing_csv(text, series_ok):
    """EQUITY_L.csv or a Nifty index list -> ([(SYMBOL, name)], counts)."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise ScanError("the symbol list is empty")
    hdr = [h.strip().upper() for h in rows[0]]
    if "SYMBOL" not in hdr:
        raise ScanError("not an NSE symbol list (no SYMBOL column; header %s)" % ",".join(hdr)[:80])
    i_sym = hdr.index("SYMBOL")
    i_name = next((hdr.index(k) for k in ("NAME OF COMPANY", "COMPANY NAME") if k in hdr), None)
    i_ser = hdr.index("SERIES") if "SERIES" in hdr else None
    kept, counts = {}, {"rows": 0}
    for r in rows[1:]:
        if not r or len(r) <= i_sym or not r[i_sym].strip():
            continue
        counts["rows"] += 1
        ser = r[i_ser].strip().upper() if i_ser is not None and len(r) > i_ser else "EQ"
        if ser not in series_ok:
            counts["series " + ser] = counts.get("series " + ser, 0) + 1
            continue
        sym = r[i_sym].strip().upper()
        kept[sym] = " ".join(r[i_name].split()) if i_name is not None and len(r) > i_name else ""
    return sorted(kept.items()), counts


def fetch_first(urls):
    errs = []
    for u in urls:
        try:
            return http_get(u, timeout=60, ua=UA_BROWSER), u
        except urllib.error.HTTPError as e:
            errs.append("%s: HTTP %d" % (u.split("/")[2], e.code))
        except (urllib.error.URLError, OSError) as e:
            errs.append("%s: %s" % (u.split("/")[2], getattr(e, "reason", e)))
    raise ScanError("could not download the symbol list (%s). Download it in a browser and pass "
                    "--offline FILE.csv" % "; ".join(errs))


# ---------------------------------------------------------------- daily setups

def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def daily_bars(series, now_utc):
    """[{d,o,h,l,c,v}] of COMPLETE daily bars: null closes dropped, today's bar
    dropped until 3:30 PM IST, anything dated after the clock dropped."""
    now = to_ist(now_utc)
    today = now.date()
    s = session(today)
    today_done = s is None or now.time() >= s[1]
    out = {}
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        d = ts_to_ist(ts).date()
        if d > today or (d == today and not today_done):
            continue

        def g(k):
            col = series.get(k)
            return None if not col or col[i] is None else float(col[i])
        out[d] = {"d": d, "c": float(c), "o": g("o"), "h": g("h"), "l": g("l"), "v": g("v")}
    return [out[k] for k in sorted(out)]


def find_setups(c):
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r, "ma20": ma20, "high20": hi20}


def atr(bars, n=ATR_N):
    """Wilder ATR on daily bars with highs and lows (None if not enough)."""
    tr = []
    for k in range(1, len(bars)):
        h, l, pc = bars[k]["h"], bars[k]["l"], bars[k - 1]["c"]
        if h is None or l is None:
            return None
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(tr) < n:
        return None
    a = sum(tr[:n]) / n
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return a


def size(risk_per_share, entry, risk_rs, capital_rs):
    if not risk_rs or risk_per_share <= 0:
        return None
    qty = int(risk_rs // risk_per_share)
    if capital_rs:
        qty = min(qty, int(capital_rs // entry))
    return qty


def daily_levels(bars, risk_rs=None, capital_rs=None):
    """Next-session swing levels from complete daily bars with highs and lows."""
    last = bars[-1]
    if last["h"] is None or last["l"] is None:
        return None
    a = atr(bars)
    if a is None or a <= 0:
        return None
    entry = to_tick(last["h"] + float(tick_for(last["h"])), "up")
    stop, rule = last["l"] - float(tick_for(last["l"])), "prior-day low"
    if stop < entry - 1.5 * a:
        stop, rule = entry - 1.5 * a, "1.5 x ATR"      # never wider than 1.5 ATR
    elif stop > entry - 0.5 * a:
        stop, rule = entry - 0.5 * a, "0.5 x ATR"      # never tighter than 0.5 ATR
    stop = to_tick(stop, "down", entry)
    risk = entry - stop
    if risk <= 0:
        return None
    t1 = to_tick(entry + risk, "near")
    t2 = to_tick(entry + 2 * risk, "near")
    lv = {"entry": entry, "entry_type": "buy above (stop-limit)", "stop": stop, "stop_rule": rule,
          "target1": t1, "target2": t2, "risk": round(risk, 2), "risk_pct": round(risk / entry * 100, 2),
          "atr14": round(a, 2), "prior_high": last["h"], "prior_low": last["l"], "valid_for": "next session"}
    qty = size(risk, entry, risk_rs, capital_rs)
    if qty is not None:
        lv["qty"] = qty
        lv["rs_at_risk"] = round(qty * risk, 2)
    return lv


def scan_daily(data, symbols, now_utc, min_price, min_turnover):
    """Stage 1 on closes. Returns (hits, counts, last_date)."""
    hits, dates = [], {}
    counts = {"requested": len(symbols), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0}
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = daily_bars(s, now_utc)
        meta = s.get("meta") or {}
        price = bars[-1]["c"] if bars else meta.get("regularMarketPrice")
        vol = bars[-1]["v"] if bars and bars[-1]["v"] is not None else meta.get("regularMarketVolume")
        turnover = (price or 0) * (vol or 0)
        if price is None or price < min_price or (min_turnover > 0 and turnover < min_turnover):
            counts["illiquid"] += 1
            continue
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        counts["scanned"] += 1
        d_last = bars[-1]["d"]
        dates[d_last] = dates.get(d_last, 0) + 1
        closes = [b["c"] for b in bars]
        tags, nums = find_setups(closes)
        if tags:
            hits.append({"symbol": sym, "setups": tags, "price": round(closes[-1], 2),
                         "close_date": d_last.isoformat(), "rsi": round(nums["rsi"], 1),
                         "volume": int(vol) if vol is not None else None, "turnover_cr": round(turnover / CRORE, 2)})
    hits.sort(key=lambda h: (-h["turnover_cr"], h["symbol"]))
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- opening-range breakout

def slot(day, k):
    return datetime.datetime.combine(day, OPEN) + datetime.timedelta(minutes=BAR_MIN * k)


def session_bars(series, day, cutoff):
    s = session(day)
    close_t = s[1] if s else CLOSE
    out = {}
    hs, ls = series.get("h"), series.get("l")
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        lt = ts_to_ist(ts)
        if lt.date() != day or lt.second or lt.minute % BAR_MIN:
            continue
        if lt.time() < OPEN or lt.time() >= close_t:
            continue
        if lt + datetime.timedelta(minutes=BAR_MIN) > cutoff:
            continue
        v = series.get("v")
        out[lt] = {"start": lt, "c": float(c),
                   "h": None if not hs or hs[i] is None else float(hs[i]),
                   "l": None if not ls or ls[i] is None else float(ls[i]),
                   "v": (v[i] or 0) if v else 0}
    return [out[k] for k in sorted(out)]


def usable(bars, day, cutoff, n_range):
    want = int((cutoff - slot(day, 0)).total_seconds() // (BAR_MIN * 60))
    want = min(want, int((datetime.datetime.combine(day, CLOSE) - slot(day, 0)).total_seconds() // (BAR_MIN * 60)))
    if want < n_range or len(bars) != want:
        return False
    return all(b["start"] == slot(day, k) for k, b in enumerate(bars))


def orb_candidate(bars, n_range):
    top = max(b["c"] for b in bars[:n_range])
    return any(b["c"] >= top for b in bars[n_range:])


def orb_hit(bars, n_range, stop_rule="or-mid", risk_rs=None, capital_rs=None, exit_by=None):
    rng = bars[:n_range]
    if any(b["h"] is None or b["l"] is None for b in rng):
        return None
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    for k in range(n_range, len(bars)):
        b = bars[k]
        if b["c"] > oh:
            closes_at = b["start"] + datetime.timedelta(minutes=BAR_MIN)
            entry = round(b["c"], 2)
            raw_stop = (oh + ol) / 2 if stop_rule == "or-mid" else ol
            stop = to_tick(raw_stop, "down", entry)
            risk = entry - stop
            if risk <= 0:
                return None
            t1 = to_tick(entry + risk, "near")
            t2 = to_tick(entry + 2 * risk, "near")
            later = bars[k + 1:]
            now_px = bars[-1]["c"]
            status = "at entry"
            if later:
                if min(x["l"] if x["l"] is not None else x["c"] for x in later) <= stop:
                    status = "STOPPED: traded at/below the stop"
                elif max(x["h"] if x["h"] is not None else x["c"] for x in later) >= t2:
                    status = "T2 already reached"
                elif max(x["h"] if x["h"] is not None else x["c"] for x in later) >= t1:
                    status = "T1 already reached"
                else:
                    status = "open, between stop and T1"
            hit = {"price": entry, "at": clock_label(closes_at), "bar_start": b["start"].strftime("%H:%M"),
                   "or_high": round(oh, 2), "or_low": round(ol, 2), "pct_over": round((b["c"] / oh - 1) * 100, 2),
                   "levels": {"entry": entry, "entry_type": "trigger close", "stop": stop,
                              "stop_rule": "range midpoint" if stop_rule == "or-mid" else "range low",
                              "target1": t1, "target2": t2, "risk": round(risk, 2),
                              "risk_pct": round(risk / entry * 100, 2),
                              "exit_by": exit_by.strftime("%H:%M") if exit_by else None,
                              "now": round(now_px, 2), "status": status, "valid_for": "today (intraday)"},
                   "_t": closes_at}
            qty = size(risk, entry, risk_rs, capital_rs)
            if qty is not None:
                hit["levels"]["qty"] = qty
                hit["levels"]["rs_at_risk"] = round(qty * risk, 2)
            return hit
    return None


def orb_window(now_utc, at, or_minutes, until, explicit_day=None):
    now = to_ist(now_utc)
    day = explicit_day or now.date()
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic)")
    ok, why = trading_day(day)
    if not ok:
        raise ScanError("No NSE session on %s (%s). Run orb on a trading day after the opening range, "
                        "or replay a saved day with --offline." % (day_label(day), why))
    range_end = slot(day, or_minutes // BAR_MIN)
    first_close = range_end + datetime.timedelta(minutes=BAR_MIN)
    close_dt = datetime.datetime.combine(day, CLOSE)
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s IST)." % (at.strftime("%H:%M"), clock_label(now)))
        if cutoff > close_dt:
            raise ScanError("--at %s is after the 3:30 PM close." % at.strftime("%H:%M"))
    else:
        cutoff = now.replace(second=0, microsecond=0)
        if cutoff >= close_dt:
            raise ScanError("NSE closed at 3:30 PM IST on %s. Replay the session with --at 10:00."
                            % day_label(day))
    if cutoff < first_close:
        raise ScanError("It is %s IST: the %d-minute opening range runs 9:15 to %s and the first bar after it "
                        "closes at %s. Run orb again after %s."
                        % (clock_label(cutoff), or_minutes, clock_label(range_end), clock_label(first_close),
                           clock_label(first_close)))
    if until is not None and cutoff.time() > until:
        raise ScanError("It is %s IST, after %s, your last time for new intraday entries: a breakout found now "
                        "is expired. Run orb tomorrow after %s, or replay today with --at 10:00."
                        % (clock_label(cutoff), until.strftime("%H:%M"), clock_label(first_close)))
    return day, cutoff


def scan_orb(stage1, symbols, day, cutoff, or_minutes, min_price=0.0, min_turnover=0.0, confirm=None,
             max_confirm=None, stop_rule="or-mid", risk_rs=None, capital_rs=None, exit_by=None):
    n_range = or_minutes // BAR_MIN
    counts = {"requested": len(symbols), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0,
              "candidates": 0, "confirmed": 0, "unconfirmed": 0}
    hits, failed, cands = [], {}, []
    for sym in symbols:
        s = stage1.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = session_bars(s, day, cutoff)
        if not usable(bars, day, cutoff, n_range):
            counts["unusable"] += 1
            continue
        vol = sum(b["v"] for b in bars) if s.get("v") else (s.get("meta") or {}).get("regularMarketVolume") or 0
        turnover = bars[-1]["c"] * vol
        if bars[-1]["c"] < min_price or turnover < min_turnover:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        if orb_candidate(bars, n_range):
            cands.append((sym, bars, turnover))
    counts["candidates"] = len(cands)
    if confirm is not None and max_confirm and len(cands) > max_confirm:
        cands.sort(key=lambda x: -x[2])
        counts["unconfirmed"] = len(cands) - max_confirm
        cands = cands[:max_confirm]
    for sym, bars, turnover in cands:
        full = bars
        if confirm is not None:
            try:
                s2 = confirm(sym)
            except ScanError as e:
                failed[sym] = str(e)
                continue
            full = session_bars(s2, day, cutoff)
            if not usable(full, day, cutoff, n_range):
                failed[sym] = "the chart has a missing bar"
                continue
        counts["confirmed"] += 1
        h = orb_hit(full, n_range, stop_rule, risk_rs, capital_rs, exit_by)
        if h:
            hits.append(dict({"symbol": sym, "setups": ["orb"], "turnover_cr": round(turnover / CRORE, 2)}, **h))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts, failed


# ---------------------------------------------------------------- fetching and files

def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


def fetch_spark(symbols, rng, interval, pause, a=None):
    data, failed = {}, {}
    batches = [symbols[i:i + SPARK_MAX] for i in range(0, len(symbols), SPARK_MAX)]
    for k, batch in enumerate(batches):
        if k and pause:
            SLEEP(pause)
        wanted = {to_yahoo(s): s for s in batch}
        try:
            got = parse_spark(yahoo_get(spark_path(batch, rng, interval)), wanted)
        except (ScanError, ValueError) as e:
            for s in batch:
                failed[s] = str(e)
            continue
        for s in batch:
            if s in got:
                data[s] = got[s]
            else:
                failed[s] = "no data from Yahoo"
        if a is not None and len(batches) > 25 and (k + 1) % 25 == 0:
            progress(a, "  %d of %d requests" % (k + 1, len(batches)))
    return data, failed


def symbols_from_file(path):
    if not os.path.exists(path):
        raise ScanError("no file at %s" % path)
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        return [from_yahoo(str(x.get("symbol") if isinstance(x, dict) else x)) for x in items if x]
    if text.lstrip().upper().startswith("SYMBOL"):
        return [s for s, _ in parse_listing_csv(text, {"EQ", "BE", "BZ", "SM", "ST"})[0]]
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(from_yahoo(ln.split("|")[0].split(",")[0].strip()))
    return out


def read_symbols(a, offline_syms=None):
    if a.symbols:
        syms = [from_yahoo(s) for s in re.split(r"[,\s]+", a.symbols.upper()) if s]
    elif a.watchlist:
        syms = symbols_from_file(a.watchlist)
    elif offline_syms is not None and not a.universe:
        syms = sorted(offline_syms)
    else:
        path = a.universe or DEFAULT_UNIVERSE
        if not os.path.exists(path):
            raise ScanError("no %s yet: run `scan_india.py universe` first, or pass --symbols" % path)
        syms = symbols_from_file(path)
    out = []
    for s in syms:
        if s not in out:
            out.append(s)
    if a.limit:
        out = out[:a.limit]
    if not out:
        raise ScanError("no symbols to scan")
    return out


def read_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError as e:
        raise ScanError("%s is not valid JSON: %s" % (path, e))
    return cfg if isinstance(cfg, dict) else {}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


CSV_COLS = ["symbol", "setup", "last_price", "entry", "entry_type", "stop", "stop_rule", "target1", "target2",
            "risk_per_share", "risk_pct", "qty", "rs_at_risk", "valid_for", "status", "turnover_cr"]


def write_csv(path, hits, mode):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLS)
        for h in hits:
            lv = h.get("levels") or {}
            w.writerow([h["symbol"], " + ".join(SHORT[t] for t in h["setups"]),
                        lv.get("now", h.get("price")), lv.get("entry", ""), lv.get("entry_type", ""),
                        lv.get("stop", ""), lv.get("stop_rule", ""), lv.get("target1", ""), lv.get("target2", ""),
                        lv.get("risk", ""), lv.get("risk_pct", ""), lv.get("qty", ""), lv.get("rs_at_risk", ""),
                        lv.get("valid_for", ""), lv.get("status", "" if lv else "no levels (not fetched)"),
                        h.get("turnover_cr", "")])


def print_levels(h, mode):
    lv = h.get("levels")
    tags = ", ".join(SHORT[t] for t in h["setups"])
    if mode == "orb":
        head = "%-12s %s at %s  (range %s - %s, %+.2f%% over)" % (
            h["symbol"], tags, h["at"], px(h["or_low"]), px(h["or_high"]), h["pct_over"])
    else:
        head = "%-12s %s  close %s  RSI %.0f  turnover %s" % (
            h["symbol"], tags, px(h["price"]), h["rsi"], crore(h["turnover_cr"] * CRORE))
    print(head)
    if not lv:
        print("             levels: not fetched (beyond --max-levels, or no highs/lows)")
        return
    if mode == "orb":
        print("             entry %s   stop %s (%s)   T1 %s   T2 %s   exit by %s" % (
            px(lv["entry"]), px(lv["stop"]), lv["stop_rule"], px(lv["target1"]), px(lv["target2"]),
            clock_label(datetime.datetime.strptime(lv["exit_by"], "%H:%M")) if lv.get("exit_by") else "-"))
        extra = "             risk %s/share (%.2f%%)   now %s: %s" % (
            px(lv["risk"]), lv["risk_pct"], px(lv["now"]), lv["status"])
    else:
        print("             buy above %s   stop %s (%s)   T1 %s   T2 %s" % (
            px(lv["entry"]), px(lv["stop"]), lv["stop_rule"], px(lv["target1"]), px(lv["target2"])))
        extra = "             risk %s/share (%.2f%%)   ATR %s   valid: %s" % (
            px(lv["risk"]), lv["risk_pct"], px(lv["atr14"]), lv["valid_for"])
    if "qty" in lv:
        extra += "   qty %d (%s at risk)" % (lv["qty"], inr(lv["rs_at_risk"]))
    print(extra)


# ---------------------------------------------------------------- commands

def cmd_universe(a):
    series_ok = {s.strip().upper() for s in a.series.split(",") if s.strip()}
    if a.offline:
        if not os.path.isfile(a.offline):
            raise ScanError("no file at %s (give the downloaded EQUITY_L.csv or ind_nifty500list.csv)" % a.offline)
        text, src = _read_maybe_gz(a.offline), os.path.basename(a.offline)
    elif a.index:
        slug = re.sub(r"[^a-z0-9]", "", a.index.lower())
        text, src = fetch_first([u % slug for u in INDEX_URLS])
    else:
        text, src = fetch_first(EQUITY_URLS)
    kept, counts = parse_listing_csv(text, series_ok)
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            for sym, name in kept:
                fh.write("%s|%s\n" % (sym, name))
    dropped = {k: v for k, v in counts.items() if k != "rows"}
    if a.json:
        print(json.dumps({"count": len(kept), "rows": counts["rows"], "source": src, "series": sorted(series_ok),
                          "dropped": dropped, "out": None if a.no_write else a.out}, indent=2))
    else:
        print("%s: %s rows." % (src, "{:,}".format(counts["rows"])))
        print("Kept %s symbols (series %s)." % ("{:,}".format(len(kept)), ", ".join(sorted(series_ok))))
        if dropped:
            print("Dropped: " + ", ".join("%s %s" % ("{:,}".format(v), k)
                                          for k, v in sorted(dropped.items(), key=lambda kv: -kv[1])) + ".")
        if not a.no_write:
            print("Wrote %s (one SYMBOL|Name a line)." % a.out)
    return 0 if kept else 1


def cmd_daily(a):
    t0 = time.time()
    now = parse_now(a.now)
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
    symbols = read_symbols(a, offline_syms)
    if not a.offline:
        progress(a, "Fetching daily closes for %s NSE symbols (%d requests)..."
                 % ("{:,}".format(len(symbols)), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, "3mo", "1d", a.pause, a)
    min_turn = a.min_turnover_cr * CRORE
    hits, counts, last_date = scan_daily(data, symbols, now, a.min_price, min_turn)

    # stage 2: levels from daily highs and lows
    leveled = 0
    for h in hits[:a.max_levels]:
        s = data.get(h["symbol"])
        if not a.offline or not (s and s.get("h")):
            if a.offline:
                continue
            if a.pause:
                SLEEP(a.pause)
            try:
                s = parse_chart(yahoo_get(chart_path(h["symbol"], "3mo", "1d")), h["symbol"])
            except ScanError as e:
                h["levels_error"] = str(e)
                continue
        lv = daily_levels(daily_bars(s, now), a.risk_rs, a.capital_rs)
        if lv:
            h["levels"] = lv
            leveled += 1
    nfailed = len(symbols) - counts["fetched"]
    as_of = ("%s close" % last_date.isoformat()) if last_date else None
    doc = {"created_by": "trader-scanner-india", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "daily",
           "as_of": as_of, "exchange": EXCHANGE.upper(), "universe": len(symbols), "scanned": counts["scanned"],
           "failed": nfailed, "source": "yahoo",
           "filters": {"min_price": a.min_price, "min_turnover_cr": a.min_turnover_cr},
           "note": NOTE + " " + STOP_NOTE, "hits": hits}
    if not a.no_write:
        write_json(a.out, doc)
        write_csv(a.csv, hits, "daily")
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2, ensure_ascii=False))
        return 0 if hits else 1
    local = to_ist(now)
    warn = calendar_warning(local.date())
    if warn:
        print(warn)
    print("Daily scan, %s IST, %s closes through %s (Yahoo, free, can lag)." % (
        local.strftime("%a %Y-%m-%d %H:%M"), EXCHANGE.upper(), last_date.isoformat() if last_date else "nothing"))
    print("Fetched %s of %s charts%s. %s passed the liquidity filter (%s+ and %s+ turnover)%s; %s scanned." % (
        "{:,}".format(counts["fetched"]), "{:,}".format(len(symbols)),
        (" (%s failed or had no data)" % "{:,}".format(nfailed)) if nfailed else "",
        "{:,}".format(counts["fetched"] - counts["illiquid"]), inr(a.min_price), crore(min_turn),
        (", %d had under %d days" % (counts["short"], MIN_DAILY_BARS)) if counts["short"] else "",
        "{:,}".format(counts["scanned"])))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched a setup: %s." % (len(hits), "" if len(hits) == 1 else "s",
                                                  ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
        print("Levels for the next session on %d (most traded first). Entry = buy only above it.\n" % leveled)
    else:
        print("No setups.")
    for h in hits[:a.show]:
        print_levels(h, "daily")
    if len(hits) > a.show:
        print("  ... and %d more in %s (--show N for more)." % (len(hits) - a.show, a.csv))
    if not a.no_write:
        print("\nWrote %s and %s." % (a.out, a.csv))
    print(NOTE + " " + STOP_NOTE)
    return 0 if hits else 1


def cmd_orb(a):
    t0 = time.time()
    now = parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    cfg = read_config(a.config)
    until = parse_hhmm(a.until or cfg.get("no_entries_after") or "14:30", "--until")
    exit_by = parse_hhmm(a.exit_by or cfg.get("exit_by") or "15:10", "--exit-by")
    risk_rs = a.risk_rs if a.risk_rs is not None else cfg.get("risk_per_trade_rs")
    capital_rs = a.capital_rs if a.capital_rs is not None else cfg.get("capital_rs")
    data = failed = None
    explicit_day = None
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
        if not a.now:
            days = [ts_to_ist(s["t"][-1]).date() for s in data.values() if s["t"]]
            if days:
                explicit_day = max(days)
                if at is None:
                    raise ScanError("offline replay: give --at HH:MM or --now \"YYYY-MM-DD HH:MM\"")
    day, cutoff = orb_window(now, at, a.or_minutes, until, explicit_day)
    symbols = read_symbols(a, offline_syms)
    confirm = None
    if not a.offline:
        today = to_ist(parse_now(None)).date()
        if (today - day).days > 6:
            raise ScanError("Yahoo's free 5-minute history is short: replay %s with --offline" % day.isoformat())
        rng = "1d" if day == today else "5d"
        progress(a, "Stage 1: 5-minute closes for %s NSE symbols (%d requests)..."
                 % ("{:,}".format(len(symbols)), (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, rng, "%dm" % BAR_MIN, a.pause, a)

        def confirm(sym):
            if a.pause:
                SLEEP(a.pause)
            return parse_chart(yahoo_get(chart_path(sym, rng, "%dm" % BAR_MIN)), sym)
    hits, counts, failed2 = scan_orb(data, symbols, day, cutoff, a.or_minutes, a.min_price,
                                     a.min_turnover_cr * CRORE, confirm, a.max_confirm, a.stop, risk_rs,
                                     capital_rs, exit_by)
    failed = dict(failed or {})
    failed.update(failed2)
    nfailed = len([s for s in symbols if s in failed])
    as_of = "%s %s IST" % (day.isoformat(), cutoff.strftime("%H:%M"))
    doc = {"created_by": "trader-scanner-india", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "orb",
           "as_of": as_of, "exchange": EXCHANGE.upper(), "universe": len(symbols), "scanned": counts["scanned"],
           "failed": nfailed, "unconfirmed": counts["unconfirmed"], "source": "yahoo", "or_minutes": a.or_minutes,
           "interval": "%dm" % BAR_MIN, "stop_rule": a.stop,
           "filters": {"min_price": a.min_price, "min_turnover_cr": a.min_turnover_cr},
           "note": NOTE + " " + STOP_NOTE, "hits": hits}
    if not a.no_write:
        write_json(a.out, doc)
        write_csv(a.csv, hits, "orb")
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2, ensure_ascii=False))
        return 0 if hits else 1
    warn = calendar_warning(day)
    if warn:
        print(warn)
    print("Opening-range breakout scan, %s %s IST: %d-minute range (9:15 to %s), 5-minute bars, Yahoo (can lag)."
          % (day_label(day), clock_label(cutoff), a.or_minutes, clock_label(slot(day, a.or_minutes // BAR_MIN))))
    print("Fetched %s of %s charts%s; %s usable%s; %s candidates, %s confirmed with real highs and lows."
          % ("{:,}".format(counts["fetched"]), "{:,}".format(len(symbols)),
             (" (%s failed or had no data)" % "{:,}".format(nfailed)) if nfailed else "",
             "{:,}".format(counts["scanned"]),
             (" (%s under the price / turnover floor)" % "{:,}".format(counts["illiquid"])) if counts["illiquid"] else "",
             "{:,}".format(counts["candidates"]), "{:,}".format(counts["confirmed"])))
    if counts["unconfirmed"]:
        print("PARTIAL: %s more candidates were not checked (only the %s most traded are; --max-confirm 0 "
              "checks all)." % ("{:,}".format(counts["unconfirmed"]), "{:,}".format(a.max_confirm)))
    if hits:
        print("%d chart%s closed above the opening-range high by %s (earliest first):\n"
              % (len(hits), "" if len(hits) == 1 else "s", clock_label(cutoff)))
        for h in hits[:a.show]:
            print_levels(h, "orb")
        if len(hits) > a.show:
            print("  ... and %d more in %s (--show N for more)." % (len(hits) - a.show, a.csv))
    else:
        print("No chart closed above its opening-range high by %s." % clock_label(cutoff))
    if not a.no_write:
        print("\nWrote %s and %s." % (a.out, a.csv))
    print(NOTE + " " + STOP_NOTE)
    return 0 if hits else 1


def main(argv=None):
    global EXCHANGE
    ap = argparse.ArgumentParser(description="NSE Chart Scanner: setups with entry, stop-loss and targets.")
    ap.add_argument("--exchange", choices=("nse", "bse"), default="nse", help="Yahoo suffix .NS or .BO (nse)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("universe", help="NSE symbol list -> trader/universe.txt")
    p.add_argument("--index", help="a Nifty index instead of all of NSE: nifty50, nifty100, nifty200, nifty500, "
                                   "niftymidcap150, niftysmallcap250 ...")
    p.add_argument("--series", default="EQ", help="series to keep, comma-separated (EQ)")
    p.add_argument("--out", default=DEFAULT_UNIVERSE)
    p.add_argument("--offline", metavar="FILE", help="a downloaded EQUITY_L.csv or Nifty index csv")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")

    def common(p, min_price, min_turnover_cr):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated NSE symbols instead of the universe")
        g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json, csv or text file of symbols")
        p.add_argument("--universe", help="symbol list, one SYMBOL|Name a line (default %s)" % DEFAULT_UNIVERSE)
        p.add_argument("--limit", type=int, help="scan only the first N symbols")
        p.add_argument("--min-price", type=float, default=min_price, help="rupees (%g)" % min_price)
        p.add_argument("--min-turnover-cr", type=float, default=min_turnover_cr,
                       help="minimum turnover in Rs crore (%g)" % min_turnover_cr)
        p.add_argument("--risk-rs", type=float, help="rupees you would risk per trade: adds a quantity")
        p.add_argument("--capital-rs", type=float, help="caps the quantity at capital / entry")
        p.add_argument("--offline", metavar="PATH", help="saved Yahoo chart files instead of the network")
        p.add_argument("--now", help='pretend it is this time ("2026-11-18 10:00" IST, or ...Z)')
        p.add_argument("--out", default="trader/watchlist.json")
        p.add_argument("--csv", default="trader/levels.csv", help="levels table (trader/levels.csv)")
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--pause", type=float, default=PAUSE, help="seconds between requests (%.1f)" % PAUSE)
        p.add_argument("--show", type=int, default=30, help="hits to print (all are written)")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("daily", help="setups from daily bars + next-session levels")
    common(p, 20.0, 5.0)
    p.add_argument("--max-levels", type=int, default=MAX_LEVELS, help="hits to fetch levels for (%d)" % MAX_LEVELS)
    p = sub.add_parser("orb", help="opening-range breakouts so far today + intraday levels")
    common(p, 20.0, 0.0)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--stop", choices=("or-mid", "or-low"), default="or-mid")
    p.add_argument("--max-confirm", type=int, default=MAX_CONFIRM,
                   help="charts to confirm in stage 2, most traded first (%d; 0 = all)" % MAX_CONFIRM)
    p.add_argument("--at", help="cut the clock at this IST time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM IST (14:30)")
    p.add_argument("--exit-by", help="be out of intraday trades by HH:MM IST (15:10)")
    p.add_argument("--config", default="trader/config.json")
    a = ap.parse_args(argv)
    EXCHANGE = a.exchange
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb}[a.cmd](a)
    except ScanError as e:
        print("scan_india.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("scan_india.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
