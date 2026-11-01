"""NSE corporate-filing RSS feeds (official, free, served from NSE's archives server,
which — unlike www.nseindia.com and BSE's API — doesn't block cloud servers).

  Online_announcements.xml → every company filing (latest few hundred). We keep the
                             ones that matter for the Corporate Actions tabs:
                             orders / analyst-investor meets / press releases.
  Board_Meetings.xml       → board-meeting intimations (today's filings only) → result
                             dates for the Nifty50 Result Analyzer.

Each feed only holds recent filings, so a background poller reads both every
~5 minutes in market hours (hourly otherwise) and keeps 7 days of history in the DB.
"""
import datetime as dt
import re
import threading
import time
import xml.etree.ElementTree as ET

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
STORE_KEY = "nse_filings"
KEEP_DAYS = 7
POLL_MARKET = 5 * 60          # feed holds only the latest few hundred filings → read often in market hours
POLL_QUIET = 60 * 60
HOSTS = ("https://nsearchives.nseindia.com", "https://archives.nseindia.com")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# NSE "SUBJECT" text → our Corporate Actions tab
FEED_RULES = {
    "orders": re.compile(r"bagging|receiving of orders|receipt of order|award of order|orders?/contracts|"
                         r"letter of award|letter of intent|\bLOA\b|\bLOI\b|work order|purchase order", re.I),
    "meets": re.compile(r"analyst|institutional investor|investor meet|con\.?\s*call|conference call", re.I),
    "press": re.compile(r"press release|media release", re.I),
}
_SKIP_MEETS = re.compile(r"audio|video|recording|transcript", re.I)      # BSE lists these elsewhere
# Monthly sales updates from the listed carmakers (for the Car Sales page)
AUTO_SALES_SYMBOLS = {"MARUTI", "TMPV", "TATAMOTORS", "M&M", "HYUNDAI"}
_AUTO_SALES = re.compile(r"\bsales\b|business update|despatch|dispatch", re.I)

_lock = threading.RLock()
_poller = {"started": False}


def now_ist():
    return dt.datetime.now(IST)


# ── parsing ───────────────────────────────────────────────────────────────

def _symbol_from_link(link):
    m = re.search(r"/corporate/([A-Z0-9&\-]{1,20})_\d{14}_", link or "")
    return m.group(1) if m else None


def parse_rss(xml_bytes):
    """RSS → [{title, link, desc, pub (IST iso), subject, headline, symbol}]. Pure."""
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        g = lambda tag: (it.findtext(tag) or "").strip()
        title, link, desc, pub = g("title"), g("link"), " ".join(g("description").split()), g("pubDate")
        try:
            pub_dt = dt.datetime.strptime(pub, "%d-%b-%Y %H:%M:%S")
        except ValueError:
            continue
        subject = ""
        m = re.search(r"\|\s*SUBJECT:\s*(.+)$", desc, re.I)
        if m:
            subject = m.group(1).strip()
        headline = re.split(r"\s*\|\s*SUBJECT:", desc, flags=re.I)[0].strip()
        out.append({"title": title, "link": link, "desc": desc, "pub": pub_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "subject": subject, "headline": headline, "symbol": _symbol_from_link(link)})
    return out


def classify(item):
    """Which Corporate Actions tab an NSE announcement belongs to (or None)."""
    text = (item.get("subject") or "") + " " + (item.get("headline") or "")
    if item.get("symbol") in AUTO_SALES_SYMBOLS and _AUTO_SALES.search(text):
        return "autosales"
    for feed in ("orders", "meets", "press"):
        if FEED_RULES[feed].search(text):
            if feed == "meets" and _SKIP_MEETS.search(item.get("subject") or ""):
                return None
            return feed
    return None


def parse_board_meeting(item):
    """Board_Meetings item → (meeting_date, purpose) or (None, None)."""
    desc = item.get("desc") or ""
    m = re.search(r"Meeting Date:\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", desc) or \
        re.search(r"held on\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", desc)
    if not m:
        return None, None
    try:
        d = dt.datetime.strptime(m.group(1), "%d-%b-%Y").date()
    except ValueError:
        return None, None
    purpose = re.split(r"\s*\|\s*Meeting Date:", desc)[0].strip()
    return d, purpose


# ── fetching + store ──────────────────────────────────────────────────────

def _get(path):
    from curl_cffi import requests as cffi
    errs = []
    for host in HOSTS:
        try:
            r = cffi.get(host + path, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
                         impersonate="chrome120", timeout=12)
            if r.status_code == 200 and b"<rss" in r.content[:500]:
                return r.content
            errs.append(f"HTTP {r.status_code}")
        except Exception as e:
            errs.append(str(e).splitlines()[0][:60])
    raise RuntimeError("NSE feed unreachable (" + ", ".join(dict.fromkeys(errs)) + ")")


