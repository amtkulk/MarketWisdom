"""Advance notice of REIT / InvIT distributions, from the trusts' own NSE filings.

Why: Yahoo only lists a distribution on its ex-date, which is too late to buy. The trusts
announce each payout (amount + record date) on NSE, normally ~4–6 days before the record
date, and announce the board meeting that will decide it a week or two before that.

nse_feeds' 5-minute poller hands us every NSE announcement / board-meeting item; we keep
the ones from the 7 listed trusts, open their payout PDFs (nsearchives works from our
server) and pull out the record date and amount. India settles T+1, so the ex-date is the
record date and you must buy by the previous trading day.
"""
import datetime as dt
import io
import re

TRUSTS = {
    "EMBASSY":   ("embassy office parks",),
    "MINDSPACE": ("mindspace business parks",),
    "BIRET":     ("brookfield india real estate",),
    "NXST":      ("nexus select",),
    "INDIGRID":  ("india grid trust", "indigrid"),
    "PGINVIT":   ("powergrid infrastructure", "power grid infrastructure"),
    "IRBINVIT":  ("irb invit",),
}
KEEP_ANN_DAYS = 120
KEEP_DIST_DAYS = 400
MAX_PDF_PER_RUN = 6
_PDF_HINT = re.compile(r"record date|distribution|outcome of (the )?board|board meeting|declar|financial result|dividend", re.I)
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9


def trust_symbol(item):
    sym = (item.get("symbol") or "").upper()
    if sym in TRUSTS:
        return sym
    name = " ".join(re.sub(r"[^a-z0-9 ]", " ", (item.get("title") or item.get("name") or "").lower()).split())
    for s, names in TRUSTS.items():
        if any(n in name for n in names):
            return s
    return None


# ── text parsing (pure) ───────────────────────────────────────────────────

