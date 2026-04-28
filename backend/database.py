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
