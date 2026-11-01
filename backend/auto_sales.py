"""Monthly car (passenger-vehicle) sales tracker.

Carmakers publish last month's domestic dispatches (factory → dealer, "wholesales")
on the 1st of every month. There is no free official API with the numbers, so:

  * the page ships with verified figures (SEED below, cross-checked between
    company releases and two trade sources),
  * admins add each new month from a form on the page (saved in the DB; a saved
    month always wins over the seed),
  * the official NSE filings ("sales figures" announcements from the listed
    carmakers) are picked up by nse_feeds' 5-minute reader and listed on the page,
    so on the 1st everyone can open the company's own PDF straight away.
"""
import datetime as dt
import re

STORE_KEY = "auto_sales"
SHOW_MONTHS = 4

# key → display name, NSE symbol if listed in India, chart colour
COMPANIES = [
    ("maruti",   "Maruti Suzuki",      "MARUTI",  "#2563eb"),
    ("tata",     "Tata Motors PV",     "TMPV",    "#0891b2"),
    ("mahindra", "Mahindra",           "M&M",     "#dc2626"),
    ("hyundai",  "Hyundai",            "HYUNDAI", "#7c3aed"),
    ("kia",      "Kia",                None,      "#db2777"),
    ("toyota",   "Toyota Kirloskar",   None,      "#ea580c"),
    ("skodavw",  "Skoda-VW",           None,      "#16a34a"),
    ("mg",       "JSW MG Motor",       None,      "#a16207"),
    ("honda",    "Honda Cars",         None,      "#64748b"),
    ("renault",  "Renault",            None,      "#0f766e"),
    ("nissan",   "Nissan",             None,      "#9333ea"),
]
KEYS = [c[0] for c in COMPANIES]
LISTED_SYMBOLS = {c[2] for c in COMPANIES if c[2]}

# Domestic passenger-vehicle dispatches. "ly" = same month a year earlier.
_S = lambda u, ly=None: {"units": u, "ly": ly}
SEED = {
    # June is kept only so July's month-on-month change can be worked out.
    "2026-06": {"prev_only": True, "rows": {"maruti": _S(147187), "tata": _S(62076), "mahindra": _S(60393), "hyundai": _S(39635),
                         "toyota": _S(28441), "kia": _S(24552), "mg": _S(7568), "honda": _S(5243),
                         "nissan": _S(3006), "renault": _S(4063)},
                "sources": [["Autocar India", "https://www.autocarindia.com/industry/july-2026-car-sales-tata-holds-slim-lead-over-mahindra-440365"]]},
    "2026-07": {"rows": {"maruti": _S(196203, 137776), "tata": _S(62611, 39521), "mahindra": _S(60048, 49871),
                         "hyundai": _S(54210, 43966), "toyota": _S(30516, 29159), "kia": _S(28200, 22135),
                         "mg": _S(8158, 6687), "honda": _S(6014, 4050), "nissan": _S(4518, 1421), "renault": _S(3293, 2575)},
                "industry": 470000, "industry_approx": True,
                "sources": [["Autocar India", "https://www.autocarindia.com/industry/july-2026-car-sales-tata-holds-slim-lead-over-mahindra-440365"],
                            ["DriveSpark", "https://www.drivespark.com/four-wheelers/2026/top-10-carmakers-july-2026-maruti-tata-mahindra-lead-087865.html"]]},
    "2026-08": {"rows": {"maruti": _S(176971, 131278), "tata": _S(65253, 41001), "mahindra": _S(59257, 39399),
                         "hyundai": _S(54396, 44001), "kia": _S(29042, 19608), "toyota": _S(28410, 29302),
                         "skodavw": _S(7933, 7690), "mg": _S(7508, 6578), "honda": _S(5385, 3850),
                         "nissan": _S(3426, 1384), "renault": _S(2842, 3015)},
                "industry": 441576, "industry_ly": 327719,
                "sources": [["Autopunditz", "https://www.autopunditz.com/post/india-car-sales-snapshot-august-2026"],
                            ["Autocar India", "https://www.autocarindia.com/industry/august-2026-car-sales-tata-widens-monthly-lead-over-mahindra-440627"]]},
    "2026-09": {"rows": {"maruti": _S(181838, 132821), "tata": _S(68810, 59667), "mahindra": _S(64092, 56233),
                         "hyundai": _S(57166, 51547), "kia": _S(32017, 22700), "toyota": _S(25027, 27089),
                         "skodavw": _S(8320, 9416), "mg": _S(8018, 6728), "honda": _S(4465, 5305),
                         "renault": _S(3399, 4265), "nissan": _S(3300, 1652)},
                "industry": 457604, "industry_ly": 378457,
                "sources": [["Autopunditz", "https://www.autopunditz.com/post/india-car-sales-september-2026-oem-wise-dispatches"],
                            ["DriveSpark (Tata)", "https://www.drivespark.com/four-wheelers/2026/tata-car-sales-september-2026-68810-units-sold-088299.html"]]},
    "2026-10": {"rows": {"maruti": _S(212384, 176318), "tata": _S(73894, 61134), "mahindra": _S(73338, 71624),
                         "hyundai": _S(62799, 53792), "kia": _S(33462, 29556), "toyota": _S(29521, 40257),
                         "skodavw": _S(9083, 8252), "mg": _S(7014, 6397), "honda": _S(5429, 6394),
                         "renault": _S(4577, 5041), "nissan": _S(3257, 2548)},
                "sources": [["Autocar India", "https://www.autocarindia.com/car-news/october-2026-car-sales-tata-retains-second-place-441189"],
                            ["Business Today", "https://www.businesstoday.in/auto/story/festive-boom-maruti-mahindra-tata-motors-hyundai-post-record-domestic-sales-in-october-565002-2026-11-01"]]},
}

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _load():
    try:
        from database import get_screener_results
        data, _ = get_screener_results(STORE_KEY)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[AutoSales] load failed: {e}")
    return {}


