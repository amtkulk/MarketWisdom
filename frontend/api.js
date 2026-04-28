/*
 * API Client Configuration
 * Handles all requests to the Flask Backend
 */

const API_BASE_URL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' ? 'http://localhost:5000/api' : '/api';

const api = {
    async fetchStock(company, ticker) {
        const res = await fetch(`${API_BASE_URL}/stock`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ company, ticker })
        });
        if (!res.ok) {
            let err;
            try { err = await res.json(); } catch(e) { throw new Error(`Server returned HTML or invalid JSON (Status: ${res.status}). The task likely timed out on the cloud.`); }
            throw new Error(err.error || 'Failed to fetch stock data');
        }
        return res.json();
    },

    async fetchStockAction(company, ticker) {
        const res = await fetch(`${API_BASE_URL}/stock_action`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ company, ticker })
        });
        if (!res.ok) {
            let err;
            try { err = await res.json(); } catch(e) { throw new Error(`Server returned HTML or invalid JSON (Status: ${res.status}). The task likely timed out.`); }
            throw new Error(err.error || 'Failed to fetch stock action');
        }
        return res.json();
    },

    async fetchNifty() {
        const res = await fetch(`${API_BASE_URL}/nifty`);
        if (!res.ok) {
            let err;
            try { err = await res.json(); } catch(e) { throw new Error(`Server returned HTML or invalid JSON (Status: ${res.status}).`); }
            throw new Error(err.error || 'Failed to fetch Nifty data');
        }
        return res.json();
    },

    async compareChartink(url1, label1, url2, label2) {
        const res = await fetch(`${API_BASE_URL}/chartink`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url1, label1, url2, label2 })
        });
        if (!res.ok) {
            let err;
            try { err = await res.json(); } catch(e) { throw new Error(`Server returned HTML or invalid JSON (Status: ${res.status}). The scrape likely timed out.`); }
            throw new Error(err.error || 'Failed to compare screeners');
        }
        return res.json();
    },

    async fetchWatchlist() {
        const res = await fetch(`${API_BASE_URL}/watchlist/data`);
        if (!res.ok) throw new Error('Failed to load watchlist');
        return res.json();
    },

    async rateStock(ticker, company_name, sector, price, rating) {
        const res = await fetch(`${API_BASE_URL}/rate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker, company_name, sector, price, rating })
        });
        return res.json();
    },

    async removeFromWatchlist(ticker) {
        const res = await fetch(`${API_BASE_URL}/watchlist/delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker })
        });
        return res.json();
    }
};
