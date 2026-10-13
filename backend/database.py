import os
from datetime import datetime

MONGODB_URI = os.environ.get("MONGODB_URI")
USE_MONGO = bool(MONGODB_URI)

if USE_MONGO:
    try:
        from pymongo import MongoClient
        import certifi
        client = MongoClient(MONGODB_URI, tlsCAFile=certifi.where())
        db = client.get_database("market_wisdom")
        watchlist_col = db.get_collection("watchlist")
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
        USE_MONGO = False # Fallback if connection code fails

if not USE_MONGO:
    import sqlite3
    DATABASE_FILE = 'watchlist.db'
    def get_db_connection():
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    if USE_MONGO:
        pass
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS watchlist (
                ticker TEXT PRIMARY KEY,
                company_name TEXT,
                sector TEXT,
                price TEXT,
                rating TEXT,
                rated_at TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS screener_results (
                market TEXT PRIMARY KEY,
                results_json TEXT,
                updated_at TEXT
            )
        ''')
        # user-scoped watchlist (v2); anonymous/legacy rows live under user_id='public'
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS watchlist_v2 (
                user_id TEXT NOT NULL DEFAULT 'public',
                ticker TEXT NOT NULL,
                company_name TEXT, sector TEXT, price TEXT, rating TEXT, rated_at TEXT,
                PRIMARY KEY (user_id, ticker)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chartink_scanners (
                user_id TEXT NOT NULL,
                url TEXT NOT NULL,
                name TEXT,
                added_at TEXT,
                PRIMARY KEY (user_id, url)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alert_actions (
                user_id TEXT NOT NULL,
                alert_id TEXT NOT NULL,
                action TEXT,
                acted_date TEXT,
                snooze_until TEXT,
                PRIMARY KEY (user_id, alert_id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                name TEXT, picture TEXT, google_sub TEXT, last_login TEXT
            )
        ''')
        # disclaimer consent: one row per user (accepted / declined)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS consents (
                email TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                version TEXT,
                decided_at TEXT
            )
        ''')
        # site usage: one row per visitor per day (visitor = email, or "anon:<browser id>")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usage_daily (
                day TEXT NOT NULL,
                visitor TEXT NOT NULL,
                vid TEXT,
                signed_in INTEGER DEFAULT 0,
                views INTEGER DEFAULT 0,
                last_seen TEXT,
                PRIMARY KEY (day, visitor)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usage_pages (
                day TEXT NOT NULL,
                page TEXT NOT NULL,
                views INTEGER DEFAULT 0,
                PRIMARY KEY (day, page)
            )
        ''')
        # NOTE: the old shared 'watchlist' table is intentionally NOT migrated into
        # any user's list. Ratings are now per-user only — new users start empty.
        # Legacy rows stay in the old table for safekeeping; if you later decide
        # to import a specific user's picks, do it manually.
        conn.commit()
        conn.close()

def add_or_update_stock(ticker, company_name, sector, price, rating, user_id="public"):
    rated_at = datetime.now().strftime("%d %b %Y  %H:%M")

    if USE_MONGO:
        watchlist_col.update_one(
            {"ticker": ticker, "user_id": user_id},
            {"$set": {
                "company_name": company_name,
                "sector": sector,
                "price": price,
                "rating": rating,
                "rated_at": rated_at,
                "user_id": user_id
            }},
            upsert=True
        )
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO watchlist_v2 (user_id, ticker, company_name, sector, price, rating, rated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, ticker) DO UPDATE SET
                company_name=excluded.company_name,
                sector=excluded.sector,
                price=excluded.price,
                rating=excluded.rating,
                rated_at=excluded.rated_at
        ''', (user_id, ticker, company_name, sector, price, rating, rated_at))
        conn.commit()
        conn.close()

def delete_stock(ticker, user_id="public"):
    if USE_MONGO:
        if user_id == "public":
            watchlist_col.delete_many({"ticker": ticker,
                                       "$or": [{"user_id": "public"}, {"user_id": {"$exists": False}}]})
        else:
            watchlist_col.delete_one({"ticker": ticker, "user_id": user_id})
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM watchlist_v2 WHERE ticker = ? AND user_id = ?', (ticker, user_id))
        conn.commit()
        conn.close()

def get_all_stocks(user_id="public"):
    if USE_MONGO:
        if user_id == "public":
            q = {"$or": [{"user_id": "public"}, {"user_id": {"$exists": False}}]}
        else:
            q = {"user_id": user_id}
        return list(watchlist_col.find(q, {"_id": 0}))
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM watchlist_v2 WHERE user_id = ?', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]


def upsert_user(email, name="", picture="", google_sub=""):
    """Store/refresh a Google-signed-in user."""
    last_login = datetime.now().strftime("%d %b %Y  %H:%M")
    if USE_MONGO:
        db.get_collection("users").update_one(
            {"email": email},
            {"$set": {"name": name, "picture": picture,
                      "google_sub": google_sub, "last_login": last_login}},
            upsert=True)
    else:
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO users (email, name, picture, google_sub, last_login)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                name=excluded.name, picture=excluded.picture,
                google_sub=excluded.google_sub, last_login=excluded.last_login
        ''', (email, name, picture, google_sub, last_login))
        conn.commit()
        conn.close()


