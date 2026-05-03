/*
 * API Client Configuration
 * Handles all requests to the Flask Backend
 */

const API_BASE_URL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' ? 'http://localhost:5000/api' : '/api';

const Cache = {
    set(key, data, ttlMins) {
        try {
            const item = { data, expiry: Date.now() + ttlMins * 60000 };
            sessionStorage.setItem('mw_cache_' + key, JSON.stringify(item));
        } catch(e) { console.warn('Cache write failed', e); }
    },
    get(key) {
        try {
            const itemStr = sessionStorage.getItem('mw_cache_' + key);
            if (!itemStr) return null;
            const item = JSON.parse(itemStr);
            if (Date.now() > item.expiry) {
                sessionStorage.removeItem('mw_cache_' + key);
                return null;
            }
            return item.data;
        } catch(e) { return null; }
    }
};

const api = {
    async fetchStock(company, ticker) {
        const cacheKey = `stock_${company}_${ticker}`;
        const cached = Cache.get(cacheKey);
        if (cached) return cached;
        
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
        const data = await res.json();
        Cache.set(cacheKey, data, 60);
        return data;
    },

    async fetchStockAction(company, ticker) {
        const cacheKey = `action_${company}_${ticker}`;
        const cached = Cache.get(cacheKey);
        if (cached) return cached;

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
        const data = await res.json();
        Cache.set(cacheKey, data, 60);
        return data;
    },

    async fetchNifty() {
        const cached = Cache.get('nifty');
        if (cached) return cached;

        const res = await fetch(`${API_BASE_URL}/nifty`);
        if (!res.ok) {
            let err;
            try { err = await res.json(); } catch(e) { throw new Error(`Server returned HTML or invalid JSON (Status: ${res.status}).`); }
            throw new Error(err.error || 'Failed to fetch Nifty data');
        }
        const data = await res.json();
        Cache.set('nifty', data, 15);
        return data;
    },

    async fetchOptionChain(symbol = 'NIFTY') {
        const cacheKey = `nse_oc_${symbol}`;
        const cached = Cache.get(cacheKey);
        if (cached) return cached;

        const res = await fetch(`${API_BASE_URL}/nse/option_chain?symbol=${symbol}`);
        if (!res.ok) {
            let err;
            try { err = await res.json(); } catch(e) { throw new Error(`Server returned HTML or invalid JSON (Status: ${res.status}).`); }
            throw new Error(err.error || 'Failed to fetch Option Chain data');
        }
        const data = await res.json();
        // Option chain changes rapidly. Cache for 2 mins to prevent spam but stay relatively live.
        Cache.set(cacheKey, data, 2);
        return data;
    },

    async compareChartink(url1, label1, url2, label2) {
        const cacheKey = `chartink_${btoa(url1 + url2)}`;
        const cached = Cache.get(cacheKey);
        if (cached) return cached;

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
        const data = await res.json();
        Cache.set(cacheKey, data, 15);
        return data;
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
    },

    async fetchGlobalMarket() {
        const cached = Cache.get('global_market');
        if (cached) return cached;

        const res = await fetch(`${API_BASE_URL}/global_market`);
        if (!res.ok) {
            let err;
            try { err = await res.json(); } catch(e) { throw new Error(`Server returned HTML or invalid JSON (Status: ${res.status}).`); }
            throw new Error(err.error || 'Failed to fetch Global Market data');
        }
        const data = await res.json();
        Cache.set('global_market', data, 10);
        return data;
    },

    async startScreenerScan(market) {
        const res = await fetch(`${API_BASE_URL}/screener/start?market=${market}`, { method: 'POST' });
        if (!res.ok) {
            let err;
            try { err = await res.json(); } catch(e) { throw new Error(`Server error (Status: ${res.status}).`); }
            throw new Error(err.error || 'Failed to start scan');
        }
        return res.json();
    },

    async getScreenerStatus(market) {
        const res = await fetch(`${API_BASE_URL}/screener/status?market=${market}`);
        if (!res.ok) return { status: 'error' };
        return res.json();
    },

    async getScreenerResults(market) {
        const res = await fetch(`${API_BASE_URL}/screener/results?market=${market}`);
        if (!res.ok) {
            let err;
            try { err = await res.json(); } catch(e) { throw new Error(`Server error (Status: ${res.status}).`); }
            throw new Error(err.error || 'Failed to fetch results');
        }
        return res.json();
    }
};
