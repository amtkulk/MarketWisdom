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