def _save(store):
    from database import save_screener_results
    save_screener_results(STORE_KEY, store)


def all_months(store=None):
    """Seed + saved months (saved wins; a saved {'deleted': True} hides a month)."""
    store = store if store is not None else _load()
    months = {k: v for k, v in SEED.items()}
    for k, v in (store.get("months") or {}).items():
        if v.get("deleted"):
            months.pop(k, None)
        else:
            months[k] = v
    return months


def _prev_month(m):
    y, mo = int(m[:4]), int(m[5:])
    return f"{y - 1}-12" if mo == 1 else f"{y}-{mo - 1:02d}"


def _pct(a, b):
    return round((a - b) / b * 100, 1) if a is not None and b else None


def month_label(m, short=False):
    return dt.date(int(m[:4]), int(m[5:]), 1).strftime("%b %Y" if short else "%B %Y")


def build_view(today=None, store=None, filings=None):
    today = today or dt.date.today()
    months = all_months(store)
    shown = sorted(m for m, v in months.items() if not v.get("prev_only"))[-SHOW_MONTHS:]
    cols = []
    for m in shown:
        v = months[m]
        rows = v.get("rows") or {}
        tot = sum(r["units"] for r in rows.values() if r and r.get("units"))
        like = [(r["units"], r["ly"]) for r in rows.values() if r and r.get("units") and r.get("ly")]
        prev = months.get(_prev_month(m)) or {}
        prows = prev.get("rows") or {}
        like_m = [(rows[k]["units"], prows[k]["units"]) for k in rows
                  if rows[k] and rows[k].get("units") and (prows.get(k) or {}).get("units")]
        cols.append({
            "month": m, "label": month_label(m), "short": month_label(m, True),
            "total": tot,
            "total_yoy": _pct(sum(a for a, _ in like), sum(b for _, b in like)) if like else None,
            "total_mom": _pct(sum(a for a, _ in like_m), sum(b for _, b in like_m)) if like_m else None,
            "industry": v.get("industry"), "industry_ly": v.get("industry_ly"),
            "industry_approx": bool(v.get("industry_approx")),
            "industry_yoy": _pct(v.get("industry"), v.get("industry_ly")),
            "sources": v.get("sources") or [], "updated_by": v.get("updated_by"), "updated_at": v.get("updated_at"),
        })
    companies = []
    for key, name, sym, color in COMPANIES:
        cells = []
        for c in cols:
            m = c["month"]
            r = ((months[m].get("rows") or {}).get(key)) or {}
            pr = (((months.get(_prev_month(m)) or {}).get("rows") or {}).get(key)) or {}
            u = r.get("units")
            cells.append({"units": u, "ly": r.get("ly"), "yoy": _pct(u, r.get("ly")),
                          "mom": _pct(u, pr.get("units")),
                          "share": round(u / c["total"] * 100, 1) if u and c["total"] else None})
        if any(x["units"] for x in cells):
            companies.append({"key": key, "name": name, "symbol": sym, "color": color, "cells": cells})
    latest = cols[-1]["month"] if cols else None
    companies.sort(key=lambda co: -(co["cells"][-1]["units"] or 0))

    # next release = 1st of the month after the latest month's following month
    # (October's numbers come out on 1 November, so after October we wait for 1 December)
    nxt, due_now = None, False
    if latest:
        y, mo = int(latest[:4]), int(latest[5:])
        rel = dt.date(y + (mo + 1 > 12), (mo % 12) + 1, 1)                 # latest month's own release day
        nm = dt.date(rel.year + (rel.month == 12), rel.month % 12 + 1, 1)   # the next one
        missing = dt.date(rel.year, rel.month, 1).strftime("%Y-%m")           # the month due on `nm`
        nxt = {"date": nm.isoformat(), "month": missing, "label": month_label(missing)}
        due_now = today >= nm
    return {
        "months": cols, "companies": companies, "latest": latest,
        "next_release": nxt, "due_now": due_now,
        "filings": filings if filings is not None else latest_filings(),
        "company_list": [{"key": k, "name": n, "symbol": s} for k, n, s, _ in COMPANIES],
        "raw": {m: {"rows": v.get("rows") or {}, "industry": v.get("industry"), "industry_ly": v.get("industry_ly"),
                    "sources": v.get("sources") or []} for m, v in months.items()},
    }