def _load():
    try:
        from database import get_screener_results
        data, _ = get_screener_results(STORE_KEY)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[NSE] load failed: {e}")
    return {}


def _save(store):
    try:
        from database import save_screener_results
        save_screener_results(STORE_KEY, store)
    except Exception as e:
        print(f"[NSE] save failed: {e}")


def _norm_name(s):
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    s = re.sub(r"\b(limited|ltd|the|india|co|company|corporation|corp)\b", " ", s)
    return " ".join(s.split())


def refresh(force=False, max_age=180):
    """Read both NSE feeds (if the saved copy is older than max_age s) and merge."""
    with _lock:
        store = _load()
        if not force and time.time() - (store.get("ts") or 0) < max_age:
            return store
        store.setdefault("ann", {})
        store.setdefault("meetings", {})
        errors = []
        try:
            for it in parse_rss(_get("/content/RSS/Online_announcements.xml")):
                feed = classify(it)
                if feed:
                    key = it["link"] or (it["title"] + it["pub"])
                    it["feed"] = feed
                    store["ann"][key] = it
        except Exception as e:
            errors.append(str(e))
        try:
            for it in parse_rss(_get("/content/RSS/Board_Meetings.xml")):
                d, purpose = parse_board_meeting(it)
                if not d:
                    continue
                key = (it.get("symbol") or _norm_name(it["title"])) + "|" + d.isoformat()
                prev = store["meetings"].get(key) or {}
                store["meetings"][key] = {"symbol": it.get("symbol") or prev.get("symbol"), "name": it["title"],
                                          "date": d.isoformat(), "purpose": purpose if len(purpose) > len(prev.get("purpose") or "") else prev.get("purpose"),
                                          "pub": it["pub"]}
        except Exception as e:
            errors.append(str(e))
        cutoff = (now_ist() - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT00:00:00")
        store["ann"] = {k: v for k, v in store["ann"].items() if v["pub"] >= cutoff}
        mcut = (now_ist().date() - dt.timedelta(days=45)).isoformat()
        store["meetings"] = {k: v for k, v in store["meetings"].items() if v["date"] >= mcut}
        store["ts"] = time.time()
        store["error"] = "; ".join(errors) or None
        if not errors:
            store["updated_at"] = now_ist().strftime("%d %b %Y %H:%M IST")
        _save(store)
        return store


def _poll_loop():
    while True:
        n = now_ist()
        busy = n.weekday() < 5 and 8 <= n.hour < 22
        try:
            refresh(max_age=(POLL_MARKET if busy else POLL_QUIET) - 30)
        except Exception as e:
            print(f"[NSE] poll failed: {e}")
        time.sleep(POLL_MARKET if busy else POLL_QUIET)


def ensure_poller():
    """Start the background reader once per server process."""
    with _lock:
        if _poller["started"]:
            return
        _poller["started"] = True
    threading.Thread(target=_poll_loop, daemon=True, name="nse-feeds").start()


# ── views used by the rest of the app ─────────────────────────────────────

def display_items(feed, store=None):
    """NSE filings for one Corporate Actions tab, shaped like the BSE items."""
    store = store if store is not None else _load()
    out = []
    for it in (store.get("ann") or {}).values():
        if it.get("feed") != feed:
            continue
        t = dt.datetime.strptime(it["pub"], "%Y-%m-%dT%H:%M:%S")
        sym = it.get("symbol")
        out.append({
            "id": "nse:" + (it.get("link") or it["title"] + it["pub"]),
            "src": "NSE", "company": it["title"], "code": sym,
            "subject": it["title"] + (" - " + sym if sym else "") + " - " + (it.get("subject") or "Announcement"),
            "category": it.get("subject") or "Announcement",
            "headline": it.get("headline"),
            "dt": it["pub"], "date": t.strftime("%Y-%m-%d"),
            "dissem": t.strftime("%d-%m-%Y %H:%M:%S"), "recv": None, "time_taken": None,
            "pdf_url": it.get("link") if (it.get("link") or "").startswith("https://") else None,
            "norm": _norm_name(it["title"]),
        })
    return out


def result_meetings(store=None):
    """{'by_symbol': {SYM: [date,…]}, 'by_name': {norm name: [date,…]}} for result board meetings."""
    store = store if store is not None else _load()
    by_sym, by_name = {}, {}
    for m in (store.get("meetings") or {}).values():
        if not re.search(r"result", m.get("purpose") or "", re.I):
            continue
        d = dt.date.fromisoformat(m["date"])
        if m.get("symbol"):
            by_sym.setdefault(m["symbol"], []).append(d)
        by_name.setdefault(_norm_name(m.get("name")), []).append(d)
    return {"by_symbol": by_sym, "by_name": by_name}


def status(store=None):
    store = store if store is not None else _load()
    return {"updated_at": store.get("updated_at"), "error": store.get("error")}
