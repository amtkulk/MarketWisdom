/*
 * Paper Trading page (#paper). Practice accounts with fake money and live prices:
 *   Stocks ₹5 lakh (Delivery / Intraday incl. short selling), F&O ₹10 lakh (options and
 *   futures, strategy builder, live option chain). Admins approve fresh-start requests.
 * Server: /api/paper/* (backend/paper.py, prices from Angel One SmartAPI). No real orders.
 */
const Paper = {
    tab: 'EQ', timers: [], acct: {}, status: null, eqSym: '', eqQuote: null, product: 'CNC',
    und: 'NIFTY', expiry: null, nStrikes: 10, chain: null, basket: [], preview: null, unds: null,
    tpl: { template: 'iron_condor', lots: 1, gap: 3, width: 2 },

    // ── helpers ──
    e(t) { return String(t == null ? '' : t).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); },
    rs(x, d) { if (x === null || x === undefined || isNaN(x)) return '—'; const v = Number(x); return (v < 0 ? '−' : '') + '₹' + Math.abs(v).toLocaleString('en-IN', { minimumFractionDigits: d === 0 ? 0 : 2, maximumFractionDigits: d === 0 ? 0 : 2 }); },
    n(x, d) { if (x === null || x === undefined || isNaN(x)) return '—'; return Number(x).toLocaleString('en-IN', { maximumFractionDigits: d == null ? 2 : d }); },
    pnl(x, d) { if (x === null || x === undefined) return '—'; const c = x > 0 ? 'var(--green)' : x < 0 ? 'var(--red)' : 'var(--text-secondary)'; return '<span style="color:' + c + ';font-weight:700">' + (x > 0 ? '+' : '') + this.rs(x, d) + '</span>'; },
    pct(x) { if (x === null || x === undefined) return ''; const c = x > 0 ? 'var(--green)' : x < 0 ? 'var(--red)' : 'var(--text-secondary)'; return '<span style="color:' + c + '">' + (x > 0 ? '+' : '') + Number(x).toFixed(2) + '%</span>'; },
    lakh(x) { return x >= 100000 ? '₹' + (x / 100000).toFixed(x % 100000 ? 2 : 0) + ' L' : this.rs(x, 0); },
    isAdmin() { try { return !!(typeof Auth !== 'undefined' && Auth.user && Auth.user() && Auth.user().is_admin); } catch (e) { return false; } },
    async call(path, opt) {
        opt = opt || {};
        const headers = Object.assign({ 'Content-Type': 'application/json' }, (typeof Auth !== 'undefined' && Auth.headers) ? Auth.headers() : {});
        const res = await fetch(`${API_BASE_URL}/paper/${path}`, { method: opt.body ? 'POST' : 'GET', headers, body: opt.body ? JSON.stringify(opt.body) : undefined, cache: 'no-store' });
        let d = {}; try { d = await res.json(); } catch (e) {}
        if (!res.ok) { const err = new Error(d.error || ('Server error ' + res.status)); err.status = res.status; throw err; }
        return d;
    },
    charges(seg, product, side, qty, px, kind) {           // same formula as the server (estimate for the order pad)
        const t = Math.abs(qty) * px, buy = side === 'BUY';
        let brok, stt, exch, stamp;
        if (seg === 'EQ') {
            brok = Math.max(5, Math.min(20, 0.001 * t)); exch = 0.0000297 * t;
            if (product === 'CNC') { stt = 0.001 * t; stamp = buy ? 0.00015 * t : 0; } else { stt = buy ? 0 : 0.00025 * t; stamp = buy ? 0.00003 * t : 0; }
        } else {
            brok = 20;
            if (kind === 'FUT') { stt = buy ? 0 : 0.0005 * t; exch = 0.0000173 * t; stamp = buy ? 0.00002 * t : 0; }
            else { stt = buy ? 0 : 0.0015 * t; exch = 0.0003503 * t; stamp = buy ? 0.00003 * t : 0; }
        }
        const sebi = 0.000001 * t;
        return brok + stt + exch + stamp + sebi + 0.18 * (brok + exch + sebi);
    },
    every(ms, fn) { const id = setInterval(() => { if (!document.getElementById('pt-root')) { this.stop(); return; } if (!document.hidden) fn(); }, ms); this.timers.push(id); },
    stop() { this.timers.forEach(clearInterval); this.timers = []; },
    toast(msg, bad) {
        const el = document.getElementById('pt-toast'); if (!el) return;
        el.innerHTML = '<div class="card" style="padding:10px 14px;margin-bottom:12px;font-size:13px;font-weight:600;border-color:' + (bad ? 'var(--red);color:var(--red)' : 'rgba(22,163,74,0.35);color:var(--green);background:rgba(22,163,74,0.06)') + '">' + this.e(msg) + '</div>';
        clearTimeout(this._tt); this._tt = setTimeout(() => { el.innerHTML = ''; }, bad ? 9000 : 5000);
    },

    // ── page ──
    render(container, startTab) {
        this.stop();
        if (startTab) this.tab = startTab;
        container.innerHTML = `
            <div id="pt-root">
            <div style="margin-bottom:14px;display:flex;justify-content:space-between;align-items:flex-end;gap:10px;flex-wrap:wrap">
                <div>
                    <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">📝 Paper Trading</h2>
                    <p style="font-size:13px;color:var(--text-secondary);line-height:1.6;max-width:820px">Practise with fake money and real market prices. Stocks start with <b>₹5 lakh</b>, F&O with <b>₹10 lakh</b>. Nothing is sent to any broker.</p>
                </div>
                <div id="pt-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px" id="pt-tabs"></div>
            <div id="pt-toast"></div>
            <div id="pt-body"><div style="text-align:center;padding:40px"><span class="spinner"></span></div></div>
            <div style="margin-top:18px;font-size:11.5px;line-height:1.7;color:var(--text-secondary);text-align:center">Practice only: fake money, no real orders. Fills use the live best bid/ask; charges are estimated at Angel One brokerage plus 2026 STT, exchange, SEBI, stamp duty and GST (DP charges not included). F&O margin comes from Angel One's margin calculator for your whole position. Not investment advice.</div>
            </div>`;
        this.loadStatus().then(() => this.paintTabs());
        this.paintTabs();
        this.show();
    },

    async loadStatus() {
        try { this.status = await this.call('status'); } catch (e) { this.status = null; }
        const c = document.getElementById('pt-chips'); if (!c) return;
        const s = this.status || {};
        const chip = (txt, on) => '<span style="font-size:11.5px;font-weight:700;padding:4px 10px;border-radius:12px;background:' + (on ? 'rgba(22,163,74,0.10);color:var(--green)' : 'rgba(220,38,38,0.08);color:var(--red)') + '">' + txt + '</span>';
        c.innerHTML = chip(s.live ? '● Live prices (Angel One)' : '● Live prices not connected', !!s.live) + chip(s.market_open ? 'Market open' : 'Market closed', !!s.market_open);
    },

    paintTabs() {
        const el = document.getElementById('pt-tabs'); if (!el) return;
        const tabs = [['EQ', '📈 Stocks · ₹5 L'], ['FO', '🧮 F&O · ₹10 L']];
        if (this.isAdmin()) tabs.push(['ADMIN', '🛡️ Requests' + (this._pending ? ' (' + this._pending + ')' : '')]);
        el.innerHTML = tabs.map(([k, t]) => '<button data-t="' + k + '" style="padding:9px 16px;border-radius:18px;font-size:13px;font-weight:700;cursor:pointer;border:1px solid var(--border-color);background:' + (this.tab === k ? 'var(--text-accent);color:#fff' : 'var(--bg-card);color:var(--text-secondary)') + '">' + t + '</button>').join('');
        el.querySelectorAll('button').forEach(b => b.addEventListener('click', () => { this.tab = b.dataset.t; this.paintTabs(); this.show(); }));
        if (this.isAdmin() && this._pending === undefined) this.call('admin/requests').then(d => { this._pending = d.pending; if (d.pending) this.paintTabs(); }).catch(() => {});
    },

    show() {
        this.stop();
        const b = document.getElementById('pt-body'); if (!b) return;
        if (this.tab === 'ADMIN') return this.showAdmin(b);
        b.innerHTML = '<div id="pt-summary"></div>' + (this.tab === 'EQ' ? this.eqPadHtml() : this.foPadHtml()) + '<div id="pt-pos"></div><div id="pt-orders"></div>';
        if (this.tab === 'EQ') this.wireEq(); else this.wireFo();
        this.loadAccount();
        this.every(this.tab === 'EQ' ? 6000 : 8000, () => this.loadAccount(true));
    },

    async loadAccount(quiet) {
        const seg = this.tab;
        try {
            const d = await this.call('account?seg=' + seg);
            if (this.tab !== seg) return;
            this.acct[seg] = d; this.paintSummary(); this.paintPositions(); this.paintOrders();
        } catch (e) {
            if (!quiet) { const s = document.getElementById('pt-summary'); if (s) s.innerHTML = '<div class="card" style="color:var(--red)">' + this.e(e.message) + '</div>'; }
        }
    },

    paintSummary() {
        const d = this.acct[this.tab], el = document.getElementById('pt-summary'); if (!d || !el) return;
        const eq = this.tab === 'EQ';
        const tile = (label, val, sub) => '<div class="card" style="padding:12px 14px;margin:0"><div style="font-size:10.5px;color:var(--text-secondary);text-transform:uppercase;font-weight:700;letter-spacing:.03em">' + label + '</div><div style="font-size:19px;font-weight:800;color:var(--text-primary);margin-top:3px;font-variant-numeric:tabular-nums">' + val + '</div>' + (sub ? '<div style="font-size:11.5px;color:var(--text-secondary);margin-top:2px">' + sub + '</div>' : '') + '</div>';
        let h = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:12px">'
            + tile('Account value', this.rs(d.account_value), 'started with ' + this.lakh(d.capital) + (d.refills ? ' · fresh starts: ' + d.refills : ''))
            + tile('Available to trade', '<span style="color:' + (d.available < 0 ? 'var(--red)' : 'var(--text-primary)') + '">' + this.rs(d.available) + '</span>', eq ? 'cash ' + this.rs(d.cash) : 'cash ' + this.rs(d.cash))
            + tile('Total P&L', this.pnl(d.total_pnl), (d.total_pnl / d.capital * 100).toFixed(2) + '% · realised ' + this.rs(d.realized))
            + (eq ? tile('Holdings', this.rs(d.holdings_value), 'invested ' + this.rs(d.invested) + (d.blocked ? ' · intraday margin ' + this.rs(d.blocked) : ''))
                  : tile('Margin blocked', this.rs(d.blocked), this.e(d.margin_src && d.blocked ? d.margin_src : 'for sold options and futures')))
            + tile('Charges paid', this.rs(d.charges), 'brokerage + taxes')
            + '</div>';
        if (d.pending_request) {
            h += '<div class="card" style="padding:10px 14px;margin-bottom:12px;font-size:13px;border-left:4px solid var(--yellow);background:rgba(234,179,8,0.06)">⏳ Your request for a fresh ' + this.lakh(d.capital) + ' (sent ' + this.e(d.pending_request.at) + ') is waiting for admin approval.</div>';
        } else if (d.can_request) {
            h += '<div class="card" style="padding:10px 14px;margin-bottom:12px;font-size:13px;border-left:4px solid var(--red);background:rgba(220,38,38,0.05);display:flex;gap:12px;align-items:center;flex-wrap:wrap"><span style="flex:1;min-width:220px">Your available funds are below ' + this.rs(d.refill_below, 0) + '. You can ask the admin for a fresh start with ' + this.lakh(d.capital) + ' (open positions will be closed).</span><button id="pt-refill" class="btn" style="padding:8px 16px;font-size:13px">Request fresh ' + this.lakh(d.capital) + '</button></div>';
        }
        el.innerHTML = h;
        const r = document.getElementById('pt-refill');
        if (r) r.addEventListener('click', async () => {
            if (window.confirm && !window.confirm('Ask the admin for a fresh ' + this.lakh(d.capital) + '? When approved, your open positions are closed and the account starts again.')) return;
            try { await this.call('refill', { body: { seg: this.tab } }); this.toast('Request sent. You\'ll see the fresh balance once the admin approves it.'); this.loadAccount(); }
            catch (e) { this.toast(e.message, true); }
        });
    },

    // ── stocks ──
    eqPadHtml() {
        const inp = 'padding:9px 11px;border:1px solid var(--border-color);border-radius:8px;font-size:14px;background:var(--bg-card);color:var(--text-primary)';
        return `<div class="card" style="margin-bottom:12px">
            <div class="section-title" style="margin-bottom:10px">Place an order</div>
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
                <label style="font-size:12px;color:var(--text-secondary);display:grid;gap:4px;position:relative">NSE symbol
                    <input id="pt-sym" autocomplete="off" placeholder="e.g. HINDZINC" value="${this.e(this.eqSym)}" style="${inp};width:170px;text-transform:uppercase">
                    <div id="pt-sugg" style="position:absolute;top:100%;left:0;right:0;z-index:20"></div></label>
                <label style="font-size:12px;color:var(--text-secondary);display:grid;gap:4px">Quantity
                    <input id="pt-qty" type="number" min="1" step="1" inputmode="numeric" value="${this._qty || 1}" style="${inp};width:110px"></label>
                <div style="display:grid;gap:4px;font-size:12px;color:var(--text-secondary)">Product
                    <div style="display:flex;border:1px solid var(--border-color);border-radius:8px;overflow:hidden">
                        <button class="pt-prod" data-p="CNC" style="padding:9px 12px;border:none;cursor:pointer;font-weight:700;font-size:12.5px">Delivery</button>
                        <button class="pt-prod" data-p="MIS" style="padding:9px 12px;border:none;cursor:pointer;font-weight:700;font-size:12.5px">Intraday</button></div></div>
                <button id="pt-buy" style="padding:10px 22px;border-radius:8px;border:none;background:var(--green);color:#fff;font-weight:800;font-size:14px;cursor:pointer">BUY</button>
                <button id="pt-sell" style="padding:10px 22px;border-radius:8px;border:none;background:var(--red);color:#fff;font-weight:800;font-size:14px;cursor:pointer">SELL</button>
            </div>
            <div id="pt-quote" style="margin-top:10px;font-size:12.5px;color:var(--text-secondary)"></div>
            <div style="font-size:11.5px;color:var(--text-secondary);margin-top:6px;line-height:1.6"><b>Delivery</b>: buy with your cash, sell only shares you hold. <b>Intraday</b>: buy or short-sell on 20% margin (5×), squared off automatically at 3:20 PM.</div>
        </div>`;
    },

    wireEq() {
        const sym = document.getElementById('pt-sym'), qty = document.getElementById('pt-qty');
        const paintProd = () => document.querySelectorAll('.pt-prod').forEach(b => { const on = b.dataset.p === this.product; b.style.background = on ? 'var(--text-accent)' : 'var(--bg-card)'; b.style.color = on ? '#fff' : 'var(--text-secondary)'; });
        document.querySelectorAll('.pt-prod').forEach(b => b.addEventListener('click', () => { this.product = b.dataset.p; paintProd(); this.paintQuote(); }));
        paintProd();
        let st;
        sym.addEventListener('input', () => {
            clearTimeout(st);
            const v = sym.value.trim().toUpperCase();
            st = setTimeout(async () => {
                const box = document.getElementById('pt-sugg'); if (!box) return;
                if (v.length < 2) { box.innerHTML = ''; return; }
                try {
                    const d = await this.call('search?seg=EQ&q=' + encodeURIComponent(v));
                    box.innerHTML = (d.items || []).length ? '<div class="card" style="padding:4px;margin:2px 0 0;max-height:240px;overflow:auto">' + d.items.map(s => '<div class="pt-sg" data-s="' + this.e(s) + '" style="padding:6px 8px;cursor:pointer;font-size:13px;border-radius:6px">' + this.e(s) + '</div>').join('') + '</div>' : '';
                    box.querySelectorAll('.pt-sg').forEach(x => x.addEventListener('mousedown', () => { sym.value = x.dataset.s; box.innerHTML = ''; this.pickSymbol(x.dataset.s); }));
                } catch (e) { box.innerHTML = ''; }
            }, 250);
        });
        sym.addEventListener('change', () => this.pickSymbol(sym.value));
        sym.addEventListener('keydown', ev => {
            if (ev.key !== 'Enter') return;
            const b = document.getElementById('pt-sugg'), first = b && b.querySelector('.pt-sg');
            if (first && first.dataset.s.indexOf(sym.value.trim().toUpperCase()) === 0) sym.value = first.dataset.s;   // HIND → HINDZINC
            if (b) b.innerHTML = '';
            this.pickSymbol(sym.value);
        });
        sym.addEventListener('blur', () => setTimeout(() => { const b = document.getElementById('pt-sugg'); if (b) b.innerHTML = ''; }, 200));
        qty.addEventListener('input', () => { this._qty = parseInt(qty.value) || 1; this.paintQuote(); });
        document.getElementById('pt-buy').addEventListener('click', () => this.placeEq('BUY'));
        document.getElementById('pt-sell').addEventListener('click', () => this.placeEq('SELL'));
        if (this.eqSym) this.pickSymbol(this.eqSym);
        this.every(3000, () => { if (this.eqSym) this.refreshQuote(); });
    },

    async pickSymbol(s) {
        s = (s || '').trim().toUpperCase(); if (!s) return;
        this.eqSym = s; this.eqQuote = null; this.paintQuote(true);
        await this.refreshQuote();
    },
    async refreshQuote() {
        const s = this.eqSym;
        try { const q = await this.call('quote?symbol=' + encodeURIComponent(s)); if (s === this.eqSym) { this.eqQuote = q; this.paintQuote(); } }
        catch (e) { if (s === this.eqSym) { this.eqQuote = { error: e.message }; this.paintQuote(); } }
    },
    paintQuote(loading) {
        const el = document.getElementById('pt-quote'); if (!el) return;
        const q = this.eqQuote;
        if (loading || !q) { el.innerHTML = this.eqSym ? '<span class="spinner"></span> Getting the live price…' : ''; return; }
        if (q.error) { el.innerHTML = '<span style="color:var(--red)">' + this.e(q.error) + '</span>'; return; }
        const qty = parseInt((document.getElementById('pt-qty') || {}).value) || 1;
        const buyPx = q.ask || q.ltp, sellPx = q.bid || q.ltp;
        const chB = this.charges('EQ', this.product, 'BUY', qty, buyPx), val = qty * buyPx;
        const need = this.product === 'CNC' ? val + chB : val * 0.2 + chB;
        const av = (this.acct.EQ || {}).available;
        el.innerHTML = '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:baseline">'
            + '<span style="font-size:17px;font-weight:800;color:var(--text-primary)">' + this.e(q.symbol) + ' ' + this.rs(q.ltp) + '</span>' + this.pct(q.chg_pct)
            + (q.bid || q.ask ? '<span>bid ' + this.rs(q.bid) + ' · ask ' + this.rs(q.ask) + '</span>' : '')
            + '<span>Buy ' + this.n(qty, 0) + ' ≈ ' + this.rs(val) + ' + ' + this.rs(chB) + ' charges → needs <b style="color:var(--text-primary)">' + this.rs(need) + '</b>' + (this.product === 'MIS' ? ' (20% margin)' : '') + '</span>'
            + (av !== undefined ? '<span>available ' + this.rs(av) + (need > av ? ' <b style="color:var(--red)">· not enough</b>' : '') + '</span>' : '')
            + '<span style="font-size:11px">' + this.e(q.source || '') + '</span></div>';
    },

    async placeEq(side) {
        const qty = parseInt(document.getElementById('pt-qty').value);
        const sym = (document.getElementById('pt-sym').value || '').trim().toUpperCase();
        if (!sym) return this.toast('Type an NSE symbol first, e.g. HINDZINC.', true);
        try {
            const d = await this.call('order', { body: { seg: 'EQ', symbol: sym, side, qty, product: this.product } });
            const o = d.order;
            this.toast((side === 'BUY' ? 'Bought ' : 'Sold ') + o.qty + ' ' + o.symbol + ' at ' + this.rs(o.price) + ' (' + (o.product === 'CNC' ? 'Delivery' : 'Intraday') + ') · charges ' + this.rs(o.charges) + (o.realized ? ' · P&L ' + this.rs(o.realized) : ''));
            this.acct.EQ = d.account; this.paintSummary(); this.paintPositions(); this.paintOrders(); this.paintQuote();
        } catch (e) { this.toast(e.message, true); }
    },

    // ── F&O ──
    foPadHtml() {
        const sel = 'padding:8px 10px;border:1px solid var(--border-color);border-radius:8px;font-size:13px;background:var(--bg-card);color:var(--text-primary)';
        const T = { iron_condor: 'Iron condor', short_straddle: 'Short straddle', long_straddle: 'Long straddle', short_strangle: 'Short strangle', long_strangle: 'Long strangle', bull_call_spread: 'Bull call spread', bear_put_spread: 'Bear put spread' };
        return `<div class="card" style="margin-bottom:12px;padding:12px 14px">
                <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">
                    <label style="font-size:12px;color:var(--text-secondary);display:grid;gap:4px">Underlying <select id="pt-und" style="${sel};min-width:140px"><option>${this.e(this.und)}</option></select></label>
                    <label style="font-size:12px;color:var(--text-secondary);display:grid;gap:4px">Expiry <select id="pt-exp" style="${sel};min-width:130px"></select></label>
                    <label style="font-size:12px;color:var(--text-secondary);display:grid;gap:4px">Strikes <select id="pt-n" style="${sel}">${[6, 10, 15, 20].map(k => '<option value="' + k + '"' + (k === this.nStrikes ? ' selected' : '') + '>±' + k + '</option>').join('')}</select></label>
                    <div id="pt-spot" style="flex:1;min-width:220px;font-size:13px;color:var(--text-secondary)"></div>
                </div>
                <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-top:10px;padding-top:10px;border-top:1px solid rgba(15,23,42,0.06)">
                    <b style="font-size:12.5px;color:var(--text-primary);align-self:center">Strategy builder</b>
                    <label style="font-size:12px;color:var(--text-secondary);display:grid;gap:4px">Strategy <select id="pt-tpl" style="${sel}">${Object.keys(T).map(k => '<option value="' + k + '"' + (k === this.tpl.template ? ' selected' : '') + '>' + T[k] + '</option>').join('')}</select></label>
                    <label style="font-size:12px;color:var(--text-secondary);display:grid;gap:4px">Lots <input id="pt-lots" type="number" min="1" value="${this.tpl.lots}" style="${sel};width:70px"></label>
                    <label style="font-size:12px;color:var(--text-secondary);display:grid;gap:4px" title="How many strikes away from ATM the sold (or OTM) legs are">Strikes from ATM <input id="pt-gap" type="number" min="0" value="${this.tpl.gap}" style="${sel};width:70px"></label>
                    <label style="font-size:12px;color:var(--text-secondary);display:grid;gap:4px" title="Distance between the sold leg and its protective wing">Wing width <input id="pt-width" type="number" min="1" value="${this.tpl.width}" style="${sel};width:70px"></label>
                    <button id="pt-build" class="btn" style="padding:9px 16px;font-size:13px">Build →</button>
                </div>
            </div>
            <div id="pt-basket"></div>
            <div id="pt-chain" class="card" style="padding:0;margin-bottom:12px;overflow:hidden"><div style="text-align:center;padding:30px"><span class="spinner"></span></div></div>`;
    },

    async wireFo() {
        const und = document.getElementById('pt-und'), exp = document.getElementById('pt-exp'), n = document.getElementById('pt-n');
        und.addEventListener('change', () => { this.und = und.value; this.expiry = null; this.loadChain(); });
        exp.addEventListener('change', () => { this.expiry = exp.value; this.loadChain(); });
        n.addEventListener('change', () => { this.nStrikes = parseInt(n.value); this.loadChain(); });
        ['tpl', 'lots', 'gap', 'width'].forEach(k => document.getElementById('pt-' + k).addEventListener('change', ev => { const key = k === 'tpl' ? 'template' : k; this.tpl[key] = k === 'tpl' ? ev.target.value : (parseInt(ev.target.value) || (k === 'gap' ? 0 : 1)); }));
        document.getElementById('pt-build').addEventListener('click', () => this.buildStrategy());
        this.paintBasket();
        try {
            if (!this.unds) this.unds = (await this.call('underlyings')).items;
            const idx = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY'];
            und.innerHTML = '<optgroup label="Indices">' + this.unds.filter(u => idx.includes(u)).map(u => '<option' + (u === this.und ? ' selected' : '') + '>' + u + '</option>').join('') + '</optgroup>'
                + '<optgroup label="Stocks">' + this.unds.filter(u => !idx.includes(u)).map(u => '<option' + (u === this.und ? ' selected' : '') + '>' + this.e(u) + '</option>').join('') + '</optgroup>';
        } catch (e) {
            document.getElementById('pt-chain').innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-secondary)">' + this.e(e.message) + (e.status === 503 ? '<div style="margin-top:6px;font-size:12px">F&O needs live prices from Angel One. Ask the admin to connect it.</div>' : '') + '</div>';
            return;
        }
        this.loadChain();
        this.every(4000, () => this.loadChain(true));
    },

    async loadChain(quiet) {
        const box = document.getElementById('pt-chain'); if (!box) return;
        if (!quiet) box.innerHTML = '<div style="text-align:center;padding:30px"><span class="spinner"></span></div>';
        if (this._chainBusy) return; this._chainBusy = true;
        try {
            const d = await this.call('chain?und=' + encodeURIComponent(this.und) + (this.expiry ? '&expiry=' + this.expiry : '') + '&n=' + this.nStrikes);
            this.chain = d; this.expiry = d.expiry;
            const exp = document.getElementById('pt-exp');
            if (exp && exp.options.length !== d.expiries.length) exp.innerHTML = d.expiries.map(x => '<option value="' + x + '"' + (x === d.expiry ? ' selected' : '') + '>' + this.dlab(x) + '</option>').join('');
            this.paintChain();
        } catch (e) {
            if (!quiet) box.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-secondary)">' + this.e(e.message) + '</div>';
        } finally { this._chainBusy = false; }
    },
    dlab(iso) { try { return new Date(iso + 'T00:00:00').toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' }); } catch (e) { return iso; } },

    paintChain() {
        const d = this.chain, box = document.getElementById('pt-chain'); if (!d || !box) return;
        const sp = document.getElementById('pt-spot');
        if (sp) sp.innerHTML = '<b style="font-size:16px;color:var(--text-primary)">' + this.e(d.und) + ' ' + this.n(d.spot) + '</b> ' + this.pct(d.spot_chg_pct)
            + (d.spot_from_future ? ' <span style="font-size:11px">(from the future)</span>' : '') + ' · lot ' + this.n(d.lot, 0)
            + (d.future ? ' · Fut ' + this.dlab(d.future.expiry) + ' ' + this.n(d.future.ltp) + ' <button class="pt-fut" data-s="BUY" style="font-size:11px;padding:2px 8px;border-radius:6px;border:1px solid var(--green);color:var(--green);background:none;cursor:pointer;font-weight:700">B</button> <button class="pt-fut" data-s="SELL" style="font-size:11px;padding:2px 8px;border-radius:6px;border:1px solid var(--red);color:var(--red);background:none;cursor:pointer;font-weight:700">S</button>' : '')
            + ' · <span style="font-size:11px">' + (d.market_open ? 'live, ' : 'market closed, ') + 'updated ' + this.e(d.at) + '</span>';
        if (sp) sp.querySelectorAll('.pt-fut').forEach(b => b.addEventListener('click', () => this.addLeg({ token: d.future.token, tsym: d.future.tsym, kind: 'FUT', strike: null, lot: d.future.lot, ltp: d.future.ltp }, b.dataset.s)));
        const th = 'padding:7px 6px;font-size:10.5px;color:var(--text-secondary);text-transform:uppercase;white-space:nowrap';
        const bs = (side, c) => c ? '<button class="pt-add" data-t="' + c.token + '" data-s="' + side + '" style="font-size:10.5px;padding:2px 7px;border-radius:5px;border:1px solid ' + (side === 'BUY' ? 'var(--green);color:var(--green)' : 'var(--red);color:var(--red)') + ';background:none;cursor:pointer;font-weight:800">' + side[0] + '</button>' : '';
        let h = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12.5px;min-width:620px;font-variant-numeric:tabular-nums"><thead><tr style="background:rgba(15,23,42,0.03)">'
            + '<th style="' + th + ';text-align:right">OI</th><th style="' + th + ';text-align:right">Chg%</th><th style="' + th + ';text-align:right">Call LTP</th><th style="' + th + '"></th>'
            + '<th style="' + th + ';text-align:center;color:var(--text-primary)">Strike</th><th style="' + th + '"></th>'
            + '<th style="' + th + ';text-align:left">Put LTP</th><th style="' + th + ';text-align:left">Chg%</th><th style="' + th + ';text-align:left">OI</th></tr></thead><tbody>';
        d.rows.forEach(r => {
            const itmC = d.spot && r.strike < d.spot, itmP = d.spot && r.strike > d.spot;
            const td = 'padding:6px;border-top:1px solid rgba(15,23,42,0.05)';
            const C = r.CE || {}, P = r.PE || {};
            h += '<tr style="' + (r.atm ? 'outline:2px solid rgba(79,70,229,0.35);outline-offset:-2px' : '') + '">'
               + '<td style="' + td + ';text-align:right;color:var(--text-secondary);' + (itmC ? 'background:rgba(234,179,8,0.07)' : '') + '">' + this.n(C.oi, 0) + '</td>'
               + '<td style="' + td + ';text-align:right;' + (itmC ? 'background:rgba(234,179,8,0.07)' : '') + '">' + this.pct(C.chg_pct) + '</td>'
               + '<td style="' + td + ';text-align:right;font-weight:700;' + (itmC ? 'background:rgba(234,179,8,0.07)' : '') + '">' + this.n(C.ltp) + '</td>'
               + '<td style="' + td + ';white-space:nowrap;text-align:right">' + bs('BUY', r.CE) + ' ' + bs('SELL', r.CE) + '</td>'
               + '<td style="' + td + ';text-align:center;font-weight:800;color:var(--text-primary);background:rgba(79,70,229,0.05)">' + this.n(r.strike) + (r.atm ? ' <span style="font-size:9.5px;color:var(--text-accent)">ATM</span>' : '') + '</td>'
               + '<td style="' + td + ';white-space:nowrap">' + bs('BUY', r.PE) + ' ' + bs('SELL', r.PE) + '</td>'
               + '<td style="' + td + ';font-weight:700;' + (itmP ? 'background:rgba(234,179,8,0.07)' : '') + '">' + this.n(P.ltp) + '</td>'
               + '<td style="' + td + ';' + (itmP ? 'background:rgba(234,179,8,0.07)' : '') + '">' + this.pct(P.chg_pct) + '</td>'
               + '<td style="' + td + ';color:var(--text-secondary);' + (itmP ? 'background:rgba(234,179,8,0.07)' : '') + '">' + this.n(P.oi, 0) + '</td></tr>';
        });
        h += '</tbody></table></div><div style="font-size:11px;color:var(--text-secondary);padding:8px 12px">B = buy, S = sell: adds the option to your basket below. Shaded = in the money. Prices refresh every few seconds.</div>';
        box.innerHTML = h;
        const look = {};
        d.rows.forEach(r => ['CE', 'PE'].forEach(t => { if (r[t]) look[r[t].token] = Object.assign({ kind: t, strike: r.strike }, r[t]); }));
        box.querySelectorAll('.pt-add').forEach(b => b.addEventListener('click', () => this.addLeg(look[b.dataset.t], b.dataset.s)));
        if (this.basket.length) this.paintBasket();
    },

    addLeg(c, side) {
        if (!c) return;
        const ex = this.basket.find(l => l.token === c.token && l.side === side);
        if (ex) ex.lots += 1;
        else { if (this.basket.length >= 8) return this.toast('A basket can have at most 8 legs.', true); this.basket.push({ token: c.token, tsym: c.tsym, kind: c.kind, strike: c.strike, lot: c.lot, side, lots: 1, ltp: c.ltp }); }
        this.preview = null; this.strategyName = null; this.paintBasket(); this.runPreview();
    },

    async buildStrategy() {
        try {
            const d = await this.call('strategy', { body: Object.assign({ und: this.und, expiry: this.expiry }, this.tpl) });
            this.basket = d.legs.map(l => ({ token: l.token, tsym: l.tsym, kind: l.kind, strike: l.strike, lot: l.lot, side: l.side, lots: l.lots, ltp: l.ltp }));
            this.preview = d; this.strategyName = d.name; this.paintBasket();
            const b = document.getElementById('pt-basket'); if (b) b.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } catch (e) { this.toast(e.message, true); }
    },

    async runPreview() {
        if (!this.basket.length) { this.preview = null; this.paintBasket(); return; }
        const legs = this.basket.map(l => ({ token: l.token, side: l.side, lots: l.lots }));
        try { const d = await this.call('strategy', { body: { legs, und: this.und } }); this.preview = d; this.paintBasket(); }
        catch (e) { this.preview = { error: e.message }; this.paintBasket(); }
    },

    paintBasket() {
        const el = document.getElementById('pt-basket'); if (!el) return;
        if (!this.basket.length) { el.innerHTML = ''; return; }
        const pv = this.preview, look = {};
        ((pv && pv.legs) || []).forEach(l => { look[l.token + l.side] = l; });
        let h = '<div class="card" style="margin-bottom:12px;border-top:3px solid var(--text-accent)"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px"><b style="font-size:14px;color:var(--text-primary)">🧺 Basket' + (this.strategyName ? ' · ' + this.e(this.strategyName) : '') + '</b><button id="pt-clear" style="background:none;border:none;color:var(--text-secondary);cursor:pointer;font-size:12px">Clear</button></div>';
        h += '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12.5px;min-width:340px"><tbody>';
        this.basket.forEach((l, i) => {
            const p = look[l.token + l.side];
            h += '<tr style="border-top:1px solid rgba(15,23,42,0.05)"><td style="padding:6px"><span style="font-weight:800;color:' + (l.side === 'BUY' ? 'var(--green)' : 'var(--red)') + '">' + l.side + '</span></td>'
               + '<td style="padding:6px;font-weight:700;overflow-wrap:anywhere">' + this.e(l.tsym) + '</td>'
               + '<td style="padding:6px;white-space:nowrap">lots <input class="pt-lot" data-i="' + i + '" type="number" min="1" value="' + l.lots + '" style="width:58px;padding:4px 6px;border:1px solid var(--border-color);border-radius:6px;background:var(--bg-card);color:var(--text-primary)"> × ' + this.n(l.lot, 0) + '</td>'
               + '<td style="padding:6px;text-align:right">' + (p ? '@ ' + this.n(p.price) : '') + '</td>'
               + '<td style="padding:6px;text-align:right"><button class="pt-rm" data-i="' + i + '" style="background:none;border:none;color:var(--red);cursor:pointer;font-size:14px">✕</button></td></tr>';
        });
        h += '</tbody></table></div>';
        if (pv && pv.error) h += '<div style="color:var(--red);font-size:12.5px;margin-top:8px">' + this.e(pv.error) + '</div>';
        else if (pv && pv.payoff) {
            const po = pv.payoff, av = (this.acct.FO || {}).available;
            const box = (l, v, s) => '<div style="min-width:120px"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase">' + l + '</div><div style="font-weight:800;font-size:14px">' + v + '</div>' + (s ? '<div style="font-size:10.5px;color:var(--text-secondary)">' + s + '</div>' : '') + '</div>';
            h += '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:10px;padding:10px 12px;border-radius:10px;background:rgba(15,23,42,0.03)">'
               + box(pv.net_premium >= 0 ? 'Premium received' : 'Premium paid', this.rs(Math.abs(pv.net_premium)))
               + box('Margin needed', this.rs(pv.margin), this.e(pv.margin_src === 'none' ? 'no margin (buying only)' : pv.margin_src))
               + box('Max profit', po.max_profit === null ? '<span style="color:var(--green)">Unlimited</span>' : this.pnl(po.max_profit))
               + box('Max loss', po.max_loss === null ? '<span style="color:var(--red)">Unlimited</span>' : this.pnl(po.max_loss))
               + box('Breakeven' + (po.breakevens.length > 1 ? 's' : ''), po.breakevens.length ? po.breakevens.map(x => this.n(x, 0)).join(' / ') : '—')
               + box('Charges', this.rs(pv.charges), 'entry only')
               + (av !== undefined ? box('Available', this.rs(av), (pv.margin + Math.max(0, -pv.net_premium) > av) ? '<b style="color:var(--red)">not enough</b>' : '') : '')
               + '</div><div id="pt-payoff" style="margin-top:8px"></div>';
        } else h += '<div style="font-size:12px;color:var(--text-secondary);margin-top:8px"><span class="spinner"></span> Working out margin and payoff…</div>';
        h += '<div style="display:flex;gap:10px;margin-top:10px;flex-wrap:wrap"><button id="pt-place" class="btn" style="padding:10px 22px;font-size:14px;font-weight:800">Place basket (' + this.basket.length + ' leg' + (this.basket.length > 1 ? 's' : '') + ')</button><span style="font-size:11.5px;color:var(--text-secondary);align-self:center">All legs fill together at the live bid/ask, or none do.</span></div></div>';
        el.innerHTML = h;
        if (pv && pv.payoff) this.drawPayoff(document.getElementById('pt-payoff'), pv.payoff, pv.spot);
        document.getElementById('pt-clear').addEventListener('click', () => { this.basket = []; this.preview = null; this.strategyName = null; this.paintBasket(); });
        el.querySelectorAll('.pt-rm').forEach(b => b.addEventListener('click', () => { this.basket.splice(+b.dataset.i, 1); this.preview = null; this.paintBasket(); this.runPreview(); }));
        el.querySelectorAll('.pt-lot').forEach(b => b.addEventListener('change', () => { this.basket[+b.dataset.i].lots = Math.max(1, parseInt(b.value) || 1); this.preview = null; this.paintBasket(); this.runPreview(); }));
        document.getElementById('pt-place').addEventListener('click', () => this.placeBasket());
    },

    drawPayoff(host, po, spot) {
        if (!host || !po.curve || po.curve.length < 3) return;
        const W = Math.max(280, host.clientWidth || 600), H = 150, m = { l: 56, r: 10, t: 8, b: 20 };
        const xs = po.curve.map(p => p[0]), ys = po.curve.map(p => p[1]);
        const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(0, ...ys), y1 = Math.max(0, ...ys) || 1;
        const X = v => m.l + (v - x0) / (x1 - x0) * (W - m.l - m.r), Y = v => m.t + (y1 - v) / ((y1 - y0) || 1) * (H - m.t - m.b);
        let path = ''; po.curve.forEach((p, i) => { path += (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ',' + Y(p[1]).toFixed(1); });
        const zero = Y(0);
        const area = path + 'L' + X(x1) + ',' + zero + 'L' + X(x0) + ',' + zero + 'Z';
        const lab = v => (Math.abs(v) >= 100000 ? (v / 100000).toFixed(1) + 'L' : Math.abs(v) >= 1000 ? Math.round(v / 1000) + 'k' : Math.round(v));
        host.innerHTML = '<svg width="' + W + '" height="' + H + '" style="display:block;max-width:100%" role="img" aria-label="Payoff at expiry">'
            + '<defs><clipPath id="pt-up"><rect x="0" y="0" width="' + W + '" height="' + zero + '"/></clipPath><clipPath id="pt-dn"><rect x="0" y="' + zero + '" width="' + W + '" height="' + (H - zero) + '"/></clipPath></defs>'
            + '<path d="' + area + '" fill="rgba(22,163,74,0.18)" clip-path="url(#pt-up)"/><path d="' + area + '" fill="rgba(220,38,38,0.16)" clip-path="url(#pt-dn)"/>'
            + '<line x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + zero + '" y2="' + zero + '" stroke="#94a3b8"/>'
            + '<path d="' + path + '" fill="none" stroke="var(--text-accent)" stroke-width="2"/>'
            + (spot ? '<line x1="' + X(spot) + '" x2="' + X(spot) + '" y1="' + m.t + '" y2="' + (H - m.b) + '" stroke="#64748b" stroke-dasharray="3 3"/><text x="' + X(spot) + '" y="' + (H - 5) + '" font-size="10" fill="#64748b" text-anchor="middle">spot ' + Math.round(spot) + '</text>' : '')
            + '<text x="' + (m.l - 4) + '" y="' + (Y(y1) + 10) + '" font-size="10" fill="#16a34a" text-anchor="end">' + lab(y1) + '</text><text x="' + (m.l - 4) + '" y="' + Y(y0) + '" font-size="10" fill="#dc2626" text-anchor="end">' + lab(y0) + '</text>'
            + '<text x="' + m.l + '" y="' + (H - 5) + '" font-size="10" fill="#64748b">' + Math.round(x0) + '</text><text x="' + (W - m.r) + '" y="' + (H - 5) + '" font-size="10" fill="#64748b" text-anchor="end">' + Math.round(x1) + '</text></svg>'
            + '<div style="font-size:11px;color:var(--text-secondary)">Profit or loss at expiry for each closing price of ' + this.e(this.und) + ' (before charges).</div>';
    },

    async placeBasket() {
        if (!this.basket.length) return;
        const legs = this.basket.map(l => ({ token: l.token, side: l.side, lots: l.lots }));
        const btn = document.getElementById('pt-place'); if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Placing…'; }
        try {
            const d = await this.call('order', { body: { seg: 'FO', legs, strategy: this.strategyName || (legs.length > 1 ? 'Custom basket' : null) } });
            const o = d.order;
            this.toast('Filled ' + o.legs.length + ' leg' + (o.legs.length > 1 ? 's' : '') + ': ' + o.legs.map(l => l.side + ' ' + l.lots + '× ' + l.tsym + ' @ ' + this.n(l.price)).join(', ') + ' · charges ' + this.rs(o.charges) + ' · margin now ' + this.rs(o.margin_after));
            this.basket = []; this.preview = null; this.strategyName = null; this.paintBasket();
            this.acct.FO = d.account; this.paintSummary(); this.paintPositions(); this.paintOrders();
        } catch (e) { this.toast(e.message, true); if (btn) { btn.disabled = false; btn.innerHTML = 'Place basket'; } }
    },

    // ── positions + orders ──
    paintPositions() {
        const d = this.acct[this.tab], el = document.getElementById('pt-pos'); if (!d || !el) return;
        const eq = this.tab === 'EQ';
        if (!d.positions.length) { el.innerHTML = '<div class="card" style="margin-bottom:12px;text-align:center;color:var(--text-secondary);font-size:13px;padding:22px">No open positions yet.' + (eq ? ' Type a symbol above, e.g. HINDZINC, and press BUY.' : ' Pick B or S in the option chain, or build a strategy.') + '</div>'; return; }
        const th = 'padding:7px 8px;font-size:10.5px;color:var(--text-secondary);text-transform:uppercase;text-align:right;white-space:nowrap';
        const td = 'padding:7px 8px;text-align:right;border-top:1px solid rgba(15,23,42,0.05);white-space:nowrap;font-variant-numeric:tabular-nums';
        let h = '<div class="card" style="padding:0;margin-bottom:12px;overflow:hidden"><div style="padding:12px 14px 4px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px"><div class="section-title" style="margin:0">' + (eq ? 'Holdings & intraday positions' : 'Open F&O positions') + '</div>'
            + '<div style="font-size:12.5px">' + (eq ? 'Today ' + this.pnl(d.day_pnl) + ' · intraday ' + this.pnl(d.mis_unrealized) : 'Open P&L ' + this.pnl(d.unrealized)) + '</div></div><div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12.5px;min-width:640px"><thead><tr>';
        if (eq) {
            h += '<th style="' + th + ';text-align:left">Stock</th><th style="' + th + '">Type</th><th style="' + th + '">Qty</th><th style="' + th + '">Avg</th><th style="' + th + '">LTP</th><th style="' + th + '">Value</th><th style="' + th + '">P&L</th><th style="' + th + '"></th></tr></thead><tbody>';
            d.positions.forEach(p => {
                h += '<tr><td style="' + td + ';text-align:left;font-weight:800">' + this.e(p.symbol) + (typeof app !== 'undefined' && app.stockLinks ? app.stockLinks(p.symbol) : '') + '</td><td style="' + td + '">' + (p.product === 'CNC' ? 'Delivery' : '<span style="color:#a16207;font-weight:700">Intraday</span>') + '</td>'
                   + '<td style="' + td + ';color:' + (p.qty < 0 ? 'var(--red)' : 'inherit') + '">' + this.n(p.qty, 0) + '</td><td style="' + td + '">' + this.n(p.avg) + '</td><td style="' + td + '">' + this.n(p.ltp) + ' ' + this.pct(p.chg_pct) + (p.live ? '' : ' <span title="last known price" style="color:#a16207">*</span>') + '</td>'
                   + '<td style="' + td + '">' + this.rs(p.value, 0) + '</td><td style="' + td + '">' + this.pnl(p.pnl) + ' <span style="font-size:11px">' + (p.pnl_pct !== null ? '(' + p.pnl_pct.toFixed(2) + '%)' : '') + '</span></td>'
                   + '<td style="' + td + '"><button class="pt-x" data-k="' + this.e(p.key) + '" style="font-size:11.5px;padding:4px 10px;border-radius:6px;border:1px solid var(--border-color);background:var(--bg-card);cursor:pointer;font-weight:700">' + (p.qty > 0 ? 'Sell all' : 'Buy back') + '</button></td></tr>';
            });
        } else {
            h += '<th style="' + th + ';text-align:left">Contract</th><th style="' + th + '">Lots</th><th style="' + th + '">Qty</th><th style="' + th + '">Avg</th><th style="' + th + '">LTP</th><th style="' + th + '">P&L</th><th style="' + th + '"></th></tr></thead><tbody>';
            let lastG = null;
            d.positions.forEach(p => {
                if (p.strategy && p.strategy !== lastG) {
                    const legs = d.positions.filter(x => x.strategy === p.strategy), tot = legs.reduce((a, x) => a + x.pnl, 0);
                    h += '<tr style="background:rgba(79,70,229,0.05)"><td colspan="5" style="padding:7px 8px;font-weight:800;color:var(--text-accent)">🧩 ' + this.e(p.strategy_name || 'Strategy') + ' · ' + legs.length + ' legs</td><td style="' + td + ';border:none">' + this.pnl(tot) + '</td><td style="' + td + ';border:none"><button class="pt-xs" data-g="' + p.strategy + '" style="font-size:11.5px;padding:4px 10px;border-radius:6px;border:1px solid var(--red);color:var(--red);background:var(--bg-card);cursor:pointer;font-weight:700">Exit strategy</button></td></tr>';
                }
                lastG = p.strategy;
                h += '<tr><td style="' + td + ';text-align:left;font-weight:700' + (p.strategy ? ';padding-left:22px' : '') + '">' + this.e(p.tsym) + '<div style="font-size:10.5px;color:var(--text-secondary);font-weight:500">' + (p.kind === 'FUT' ? 'Future' : (p.kind === 'CE' ? 'Call ' : 'Put ') + this.n(p.strike)) + ' · exp ' + this.dlab(p.expiry) + (p.spot ? ' · spot ' + this.n(p.spot, 0) : '') + '</div></td>'
                   + '<td style="' + td + ';color:' + (p.qty < 0 ? 'var(--red)' : 'var(--green)') + ';font-weight:700">' + (p.qty < 0 ? '−' : '+') + this.n(Math.abs(p.lots), 0) + '</td><td style="' + td + '">' + this.n(p.qty, 0) + '</td><td style="' + td + '">' + this.n(p.avg) + '</td>'
                   + '<td style="' + td + '">' + this.n(p.ltp) + ' ' + this.pct(p.chg_pct) + (p.live ? '' : ' <span title="last known price" style="color:#a16207">*</span>') + '</td><td style="' + td + '">' + this.pnl(p.pnl) + '</td>'
                   + '<td style="' + td + '"><button class="pt-x" data-k="' + this.e(p.key) + '" style="font-size:11.5px;padding:4px 10px;border-radius:6px;border:1px solid var(--border-color);background:var(--bg-card);cursor:pointer;font-weight:700">Exit</button></td></tr>';
            });
        }
        h += '</tbody></table></div>' + (eq ? '' : '<div style="font-size:11px;color:var(--text-secondary);padding:6px 12px 10px">Premium received from selling is held until you close the position; losses on open positions reduce what\'s available. Expired contracts settle automatically at their value at 3:30 PM on expiry day.</div>') + '</div>';
        el.innerHTML = h;
        el.querySelectorAll('.pt-x').forEach(b => b.addEventListener('click', async () => {
            b.disabled = true;
            try { const r = await this.call('close', { body: eq ? { seg: 'EQ', key: b.dataset.k } : { seg: 'FO', keys: [b.dataset.k] } }); this.acct[this.tab] = r.account; this.toast('Position closed.'); this.paintSummary(); this.paintPositions(); this.paintOrders(); }
            catch (e) { b.disabled = false; this.toast(e.message, true); }
        }));
        el.querySelectorAll('.pt-xs').forEach(b => b.addEventListener('click', async () => {
            b.disabled = true;
            try { const r = await this.call('close', { body: { seg: 'FO', strategy: b.dataset.g } }); this.acct.FO = r.account; this.toast('Strategy closed: all legs squared off.'); this.paintSummary(); this.paintPositions(); this.paintOrders(); }
            catch (e) { b.disabled = false; this.toast(e.message, true); }
        }));
    },

    paintOrders() {
        const d = this.acct[this.tab], el = document.getElementById('pt-orders'); if (!d || !el) return;
        if (!d.orders.length) { el.innerHTML = ''; return; }
        const open = this.ls_open = this.ls_open === undefined ? false : this.ls_open;
        let h = '<details class="card" style="margin-bottom:12px"' + (open ? ' open' : '') + ' id="pt-od"><summary style="cursor:pointer;font-weight:800;color:var(--text-primary);font-size:13.5px">🧾 Order history (' + d.orders.length + ')</summary><div style="display:grid;gap:4px;margin-top:8px;font-size:12px">';
        d.orders.slice(0, 40).forEach(o => {
            let t;
            if (o.legs) t = o.legs.map(l => '<b style="color:' + (l.side === 'BUY' ? 'var(--green)' : l.side === 'SELL' ? 'var(--red)' : 'inherit') + '">' + l.side + '</b> ' + (l.lots ? l.lots + '× ' : '') + this.e(l.tsym) + ' @ ' + this.n(l.price)).join(' · ') + (o.strategy ? ' <span style="color:var(--text-accent)">(' + this.e(o.strategy) + ')</span>' : '');
            else if (o.symbol) t = '<b style="color:' + (o.side === 'BUY' ? 'var(--green)' : 'var(--red)') + '">' + o.side + '</b> ' + o.qty + ' ' + this.e(o.symbol) + ' @ ' + this.n(o.price) + ' · ' + (o.product === 'CNC' ? 'Delivery' : 'Intraday');
            else t = '';
            h += '<div style="padding:5px 0;border-top:1px solid rgba(15,23,42,0.05);display:flex;gap:10px;flex-wrap:wrap"><span style="color:var(--text-secondary);min-width:140px">' + this.e(o.time) + '</span><span style="flex:1;min-width:200px">' + t + (o.note ? ' <i style="color:var(--text-secondary)">' + this.e(o.note) + '</i>' : '') + '</span>'
               + '<span style="color:var(--text-secondary)">' + (o.charges ? 'charges ' + this.rs(o.charges) : '') + (o.realized ? ' · P&L ' + this.pnl(o.realized) : '') + '</span></div>';
        });
        el.innerHTML = h + '</div></details>';
        const det = document.getElementById('pt-od'); if (det) det.addEventListener('toggle', () => { this.ls_open = det.open; });
    },

    // ── admin ──
    async showAdmin(b) {
        b.innerHTML = '<div class="card"><span class="spinner"></span></div>';
        try {
            const d = await this.call('admin/requests');
            this._pending = d.pending; this.paintTabs();
            const s = d.data || {}, m = s.master || {};
            let h = '<div class="card" style="margin-bottom:12px;font-size:12.5px;line-height:1.8"><div class="section-title">📡 Live data (Angel One)</div>'
                + (s.configured ? '✅ Connected settings found' : '❌ Not connected: add ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN and ANGEL_TOTP_SECRET in Azure settings')
                + ' · login: ' + (s.logged_in ? '✅ OK' : (s.error ? '❌ ' + this.e(s.error) : 'not tried yet'))
                + ' · last good call: ' + this.e(s.last_ok || '—')
                + '<br>Instrument list: ' + (m.loaded_at ? this.n(m.equities, 0) + ' stocks, ' + this.n(m.fo_underlyings, 0) + ' F&O underlyings, loaded ' + this.e(m.loaded_at) : 'not loaded yet') + (m.error ? ' · <span style="color:var(--red)">' + this.e(m.error) + '</span>' : '') + '</div>';
            h += '<div class="card"><div class="section-title">🛡️ Fresh-start requests</div>';
            if (!d.items.length) h += '<div style="color:var(--text-secondary);font-size:13px">No requests yet.</div>';
            else {
                h += '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:12.5px;min-width:640px"><thead><tr style="text-align:left;color:var(--text-secondary);font-size:10.5px;text-transform:uppercase"><th style="padding:6px">When</th><th style="padding:6px">User</th><th style="padding:6px">Account</th><th style="padding:6px;text-align:right">Value at request</th><th style="padding:6px">Status</th><th></th></tr></thead><tbody>';
                d.items.forEach(r => {
                    h += '<tr style="border-top:1px solid rgba(15,23,42,0.06)"><td style="padding:6px">' + this.e(r.at) + '</td><td style="padding:6px"><b>' + this.e(r.name || '') + '</b><div style="font-size:11px;color:var(--text-secondary)">' + this.e(r.email) + '</div></td>'
                       + '<td style="padding:6px">' + (r.seg === 'FO' ? 'F&O ₹10 L' : 'Stocks ₹5 L') + (r.refills_so_far ? ' · ' + r.refills_so_far + ' before' : '') + '</td><td style="padding:6px;text-align:right">' + this.rs(r.account_value, 0) + '</td>'
                       + '<td style="padding:6px">' + (r.status === 'pending' ? '<b style="color:#a16207">Waiting</b>' : (r.status === 'approved' ? '<span style="color:var(--green)">Approved</span>' : '<span style="color:var(--red)">Rejected</span>') + '<div style="font-size:11px;color:var(--text-secondary)">' + this.e(r.decided_at || '') + '</div>') + '</td>'
                       + '<td style="padding:6px;white-space:nowrap">' + (r.status === 'pending' ? '<button class="pt-dec" data-id="' + r.id + '" data-a="1" style="padding:5px 12px;border-radius:6px;border:none;background:var(--green);color:#fff;font-weight:700;cursor:pointer">Approve</button> <button class="pt-dec" data-id="' + r.id + '" data-a="0" style="padding:5px 12px;border-radius:6px;border:1px solid var(--red);background:none;color:var(--red);font-weight:700;cursor:pointer">Reject</button>' : '') + '</td></tr>';
                });
                h += '</tbody></table></div>';
            }
            b.innerHTML = h + '</div>';
            b.querySelectorAll('.pt-dec').forEach(x => x.addEventListener('click', async () => {
                x.disabled = true;
                try { await this.call('admin/decide', { body: { id: x.dataset.id, approve: x.dataset.a === '1' } }); this.toast(x.dataset.a === '1' ? 'Approved: the account has a fresh balance.' : 'Rejected.'); this.showAdmin(b); }
                catch (e) { x.disabled = false; this.toast(e.message, true); }
            }));
        } catch (e) { b.innerHTML = '<div class="card" style="color:var(--red)">' + this.e(e.message) + '</div>'; }
    },
};
window.Paper = Paper;
