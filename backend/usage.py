"""Site usage stats for the admin "Site Usage" page.

Visits are recorded per visitor per day (IST):
  • signed-in users by email
  • everyone else by a random browser ID kept in localStorage ("anon:<id>")
If someone browses anonymously and then signs in the same day, the anonymous
row is merged into their email (matched by browser ID), so they count once.
"""
import datetime as dt
import re

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

BOT_RE = re.compile(r"bot|crawl|spider|slurp|mediapartners|adsbot|lighthouse|headless|"
                    r"pagespeed|preview|facebookexternalhit|curl|wget|python-requests|uptime", re.I)
VID_RE = re.compile(r"^[a-z0-9\-]{8,48}$")
PAGE_RE = re.compile(r"^[a-z0-9\-_]{1,32}$")


def now_ist():
    return dt.datetime.now(IST)


def is_bot(user_agent):
    return not user_agent or bool(BOT_RE.search(user_agent))


def week_start(d):
    return d - dt.timedelta(days=d.weekday())             # Monday


def _merge_anon(rows):
    """Drop anonymous rows whose browser ID belongs to a signed-in user the same
    day (same person, counted before and after signing in)."""
    linked = {}                                   # (day, vid) → the signed-in row
    rows = [dict(r) for r in rows]
    for r in rows:
        if not str(r.get("visitor", "")).startswith("anon:") and r.get("vid"):
            linked[(r["day"], r["vid"])] = r
    out = []
    for r in rows:
        v = str(r.get("visitor", ""))
        owner = linked.get((r["day"], v[5:])) if v.startswith("anon:") else None
        if owner is not None:
            owner["views"] = int(owner.get("views") or 0) + int(r.get("views") or 0)   # keep the page views
            continue
        out.append(r)
    return out


def compute(rows, page_rows, today, days=30, weeks=12, exclude=()):
    """Aggregate raw rows into the page payload. Pure (testable)."""
    exclude = {e.lower() for e in exclude}
    rows = [r for r in _merge_anon(rows) if str(r.get("visitor", "")).lower() not in exclude]

    by_day = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)

    def day_stats(d):
        rs = by_day.get(d.isoformat(), [])
        si = sum(1 for r in rs if not str(r["visitor"]).startswith("anon:"))
        return {"date": d.isoformat(), "users": len(rs), "signed_in": si, "anonymous": len(rs) - si,
                "views": sum(int(r.get("views") or 0) for r in rs)}

    daily = [day_stats(today - dt.timedelta(days=i)) for i in range(days - 1, -1, -1)]

    def uniq(start, end):                       # inclusive dates
        s, e = start.isoformat(), end.isoformat()
        vis = {r["visitor"] for r in rows if s <= r["day"] <= e}
        si = sum(1 for v in vis if not str(v).startswith("anon:"))
        views = sum(int(r.get("views") or 0) for r in rows if s <= r["day"] <= e)
        return len(vis), si, views

    weekly = []
    ws0 = week_start(today)
    for i in range(weeks - 1, -1, -1):
        ws = ws0 - dt.timedelta(weeks=i)
        we = ws + dt.timedelta(days=6)
        u, si, v = uniq(ws, min(we, today))
        weekly.append({"week_start": ws.isoformat(), "week_end": we.isoformat(), "users": u,
                       "signed_in": si, "anonymous": u - si, "views": v, "partial": we > today})

    u7, si7, v7 = uniq(today - dt.timedelta(days=6), today)
    u30, si30, v30 = uniq(today - dt.timedelta(days=29), today)
    yday = day_stats(today - dt.timedelta(days=1))
    kpis = {"today": daily[-1]["users"], "yesterday": yday["users"],
            "last7": u7, "last30": u30, "signed_in_30": si30, "views_30": v30,
            "avg_daily_7": round(sum(d["users"] for d in daily[-7:]) / 7, 1)}

    # top pages (last 7 days)
    since7 = (today - dt.timedelta(days=6)).isoformat()
    pages = {}
    for p in page_rows:
        if p["day"] >= since7:
            pages[p["page"]] = pages.get(p["page"], 0) + int(p.get("views") or 0)
    top_pages = [{"page": k, "views": v} for k, v in sorted(pages.items(), key=lambda kv: -kv[1])[:12]]

    # most active signed-in users (last 30 days)
    since30 = (today - dt.timedelta(days=29)).isoformat()
    users = {}
    for r in rows:
        v = str(r["visitor"])
        if v.startswith("anon:") or r["day"] < since30:
            continue
        u = users.setdefault(v, {"email": v, "days": 0, "views": 0, "last_seen": ""})
        u["days"] += 1
        u["views"] += int(r.get("views") or 0)
        u["last_seen"] = max(u["last_seen"], r.get("last_seen") or r["day"])
    top_users = sorted(users.values(), key=lambda u: (-u["days"], -u["views"], u["email"]))[:25]

    first = min((r["day"] for r in rows), default=None)
    return {"daily": daily, "weekly": weekly, "kpis": kpis, "top_pages": top_pages,
            "top_users": top_users, "tracking_since": first, "today": today.isoformat()}