def set_consent(email, status, version):
    """Record a user's disclaimer decision ('accepted' or 'declined')."""
    email = (email or "").strip().lower()
    decided_at = datetime.now().strftime("%d %b %Y  %H:%M")
    if USE_MONGO:
        db.get_collection("consents").update_one(
            {"email": email}, {"$set": {"status": status, "version": version, "decided_at": decided_at}}, upsert=True)
    else:
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO consents (email, status, version, decided_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET status=excluded.status, version=excluded.version,
                decided_at=excluded.decided_at
        ''', (email, status, version, decided_at))
        conn.commit()
        conn.close()


def get_consent(email):
    """{'status', 'version', 'decided_at'} or None if the user hasn't decided yet."""
    email = (email or "").strip().lower()
    if USE_MONGO:
        return db.get_collection("consents").find_one({"email": email}, {"_id": 0})
    conn = get_db_connection()
    row = conn.execute("SELECT status, version, decided_at FROM consents WHERE email = ?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_user_consents():
    """Every signed-in user (and anyone who decided), with their consent status."""
    if USE_MONGO:
        users = {(u.get("email") or "").lower(): u for u in db.get_collection("users").find({}, {"_id": 0})}
        cons = {(c.get("email") or "").lower(): c for c in db.get_collection("consents").find({}, {"_id": 0})}
    else:
        conn = get_db_connection()
        users = {(r["email"] or "").lower(): dict(r) for r in conn.execute("SELECT email, last_login FROM users").fetchall()}
        cons = {(r["email"] or "").lower(): dict(r) for r in conn.execute("SELECT email, status, decided_at FROM consents").fetchall()}
        conn.close()
    out = []
    for email in sorted(set(users) | set(cons)):
        if not email:
            continue
        c = cons.get(email) or {}
        out.append({"email": email, "status": c.get("status") or "pending", "decided_at": c.get("decided_at"),
                    "last_login": (users.get(email) or {}).get("last_login")})
    return out


def save_screener_results(market, data):
    """Save screener scan results to database."""
    import json
    updated_at = datetime.now().strftime("%d %b %Y  %H:%M:%S")
    results_json = json.dumps(data)

    if USE_MONGO:
        screener_col = db.get_collection("screener_results")
        screener_col.update_one(
            {"market": market},
            {"$set": {"results_json": results_json, "updated_at": updated_at}},
            upsert=True
        )
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO screener_results (market, results_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(market) DO UPDATE SET
                results_json=excluded.results_json,
                updated_at=excluded.updated_at
        ''', (market, results_json, updated_at))
        conn.commit()
        conn.close()


def get_screener_results(market):
    """Get last saved screener results from database."""
    import json

    if USE_MONGO:
        screener_col = db.get_collection("screener_results")
        doc = screener_col.find_one({"market": market}, {"_id": 0})
        if doc:
            return json.loads(doc["results_json"]), doc["updated_at"]
        return None, None
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT results_json, updated_at FROM screener_results WHERE market = ?', (market,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return json.loads(row["results_json"]), row["updated_at"]
        return None, None


def save_chartink_scanner(user_id, url, name=""):
    """Upsert a saved Chartink scanner URL for a user."""
    added_at = datetime.now().strftime("%d %b %Y  %H:%M")
    if USE_MONGO:
        db.get_collection("chartink_scanners").update_one(
            {"user_id": user_id, "url": url},
            {"$set": {"name": name, "added_at": added_at}},
            upsert=True)
    else:
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO chartink_scanners (user_id, url, name, added_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, url) DO UPDATE SET name=excluded.name
        """, (user_id, url, name, added_at))
        conn.commit()
        conn.close()


def get_chartink_scanners(user_id):
    if USE_MONGO:
        return list(db.get_collection("chartink_scanners")
                    .find({"user_id": user_id}, {"_id": 0, "user_id": 0})
                    .sort("added_at", -1))
    conn = get_db_connection()
    cur = conn.execute(
        "SELECT url, name, added_at FROM chartink_scanners WHERE user_id = ? ORDER BY added_at DESC",
        (user_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_chartink_scanners_by_users(user_ids):
    """Scanners saved by any of these users (e.g. the admins), one row per URL, newest first."""
    ids = sorted({u for u in (user_ids or []) if u})
    if not ids:
        return []
    if USE_MONGO:
        rows = list(db.get_collection("chartink_scanners")
                    .find({"user_id": {"$in": ids}}, {"_id": 0, "user_id": 0})
                    .sort("added_at", -1))
    else:
        conn = get_db_connection()
        cur = conn.execute(
            "SELECT url, name, added_at FROM chartink_scanners WHERE user_id IN (%s) ORDER BY added_at DESC"
            % ",".join("?" * len(ids)), ids)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    seen, out = set(), []
    for r in rows:
        if r.get("url") and r["url"] not in seen:
            seen.add(r["url"])
            out.append(r)
    return out


def delete_chartink_scanner_for_users(user_ids, url):
    """Remove a URL from every listed user's saved scanners (admin removing a shared scanner)."""
    ids = sorted({u for u in (user_ids or []) if u})
    if not ids:
        return
    if USE_MONGO:
        db.get_collection("chartink_scanners").delete_many({"user_id": {"$in": ids}, "url": url})
    else:
        conn = get_db_connection()
        conn.execute("DELETE FROM chartink_scanners WHERE url = ? AND user_id IN (%s)"
                     % ",".join("?" * len(ids)), [url] + ids)
        conn.commit()
        conn.close()


