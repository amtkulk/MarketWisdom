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
        conn.commit()
        conn.close()

def add_or_update_stock(ticker, company_name, sector, price, rating):
    rated_at = datetime.now().strftime("%d %b %Y  %H:%M")
    
    if USE_MONGO:
        watchlist_col.update_one(
            {"ticker": ticker},
            {"$set": {
                "company_name": company_name,
                "sector": sector,
                "price": price,
                "rating": rating,
                "rated_at": rated_at
            }},
            upsert=True
        )
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO watchlist (ticker, company_name, sector, price, rating, rated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                company_name=excluded.company_name,
                sector=excluded.sector,
                price=excluded.price,
                rating=excluded.rating,
                rated_at=excluded.rated_at
        ''', (ticker, company_name, sector, price, rating, rated_at))
        conn.commit()
        conn.close()

def delete_stock(ticker):
    if USE_MONGO:
        watchlist_col.delete_one({"ticker": ticker})
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM watchlist WHERE ticker = ?', (ticker,))
        conn.commit()
        conn.close()

def get_all_stocks():
    if USE_MONGO:
        # Exclude _id to prevent JSON serialization errors
        return list(watchlist_col.find({}, {"_id": 0}))
    else:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM watchlist')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]


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
