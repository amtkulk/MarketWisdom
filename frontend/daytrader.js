/*
 * Day Trader Scanner page (#daytrader).
 * Self-contained: its own API calls (/api/daytrader/*) and its own state, separate
 * from the other screeners. Two scans over Nifty 500:
 *   daily - setups with entry / stop-loss / targets for the next session
 *   orb   - 15-minute opening-range breakouts with intraday levels
 * Scan rules adapted from trader-scanner (github.com/Jakeschincariol/day-trader-skill, MIT).
 */

const DayTrader = {
    mode: 'daily',
    filter: 'all',
    data: { daily: null, orb: null },
    pollTimer: null,

    api: {
        async start(mode) {
            const res = await fetch(`${API_BASE_URL}/daytrader/start?mode=${mode}`, { method: 'POST' });
            let body = {};
            try { body = await res.json(); } catch (e) {}
            if (!res.ok) throw new Error(body.error || `Server error (${res.status})`);
            return body;
        },
        async status(mode) {
            const res = await fetch(`${API_BASE_URL}/daytrader/status?mode=${mode}`);
            if (!res.ok) return { status: 'error' };
            return res.json();
        },
        async results(mode) {
            const res = await fetch(`${API_BASE_URL}/daytrader/results?mode=${mode}`);
            if (!res.ok) throw new Error(`Server error (${res.status})`);
            return res.json();
        },
    },

    // ---------- small helpers ----------
    e(t) { return (typeof esc === 'function') ? esc(t) : String(t == null ? '' : t).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); },
    rs(x) {
        if (x === null || x === undefined || x === '') return '—';
        return '₹' + Number(x).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    },
    riskRs() {
        try { const v = parseFloat(localStorage.getItem('mw_dt_risk')); return v > 0 ? v : null; } catch (e) { return null; }
    },
    setRiskRs(v) {
        try { if (v > 0) localStorage.setItem('mw_dt_risk', String(v)); else localStorage.removeItem('mw_dt_risk'); } catch (e) {}
    },

    LABELS: {
        breakout_20d: '📈 20-day breakout', bull_flag: '🚩 Bull flag', reclaim_ma20: '↩️ Reclaimed 20-DMA',
        oversold: '📉 Oversold', oversold_bounce: '🔄 Oversold bounce', orb: '⚡ Range breakout',
    },

    render(container) {
        if (this.pollTimer) { clearInterval(this.pollTimer); this.pollTimer = null; }
        this.filter = 'all';
        const risk = this.riskRs();
        container.innerHTML = `
            <div style="margin-bottom:16px">
                <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">🎯 Day Trader Scanner</h2>
                <p style="font-size:13px;color:var(--text-secondary)">Scans the Nifty 500 for trading setups and gives each one an entry, a stop-loss and two targets. Use it to build a watchlist, then check the chart before you act.</p>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px" id="dt-tabs">
                <button class="dt-tab" data-mode="daily" style="padding:9px 16px;border-radius:18px;font-size:13px;font-weight:700;cursor:pointer;border:1px solid var(--border-color)">📅 Daily setups · next session</button>
                <button class="dt-tab" data-mode="orb" style="padding:9px 16px;border-radius:18px;font-size:13px;font-weight:700;cursor:pointer;border:1px solid var(--border-color)">⚡ Intraday breakouts · today</button>
            </div>
            <div id="dt-rules" class="card" style="margin-bottom:14px;font-size:12.5px;color:var(--text-secondary);line-height:1.8"></div>
            <div class="card" style="margin-bottom:14px;display:flex;gap:12px;flex-wrap:wrap;align-items:center">
                <label style="font-size:12.5px;color:var(--text-secondary);display:flex;align-items:center;gap:8px">
                    Risk per trade ₹
                    <input id="dt-risk" type="number" min="0" step="100" inputmode="numeric" placeholder="e.g. 500" value="${risk ? risk : ''}"
                        style="width:110px;padding:7px 10px;border:1px solid var(--border-color);border-radius:8px;font-size:13px;background:var(--bg-card);color:var(--text-primary)">
                </label>
                <span style="font-size:11.5px;color:var(--text-secondary)">Optional: shows how many shares keep your loss at the stop near this amount.</span>
                <div style="flex:1"></div>
                <button id="dt-scan" class="btn" style="padding:10px 22px;font-size:14px;font-weight:700">🎯 Run Scan</button>
            </div>
            <div id="dt-status" style="display:none"></div>
            <div id="dt-meta" style="font-size:12px;color:var(--text-secondary);margin-bottom:10px"></div>
            <div id="dt-filter" style="display:none;gap:8px;flex-wrap:wrap;margin-bottom:14px"></div>
            <div id="dt-result"><div style="text-align:center;padding:30px"><span class="spinner"></span></div></div>
            <div style="margin-top:18px;font-size:11.5px;line-height:1.7;color:var(--text-secondary);text-align:center">
                A scan lists charts that match a rule. It is not a list of stocks to buy and not investment advice.
                A stop-loss is a plan, not a guarantee: gaps and fast markets can fill past it. Prices are from a free
                feed that can lag; confirm on your broker's chart. Most people who day trade lose money.
            </div>`;

        container.querySelectorAll('.dt-tab').forEach(b => b.addEventListener('click', () => {
            if (this.mode === b.getAttribute('data-mode')) return;
            this.mode = b.getAttribute('data-mode');
            this.filter = 'all';
            if (this.pollTimer) { clearInterval(this.pollTimer); this.pollTimer = null; }
            this.paintTabs(); this.load();
        }));
        document.getElementById('dt-risk').addEventListener('input', (ev) => {
            this.setRiskRs(parseFloat(ev.target.value));
            this.paintRows();
        });
        document.getElementById('dt-scan').addEventListener('click', async () => {
            const mode = this.mode;
            try { await this.api.start(mode); this.poll(mode); }
            catch (err) { this.showStatus('error', 'Could not start the scan: ' + err.message); }
        });
        this.paintTabs();
        this.load();
    },

    paintTabs() {
        document.querySelectorAll('.dt-tab').forEach(b => {
            const on = b.getAttribute('data-mode') === this.mode;
            b.style.background = on ? 'var(--text-accent)' : 'var(--bg-card)';
            b.style.color = on ? '#fff' : 'var(--text-secondary)';
        });
        const rules = document.getElementById('dt-rules');
        if (!rules) return;
        rules.innerHTML = this.mode === 'daily'
            ? `<b style="color:var(--text-primary)">How the daily levels work</b> · run after 3:30 PM or before 9:15 AM<br>
               <b>Setups:</b> 20-day closing-high breakout · bull flag (10%+ run, then a tight 5-day range) · close back above the 20-day average · oversold (RSI under 30) · oversold bounce.<br>
               <b>Entry:</b> buy only if price trades above the previous day's high. If it opens far above the entry, skip it.<br>
               <b>Stop-loss:</b> the previous day's low, kept between 0.5× and 1.5× the 14-day ATR below the entry.<br>
               <b>Targets:</b> T1 = entry + 1× risk, T2 = entry + 2× risk. Filters: price ≥ ₹20, turnover ≥ ₹5 crore.`
            : `<b style="color:var(--text-primary)">How the intraday levels work</b> · run after 9:35 AM on a trading day<br>
               <b>Setup:</b> the opening range is 9:15–9:30 AM. A breakout is the first 5-minute candle that closes above the range high.<br>
               <b>Entry:</b> the breakout candle's close. <b>Stop-loss:</b> the middle of the opening range.<br>
               <b>Targets:</b> T1 = entry + 1× risk, T2 = entry + 2× risk. <b>Exit by 3:10 PM</b>, before brokers square off intraday trades.<br>
               "Now" shows where each stock is: stopped out, past a target, or still open. Do not chase a stale breakout; breakouts after 2:30 PM are marked late.`;
    },

    showStatus(kind, text) {
        const bar = document.getElementById('dt-status');
        if (!bar) return;
        if (!kind) { bar.style.display = 'none'; return; }
        const colors = kind === 'error'
            ? 'border-color:var(--red);color:var(--red)'
            : 'background:rgba(22,163,74,0.06);border-color:rgba(22,163,74,0.25);color:var(--green)';
        bar.style.display = 'block';
        bar.innerHTML = '<div class="card" style="padding:12px 16px;margin-bottom:14px;display:flex;align-items:center;gap:12px;' + colors + '">'
            + (kind === 'running' ? '<span class="spinner"></span>' : '') + '<span style="font-weight:600">' + this.e(text) + '</span></div>';
    },

    setButton(running) {
        const btn = document.getElementById('dt-scan');
        if (!btn) return;
        btn.disabled = running;
        btn.innerHTML = running ? '<span class="spinner" style="vertical-align:middle;margin-right:6px"></span> Scanning...' : '🎯 Run Scan';
    },

    poll(mode) {
        if (this.pollTimer) clearInterval(this.pollTimer);
        this.setButton(true);
        this.showStatus('running', mode === 'daily'
            ? 'Scanning Nifty 500 daily charts (about 1 minute). You can leave this page; results are saved.'
            : 'Scanning Nifty 500 5-minute charts (about 1 minute). You can leave this page; results are saved.');
        this.pollTimer = setInterval(async () => {
            if (!document.getElementById('dt-result') || this.mode !== mode) { clearInterval(this.pollTimer); this.pollTimer = null; return; }
            try {
                const st = await this.api.status(mode);
                if (st.status === 'done' || st.status === 'idle') {
                    clearInterval(this.pollTimer); this.pollTimer = null;
                    this.setButton(false); this.showStatus(null); this.load();
                } else if (st.status === 'error') {
                    clearInterval(this.pollTimer); this.pollTimer = null;
                    this.setButton(false); this.showStatus('error', 'Scan failed: ' + (st.error || 'unknown error'));
                }
            } catch (e) {}
        }, 5000);
    },

    async load() {
        const mode = this.mode;
        const el = document.getElementById('dt-result');
        if (!el) return;
        el.innerHTML = '<div style="text-align:center;padding:30px"><span class="spinner"></span></div>';
        document.getElementById('dt-meta').innerHTML = '';
        document.getElementById('dt-filter').style.display = 'none';
        try {
            const data = await this.api.results(mode);
            if (this.mode !== mode) return;
            this.data[mode] = data.empty ? null : data;
            if (data.empty) {
                el.innerHTML = '<div class="card" style="text-align:center;padding:40px"><div style="font-size:40px;margin-bottom:12px">🎯</div>'
                    + '<div style="color:var(--text-accent);font-weight:800;font-size:16px">No scan yet</div>'
                    + '<div style="color:var(--text-secondary);margin-top:8px">Click <b>Run Scan</b>. '
                    + (mode === 'daily' ? 'Best after the 3:30 PM close, for tomorrow\'s levels.' : 'Best after 9:35 AM on a trading day.') + '</div></div>';
            } else {
                this.paintMeta(); this.paintFilter(); this.paintRows();
            }
        } catch (e) {
            el.innerHTML = '<div class="card" style="text-align:center;padding:20px;color:var(--text-secondary)">Could not load the last scan.</div>';
        }
        try { const st = await this.api.status(mode); if (st.status === 'running' && this.mode === mode) this.poll(mode); } catch (e) {}
    },

    paintMeta() {
        const d = this.data[this.mode];
        const el = document.getElementById('dt-meta');
        if (!d || !el) return;
        let t = '';
        if (d.mode === 'daily') {
            t = 'Scanned ' + d.scanned + ' of ' + d.requested + ' ' + this.e(d.universe) + ' stocks · closes through <b>' + this.e(d.closes_through || '—')
              + '</b> · levels for the next session · ' + d.results.length + ' setups';
        } else {
            t = 'Session <b>' + this.e(d.session) + '</b> up to <b>' + this.e(d.cutoff_ist) + '</b>' + (d.live ? ' (live)' : ' (full session)')
              + ' · ' + d.scanned + ' of ' + d.requested + ' ' + this.e(d.universe) + ' charts usable · ' + d.results.length + ' breakouts';
        }
        t += ' · last run ' + this.e(d.timestamp || d.scanned_at_ist || '');
        if (d.calendar_warning) t += '<div style="color:var(--yellow);margin-top:4px">' + this.e(d.calendar_warning) + '</div>';
        el.innerHTML = t;
    },

    paintFilter() {
        const d = this.data[this.mode];
        const bar = document.getElementById('dt-filter');
        if (!bar) return;
        if (!d || !d.results.length) { bar.style.display = 'none'; return; }
        const counts = { all: d.results.length };
        let keys;
        if (this.mode === 'daily') {
            keys = ['breakout_20d', 'bull_flag', 'reclaim_ma20', 'oversold', 'oversold_bounce'];
            d.results.forEach(r => r.setups.forEach(s => { counts[s] = (counts[s] || 0) + 1; }));
        } else {
            keys = ['open', 'stopped', 'target'];
            d.results.forEach(r => { const k = this.orbBucket(r); counts[k] = (counts[k] || 0) + 1; });
        }
        const names = { open: '🟢 Still open', stopped: '🔴 Stopped out', target: '🎯 Target reached' };
        const chip = (k, label) => '<button data-f="' + k + '" class="dt-chip" style="padding:6px 12px;border-radius:16px;font-size:12px;font-weight:700;cursor:pointer;border:1px solid var(--border-color);background:'
            + (this.filter === k ? 'var(--text-accent)' : 'var(--bg-card)') + ';color:' + (this.filter === k ? '#fff' : 'var(--text-secondary)') + '">' + label + '</button>';
        bar.style.display = 'flex';
        bar.innerHTML = chip('all', 'All (' + counts.all + ')')
            + keys.filter(k => counts[k]).map(k => chip(k, (this.LABELS[k] || names[k]) + ' (' + counts[k] + ')')).join('');
        bar.querySelectorAll('.dt-chip').forEach(b => b.addEventListener('click', () => {
            this.filter = b.getAttribute('data-f'); this.paintFilter(); this.paintRows();
        }));
    },

    orbBucket(r) {
        const s = (r.levels && r.levels.status) || '';
        if (s.indexOf('STOPPED') >= 0) return 'stopped';
        if (s.indexOf('reached') >= 0) return 'target';
        return 'open';
    },

    statusPill(r) {
        const s = r.levels.status || '';
        const b = this.orbBucket(r);
        const c = b === 'stopped' ? 'var(--red)' : b === 'target' ? 'var(--yellow)' : 'var(--green)';
        const bg = b === 'stopped' ? 'rgba(220,38,38,0.08)' : b === 'target' ? 'rgba(234,179,8,0.10)' : 'rgba(22,163,74,0.08)';
        return '<span style="font-size:11.5px;font-weight:700;padding:3px 9px;border-radius:10px;color:' + c + ';background:' + bg + '">' + this.e(s) + '</span>';
    },

    paintRows() {
        const d = this.data[this.mode];
        const el = document.getElementById('dt-result');
        if (!el || !d) return;
        let rows = d.results || [];
        if (this.filter !== 'all') {
            rows = this.mode === 'daily' ? rows.filter(r => r.setups.includes(this.filter)) : rows.filter(r => this.orbBucket(r) === this.filter);
        }
        if (!rows.length) {
            el.innerHTML = '<div class="card" style="text-align:center;padding:30px;color:var(--text-secondary)">'
                + (d.results.length ? 'No stocks in this filter.' : (this.mode === 'daily'
                    ? 'No Nifty 500 stock matched a setup on the last close.'
                    : 'No Nifty 500 stock closed above its opening range in this session yet.')) + '</div>';
            return;
        }
        const risk = this.riskRs();
        const box = (label, val, color, sub) => '<div style="min-width:92px"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.03em">' + label + '</div>'
            + '<div style="font-weight:800;font-size:15px;color:' + (color || 'var(--text-primary)') + '">' + val + '</div>'
            + (sub ? '<div style="font-size:10.5px;color:var(--text-secondary)">' + sub + '</div>' : '') + '</div>';
        let h = '<div style="display:grid;gap:10px">';
        rows.forEach(r => {
            const lv = r.levels;
            const tags = r.setups.map(s => '<span style="font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;background:rgba(79,70,229,0.08);color:var(--text-accent)">' + this.e(this.LABELS[s] || s) + '</span>').join(' ');
            const qty = risk && lv.risk > 0 ? Math.floor(risk / lv.risk) : null;
            h += '<div class="card" style="padding:14px 16px">';
            h += '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:10px">'
               + '<div style="display:flex;gap:12px;align-items:center;flex:1;min-width:0"><div style="font-size:16px;font-weight:800;color:var(--text-secondary);width:24px;flex:none">' + r.rank + '</div>'
               + '<div style="min-width:0"><div style="font-weight:800;font-size:15px;color:var(--text-primary);overflow-wrap:anywhere">' + this.e(r.ticker) + (app.stockLinks ? app.stockLinks(r.ticker) : '') + '</div>'
               + '<div style="margin-top:5px;display:flex;gap:5px;flex-wrap:wrap">' + tags + '</div></div></div>'
               + '<div style="text-align:right"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase">' + (this.mode === 'daily' ? 'Last close' : 'Now') + '</div>'
               + '<div style="font-weight:800">' + this.rs(r.price) + '</div></div></div>';
            h += '<div style="display:flex;gap:16px;flex-wrap:wrap;padding:10px 12px;border-radius:10px;background:rgba(15,23,42,0.03)">'
               + box(this.mode === 'daily' ? 'Buy above' : 'Entry', this.rs(lv.entry), 'var(--text-accent)')
               + box('Stop-loss', this.rs(lv.stop), 'var(--red)', this.e(lv.stop_rule))
               + box('Target 1', this.rs(lv.target1), 'var(--green)', '1R')
               + box('Target 2', this.rs(lv.target2), 'var(--green)', '2R')
               + box('Risk / share', this.rs(lv.risk), null, lv.risk_pct + '%')
               + (qty !== null ? box('Qty', qty.toLocaleString('en-IN'), null, '≈ ' + this.rs(qty * lv.risk) + ' risk') : '')
               + '</div>';
            if (this.mode === 'daily') {
                h += '<div style="margin-top:8px;font-size:11.5px;color:var(--text-secondary)">ATR(14) ' + this.rs(lv.atr14) + ' · RSI ' + r.rsi
                   + ' · turnover ₹' + Number(r.turnover_cr).toLocaleString('en-IN') + ' Cr · prev. day ' + this.rs(lv.prior_low) + '–' + this.rs(lv.prior_high) + ' · valid for the next session</div>';
            } else {
                h += '<div style="margin-top:8px;font-size:11.5px;color:var(--text-secondary);display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
                   + '<span>Breakout ' + this.e(r.breakout_at) + ' · range ' + this.rs(r.or_low) + '–' + this.rs(r.or_high) + ' · exit by 3:10 PM</span>' + this.statusPill(r) + '</div>';
            }
            h += '</div>';
        });
        h += '</div>';
        el.innerHTML = h;
    },
};
window.DayTrader = DayTrader;
