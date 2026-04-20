import sqlite3
import os
from datetime import datetime

DATABASE_FILE = 'watchlist.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
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
    conn = get_db_connection()
    cursor = conn.cursor()
    rated_at = datetime.now().strftime("%d %b %Y  %H:%M")
    
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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM watchlist WHERE ticker = ?', (ticker,))
    conn.commit()
    conn.close()

def get_all_stocks():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM watchlist')
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]