def latest_filings(limit=12):
    """Sales-figure announcements from the listed carmakers, newest first (NSE feed)."""
    try:
        import nse_feeds
        items = [x for x in nse_feeds.display_items("autosales")]
    except Exception as e:
        print(f"[AutoSales] filings: {e}")
        return []
    items.sort(key=lambda x: x["dt"], reverse=True)
    return [{"company": x["company"], "symbol": x["code"], "when": x["dissem"], "dt": x["dt"],
             "headline": x.get("headline") or x.get("category"), "pdf_url": x.get("pdf_url")} for x in items[:limit]]


def save_month(month, rows, sources=None, industry=None, industry_ly=None, user=None):
    """Admin entry. rows = {key: {'units': int, 'ly': int|None}}. Returns the saved month."""
    if not MONTH_RE.match(month or ""):
        raise ValueError("Month must look like 2026-11.")
    names = {c[0]: c[1] for c in COMPANIES}

    def num(v, label):
        if v in (None, ""):
            return None
        try:
            n = int(float(str(v).replace(",", "").strip()))
        except ValueError:
            raise ValueError(f"{label}: '{v}' is not a number.")
        if n < 0 or n > 2_000_000:
            raise ValueError(f"{label}: {n} doesn't look like a monthly sales number.")
        return n

    clean = {}
    for k, r in (rows or {}).items():
        if k not in KEYS or not isinstance(r, dict):
            continue
        u, ly = num(r.get("units"), names[k]), num(r.get("ly"), names[k])
        if u is not None:
            clean[k] = {"units": u, "ly": ly}
    if len(clean) < 3:
        raise ValueError("Enter at least 3 carmakers' sales for the month.")
    src = []
    for s in (sources or [])[:5]:
        if isinstance(s, (list, tuple)) and len(s) == 2 and str(s[1]).startswith("http"):
            src.append([str(s[0])[:60], str(s[1])[:400]])
    store = _load()
    store.setdefault("months", {})
    rec = {"rows": clean, "sources": src,
           "industry": num(industry, "Industry total"), "industry_ly": num(industry_ly, "Industry total last year"),
           "updated_by": user, "updated_at": dt.datetime.now().strftime("%d %b %Y %H:%M")}
    store["months"][month] = rec
    _save(store)
    return rec


def delete_month(month, user=None):
    if not MONTH_RE.match(month or ""):
        raise ValueError("Month must look like 2026-11.")
    store = _load()
    store.setdefault("months", {})
    store["months"][month] = {"deleted": True, "updated_by": user}
    _save(store)