_MON = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_DATE_RES = [
    ("mdy", re.compile(_MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.I)),
    ("dmy", re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?" + _MON + r"\.?,?\s+(\d{4})", re.I)),
    ("dmy", re.compile(r"(\d{1,2})[-/ ]" + _MON + r"[-/ ](\d{4})", re.I)),
    ("num", re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")),
]


def find_dates(text):
    """[(position, date)] for every date written in a common Indian filing style."""
    out, seen = [], set()
    for kind, rx in _DATE_RES:
        for m in rx.finditer(text):
            try:
                if kind == "mdy":
                    mo, d, y = _MONTHS[m.group(1).lower()[:3]], int(m.group(2)), int(m.group(3))
                elif kind == "dmy":
                    d, mo, y = int(m.group(1)), _MONTHS[m.group(2).lower()[:3]], int(m.group(3))
                else:
                    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                val = dt.date(y, mo, d)
            except (ValueError, KeyError):
                continue
            if m.start() in seen:
                continue
            seen.add(m.start())
            out.append((m.start(), val))
    return sorted(out)


_AMT = re.compile(r"(?:rs\.?|inr|₹)\s*([0-9]{1,3}(?:\.[0-9]{1,4})?)\s*(?:/-)?\s*(?:per\s+unit|/\s*unit|for each unit|each unit)", re.I)


def find_amount(text):
    """Total distribution per unit. Filings often list the parts (interest, dividend, return
    of capital) as well as the total, so the largest per-unit figure is the total."""
    vals = []
    for m in _AMT.finditer(text):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if 0 < v <= 100:
            vals.append(v)
    return max(vals) if vals else None


def _date_near(text, keyword_rx, filed):
    """The date written closest to a keyword (e.g. 'record date'), sensibly after filing."""
    dates = find_dates(text)
    best = None
    for k in re.finditer(keyword_rx, text, re.I):
        for pos, d in dates:
            gap = abs(pos - k.start())
            if gap > 220:
                continue
            if filed and not (filed - dt.timedelta(days=3) <= d <= filed + dt.timedelta(days=75)):
                continue
            if best is None or gap < best[0]:
                best = (gap, d)
    return best[1] if best else None


def parse_distribution(text, filed=None):
    """Text of a filing → {'record_date', 'ex_date', 'amount'} or None."""
    text = " ".join((text or "").split())
    if not re.search(r"distribution|dividend|record date|ex-?date", text, re.I):
        return None
    rd = _date_near(text, r"record\s*date", filed)
    ex = _date_near(text, r"ex[-\s]?date", filed)
    amt = find_amount(text)
    if not (rd or ex):
        return None
    if not amt and not re.search(r"distribution|dividend", text, re.I):
        return None
    rd = rd or ex
    return {"record_date": rd.isoformat(), "ex_date": (ex or rd).isoformat(),    # T+1: ex-date = record date
            "amount": amt}


def buy_by(ex_iso):
    """Last trading day to buy and still get the payout (weekday before the ex-date)."""
    d = dt.date.fromisoformat(ex_iso) - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


# ── fetching ──────────────────────────────────────────────────────────────

def pdf_text(raw, max_pages=6):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(raw))
    return "\n".join((p.extract_text() or "") for p in r.pages[:max_pages])


def _get_bytes(url):
    from curl_cffi import requests as cffi
    r = cffi.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"},
                 impersonate="chrome120", timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.content


def _add_dist(store, sym, info, filed_iso, link, source):
    key = f"{sym}|{info['ex_date']}"
    prev = store["reit_dist"].get(key) or {}
    store["reit_dist"][key] = {
        "symbol": sym, "ex_date": info["ex_date"], "record_date": info["record_date"],
        "amount": info.get("amount") if info.get("amount") is not None else prev.get("amount"),
        "announced": min(filter(None, [prev.get("announced"), filed_iso[:10]])) if filed_iso else prev.get("announced"),
        "link": prev.get("link") or link, "source": prev.get("source") or source,
    }


def ingest(store, ann_items, ca_items=(), fetch=None, today=None):
    """Called by nse_feeds.refresh with freshly parsed RSS items. Mutates `store`."""
    fetch = fetch or _get_bytes
    today = today or dt.date.today()
    store.setdefault("reit_ann", {})
    store.setdefault("reit_dist", {})
    store.setdefault("reit_pdf", {})
    for it in ann_items:
        sym = trust_symbol(it)
        if not sym:
            continue
        key = it.get("link") or (it.get("title", "") + it.get("pub", ""))
        store["reit_ann"][key] = {"symbol": sym, "pub": it["pub"], "subject": it.get("subject"),
                                  "headline": it.get("headline"), "link": it.get("link")}
    # payout details: description first, then the PDF (each PDF read once)
    budget = MAX_PDF_PER_RUN
    for key, a in sorted(store["reit_ann"].items(), key=lambda kv: kv[1]["pub"], reverse=True):
        filed = dt.date.fromisoformat(a["pub"][:10])
        text = (a.get("subject") or "") + " " + (a.get("headline") or "")
        info = parse_distribution(text, filed)
        link = a.get("link") or ""
        if not info and link.lower().endswith(".pdf") and link not in store["reit_pdf"] \
                and _PDF_HINT.search(text) and budget > 0 and (today - filed).days <= 30:
            budget -= 1
            try:
                info = parse_distribution(pdf_text(fetch(link)), filed)
                store["reit_pdf"][link] = {"ok": bool(info)}
            except Exception as e:
                store["reit_pdf"][link] = {"ok": False, "err": str(e)[:80]}
        if info:
            _add_dist(store, a["symbol"], info, a["pub"], link or None, "NSE filing")
    for it in ca_items:                                    # NSE corporate-actions feed
        sym = trust_symbol(it)
        if not sym:
            continue
        filed = dt.date.fromisoformat(it["pub"][:10])
        info = parse_distribution(it.get("title", "") + " " + it.get("desc", ""), filed)
        if info:
            _add_dist(store, sym, info, it["pub"], None, "NSE corporate actions")
    # prune
    acut = (today - dt.timedelta(days=KEEP_ANN_DAYS)).isoformat()
    store["reit_ann"] = {k: v for k, v in store["reit_ann"].items() if v["pub"][:10] >= acut}
    dcut = (today - dt.timedelta(days=KEEP_DIST_DAYS)).isoformat()
    store["reit_dist"] = {k: v for k, v in store["reit_dist"].items() if v["ex_date"] >= dcut}
    store["reit_pdf"] = {k: v for k, v in store["reit_pdf"].items() if k in store["reit_ann"]}


def view(sym, store, today=None):
    """What the REIT page needs for one trust."""
    today = (today or dt.date.today()).isoformat()
    dists = sorted((d for d in (store.get("reit_dist") or {}).values() if d["symbol"] == sym),
                   key=lambda d: d["ex_date"], reverse=True)
    meets = []
    for m in (store.get("meetings") or {}).values():
        if trust_symbol({"symbol": m.get("symbol"), "title": m.get("name")}) == sym and m["date"] >= today:
            meets.append({"date": m["date"], "purpose": m.get("purpose")})
    meets.sort(key=lambda m: m["date"])
    filings = sorted((a for a in (store.get("reit_ann") or {}).values() if a["symbol"] == sym),
                     key=lambda a: a["pub"], reverse=True)[:3]
    return {"dists": dists, "meeting": meets[0] if meets else None,
            "filings": [{"date": a["pub"][:10], "subject": a.get("subject") or a.get("headline"),
                         "link": a.get("link")} for a in filings]}
