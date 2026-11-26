/*
 * Day Trader Scanner page (#daytrader).
 * Self-contained: its own API calls (/api/daytrader/*) and its own state, separate
 * from the other screeners. Two scans over every NSE stock (or the Nifty 500):
 *   daily - setups with entry / stop-loss / targets for the next session
 *   orb   - 15-minute opening-range breakouts with intraday levels
 * Each hit carries a Time Machine: how the same trade worked on that stock before
 * (its own history + 5,000 replays, after Indian costs), plus events and news.
 * Scan rules adapted from trader-scanner (github.com/Jakeschincariol/day-trader-skill, MIT);
 * Time Machine method as in trader-timemachine and its NSE port (ankurgoels/trader-skills-india).
 */

const DayTrader = {
    mode: 'daily',
    filter: 'all',
    sort: 'record',
    onlyGood: false,
    open: {},
    data: { daily: null, orb: null },
    pollTimer: null,

    api: {
        async start(mode, universe) {
            const res = await fetch(`${API_BASE_URL}/daytrader/start?mode=${mode}&universe=${universe}`, {
                method: 'POST', headers: (typeof Auth !== 'undefined' && Auth.headers) ? Auth.headers() : {} });
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
    e(t) { return String(t == null ? '' : t).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); },
    rs(x) {
        if (x === null || x === undefined || x === '') return '—';
        return '₹' + Number(x).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    },
    rs0(x) { return '₹' + Math.round(Number(x)).toLocaleString('en-IN'); },
    pct(x) { return (x === null || x === undefined) ? '—' : Number(x).toFixed(0) + '%'; },
    rR(x) { if (x === null || x === undefined) return '—'; const v = Number(x); return (v > 0 ? '+' : '') + v.toFixed(2) + 'R'; },
    ls(k, v) {
        try { if (v === undefined) return localStorage.getItem(k); if (v === null || v === '') localStorage.removeItem(k); else localStorage.setItem(k, String(v)); } catch (e) { return null; }
    },
    num(k) { const v = parseFloat(this.ls(k)); return v > 0 ? v : null; },
    universe() { const u = this.ls('mw_dt_universe'); return u === 'nifty500' ? 'nifty500' : 'all'; },

    LABELS: {
        breakout_20d: '📈 20-day breakout', bull_flag: '🚩 Bull flag', reclaim_ma20: '↩️ Reclaimed 20-DMA',
        oversold: '📉 Oversold', oversold_bounce: '🔄 Oversold bounce', orb: '⚡ Range breakout',
    },
    VERDICT: {
        good_strong: ['var(--green)', 'rgba(22,163,74,0.12)', '✅'], good: ['var(--green)', 'rgba(22,163,74,0.08)', '👍'],
        flip: ['#a16207', 'rgba(234,179,8,0.12)', '🪙'], bad: ['var(--red)', 'rgba(220,38,38,0.07)', '👎'],
        bad_strong: ['var(--red)', 'rgba(220,38,38,0.12)', '⛔'], few: ['var(--text-secondary)', 'rgba(15,23,42,0.05)', '…'],
        none: ['var(--text-secondary)', 'rgba(15,23,42,0.05)', '…'],
    },
    badge(v, small) {
        if (!v) return '';
        const c = this.VERDICT[v.key] || this.VERDICT.few;
        return '<span style="display:inline-block;font-size:' + (small ? '11px' : '12px') + ';font-weight:700;padding:3px 9px;border-radius:10px;color:' + c[0] + ';background:' + c[1] + '">' + c[2] + ' ' + this.e(v.text) + '</span>';
    },

    render(container) {
        if (this.pollTimer) { clearInterval(this.pollTimer); this.pollTimer = null; }
        this.filter = 'all'; this.open = {};
        const risk = this.num('mw_dt_risk'), cap = this.num('mw_dt_capital'), uni = this.universe();
        const inp = 'padding:7px 10px;border:1px solid var(--border-color);border-radius:8px;font-size:13px;background:var(--bg-card);color:var(--text-primary)';
        container.innerHTML = `
            <div style="margin-bottom:16px">
                <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">🎯 Day Trader Scanner</h2>
                <p style="font-size:13px;color:var(--text-secondary);line-height:1.6">Scans every NSE stock for trading setups and gives each one an entry, a stop-loss and two targets. The <b>⏳ Time Machine</b> then checks how often the same trade actually worked on that stock before, after trading costs, so you can tell a real edge from a coin flip.</p>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px" id="dt-tabs">
                <button class="dt-tab" data-mode="daily" style="padding:9px 16px;border-radius:18px;font-size:13px;font-weight:700;cursor:pointer;border:1px solid var(--border-color)">📅 Daily setups · next session</button>
                <button class="dt-tab" data-mode="orb" style="padding:9px 16px;border-radius:18px;font-size:13px;font-weight:700;cursor:pointer;border:1px solid var(--border-color)">⚡ Intraday breakouts · today</button>
            </div>
            <div id="dt-rules" class="card" style="margin-bottom:14px;font-size:12.5px;color:var(--text-secondary);line-height:1.8"></div>
            <div class="card" style="margin-bottom:14px;display:flex;gap:12px;flex-wrap:wrap;align-items:center">
                <label style="font-size:12.5px;color:var(--text-secondary);display:flex;align-items:center;gap:8px">Stocks
                    <select id="dt-uni" style="${inp}">
                        <option value="all"${uni === 'all' ? ' selected' : ''}>All NSE stocks (~2,000 · about 5 min)</option>
                        <option value="nifty500"${uni === 'nifty500' ? ' selected' : ''}>Nifty 500 (about 2 min)</option>
                    </select></label>
                <label style="font-size:12.5px;color:var(--text-secondary);display:flex;align-items:center;gap:8px">Risk per trade ₹
                    <input id="dt-risk" type="number" min="0" step="100" inputmode="numeric" placeholder="e.g. 500" value="${risk || ''}" style="width:100px;${inp}"></label>
                <label style="font-size:12.5px;color:var(--text-secondary);display:flex;align-items:center;gap:8px">Capital ₹
                    <input id="dt-cap" type="number" min="0" step="1000" inputmode="numeric" placeholder="optional" value="${cap || ''}" style="width:110px;${inp}"></label>
                <div style="flex:1"></div>
                <button id="dt-scan" class="btn" style="padding:10px 22px;font-size:14px;font-weight:700">🎯 Run Scan</button>
                <div style="flex-basis:100%;font-size:11.5px;color:var(--text-secondary);line-height:1.6">Risk and capital are optional: they size each trade (shares so the loss at the stop stays near your risk, capped by capital) and estimate charges. On trading days the server scans every NSE stock by itself at <b>9:50 AM</b> and <b>11 AM</b> (breakouts) and <b>4:20 PM</b> (next-day setups), so a fresh scan is usually already here.</div>
            </div>
            <div id="dt-status" style="display:none"></div>
            <div id="dt-regime"></div>
            <div id="dt-meta" style="font-size:12px;color:var(--text-secondary);margin-bottom:10px"></div>
            <div id="dt-card"></div>
            <div id="dt-filter" style="display:none;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center"></div>
            <div id="dt-result"><div style="text-align:center;padding:30px"><span class="spinner"></span></div></div>
            <div style="margin-top:18px;font-size:11.5px;line-height:1.7;color:var(--text-secondary);text-align:center">
                A scan lists charts that match a rule. It is not a list of stocks to buy and not investment advice.
                The Time Machine is a record of the past and a simulation, not a prediction: it can't see results, news or halts coming.
                A stop-loss is a plan, not a guarantee: gaps and fast markets can fill past it. Prices are from a free
                feed that can lag; confirm on your broker's chart. Most people who day trade lose money.
            </div>`;

        container.querySelectorAll('.dt-tab').forEach(b => b.addEventListener('click', () => {
            if (this.mode === b.getAttribute('data-mode')) return;
            this.mode = b.getAttribute('data-mode');
            this.filter = 'all'; this.open = {};
            if (this.pollTimer) { clearInterval(this.pollTimer); this.pollTimer = null; }
            this.paintTabs(); this.load();
        }));
        document.getElementById('dt-uni').addEventListener('change', (ev) => this.ls('mw_dt_universe', ev.target.value));
        document.getElementById('dt-risk').addEventListener('input', (ev) => { this.ls('mw_dt_risk', parseFloat(ev.target.value) > 0 ? ev.target.value : null); this.paintRows(); });
        document.getElementById('dt-cap').addEventListener('input', (ev) => { this.ls('mw_dt_capital', parseFloat(ev.target.value) > 0 ? ev.target.value : null); this.paintRows(); });
        document.getElementById('dt-scan').addEventListener('click', async () => {
            const mode = this.mode;
            try {
                const r = await this.api.start(mode, this.universe());
                if (r.status === 'recent') { this.showStatus('info', 'A scan just finished a few minutes ago: showing it. You can run a new one in a few minutes.'); this.load(); return; }
                this.poll(mode);
            } catch (err) { this.showStatus('error', 'Could not start the scan: ' + err.message); }
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
               <b>Targets:</b> T1 = entry + 1× risk, T2 = entry + 2× risk. Filters: price ≥ ₹20, turnover ≥ ₹5 crore.<br>
               <b>⏳ Time Machine:</b> the same rule re-run on the stock's last 5 years: every day the setup fired, did price reach T2 before the stop within 10 sessions? Plus 5,000 simulated 10-day futures from its own last year. After ~0.22% delivery costs. A 2R target needs to come first in about 1 trade in 3 just to break even.`
            : `<b style="color:var(--text-primary)">How the intraday levels work</b> · run after 9:35 AM on a trading day<br>
               <b>Setup:</b> the opening range is 9:15–9:30 AM. A breakout is the first 5-minute candle that closes above the range high.<br>
               <b>Entry:</b> the breakout candle's close. <b>Stop-loss:</b> the middle of the opening range.<br>
               <b>Targets:</b> T1 = entry + 1× risk, T2 = entry + 2× risk. <b>Exit by 3:10 PM</b>, before brokers square off intraday trades.<br>
               "Now" shows where each stock is: stopped out, past a target, or still open. Do not chase a stale breakout; breakouts after 2:30 PM are marked late.<br>
               <b>⏳ Time Machine:</b> the same breakout rule re-run on each of the stock's last ~60 sessions (5-minute bars), plus 5,000 replays of the rest of today built from bars at the same time of day. After ~0.08% intraday costs.`;
    },

    showStatus(kind, text, prog) {
        const bar = document.getElementById('dt-status');
        if (!bar) return;
        if (!kind) { bar.style.display = 'none'; return; }
        const colors = kind === 'error' ? 'border-color:var(--red);color:var(--red)'
            : kind === 'info' ? 'color:var(--text-secondary)'
            : 'background:rgba(22,163,74,0.06);border-color:rgba(22,163,74,0.25);color:var(--green)';
        let p = '';
        if (prog && prog.total > 0) {
            const w = Math.max(2, Math.min(100, Math.round(prog.done / prog.total * 100)));
            p = '<div style="height:6px;border-radius:4px;background:rgba(15,23,42,0.08);margin-top:8px;overflow:hidden"><div style="height:100%;width:' + w + '%;background:var(--green);transition:width .4s"></div></div>';
        }
        bar.style.display = 'block';
        bar.innerHTML = '<div class="card" style="padding:12px 16px;margin-bottom:14px;' + colors + '"><div style="display:flex;align-items:center;gap:12px">'
            + (kind === 'running' ? '<span class="spinner"></span>' : '') + '<span style="font-weight:600">' + this.e(text) + '</span></div>' + p + '</div>';
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
        const intro = 'Scanning ' + (this.universe() === 'all' ? 'every NSE stock' : 'the Nifty 500') + '. This takes a few minutes; you can leave this page, the result is saved.';
        this.showStatus('running', intro);
        const tick = async () => {
            if (!document.getElementById('dt-result') || this.mode !== mode) { clearInterval(this.pollTimer); this.pollTimer = null; return; }
            try {
                const st = await this.api.status(mode);
                if (st.status === 'done' || st.status === 'idle') {
                    clearInterval(this.pollTimer); this.pollTimer = null;
                    this.setButton(false); this.showStatus(null); this.load();
                } else if (st.status === 'error') {
                    clearInterval(this.pollTimer); this.pollTimer = null;
                    this.setButton(false); this.showStatus('error', 'Scan failed: ' + (st.error || 'unknown error'));
                } else if (st.status === 'running') {
                    const el = st.elapsed_s ? ' · ' + Math.floor(st.elapsed_s / 60) + ' min ' + (st.elapsed_s % 60) + ' s' : '';
                    const cnt = st.total ? ' (' + Number(st.done).toLocaleString('en-IN') + ' of ' + Number(st.total).toLocaleString('en-IN') + ')' : '';
                    this.showStatus('running', (st.stage || 'Working') + cnt + el, st);
                }
            } catch (e) {}
        };
        this.pollTimer = setInterval(tick, 4000);
        tick();
    },

    async load() {
        const mode = this.mode;
        const el = document.getElementById('dt-result');
        if (!el) return;
        el.innerHTML = '<div style="text-align:center;padding:30px"><span class="spinner"></span></div>';
        ['dt-meta', 'dt-regime', 'dt-card'].forEach(id => { const x = document.getElementById(id); if (x) x.innerHTML = ''; });
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
                this.paintMeta(); this.paintRegime(); this.paintCard(); this.paintFilter(); this.paintRows();
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
            t = 'Scanned ' + Number(d.scanned).toLocaleString('en-IN') + ' liquid stocks of ' + Number(d.requested).toLocaleString('en-IN') + ' (' + this.e(d.universe) + ') · closes through <b>' + this.e(d.closes_through || '—')
              + '</b> · levels for the next session · ' + d.results.length + ' setups';
        } else {
            t = 'Session <b>' + this.e(d.session) + '</b> up to <b>' + this.e(d.cutoff_ist) + '</b>' + (d.live ? ' (live)' : ' (full session)')
              + ' · ' + Number(d.scanned).toLocaleString('en-IN') + ' of ' + Number(d.requested).toLocaleString('en-IN') + ' charts usable (' + this.e(d.universe) + ') · ' + d.results.length + ' breakouts';
        }
        t += ' · last run ' + this.e(d.timestamp || d.scanned_at_ist || '') + (d.run_by === 'auto' ? ' (automatic)' : '');
        if (d.elapsed_s) t += ' · took ' + Math.round(d.elapsed_s / 60 * 10) / 10 + ' min';
        if (d.tm_info && d.results.length) {
            t += '<div style="margin-top:3px">⏳ Time Machine ran for ' + d.tm_info.covered + ' of ' + d.results.length + ' stocks'
               + (d.results.length > d.tm_info.cap ? ' (the ' + d.tm_info.cap + ' most traded)' : '') + ' · ' + Number(d.tm_info.replays).toLocaleString('en-IN') + ' replays each, seed ' + d.tm_info.seed + ' · costs ' + d.tm_info.cost_pct.toFixed(2) + '% round trip</div>';
        }
        if (d.calendar_warning) t += '<div style="color:var(--yellow);margin-top:4px">' + this.e(d.calendar_warning) + '</div>';
        el.innerHTML = t;
    },

    paintRegime() {
        const d = this.data[this.mode], el = document.getElementById('dt-regime');
        if (!el) return;
        const g = d && d.regime;
        if (!g) { el.innerHTML = ''; return; }
        const col = g.key === 'up' ? ['var(--green)', 'rgba(22,163,74,0.06)', '📈'] : g.key === 'down' ? ['var(--red)', 'rgba(220,38,38,0.05)', '📉'] : ['#a16207', 'rgba(234,179,8,0.08)', '↔️'];
        el.innerHTML = '<div class="card" style="padding:10px 14px;margin-bottom:12px;border-left:4px solid ' + col[0] + ';background:' + col[1] + ';font-size:12.5px;line-height:1.6">'
            + '<b style="color:' + col[0] + '">' + col[2] + ' Market: ' + this.e(g.text) + '</b>'
            + '<span style="color:var(--text-secondary)"> · Nifty ' + Number(g.nifty).toLocaleString('en-IN') + ' vs 20-day ' + Number(g.ma20).toLocaleString('en-IN') + ' / 50-day ' + Number(g.ma50).toLocaleString('en-IN') + '.'
            + (g.key === 'down' ? ' Long setups fail more often when the market is falling: the report card below shows by how much.' : '') + '</span></div>';
    },

    paintCard() {
        const d = this.data[this.mode], el = document.getElementById('dt-card');
        if (!el) return;
        const rc = d && d.report_card;
        if (!rc || !Object.keys(rc).length) { el.innerHTML = ''; return; }
        const daily = d.mode === 'daily';
        const order = daily ? ['breakout_20d', 'bull_flag', 'reclaim_ma20', 'oversold', 'oversold_bounce'] : ['9:30–10:30', '10:30–12:30', '12:30–2:30'];
        const th = 'padding:7px 8px;font-size:10.5px;text-transform:uppercase;color:var(--text-secondary);text-align:right;white-space:nowrap';
        const td = 'padding:7px 8px;text-align:right;border-top:1px solid rgba(15,23,42,0.06);white-space:nowrap';
        let h = '<details class="card" style="margin-bottom:14px;padding:12px 14px"' + (this.ls('mw_dt_card_open') === '0' ? '' : ' open') + ' id="dt-card-d">'
            + '<summary style="cursor:pointer;font-weight:800;color:var(--text-primary);font-size:13.5px">📊 Report card: how these setups did in the past ' + (daily ? '(every hit\'s last 5 years pooled)' : '(every hit\'s last ~60 sessions pooled, by breakout time)') + '</summary>'
            + '<div style="overflow-x:auto;margin-top:8px"><table style="width:100%;border-collapse:collapse;font-size:12.5px;min-width:560px"><thead><tr>'
            + '<th style="' + th + ';text-align:left">' + (daily ? 'Setup' : 'Breakout time') + '</th><th style="' + th + '">Past trades</th><th style="' + th + '">🎯 T2 first</th><th style="' + th + '">🛑 Stop first</th><th style="' + th + '">Reached T1</th><th style="' + th + '">Avg R</th>'
            + (daily ? '<th style="' + th + '">T2 first: Nifty up / down</th>' : '') + '<th style="' + th + ';text-align:left">Verdict</th></tr></thead><tbody>';
        order.filter(k => rc[k]).concat(Object.keys(rc).filter(k => order.indexOf(k) < 0)).forEach(k => {
            const s = rc[k], br = s.by_regime || {};
            h += '<tr><td style="' + td + ';text-align:left;font-weight:700">' + this.e(this.LABELS[k] || k) + '</td><td style="' + td + '">' + Number(s.trades).toLocaleString('en-IN') + '</td>'
               + '<td style="' + td + ';color:var(--green);font-weight:700">' + this.pct(s.target_pct) + '</td><td style="' + td + ';color:var(--red)">' + this.pct(s.stop_pct) + '</td>'
               + '<td style="' + td + '">' + this.pct(s.t1_pct) + '</td><td style="' + td + ';font-weight:700;color:' + (s.avg_r > 0 ? 'var(--green)' : 'var(--red)') + '">' + this.rR(s.avg_r) + '</td>'
               + (daily ? '<td style="' + td + '">' + (br.up && br.up.trades ? this.pct(br.up.target_pct) : '—') + ' / ' + (br.down && br.down.trades ? this.pct(br.down.target_pct) : '—') + '</td>' : '')
               + '<td style="' + td + ';text-align:left">' + this.badge(s.verdict, true) + '</td></tr>';
        });
        h += '</tbody></table></div><div style="font-size:11.5px;color:var(--text-secondary);margin-top:6px;line-height:1.6">Pooled from the stocks in today\'s scan, so it describes these stocks, not the whole market. A 2R target breaks even at about 33% T2-first; "Avg R" is the average result per trade in units of risk after costs (+0.20R on ₹500 risk ≈ +₹100 a trade on average).</div></details>';
        el.innerHTML = h;
        const det = document.getElementById('dt-card-d');
        if (det) det.addEventListener('toggle', () => this.ls('mw_dt_card_open', det.open ? '1' : '0'));
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
            + keys.filter(k => counts[k]).map(k => chip(k, (this.LABELS[k] || names[k]) + ' (' + counts[k] + ')')).join('')
            + '<div style="flex:1"></div>'
            + '<label style="font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" id="dt-good"' + (this.onlyGood ? ' checked' : '') + '> Only stocks where the trade worked before</label>'
            + '<select id="dt-sort" style="padding:6px 8px;border:1px solid var(--border-color);border-radius:8px;font-size:12px;background:var(--bg-card);color:var(--text-primary)">'
            + [['record', 'Best past record first'], ['liquid', 'Most traded first'], ['risk', 'Tightest stop first']].map(o => '<option value="' + o[0] + '"' + (this.sort === o[0] ? ' selected' : '') + '>' + o[1] + '</option>').join('') + '</select>';
        bar.querySelectorAll('.dt-chip').forEach(b => b.addEventListener('click', () => { this.filter = b.getAttribute('data-f'); this.paintFilter(); this.paintRows(); }));
        document.getElementById('dt-sort').addEventListener('change', (ev) => { this.sort = ev.target.value; this.paintRows(); });
        document.getElementById('dt-good').addEventListener('change', (ev) => { this.onlyGood = ev.target.checked; this.paintRows(); });
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
        const c = b === 'stopped' ? 'var(--red)' : b === 'target' ? '#a16207' : 'var(--green)';
        const bg = b === 'stopped' ? 'rgba(220,38,38,0.08)' : b === 'target' ? 'rgba(234,179,8,0.10)' : 'rgba(22,163,74,0.08)';
        return '<span style="font-size:11.5px;font-weight:700;padding:3px 9px;border-radius:10px;color:' + c + ';background:' + bg + '">' + this.e(s) + '</span>';
    },

    good(r) { const p = r.tm && r.tm.primary_summary; return !!(p && p.trades >= 8 && p.avg_r > 0 && p.verdict && /^good/.test(p.verdict.key)); },

    tmHtml(r) {
        const t = r.tm, daily = this.mode === 'daily';
        if (!t) return '<div style="margin-top:10px;font-size:11.5px;color:var(--text-secondary)">⏳ Time Machine not run for this stock (it runs for the most traded hits, or there wasn\'t enough history).</div>';
        const p = t.primary_summary || {}, rp = t.replays;
        const span = daily ? 'last ' + t.years + ' years' : 'last ' + t.sessions + ' sessions';
        const bar = (a, b, c) => '<div style="display:flex;height:8px;border-radius:5px;overflow:hidden;background:rgba(15,23,42,0.06);margin:5px 0 4px">'
            + '<div style="width:' + a + '%;background:var(--green)"></div><div style="width:' + c + '%;background:#cbd5e1"></div><div style="width:' + b + '%;background:var(--red)"></div></div>';
        let h = '<div style="margin-top:10px;border:1px solid rgba(79,70,229,0.18);border-radius:10px;padding:10px 12px">'
            + '<div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center"><b style="font-size:12.5px;color:var(--text-primary)">⏳ Time Machine · ' + this.e(this.LABELS[t.primary] || t.primary) + ' on ' + this.e(r.ticker) + ', ' + span + '</b>' + this.badge(p.verdict) + '</div>';
        h += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin-top:6px;font-size:12px">';
        if (p.trades) {
            h += '<div><div style="color:var(--text-secondary)">What really happened: <b style="color:var(--text-primary)">' + p.trades + ' past trades</b>' + (daily && p.signals ? ' (setup fired ' + p.signals + ' times)' : '') + '</div>'
               + bar(p.target_pct, p.stop_pct, p.neither_pct)
               + '<div><span style="color:var(--green);font-weight:700">🎯 T2 first ' + this.pct(p.target_pct) + '</span> · <span style="color:var(--red);font-weight:700">🛑 stop first ' + this.pct(p.stop_pct) + '</span> · neither ' + this.pct(p.neither_pct) + ' <span style="color:var(--text-secondary)">(2R breaks even at ~33%)</span></div>'
               + '<div style="color:var(--text-secondary)">Reached T1 ' + this.pct(p.t1_pct) + ' · won ' + this.pct(p.win_pct) + ' · avg <b style="color:' + (p.avg_r > 0 ? 'var(--green)' : 'var(--red)') + '">' + this.rR(p.avg_r) + '</b> per trade after costs</div></div>';
        } else {
            h += '<div style="color:var(--text-secondary)">The setup never produced a complete trade on this stock in the ' + span + '.</div>';
        }
        if (rp) {
            h += '<div><div style="color:var(--text-secondary)">' + Number(rp.replays).toLocaleString('en-IN') + ' replays from today\'s levels' + (daily ? ' (' + rp.days + ' sessions)' : ' (to ' + rp.until + ')') + '</div>'
               + bar(rp.target_pct, rp.stop_pct, rp.neither_pct)
               + '<div><span style="color:var(--green);font-weight:700">🎯 ' + this.pct(rp.target_pct) + '</span> · <span style="color:var(--red);font-weight:700">🛑 ' + this.pct(rp.stop_pct) + '</span> · neither ' + this.pct(rp.neither_pct) + '</div>'
               + '<div style="color:var(--text-secondary)">avg ' + this.rR(rp.avg_r) + ' · middle 80%: ' + this.rR(rp.p10_r) + ' to ' + this.rR(rp.p90_r) + ' · ' + this.badge(rp.verdict, true) + '</div></div>';
        }
        h += '</div>';
        const open = !!this.open[r.ticker];
        h += '<button class="dt-more" data-t="' + this.e(r.ticker) + '" style="margin-top:6px;background:none;border:none;color:var(--text-accent);font-weight:700;font-size:12px;cursor:pointer;padding:0">' + (open ? '▲ Hide details' : '▼ Every setup and the last trades') + '</button>';
        if (open) {
            const th = 'padding:5px 6px;font-size:10.5px;color:var(--text-secondary);text-align:right;text-transform:uppercase';
            const td = 'padding:5px 6px;text-align:right;border-top:1px solid rgba(15,23,42,0.06)';
            h += '<div style="overflow-x:auto;margin-top:6px"><table style="width:100%;border-collapse:collapse;font-size:12px;min-width:420px"><thead><tr><th style="' + th + ';text-align:left">Setup</th><th style="' + th + '">Trades</th><th style="' + th + '">T2 first</th><th style="' + th + '">Stop first</th><th style="' + th + '">Avg R</th><th style="' + th + ';text-align:left">Verdict</th></tr></thead><tbody>';
            Object.keys(t.setups).forEach(k => {
                const s = t.setups[k];
                h += '<tr><td style="' + td + ';text-align:left">' + this.e(this.LABELS[k] || k) + '</td><td style="' + td + '">' + (s.trades || 0) + '</td><td style="' + td + '">' + this.pct(s.target_pct) + '</td><td style="' + td + '">' + this.pct(s.stop_pct) + '</td><td style="' + td + '">' + this.rR(s.avg_r) + '</td><td style="' + td + ';text-align:left">' + this.badge(s.verdict, true) + '</td></tr>';
            });
            h += '</tbody></table></div>';
            const rec = (p.recent || []);
            if (rec.length) {
                const lab = { target: '🎯 T2', stop: '🛑 stop', neither: '⏸ neither' };
                h += '<div style="font-size:11.5px;color:var(--text-secondary);margin-top:6px">Last trades: ' + rec.map(x => this.e(x.date) + (x.at ? ' ' + this.e(x.at) : '') + ' ' + lab[x.out] + ' ' + this.rR(x.r) + (x.days ? ' in ' + x.days + 'd' : '')).join(' · ') + '</div>';
            }
            const sk = p.skipped || {};
            if (daily && (sk.not_triggered || sk.gapped_past)) h += '<div style="font-size:11.5px;color:var(--text-secondary);margin-top:4px">Also: ' + (sk.not_triggered || 0) + ' times price never traded above the entry next day (no trade), ' + (sk.gapped_past || 0) + ' times it opened above T1 (skipped, as the rules say).</div>';
        }
        return h + '</div>';
    },

    eventsHtml(r) {
        const ev = r.events || {};
        let h = '';
        if (ev.results_on) {
            h += '<div style="margin-top:8px;font-size:12px;padding:6px 10px;border-radius:8px;background:rgba(220,38,38,0.07);color:var(--red);font-weight:700">⚠️ Results board meeting on ' + this.e(new Date(ev.results_on + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }))
               + ': a big gap is possible. The Time Machine can\'t see this coming.</div>';
        }
        const items = [];
        (ev.filings || []).forEach(f => items.push('<div style="overflow-wrap:anywhere">📑 <b>NSE filing</b> ' + this.e((f.when || '').slice(0, 10)) + ': ' + (f.link ? '<a href="' + this.e(f.link) + '" target="_blank" rel="noopener" style="color:var(--text-accent);text-decoration:none">' + this.e(f.subject) + ' ↗</a>' : this.e(f.subject)) + '</div>'));
        (ev.news || []).forEach(n => items.push('<div style="overflow-wrap:anywhere">📰 ' + this.e(n.when) + ' · <a href="' + this.e(n.link) + '" target="_blank" rel="noopener" style="color:var(--text-primary);text-decoration:none">' + this.e(n.title) + '</a></div>'));
        if (items.length) h += '<div style="margin-top:8px;font-size:12px;line-height:1.6;color:var(--text-secondary);display:grid;gap:2px">' + items.join('') + '</div>';
        return h;
    },

    sizeHtml(r) {
        const risk = this.num('mw_dt_risk'), cap = this.num('mw_dt_capital'), lv = r.levels;
        if (!risk || !(lv.risk > 0)) return '';
        let qty = Math.floor(risk / lv.risk);
        if (cap) qty = Math.min(qty, Math.floor(cap / lv.entry));
        if (qty < 1) return '<div style="margin-top:8px;font-size:12px;color:var(--red)">Your risk per trade is smaller than one share\'s risk (' + this.rs(lv.risk) + '): skip, or raise the risk.</div>';
        const val = qty * lv.entry;
        const costPct = (this.data[this.mode].tm_info || {}).cost_pct || (this.mode === 'daily' ? 0.22 : 0.08);
        const charges = val * costPct / 100;
        return '<div style="margin-top:8px;font-size:12px;color:var(--text-secondary);display:flex;gap:14px;flex-wrap:wrap">'
            + '<span>Qty <b style="color:var(--text-primary)">' + qty.toLocaleString('en-IN') + '</b></span><span>Needs ' + this.rs0(val) + '</span>'
            + '<span>Loss at stop ≈ <b style="color:var(--red)">' + this.rs0(qty * lv.risk + charges) + '</b></span><span>At T2 ≈ <b style="color:var(--green)">+' + this.rs0(qty * (lv.target2 - lv.entry) - charges) + '</b></span>'
            + '<span>Charges ≈ ' + this.rs0(charges) + ' (' + (this.mode === 'daily' ? 'delivery' : 'intraday') + ')</span></div>';
    },

    paintRows() {
        const d = this.data[this.mode];
        const el = document.getElementById('dt-result');
        if (!el || !d) return;
        let rows = (d.results || []).slice();
        if (this.filter !== 'all') rows = this.mode === 'daily' ? rows.filter(r => r.setups.includes(this.filter)) : rows.filter(r => this.orbBucket(r) === this.filter);
        if (this.onlyGood) rows = rows.filter(r => this.good(r));
        if (this.sort === 'liquid') rows.sort((a, b) => (b.turnover_cr || 0) - (a.turnover_cr || 0));
        else if (this.sort === 'risk') rows.sort((a, b) => (a.levels.risk_pct || 99) - (b.levels.risk_pct || 99));
        else rows.sort((a, b) => a.rank - b.rank);
        if (!rows.length) {
            el.innerHTML = '<div class="card" style="text-align:center;padding:30px;color:var(--text-secondary)">'
                + (d.results.length ? (this.onlyGood ? 'No stock in this filter has a positive past record for its setup. That\'s useful to know too.' : 'No stocks in this filter.')
                    : (this.mode === 'daily' ? 'No stock matched a setup on the last close.' : 'No stock closed above its opening range in this session yet.')) + '</div>';
            return;
        }
        const box = (label, val, color, sub) => '<div style="min-width:84px"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.03em">' + label + '</div>'
            + '<div style="font-weight:800;font-size:15px;color:' + (color || 'var(--text-primary)') + '">' + val + '</div>'
            + (sub ? '<div style="font-size:10.5px;color:var(--text-secondary)">' + sub + '</div>' : '') + '</div>';
        let h = '<div style="display:grid;gap:10px">';
        rows.forEach((r, i) => {
            const lv = r.levels;
            const tags = r.setups.map(s => '<span style="font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;background:rgba(79,70,229,0.08);color:var(--text-accent)">' + this.e(this.LABELS[s] || s) + '</span>').join(' ');
            h += '<div class="card" style="padding:14px 16px">';
            h += '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:10px">'
               + '<div style="display:flex;gap:12px;align-items:center;flex:1;min-width:0"><div style="font-size:16px;font-weight:800;color:var(--text-secondary);width:26px;flex:none">' + (i + 1) + '</div>'
               + '<div style="min-width:0"><div style="font-weight:800;font-size:15px;color:var(--text-primary);overflow-wrap:anywhere">' + this.e(r.ticker) + (typeof app !== 'undefined' && app.stockLinks ? app.stockLinks(r.ticker) : '') + '</div>'
               + (r.name ? '<div style="font-size:11.5px;color:var(--text-secondary);overflow-wrap:anywhere">' + this.e(r.name) + '</div>' : '')
               + '<div style="margin-top:5px;display:flex;gap:5px;flex-wrap:wrap">' + tags + '</div></div></div>'
               + '<div style="text-align:right"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase">' + (this.mode === 'daily' ? 'Last close' : 'Now') + '</div>'
               + '<div style="font-weight:800">' + this.rs(r.price) + '</div></div></div>';
            h += '<div style="display:flex;gap:14px;flex-wrap:wrap;padding:10px 12px;border-radius:10px;background:rgba(15,23,42,0.03)">'
               + box(this.mode === 'daily' ? 'Buy above' : 'Entry', this.rs(lv.entry), 'var(--text-accent)')
               + box('Stop-loss', this.rs(lv.stop), 'var(--red)', this.e(lv.stop_rule))
               + box('Target 1', this.rs(lv.target1), 'var(--green)', '1R')
               + box('Target 2', this.rs(lv.target2), 'var(--green)', '2R')
               + box('Risk / share', this.rs(lv.risk), null, lv.risk_pct + '%')
               + '</div>';
            if (this.mode === 'daily') {
                h += '<div style="margin-top:8px;font-size:11.5px;color:var(--text-secondary)">ATR(14) ' + this.rs(lv.atr14) + ' · RSI ' + r.rsi
                   + ' · turnover ₹' + Number(r.turnover_cr).toLocaleString('en-IN') + ' Cr · prev. day ' + this.rs(lv.prior_low) + '–' + this.rs(lv.prior_high) + ' · valid for the next session</div>';
            } else {
                h += '<div style="margin-top:8px;font-size:11.5px;color:var(--text-secondary);display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
                   + '<span>Breakout ' + this.e(r.breakout_at) + ' · range ' + this.rs(r.or_low) + '–' + this.rs(r.or_high) + ' · turnover ₹' + Number(r.turnover_cr).toLocaleString('en-IN') + ' Cr · exit by 3:10 PM</span>' + this.statusPill(r) + '</div>';
            }
            h += this.sizeHtml(r) + this.eventsHtml(r) + this.tmHtml(r);
            h += '</div>';
        });
        h += '</div>';
        el.innerHTML = h;
        el.querySelectorAll('.dt-more').forEach(b => b.addEventListener('click', () => {
            const t = b.getAttribute('data-t'); this.open[t] = !this.open[t]; this.paintRows();
        }));
    },
};
window.DayTrader = DayTrader;