def delete_chartink_scanner(user_id, url):
    if USE_MONGO:
        db.get_collection("chartink_scanners").delete_one({"user_id": user_id, "url": url})
    else:
        conn = get_db_connection()
        conn.execute("DELETE FROM chartink_scanners WHERE user_id = ? AND url = ?", (user_id, url))
        conn.commit()
        conn.close()


def record_alert_action(user_id, alert_id, action, acted_date, snooze_until=None):
    """Record a user's Read/Snooze action on a dividend ex-date alert."""
    if USE_MONGO:
        db.get_collection("alert_actions").update_one(
            {"user_id": user_id, "alert_id": alert_id},
            {"$set": {"action": action, "acted_date": acted_date,
                      "snooze_until": snooze_until}},
            upsert=True)
    else:
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO alert_actions (user_id, alert_id, action, acted_date, snooze_until)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, alert_id) DO UPDATE SET
                action=excluded.action, acted_date=excluded.acted_date,
                snooze_until=excluded.snooze_until
        """, (user_id, alert_id, action, acted_date, snooze_until))
        conn.commit()
        conn.close()


def get_alert_actions(user_id):
    """Return {alert_id: {action, acted_date, snooze_until}} for a user."""
    if USE_MONGO:
        rows = db.get_collection("alert_actions").find({"user_id": user_id}, {"_id": 0})
        return {r["alert_id"]: r for r in rows}
    conn = get_db_connection()
    cur = conn.execute(
        "SELECT alert_id, action, acted_date, snooze_until FROM alert_actions WHERE user_id = ?",
        (user_id,))
    out = {r["alert_id"]: dict(r) for r in cur.fetchall()}
    conn.close()
    return out


# ══════════════════════════════════════════════════════════════
#  SITE USAGE (admin Site Usage page)
# ══════════════════════════════════════════════════════════════

def record_visit(day, visitor, vid, signed_in, page, seen_at):
    """Count one page view: per-visitor-per-day row + per-page-per-day row."""
    if USE_MONGO:
        db.get_collection("usage_daily").update_one(
            {"day": day, "visitor": visitor},
            {"$inc": {"views": 1}, "$set": {"last_seen": seen_at, "vid": vid},
             "$max": {"signed_in": 1 if signed_in else 0}},
            upsert=True)
        db.get_collection("usage_pages").update_one(
            {"day": day, "page": page}, {"$inc": {"views": 1}}, upsert=True)
        return
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO usage_daily (day, visitor, vid, signed_in, views, last_seen) VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(day, visitor) DO UPDATE SET views = views + 1, last_seen = excluded.last_seen,
            vid = excluded.vid, signed_in = MAX(signed_in, excluded.signed_in)
    """, (day, visitor, vid, 1 if signed_in else 0, seen_at))
    conn.execute("""
        INSERT INTO usage_pages (day, page, views) VALUES (?, ?, 1)
        ON CONFLICT(day, page) DO UPDATE SET views = views + 1
    """, (day, page))
    conn.commit()
    conn.close()


def get_usage_rows(since_day):
    """(visitor rows, page rows) with day >= since_day ('YYYY-MM-DD')."""
    if USE_MONGO:
        v = list(db.get_collection("usage_daily").find({"day": {"$gte": since_day}}, {"_id": 0}))
        p = list(db.get_collection("usage_pages").find({"day": {"$gte": since_day}}, {"_id": 0}))
        return v, p
    conn = get_db_connection()
    v = [dict(r) for r in conn.execute(
        "SELECT day, visitor, vid, signed_in, views, last_seen FROM usage_daily WHERE day >= ?", (since_day,))]
    p = [dict(r) for r in conn.execute("SELECT day, page, views FROM usage_pages WHERE day >= ?", (since_day,))]
    conn.close()
    return v, p
