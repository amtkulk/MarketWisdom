/*
 * Main Application Logic & Router
 */

const app = {
    VERSION: 'v29',
    // Root bug fixed: _isSignedIn was checking window.Auth (always undefined for
    // top-level `const Auth`), so it always returned false. Same bug had broken
    // the auth header on watchlist calls. Both fixed → gate can safely be ON.
    GATE_ENABLED: true,
    PUBLIC_ROUTES: new Set(['home']),

    setupMobileMenu() {
        const toggle  = document.getElementById('menu-toggle');
        const links   = document.getElementById('nav-links');
        const overlay = document.getElementById('nav-overlay');
        if (!toggle || !links || !overlay) return;
        const close = () => { links.classList.remove('open'); overlay.classList.remove('show'); };
        const open  = () => { links.classList.add('open');    overlay.classList.add('show'); };
        toggle.addEventListener('click', () => links.classList.contains('open') ? close() : open());
        overlay.addEventListener('click', close);
        // Selecting any page closes the drawer
        links.querySelectorAll('.nav-btn').forEach(a => a.addEventListener('click', close));
    },

    init() {
        this.setupMobileMenu();
        console.log('[MarketWisdom] app', this.VERSION,
                    '| gate:', this.GATE_ENABLED ? 'ON' : 'OFF',
                    '| signed in?', this._isSignedIn(),
                    '| session key present?', !!localStorage.getItem('mw_auth'));
        this.bindNav();
        // Default to landing page; only respect a #hash if it's public or the user is signed in.
        const hashRoute = window.location.hash.replace('#', '');
        const initial = this._resolveRoute(hashRoute || 'home');
        this.navigate(initial);

        // Handle browser back/forward buttons
        window.addEventListener('hashchange', () => {
            this.navigate(this._resolveRoute(window.location.hash.replace('#', '') || 'home'));
        });

        // Re-render the current page after a sign-in / sign-out so gates update live.
        window.addEventListener('mw-auth-changed', () => {
            const route = window.location.hash.replace('#', '') || 'home';
            this.navigate(this._resolveRoute(route));
        });
    },

    _isSignedIn() {
        // NOTE: `const Auth = {...}` in a classic script does NOT set window.Auth,
        // so we check the identifier directly via typeof (safe even if undefined).
        return typeof Auth !== 'undefined' && !!(Auth.user && Auth.user());
    },

    _resolveRoute(route) {
        // Gate disabled → all routes are always themselves; no gate ever appears.
        if (!this.GATE_ENABLED) return route;
        // Public routes always resolve as themselves; everything else needs sign-in.
        if (this.PUBLIC_ROUTES.has(route)) return route;
        if (this._isSignedIn()) return route;
        return '__gate:' + route;
    },

    bindNav() {
        document.querySelectorAll('.nav-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const target = e.currentTarget.getAttribute('data-target');
                const current = window.location.hash.replace('#', '');
                if (current === target) {
                    // Same page tapped: no hashchange event fires, so navigate directly
                    this.navigate(this._resolveRoute(target));
                } else {
                    window.location.hash = target;
                }
            });
        });
    },

    updateNavState(route) {
        const shown = route.startsWith('__gate:') ? route.slice(7) : route;
        document.querySelectorAll('.nav-btn').forEach(btn => {
            if (btn.getAttribute('data-target') === shown) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
    },

    renderSignInGate(container, attemptedRoute) {
        // Self-heal: if the user IS already signed in but somehow landed on the gate,
        // just navigate them into the page they wanted. This is what fixed the "chip
        // says signed-in but gate still shows" bug.
        if (this._isSignedIn()) {
            this.navigate(attemptedRoute);
            return;
        }
        const label = ({
            global: 'Global Market', 'war-news': 'War News', telegram: 'Telegram Feed',
            screener: 'Stock Screener', master: 'Master Screener', smallmid: 'Small/Mid Master', microcap: 'Micro Cap Scanner', gems: 'Hidden Gems', stock: 'Stock Research',
            overview: 'Stock Overview', action: 'Stock Action', heatmap: 'Indices Heatmap', chartink: 'Chartink Comparator',
            nifty: 'Nifty Analysis', watchlist: 'Watchlist',
        })[attemptedRoute] || 'this page';
        container.innerHTML = `
            <div class="card" style="max-width:520px;margin:60px auto;text-align:center;padding:44px 28px;border-color:rgba(129,140,248,0.35)">
                <div style="font-size:52px;margin-bottom:14px">🔒</div>
                <h2 style="font-family:'Space Grotesk',sans-serif;font-size:22px;font-weight:800;margin:0 0 8px">Please sign in first</h2>
                <p style="color:var(--text-secondary);font-size:14px;line-height:1.6;margin:0 0 22px">
                    Please sign in with Google to see <b style="color:var(--text-primary)">${label}</b> and the rest of Market Wisdom.
                    Your watchlist and preferences will be saved to your account.
                </p>
                <div id="gate-signin" style="display:flex;justify-content:center;margin-bottom:10px"></div>
                <div style="font-size:12px;color:var(--text-secondary)">
                    Or <a href="#home" style="color:var(--text-accent);text-decoration:none;font-weight:600">go back to the landing page</a>
                </div>
            </div>
        `;
        const boot = () => {
            if (window.google && google.accounts && google.accounts.id && typeof Auth !== "undefined") {
                try {
                    // Re-initialize so the callback is wired for THIS button too.
                    // Without this, clicking "Sign in as ..." talks to Google but never
                    // calls our /api/auth/google endpoint — so the session never lands.
                    google.accounts.id.initialize({
                        client_id: GOOGLE_CLIENT_ID,
                        callback: (r) => Auth.handleCredential(r),
                    });
                    google.accounts.id.renderButton(
                        document.getElementById('gate-signin'),
                        { theme: 'filled_blue', size: 'large', shape: 'pill', text: 'signin_with', width: 260 }
                    );
                } catch (e) { console.warn('Gate GSI init failed', e); }
            } else {
                setTimeout(boot, 250);
            }
        };
        boot();
    },

    navigate(route) {
        this.updateNavState(route);
        const container = document.getElementById('app-container');
        container.innerHTML = ''; // Clear current view

        // Gated: user tried to open a page but isn't signed in.
        if (route.startsWith('__gate:')) {
            this.renderSignInGate(container, route.slice(7));
            return;
        }

        switch(route) {
            case 'home':
                this.renderHome(container);
                break;
            case 'global':
                this.renderGlobalMarket(container);
                break;
            case 'heatmap':
                this.renderHeatmap(container);
                break;
            case 'war-news':
                this.renderWarNews(container);
                break;
            case 'telegram':
                this.renderTelegramFeed(container);
                break;
            case 'screener':
                this.renderScreener(container);
                break;
            case 'master':
                this.renderMasterScreener(container);
                break;
            case 'smallmid':
                this.renderSmallMidMasterScreener(container);
                break;
            case 'microcap':
                this.renderMicroCapScreener(container);
                break;
            case 'gems':
                this.renderHiddenGems(container);
                break;
            case 'stock':
                this.renderStock(container);
                break;
            case 'overview':
                this.renderStockOverview(container);
                break;
            case 'action':
                this.renderStockAction(container);
                break;
            case 'chartink':
                this.renderChartink(container);
                break;
            case 'nifty':
                this.renderNifty(container);
                break;
            case 'fno':
                this.renderFnoDashboard(container);
                break;
            case 'watchlist':
                this.renderWatchlist(container);
                break;
            default:
                this.renderHome(container);
        }
    },

    // -------------------------------------------------------------
    // VIEWS
    // -------------------------------------------------------------

    renderGlobalMarket(container) {
        container.innerHTML = `
            <div style="margin-bottom:20px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                <div>
                    <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">🌍 Global Market Overview</h2>
                    <p style="font-size:13px;color:var(--text-secondary)">Live indices, commodities & FX · accurate values · newest market news on top</p>
                </div>
                <button id="btn-refresh-global" class="btn" style="padding:8px 16px;font-size:13px;">🔄 Refresh</button>
            </div>
            <div id="global-content">
                <div class="card" style="text-align:center;padding:40px">
                    <div class="big-spinner"></div>
                    <div style="color:var(--text-accent);font-weight:600">Fetching global data...</div>
                </div>
            </div>
        `;

        const esc = (s) => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        const col = (v) => (v>0?'var(--green)':v<0?'var(--red)':'var(--text-secondary)');
        const sign = (v) => (v>0?'+':'');
        const fmtPrice = (p) => (p==='N/A'||p==null)?'N/A':Number(p).toLocaleString(undefined,{maximumFractionDigits:2});

        const tableHTML = (title, emoji, items, accent) => {
            if (!items || !items.length) return '';
            let h = '<div class="card" style="padding:0;overflow:hidden;border-top:3px solid '+accent+'">'
                + '<div style="padding:14px 16px;font-weight:800;color:var(--text-primary);font-size:15px">'+emoji+' '+esc(title)+'</div>'
                + '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">'
                + '<thead><tr style="background:rgba(15,23,42,0.04);text-align:left">'
                + '<th style="padding:10px 16px;color:var(--text-secondary);font-size:11px;text-transform:uppercase">Name</th>'
                + '<th style="padding:10px 16px;color:var(--text-secondary);font-size:11px;text-transform:uppercase;text-align:right">Price</th>'
                + '<th style="padding:10px 16px;color:var(--text-secondary);font-size:11px;text-transform:uppercase;text-align:right">Change</th>'
                + '<th style="padding:10px 16px;color:var(--text-secondary);font-size:11px;text-transform:uppercase;text-align:right">%</th>'
                + '</tr></thead><tbody>';
            items.forEach(d => {
                const c = col(d.change);
                const unit = d.unit ? ' <span style="font-size:10px;color:var(--text-secondary)">'+esc(d.unit)+'</span>' : '';
                const na = d.price==='N/A';
                h += '<tr style="border-bottom:1px solid rgba(15,23,42,0.06)">'
                    + '<td style="padding:12px 16px;font-weight:600;color:var(--text-primary)">'+esc(d.name)+'</td>'
                    + '<td style="padding:12px 16px;text-align:right;font-weight:700">'+fmtPrice(d.price)+unit+'</td>'
                    + '<td style="padding:12px 16px;text-align:right;color:'+c+';font-weight:600">'+(na?'—':sign(d.change)+d.change)+'</td>'
                    + '<td style="padding:12px 16px;text-align:right;color:'+c+';font-weight:700">'+(na?'—':sign(d.pct)+d.pct+'%')+'</td>'
                    + '</tr>';
            });
            h += '</tbody></table></div></div>';
            return h;
        };

        const load = async () => {
            const content = document.getElementById('global-content');
            content.innerHTML = '<div class="card" style="text-align:center;padding:40px"><div class="big-spinner"></div><div style="color:var(--text-accent);font-weight:600">Fetching global data...</div></div>';
            try {
                const data = await api.fetchGlobalMarket();
                let html = '';

                // Biggest moves strip (computed from real index data)
                if (data.movers && data.movers.length) {
                    html += '<div class="card" style="margin-bottom:16px;padding:14px 16px;background:rgba(99,102,241,0.06)">'
                        + '<div style="font-size:12px;font-weight:800;color:var(--text-accent);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">⚡ Today\'s Biggest Moves</div>'
                        + '<div style="display:flex;gap:10px;flex-wrap:wrap">';
                    data.movers.forEach(mv => {
                        const c = col(mv.pct);
                        html += '<div style="background:'+c+'1a;border:1px solid '+c+'40;border-radius:8px;padding:8px 12px">'
                            + '<span style="font-weight:700;color:var(--text-primary);font-size:13px">'+esc(mv.name)+'</span>'
                            + '<span style="font-weight:800;color:'+c+';margin-left:8px">'+sign(mv.pct)+mv.pct+'%</span></div>';
                    });
                    html += '</div></div>';
                }

                // Key events strip (grounded AI)
                if (data.events && data.events.length) {
                    html += '<div class="card" style="margin-bottom:24px;padding:14px 16px;background:rgba(245,158,11,0.06);border-color:rgba(245,158,11,0.2)">'
                        + '<div style="font-size:12px;font-weight:800;color:var(--yellow);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">📅 Key Events This Week <span style="font-weight:500;text-transform:none;color:var(--text-secondary)">· AI-curated from web, verify times</span></div>'
                        + '<div style="display:flex;flex-direction:column;gap:8px">';
                    data.events.forEach(ev => {
                        const hi = String(ev.importance||'').toLowerCase()==='high';
                        html += '<div style="display:flex;gap:12px;align-items:center;font-size:13px;flex-wrap:wrap">'
                            + '<span style="min-width:56px;font-weight:700;color:var(--text-primary)">'+esc(ev.date||'')+'</span>'
                            + '<span style="background:rgba(15,23,42,0.08);border-radius:5px;padding:2px 8px;font-size:11px;font-weight:700;color:var(--text-accent)">'+esc(ev.region||'')+'</span>'
                            + '<span style="color:var(--text-primary)">'+(hi?'🔴 ':'')+esc(ev.title||'')+'</span></div>';
                    });
                    html += '</div></div>';
                }

                // Commodities (own table)
                html += tableHTML('Commodities', '🛢️', data.commodities, '#f59e0b');
                // Asia + Europe side by side
                html += '<div class="two-col" style="margin-top:16px;align-items:start">';
                html += tableHTML('Asian Markets', '🌏', data.asia, '#ef4444');
                html += tableHTML('European Markets', '🇪🇺', data.europe, '#3b82f6');
                html += '</div>';
                // US full width
                html += '<div style="margin-top:16px">' + tableHTML('US Markets', '🇺🇸', data.us, '#10b981') + '</div>';
                // Currencies
                if (data.currencies && data.currencies.length) {
                    html += '<div style="margin-top:16px">' + tableHTML('Currencies', '💱', data.currencies, '#818cf8') + '</div>';
                }

                // Top market news (live, 15)
                if (data.news && data.news.length) {
                    html += '<div style="margin-top:32px;margin-bottom:14px"><h3 style="font-size:18px;font-weight:800;color:var(--text-primary)">📰 Top Market News <span style="font-size:12px;font-weight:500;color:var(--text-secondary)">· live, newest first</span></h3></div>';
                    html += '<div class="card" style="display:flex;flex-direction:column;gap:14px">';
                    data.news.forEach(n => {
                        const ta = n.time_ago || '';
                        const fresh = ta==='just now'||ta.endsWith('m ago')||ta==='1h ago';
                        const headline = n.link ? '<a href="'+esc(n.link)+'" target="_blank" rel="noopener" style="color:var(--text-primary);text-decoration:none">'+esc(n.headline)+'</a>' : esc(n.headline);
                        html += '<div style="padding-bottom:12px;border-bottom:1px solid rgba(15,23,42,0.06)">'
                            + '<div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline;margin-bottom:4px">'
                            + '<span style="font-size:11px;font-weight:700;color:'+(fresh?'var(--green)':'var(--text-secondary)')+'">'+(fresh?'🟢 ':'')+esc(ta)+'</span>'
                            + '<span style="font-size:10px;color:var(--text-secondary)">'+esc(n.date||'')+'</span></div>'
                            + '<div style="font-size:14px;font-weight:700;line-height:1.45;margin-bottom:5px">'+headline+'</div>'
                            + '<div style="font-size:11px;font-weight:700;color:var(--text-accent)">'+esc(n.source||'')+'</div></div>';
                    });
                    html += '</div>';
                }

                html += '<div style="text-align:right;margin-top:16px;font-size:11px;color:var(--text-secondary);font-style:italic">Values: yfinance (batched) • News: Google News • Updated: '+esc(data.updated||data.timestamp||'')+'</div>';
                content.innerHTML = html;
            } catch (err) {
                content.innerHTML = '<div class="card" style="text-align:center;padding:40px;border-color:var(--red)">'
                    + '<div style="font-size:40px;margin-bottom:16px">⚠️</div>'
                    + '<div style="color:var(--red);font-weight:800;font-size:18px">Failed to load Global Market Data</div>'
                    + '<div style="color:var(--text-secondary);margin-top:8px">'+esc(err.message)+'</div></div>';
            }
        };

        const rb = document.getElementById('btn-refresh-global');
        if (rb) rb.addEventListener('click', load);
        load();
    },

    renderHeatmap(container) {
        container.innerHTML = `
            <div style="margin-bottom:18px;display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px">
                <div>
                    <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">🗺️ Indices Heatmap</h2>
                    <p style="font-size:13px;color:var(--text-secondary)">Live NSE indices, coloured by today's % change · green = up, red = down</p>
                </div>
                <div style="display:flex;align-items:center;gap:12px">
                    <div id="heatmap-legend" style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--text-secondary)"></div>
                    <button class="btn" id="btn-refresh-heatmap" style="padding:8px 16px;font-size:13px">↻ Refresh</button>
                </div>
            </div>
            <div id="heatmap-body"><div style="text-align:center;padding:40px"><div class="big-spinner"></div><div style="color:var(--text-secondary);font-size:13px">Loading indices…</div></div></div>
        `;

        // Build the legend swatches once.
        const legend = document.getElementById('heatmap-legend');
        if (legend) {
            const stops = [-3, -1.5, 0, 1.5, 3];
            legend.innerHTML = '<span>-3%</span>' +
                stops.map(v => `<span style="display:inline-block;width:20px;height:12px;border-radius:2px;background:${tileColor(v)}"></span>`).join('') +
                '<span>+3%</span>';
        }

        // Diverging colour scale: red (neg) → neutral → green (pos), intensity by magnitude.
        function tileColor(pct) {
            if (pct === null || pct === undefined || isNaN(pct)) return '#e2e8f0';
            const cap = 3;                                   // saturate at ±3%
            const t = Math.max(-1, Math.min(1, pct / cap));  // -1..1
            const mag = Math.abs(t);
            // Light→deep shade so text stays readable; mix with white at low magnitude.
            if (t >= 0) {
                const base = [22, 163, 74];                  // green
                return mix([255,255,255], base, 0.15 + 0.85 * mag);
            } else {
                const base = [220, 38, 38];                  // red
                return mix([255,255,255], base, 0.15 + 0.85 * mag);
            }
        }
        function mix(a, b, w) {
            const r = Math.round(a[0] + (b[0]-a[0])*w);
            const g = Math.round(a[1] + (b[1]-a[1])*w);
            const bl = Math.round(a[2] + (b[2]-a[2])*w);
            return `rgb(${r},${g},${bl})`;
        }
        function textOn(pct) {
            // Deep tiles need white text; pale tiles keep dark text.
            return (pct !== null && Math.abs(pct) >= 1.2) ? '#ffffff' : '#1e293b';
        }

        const render = (data) => {
            const body = document.getElementById('heatmap-body');
            if (!body) return;
            if (!data || !data.ok || !data.groups || !data.groups.length) {
                body.innerHTML = `<div class="card" style="text-align:center;padding:36px;border-color:var(--yellow)">
                    <div style="font-size:34px;margin-bottom:10px">🗺️</div>
                    <div style="color:var(--yellow);font-weight:800">Couldn't load the indices feed</div>
                    <div style="color:var(--text-secondary);margin-top:6px;font-size:13px">${(data && data.error) ? data.error : 'NSE did not respond. Try Refresh in a moment.'}</div>
                </div>`;
                return;
            }
            let html = '';
            data.groups.forEach(g => {
                html += `<div style="margin-bottom:22px">
                    <div style="font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--text-accent);margin-bottom:10px">${g.group} <span style="color:var(--text-secondary);font-weight:600">· ${g.indices.length}</span></div>
                    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px">`;
                g.indices.forEach(ix => {
                    const bg = tileColor(ix.pct);
                    const fg = textOn(ix.pct);
                    const sign = (ix.pct !== null && ix.pct > 0) ? '+' : '';
                    const pctTxt = (ix.pct === null || ix.pct === undefined) ? '—' : `${sign}${ix.pct.toFixed(2)}%`;
                    const lastTxt = (ix.last === null || ix.last === undefined) ? '' : Number(ix.last).toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2});
                    html += `<div title="${ix.name}" style="background:${bg};color:${fg};border-radius:10px;padding:11px 12px;overflow:hidden">
                        <div style="font-size:11.5px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;opacity:.95">${ix.name}</div>
                        <div style="font-size:18px;font-weight:800;margin-top:4px;line-height:1">${pctTxt}</div>
                        <div style="font-size:10.5px;margin-top:3px;opacity:.85">${lastTxt}</div>
                    </div>`;
                });
                html += `</div></div>`;
            });
            html += `<div style="text-align:right;font-size:11px;color:var(--text-secondary);font-style:italic;margin-top:4px">${data.source === 'yfinance' ? 'Yahoo Finance (NSE feed unavailable — major indices only, no breadth) · ' : 'NSE · '}as of ${esc(data.timestamp || '')}</div>`;
            body.innerHTML = html;
        };
        const esc = (s) => String(s||'').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));

        const load = async () => {
            const body = document.getElementById('heatmap-body');
            try {
                const data = await api.fetchIndicesHeatmap();
                render(data);
            } catch (e) {
                if (body) body.innerHTML = `<div class="card" style="text-align:center;padding:30px;color:var(--text-secondary)">Could not load heatmap: ${e.message}</div>`;
            }
        };
        const rb = document.getElementById('btn-refresh-heatmap');
        if (rb) rb.addEventListener('click', load);
        load();
    },

    renderWarNews(container) {
        container.innerHTML = `
            <div style="margin-bottom:20px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                <div>
                    <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">📰 War News — Live</h2>
                    <p style="font-size:13px;color:var(--text-secondary)">Latest headlines · US-Iran-Middle East and Russia-Ukraine</p>
                </div>
                <button id="btn-refresh-warnews" class="btn" style="padding:8px 16px;font-size:13px;">🔄 Refresh</button>
            </div>
            <div id="warnews-content">
                <div class="card" style="text-align:center;padding:40px">
                    <div class="big-spinner"></div>
                    <div style="color:var(--text-accent);font-weight:600">Fetching latest headlines...</div>
                </div>
            </div>
        `;

        const esc = (s) => String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

        const load = async () => {
            const content = document.getElementById('warnews-content');
            content.innerHTML = `<div class="card" style="text-align:center;padding:40px"><div class="big-spinner"></div><div style="color:var(--text-accent);font-weight:600">Fetching latest headlines...</div></div>`;
            try {
                const data = await api.fetchWarNews();
                const conflicts = (data && data.conflicts) || [];
                const hasAny = conflicts.some(c => (c.items || []).length > 0);
                if (!hasAny) {
                    content.innerHTML = `<div class="card" style="text-align:center;padding:40px;color:var(--text-secondary)">No live headlines right now — try Refresh in a moment.</div>`;
                    return;
                }

                let html = '<div class="two-col">';
                conflicts.forEach(c => {
                    const items = c.items || [];
                    html += `<div class="card" style="border-top:4px solid ${c.color}; display:block; width:100%; align-self:start;">
                        <div class="section-title" style="color:${c.color}; display:flex; align-items:center; gap:8px;">
                            ${c.flag || '📰'} ${esc(c.title)}
                            <span style="margin-left:auto; font-size:11px; font-weight:600; color:var(--text-secondary)">${items.length} stories</span>
                        </div>
                        <div style="display:flex;flex-direction:column;gap:14px;margin-top:12px;">`;
                    if (items.length === 0) {
                        html += `<div style="color:var(--text-secondary);font-size:13px">No recent headlines.</div>`;
                    }
                    items.forEach(n => {
                        const ta = n.time_ago || '';
                        const fresh = ta === 'just now' || ta.endsWith('m ago') || ta === '1h ago';
                        const ago = ta ? `<span style="font-size:11px;font-weight:700;color:${fresh ? 'var(--green)' : 'var(--text-secondary)'}">${fresh ? '🟢 ' : ''}${esc(ta)}</span>` : '<span></span>';
                        const headline = n.link
                            ? `<a href="${esc(n.link)}" target="_blank" rel="noopener" style="color:var(--text-primary);text-decoration:none">${esc(n.headline)}</a>`
                            : esc(n.headline);
                        html += `
                            <div style="padding-bottom:12px;border-bottom:1px solid rgba(15,23,42,0.06)">
                                <div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline;margin-bottom:5px">
                                    ${ago}
                                    <span style="font-size:10px;color:var(--text-secondary)">${esc(n.date)}</span>
                                </div>
                                <div style="font-size:14px;font-weight:700;line-height:1.45;margin-bottom:6px">${headline}</div>
                                <div style="font-size:11px;font-weight:700;color:var(--text-accent)">${esc(n.source)}</div>
                            </div>`;
                    });
                    html += `</div></div>`;
                });
                html += '</div>';
                html += `<div style="text-align:right; margin-top:12px; font-size:11px; color:var(--text-secondary); font-style:italic;">Live from Google News • Updated: ${esc(data.updated || '')}</div>`;
                content.innerHTML = html;
            } catch (err) {
                content.innerHTML = `<div class="card" style="text-align:center; padding:40px; border-color:var(--red);">
                    <div style="font-size:40px; margin-bottom:16px;">⚠️</div>
                    <div style="color:var(--red); font-weight:800; font-size:18px;">Failed to load War News</div>
                    <div style="color:var(--text-secondary); margin-top:8px;">${esc(err.message)}</div>
                </div>`;
            }
        };

        const refreshBtn = document.getElementById('btn-refresh-warnews');
        if (refreshBtn) refreshBtn.addEventListener('click', load);

        load();
    },

    renderTelegramFeed(container) {
        container.innerHTML = `
            <div style="margin-bottom:24px; display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">💬 Telegram Feed</h2>
                    <p style="font-size:13px;color:var(--text-secondary)">Live updates from our official Telegram channel.</p>
                </div>
                <button id="btn-refresh-telegram" class="btn" style="background:#2AABEE;color:white;padding:8px 16px;font-size:13px;border-radius:24px;">🔄 Refresh Feed</button>
            </div>
            <div id="telegram-content">
                <div class="card" style="text-align:center;padding:40px">
                    <div class="big-spinner"></div>
                    <div style="color:#2AABEE;font-weight:600;margin-top:16px;">Loading Telegram Messages...</div>
                </div>
            </div>
        `;

        const load = async () => {
            const content = document.getElementById('telegram-content');
            try {
                const data = await api.fetchTelegramFeed();
                if (data.error) throw new Error(data.error);
                
                if (!data.messages || data.messages.length === 0) {
                    content.innerHTML = `<div class="card" style="text-align:center;padding:40px;color:var(--text-secondary)">No recent messages found.</div>`;
                    return;
                }

                let html = '<div style="display:flex; flex-direction:column; gap:16px; max-width:700px; margin:0 auto;">';
                data.messages.forEach(msg => {
                    html += `
                        <div class="card" style="border-left: 4px solid #2AABEE; padding:16px;">
                            <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px;">
                                <div style="font-weight:700; color:#2AABEE;">Market Wisdom</div>
                                <div style="font-size:11px; color:var(--text-secondary);">${msg.timestamp.replace('+00:00', '')}</div>
                            </div>
                            <div style="font-size:14px; line-height:1.6; color:var(--text-primary); white-space:pre-wrap;">${msg.text}</div>
                            ${msg.link ? `<div style="margin-top:12px;text-align:right;"><a href="${msg.link}" target="_blank" style="font-size:12px; color:#2AABEE; text-decoration:none;">View on Telegram →</a></div>` : ''}
                        </div>
                    `;
                });
                html += '</div>';
                html += `<div style="text-align:center; margin-top:20px; font-size:11px; color:var(--text-secondary); font-style:italic;">Last Refreshed: ${data.fetch_time}</div>`;
                content.innerHTML = html;
            } catch (err) {
                content.innerHTML = `<div class="card" style="text-align:center; padding:40px; border-color:var(--red);">
                    <div style="font-size:40px; margin-bottom:16px;">⚠️</div>
                    <div style="color:var(--red); font-weight:800; font-size:18px;">Failed to load Telegram Feed</div>
                    <div style="color:var(--text-secondary); margin-top:8px;">${err.message}</div>
                </div>`;
            }
        };

        const refreshBtn = document.getElementById('btn-refresh-telegram');
        if (refreshBtn) refreshBtn.addEventListener('click', () => {
            document.getElementById('telegram-content').innerHTML = '<div class="card" style="text-align:center;padding:40px"><div class="big-spinner"></div></div>';
            load();
        });

        load();
    },

    renderScreener(container) {
        container.innerHTML = `
            <div style="margin-bottom:24px">
                <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">📊 Stock Screener</h2>
                <p style="font-size:13px;color:var(--text-secondary)">Scan Nifty 500 or S&P 500 · P/E < 20 · Volume > 2x · RSI > 50</p>
            </div>
            <div class="card" style="margin-bottom:20px;display:flex;gap:12px;flex-wrap:wrap;align-items:center;">
                <label style="font-size:14px;font-weight:600;">Market:</label>
                <button id="btn-mkt-india" class="btn screener-mkt-btn active-mkt" style="padding:8px 20px;font-size:13px;" data-market="india">🇮🇳 Nifty 500</button>
                <button id="btn-mkt-us" class="btn screener-mkt-btn" style="padding:8px 20px;font-size:13px;background:var(--bg-card);color:var(--text-secondary);border:1px solid var(--border-color);" data-market="us">🇺🇸 S&P 500</button>
                <div style="flex:1"></div>
                <button id="btn-scan" class="btn" style="background:var(--accent-color);color:white;padding:10px 24px;font-size:14px;font-weight:700;">🔍 Scan Now</button>
            </div>
            <div class="card" style="margin-bottom:16px;padding:12px 16px;background:rgba(129,140,248,0.06);border-color:rgba(129,140,248,0.15);display:flex;gap:24px;flex-wrap:wrap;font-size:12px;color:var(--text-secondary);">
                <span><b style="color:var(--text-primary)">Filters:</b></span>
                <span>P/E < 20</span><span>Volume > 2x 20d Avg</span><span>RSI(14) > 50</span><span>🏆 Ranked by Score</span>
            </div>
            <div id="screener-status-bar" style="display:none"></div>
            <div id="screener-result"><div style="text-align:center;padding:30px;color:var(--text-secondary)"><div class="spinner"></div></div></div>

            <div id="screener-next-wrap" style="margin-top:36px">
                <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:14px">
                    <div>
                        <h3 style="font-size:18px;font-weight:800;color:var(--text-primary);margin-bottom:2px">🔭 Nifty 501-1000</h3>
                        <p style="font-size:12px;color:var(--text-secondary)">Same filters, on stocks beyond the top 500 (Total Market + Microcap, minus Nifty 500).</p>
                    </div>
                    <button id="btn-scan-next" class="btn" style="background:var(--accent-color);color:white;padding:10px 24px;font-size:14px;font-weight:700;">🔍 Scan Now</button>
                </div>
                <div id="screener-next-status-bar" style="display:none"></div>
                <div id="screener-next-result"><div style="text-align:center;padding:30px;color:var(--text-secondary)"><div class="spinner"></div></div></div>
            </div>
        `;
        const self = this;
        let selectedMarket = 'india';
        let pollTimer = null;
        let pollTimerNext = null;

        // ───────── Main table (Nifty 500 / S&P 500 toggle) ─────────
        const loadLastResults = async (market) => {
            const resDiv = document.getElementById('screener-result');
            if(!resDiv) return;
            try {
                const data = await api.getScreenerResults(market);
                if(data.empty){
                    resDiv.innerHTML='<div class="card" style="text-align:center;padding:40px;border-color:rgba(129,140,248,0.2)"><div style="font-size:40px;margin-bottom:16px">📊</div><div style="color:var(--text-accent);font-weight:800;font-size:16px">No previous scan results</div><div style="color:var(--text-secondary);margin-top:8px">Click <b>Scan Now</b> to run your first scan. You can navigate away and results will be saved.</div></div>';
                } else { resDiv.innerHTML = self.screenerTableHTML(data, market); }
            } catch(e) { resDiv.innerHTML='<div class="card" style="text-align:center;padding:20px;color:var(--text-secondary)">Could not load previous results.</div>'; }
            try {
                const st = await api.getScreenerStatus(market);
                if(st.status==='running') startPolling(market);
            } catch(e){}
        };

        const startPolling = (market) => {
            if(pollTimer) clearInterval(pollTimer);
            const btn=document.getElementById('btn-scan');
            const bar=document.getElementById('screener-status-bar');
            if(btn){btn.disabled=true;btn.innerHTML='<span class="spinner" style="vertical-align:middle;margin-right:6px"></span> Scanning...';}
            if(bar){bar.style.display='block';bar.innerHTML='<div class="card" style="padding:12px 16px;background:rgba(16,185,129,0.08);border-color:rgba(16,185,129,0.2);display:flex;align-items:center;gap:12px;margin-bottom:16px"><span class="spinner"></span><span style="color:var(--green);font-weight:600">Background scan running... You can navigate away. Results will be saved.</span></div>';}
            pollTimer = setInterval(async()=>{
                try {
                    const st = await api.getScreenerStatus(market);
                    if(st.status==='done'){
                        clearInterval(pollTimer);pollTimer=null;
                        if(btn){btn.disabled=false;btn.innerHTML='🔍 Scan Now';}
                        if(bar) bar.style.display='none';
                        loadLastResults(market);
                    } else if(st.status==='error'){
                        clearInterval(pollTimer);pollTimer=null;
                        if(btn){btn.disabled=false;btn.innerHTML='🔍 Scan Now';}
                        if(bar){bar.style.display='block';bar.innerHTML='<div class="card" style="padding:12px 16px;border-color:var(--red);margin-bottom:16px"><span style="color:var(--red);font-weight:600">Scan failed: '+(st.error||'Unknown error')+'</span></div>';}
                    }
                } catch(e){}
            }, 5000);
        };

        // ───────── Second table (Nifty 501-1000) ─────────
        const NEXT = 'india_next500';
        const loadNext = async () => {
            const el = document.getElementById('screener-next-result');
            if(!el) return;
            try {
                const data = await api.getScreenerResults(NEXT);
                if(data.empty){
                    el.innerHTML='<div class="card" style="text-align:center;padding:40px;border-color:rgba(129,140,248,0.2)"><div style="font-size:40px;margin-bottom:16px">🔭</div><div style="color:var(--text-accent);font-weight:800;font-size:16px">No previous scan results</div><div style="color:var(--text-secondary);margin-top:8px">Click <b>Scan Now</b> above to scan the 501-1000 universe. This set is larger, so the first scan can take a little longer. Results are saved.</div></div>';
                } else { el.innerHTML = self.screenerTableHTML(data, NEXT); }
            } catch(e) { el.innerHTML='<div class="card" style="text-align:center;padding:20px;color:var(--text-secondary)">Could not load previous results.</div>'; }
            try {
                const st = await api.getScreenerStatus(NEXT);
                if(st.status==='running') startPollingNext();
            } catch(e){}
        };

        const startPollingNext = () => {
            if(pollTimerNext) clearInterval(pollTimerNext);
            const btn=document.getElementById('btn-scan-next');
            const bar=document.getElementById('screener-next-status-bar');
            if(btn){btn.disabled=true;btn.innerHTML='<span class="spinner" style="vertical-align:middle;margin-right:6px"></span> Scanning...';}
            if(bar){bar.style.display='block';bar.innerHTML='<div class="card" style="padding:12px 16px;background:rgba(16,185,129,0.08);border-color:rgba(16,185,129,0.2);display:flex;align-items:center;gap:12px;margin-bottom:16px"><span class="spinner"></span><span style="color:var(--green);font-weight:600">Scanning Nifty 501-1000... You can navigate away. Results will be saved.</span></div>';}
            pollTimerNext = setInterval(async()=>{
                try {
                    const st = await api.getScreenerStatus(NEXT);
                    if(st.status==='done'){
                        clearInterval(pollTimerNext);pollTimerNext=null;
                        if(btn){btn.disabled=false;btn.innerHTML='🔍 Scan Now';}
                        if(bar) bar.style.display='none';
                        loadNext();
                    } else if(st.status==='error'){
                        clearInterval(pollTimerNext);pollTimerNext=null;
                        if(btn){btn.disabled=false;btn.innerHTML='🔍 Scan Now';}
                        if(bar){bar.style.display='block';bar.innerHTML='<div class="card" style="padding:12px 16px;border-color:var(--red);margin-bottom:16px"><span style="color:var(--red);font-weight:600">Scan failed: '+(st.error||'Unknown error')+'</span></div>';}
                    }
                } catch(e){}
            }, 5000);
        };

        // ───────── Toggle: 501-1000 table only applies to India ─────────
        document.querySelectorAll('.screener-mkt-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                selectedMarket = btn.dataset.market;
                document.querySelectorAll('.screener-mkt-btn').forEach(b => {
                    b.style.background='var(--bg-card)';b.style.color='var(--text-secondary)';
                    b.style.border='1px solid var(--border-color)';b.classList.remove('active-mkt');
                });
                btn.style.background='';btn.style.color='';btn.style.border='';btn.classList.add('active-mkt');
                loadLastResults(selectedMarket);
                const wrap = document.getElementById('screener-next-wrap');
                if(selectedMarket==='us'){
                    if(wrap) wrap.style.display='none';
                } else {
                    if(wrap) wrap.style.display='block';
                    loadNext();
                }
            });
        });

        document.getElementById('btn-scan').addEventListener('click', async()=>{
            try { await api.startScreenerScan(selectedMarket); startPolling(selectedMarket); }
            catch(err) { alert('Failed to start scan: '+err.message); }
        });
        document.getElementById('btn-scan-next').addEventListener('click', async()=>{
            try { await api.startScreenerScan(NEXT); startPollingNext(); }
            catch(err) { alert('Failed to start scan: '+err.message); }
        });

        loadLastResults(selectedMarket);
        loadNext();   // India is the default market, so show the 501-1000 table too
    },

    screenerTableHTML(data, market) {
        const cur = market === 'us' ? '$' : '₹';
        let h = '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px">';
        h += '<div class="card" style="flex:1;min-width:130px;text-align:center;padding:16px;border-left:4px solid var(--accent-color)"><div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase">Market</div><div style="font-size:20px;font-weight:800;margin-top:6px">'+data.market+'</div></div>';
        h += '<div class="card" style="flex:1;min-width:130px;text-align:center;padding:16px;border-left:4px solid #818cf8"><div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase">Scanned</div><div style="font-size:20px;font-weight:800;color:var(--text-accent);margin-top:6px">'+data.total_scanned+'</div></div>';
        h += '<div class="card" style="flex:1;min-width:130px;text-align:center;padding:16px;border-left:4px solid var(--green)"><div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase">Passed</div><div style="font-size:20px;font-weight:800;color:var(--green);margin-top:6px">'+data.total_passed+'</div></div>';
        h += '<div class="card" style="flex:1;min-width:130px;text-align:center;padding:16px;border-left:4px solid var(--yellow)"><div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase">Time</div><div style="font-size:20px;font-weight:800;color:var(--yellow);margin-top:6px">'+data.scan_time_seconds+'s</div></div>';
        h += '</div>';
        if(data.results && data.results.length>0){
            h+='<div class="card" style="padding:0;overflow:hidden"><div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">';
            h+='<thead><tr style="background:rgba(15,23,42,0.06);text-align:left">';
            h+='<th style="padding:14px 12px;color:var(--text-secondary);font-size:11px;text-transform:uppercase;width:40px">#</th>';
            h+='<th style="padding:14px 12px;color:var(--text-secondary);font-size:11px;text-transform:uppercase">Ticker</th>';
            h+='<th style="padding:14px 12px;color:var(--text-secondary);font-size:11px;text-transform:uppercase;text-align:right">Price</th>';
            h+='<th style="padding:14px 12px;color:var(--text-secondary);font-size:11px;text-transform:uppercase;text-align:right">P/E</th>';
            h+='<th style="padding:14px 12px;color:var(--text-secondary);font-size:11px;text-transform:uppercase;text-align:right">Vol Ratio</th>';
            h+='<th style="padding:14px 12px;color:var(--text-secondary);font-size:11px;text-transform:uppercase;text-align:right">RSI</th>';
            h+='<th style="padding:14px 12px;color:var(--text-secondary);font-size:11px;text-transform:uppercase;text-align:right">Score</th>';
            h+='</tr></thead><tbody>';
            data.results.forEach((s,i)=>{
                const r=i+1;
                const bg=r<=3?'background:rgba(16,185,129,0.08);':'';
                const re=r===1?'🥇':r===2?'🥈':r===3?'🥉':r;
                const vc=s.vol_ratio>=5?'var(--red)':s.vol_ratio>=3?'var(--yellow)':'var(--green)';
                const rc=s.rsi>=70?'var(--red)':s.rsi>=60?'var(--yellow)':'var(--green)';
                const sc=s.score>=60?'var(--green)':s.score>=40?'var(--yellow)':'var(--text-secondary)';
                h+='<tr style="border-bottom:1px solid rgba(15,23,42,0.06);'+bg+'">';
                h+='<td style="padding:14px 12px;font-weight:800;font-size:14px">'+re+'</td>';
                h+='<td style="padding:14px 12px;font-weight:800;color:var(--text-primary);font-size:14px">'+s.ticker+'</td>';
                h+='<td style="padding:14px 12px;text-align:right;font-weight:700;font-size:14px">'+cur+s.price.toLocaleString()+'</td>';
                h+='<td style="padding:14px 12px;text-align:right;color:var(--green);font-weight:600">'+s.pe+'</td>';
                h+='<td style="padding:14px 12px;text-align:right"><span style="background:'+vc+'20;color:'+vc+';padding:3px 8px;border-radius:10px;font-size:11px;font-weight:700">'+s.vol_ratio+'x</span></td>';
                h+='<td style="padding:14px 12px;text-align:right;color:'+rc+';font-weight:700">'+s.rsi+'</td>';
                h+='<td style="padding:14px 12px;text-align:right"><div style="display:flex;align-items:center;justify-content:flex-end;gap:8px"><div style="width:60px;height:6px;background:rgba(15,23,42,0.08);border-radius:3px;overflow:hidden"><div style="width:'+Math.min(s.score,100)+'%;height:100%;background:'+sc+';border-radius:3px"></div></div><span style="font-weight:800;color:'+sc+';font-size:13px">'+s.score+'</span></div></td>';
                h+='</tr>';
            });
            h+='</tbody></table></div></div>';
        } else {
            h+='<div class="card" style="text-align:center;padding:40px;border-color:var(--yellow)"><div style="font-size:40px;margin-bottom:16px">🔍</div><div style="color:var(--yellow);font-weight:800">No stocks passed all filters</div><div style="color:var(--text-secondary);margin-top:8px">Try during market hours.</div></div>';
        }
        h+='<div style="text-align:right;margin-top:16px;font-size:11px;color:var(--text-secondary);font-style:italic">Last scanned: '+data.timestamp+'</div>';
        return h;
    },

    renderMasterScreener(container, opts) {
        opts = opts || {};
        const MKT      = opts.market      || 'india_master';
        const TITLE    = opts.title       || 'Master Screener — Nifty 1000';
        const SUBTITLE = opts.subtitle    || 'One composite score from 12 technical + fundamental checks · daily &amp; weekly candles · Top 10 ranked';
        const HELP     = opts.help        || 'Scans ~1000 stocks with 1 year of data — the first run takes <b style="color:var(--text-primary)">2–5 minutes</b>. You can navigate away; results are saved.';
        const BTN      = opts.btnLabel    || '🏆 Run Master Scan';
        const SCAN_MSG = opts.scanMsg     || 'Scanning ~1000 stocks';
        const EMPTY    = opts.emptyBody   || 'Click <b>Run Master Scan</b> to rank the Nifty 1000. Run it once or twice a day, or weekly — results update with the market.';
        const GATE     = opts.gateNote    || 'Hard gate before scoring: price &gt; 200-DMA <i>and</i> weekly close &gt; 30-week MA <i>and</i> tradeable liquidity.';
        const SHOWMCAP = !!opts.showMcap;
        const idResult = 'master-result-' + MKT;
        const idStatus = 'master-status-' + MKT;
        const idBtn    = 'btn-scan-' + MKT;
        container.innerHTML = `
            <div style="margin-bottom:20px">
                <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">${TITLE}</h2>
                <p style="font-size:13px;color:var(--text-secondary)">${SUBTITLE}</p>
            </div>
            <div class="two-col" style="align-items:start;margin-bottom:16px">
                <div class="card" style="border-top:3px solid #818cf8">
                    <div style="font-weight:800;font-size:14px;margin-bottom:10px;color:var(--text-accent)">📐 Technical — 60 pts</div>
                    <div style="font-size:12.5px;color:var(--text-secondary);line-height:2">
                        Trend stack: Price &gt; 50-DMA &gt; 200-DMA <b style="color:var(--text-primary)">(16)</b><br>
                        Weekly close &gt; 30-week MA <b style="color:var(--text-primary)">(8)</b><br>
                        Daily RSI in the 50–70 power zone <b style="color:var(--text-primary)">(8)</b><br>
                        Weekly RSI &gt; 50 <b style="color:var(--text-primary)">(6)</b><br>
                        MACD above its signal line <b style="color:var(--text-primary)">(6)</b><br>
                        Outperforming Nifty over 6 months <b style="color:var(--text-primary)">(8)</b><br>
                        Within 15% of the 52-week high <b style="color:var(--text-primary)">(8)</b>
                    </div>
                </div>
                <div class="card" style="border-top:3px solid #fbbf24">
                    <div style="font-weight:800;font-size:14px;margin-bottom:10px;color:var(--yellow)">🧮 Fundamental — 40 pts <span style="font-weight:500;font-size:11px;color:var(--text-secondary)">(Screener.in, top 40 only)</span></div>
                    <div style="font-size:12.5px;color:var(--text-secondary);line-height:2">
                        ROE ≥ 20% <b style="color:var(--text-primary)">(10)</b> · ROCE ≥ 20% <b style="color:var(--text-primary)">(10)</b><br>
                        5-yr Sales CAGR ≥ 15% <b style="color:var(--text-primary)">(8)</b><br>
                        5-yr Profit CAGR ≥ 15% <b style="color:var(--text-primary)">(8)</b><br>
                        P/E ≤ 25 (valuation sanity) <b style="color:var(--text-primary)">(4)</b><br>
                        <span style="font-size:11.5px">${GATE}</span>
                    </div>
                </div>
            </div>
            <div class="card" style="margin-bottom:16px;display:flex;gap:12px;flex-wrap:wrap;align-items:center;">
                <div style="font-size:12px;color:var(--text-secondary)">${HELP}</div>
                <div style="flex:1"></div>
                <button id="${idBtn}" class="btn" style="background:var(--accent-color);color:white;padding:10px 24px;font-size:14px;font-weight:700;">${BTN}</button>
            </div>
            <div id="${idStatus}" style="display:none"></div>
            <div id="${idResult}"><div style="text-align:center;padding:30px;color:var(--text-secondary)"><div class="spinner"></div></div></div>
            <div style="text-align:center;margin-top:18px;font-size:11px;color:var(--text-secondary)">A high score means the stock currently passes more of the checks above — it is a screening aid, not investment advice. Do your own research before investing.</div>
        `;
        let pollTimer = null;

        const fmt = (v, suf='') => (v === null || v === undefined) ? '—' : v + suf;

        const renderResults = (data) => {
            const el = document.getElementById(idResult);
            if (!el) return;
            let h = '<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px">';
            h += '<div class="card" style="flex:1;min-width:130px;text-align:center;padding:16px;border-left:4px solid var(--accent-color)"><div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase">Universe</div><div style="font-size:20px;font-weight:800;margin-top:6px">'+data.market+'</div></div>';
            h += '<div class="card" style="flex:1;min-width:130px;text-align:center;padding:16px;border-left:4px solid #818cf8"><div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase">Scanned</div><div style="font-size:20px;font-weight:800;color:var(--text-accent);margin-top:6px">'+data.total_scanned+'</div></div>';
            h += '<div class="card" style="flex:1;min-width:130px;text-align:center;padding:16px;border-left:4px solid var(--green)"><div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase">Passed Gate</div><div style="font-size:20px;font-weight:800;color:var(--green);margin-top:6px">'+data.total_passed+'</div></div>';
            h += '<div class="card" style="flex:1;min-width:130px;text-align:center;padding:16px;border-left:4px solid var(--yellow)"><div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase">Time</div><div style="font-size:20px;font-weight:800;color:var(--yellow);margin-top:6px">'+data.scan_time_seconds+'s</div></div>';
            h += '</div>';
            if (data.results && data.results.length) {
                h += '<div class="card" style="padding:0;overflow:hidden"><div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;min-width:900px">';
                h += '<thead><tr style="background:rgba(15,23,42,0.06);text-align:left">';
                const cols = ['#','Ticker','Price'].concat(SHOWMCAP ? ['MCap ₹Cr'] : []).concat(['Score','Tech','Fund','RSI D/W','RS 6m','52WH Δ','ROE','ROCE','Sales 5y','Profit 5y','P/E']);
                cols.forEach((c,i)=>{
                    h += '<th style="padding:12px 10px;color:var(--text-secondary);font-size:11px;text-transform:uppercase;'+(i>1?'text-align:right':'')+'">'+c+'</th>';
                });
                h += '</tr></thead><tbody>';
                data.results.forEach(s => {
                    const bg = s.rank <= 3 ? 'background:rgba(16,185,129,0.08);' : '';
                    const medal = s.rank===1?'🥇':s.rank===2?'🥈':s.rank===3?'🥉':s.rank;
                    const sc = s.score>=75?'var(--green)':s.score>=55?'var(--yellow)':'var(--text-secondary)';
                    const rsCol = (s.rs_6m||0) >= 0 ? 'var(--green)' : 'var(--red)';
                    h += '<tr style="border-bottom:1px solid rgba(15,23,42,0.06);'+bg+'">';
                    h += '<td style="padding:12px 10px;font-weight:800">'+medal+'</td>';
                    h += '<td style="padding:12px 10px;font-weight:800;color:var(--text-primary)">'+s.ticker+(s.macd_bull?' <span title="MACD bullish" style="font-size:10px">📈</span>':'')+'</td>';
                    h += '<td style="padding:12px 10px;text-align:right;font-weight:700">₹'+Number(s.price).toLocaleString("en-IN")+'</td>';
                    if (SHOWMCAP) h += '<td style="padding:12px 10px;text-align:right;color:var(--text-secondary);font-weight:600">'+fmt(s.mcap)+'</td>';
                    h += '<td style="padding:12px 10px;text-align:right"><div style="display:flex;align-items:center;justify-content:flex-end;gap:8px"><div style="width:56px;height:6px;background:rgba(15,23,42,0.08);border-radius:3px;overflow:hidden"><div style="width:'+Math.min(s.score,100)+'%;height:100%;background:'+sc+'"></div></div><b style="color:'+sc+'">'+s.score+'</b></div></td>';
                    h += '<td style="padding:12px 10px;text-align:right;color:var(--text-accent);font-weight:600">'+s.tech_score+'</td>';
                    h += '<td style="padding:12px 10px;text-align:right;color:var(--yellow);font-weight:600">'+s.fund_score+'</td>';
                    h += '<td style="padding:12px 10px;text-align:right">'+fmt(s.rsi_d)+' / '+fmt(s.rsi_w)+'</td>';
                    h += '<td style="padding:12px 10px;text-align:right;color:'+rsCol+';font-weight:600">'+fmt(s.rs_6m,'%')+'</td>';
                    h += '<td style="padding:12px 10px;text-align:right">-'+fmt(s.dist_52wh,'%')+'</td>';
                    h += '<td style="padding:12px 10px;text-align:right">'+fmt(s.roe,'%')+'</td>';
                    h += '<td style="padding:12px 10px;text-align:right">'+fmt(s.roce,'%')+'</td>';
                    h += '<td style="padding:12px 10px;text-align:right">'+fmt(s.sales_g,'%')+'</td>';
                    h += '<td style="padding:12px 10px;text-align:right">'+fmt(s.profit_g,'%')+'</td>';
                    h += '<td style="padding:12px 10px;text-align:right">'+fmt(s.pe)+'</td>';
                    h += '</tr>';
                });
                h += '</tbody></table></div></div>';
            } else {
                h += '<div class="card" style="text-align:center;padding:40px;border-color:var(--yellow)"><div style="font-size:40px;margin-bottom:16px">🏆</div><div style="color:var(--yellow);font-weight:800">No stocks passed the gate</div><div style="color:var(--text-secondary);margin-top:8px">In weak markets few names trade above their 200-DMA and 30-week MA. Try again another day.</div></div>';
            }
            h += '<div style="text-align:right;margin-top:14px;font-size:11px;color:var(--text-secondary);font-style:italic">Last scanned: '+(data.timestamp||'')+'</div>';
            el.innerHTML = h;
        };

        const startPolling = () => {
            if (pollTimer) clearInterval(pollTimer);
            const btn = document.getElementById(idBtn);
            const bar = document.getElementById(idStatus);
            if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner" style="vertical-align:middle;margin-right:6px"></span> ' + SCAN_MSG + '...'; }
            if (bar) { bar.style.display='block'; bar.innerHTML = '<div class="card" style="padding:12px 16px;background:rgba(16,185,129,0.08);border-color:rgba(16,185,129,0.2);display:flex;align-items:center;gap:12px;margin-bottom:16px"><span class="spinner"></span><span style="color:var(--green);font-weight:600">' + SCAN_MSG + ' (2–5 min)... You can navigate away. Results will be saved.</span></div>'; }
            pollTimer = setInterval(async () => {
                try {
                    const st = await api.getScreenerStatus(MKT);
                    if (st.status === 'done') {
                        clearInterval(pollTimer); pollTimer = null;
                        if (btn) { btn.disabled = false; btn.innerHTML = BTN; }
                        if (bar) bar.style.display = 'none';
                        loadLast();
                    } else if (st.status === 'error') {
                        clearInterval(pollTimer); pollTimer = null;
                        if (btn) { btn.disabled = false; btn.innerHTML = BTN; }
                        if (bar) { bar.style.display='block'; bar.innerHTML = '<div class="card" style="padding:12px 16px;border-color:var(--red);margin-bottom:16px"><span style="color:var(--red);font-weight:600">Scan failed: '+(st.error||'Unknown error')+'</span></div>'; }
                    }
                } catch (e) {}
            }, 6000);
        };

        const loadLast = async () => {
            const el = document.getElementById(idResult);
            if (!el) return;
            try {
                const data = await api.getScreenerResults(MKT);
                if (data.empty) {
                    el.innerHTML = '<div class="card" style="text-align:center;padding:40px;border-color:rgba(129,140,248,0.2)"><div style="font-size:40px;margin-bottom:16px">🏆</div><div style="color:var(--text-accent);font-weight:800;font-size:16px">No scan yet</div><div style="color:var(--text-secondary);margin-top:8px">' + EMPTY + '</div></div>';
                } else { renderResults(data); }
            } catch (e) { el.innerHTML = '<div class="card" style="text-align:center;padding:20px;color:var(--text-secondary)">Could not load previous results.</div>'; }
            try {
                const st = await api.getScreenerStatus(MKT);
                if (st.status === 'running') startPolling();
            } catch (e) {}
        };

        document.getElementById(idBtn).addEventListener('click', async () => {
            try { await api.startScreenerScan(MKT); startPolling(); }
            catch (err) { alert('Failed to start scan: ' + err.message); }
        });

        loadLast();
    },

    renderSmallMidMasterScreener(container) {
        return this.renderMasterScreener(container, {
            market:    'india_smallmid_master',
            title:     '💎 Small &amp; Mid Cap Master — Beyond Nifty 1000',
            subtitle:  'Same 12-factor score on the small/mid-cap universe (NSE Total Market ∪ Microcap 250, minus Nifty 1000)',
            help:      'Scans the small/mid-cap universe with 1 year of data — the first run takes <b style="color:var(--text-primary)">2–4 minutes</b>. You can navigate away; results are saved.',
            btnLabel:  '💎 Run Small/Mid Scan',
            scanMsg:   'Scanning small &amp; mid caps',
            emptyBody: 'Click <b>Run Small/Mid Scan</b> to rank the small/mid-cap universe. Higher-risk category than the Master Screener; do extra due diligence before acting.',
        });
    },

    renderMicroCapScreener(container) {
        return this.renderMasterScreener(container, {
            market:    'india_microcap',
            title:     '🔬 Micro Cap Scanner — &lt; ₹2,000 Cr, coiled at highs',
            subtitle:  'The 12-factor engine on beyond-Nifty-1000 stocks, with two extra hard gates: market cap under ₹2,000 Cr (verified) and price within 7% of the 52-week high',
            help:      'Scans the beyond-Nifty-1000 universe with 1 year of data, then verifies market cap for every finalist — the first run takes <b style="color:var(--text-primary)">2–4 minutes</b>. You can navigate away; results are saved.',
            btnLabel:  '🔬 Run Micro Cap Scan',
            scanMsg:   'Scanning micro caps',
            emptyBody: 'Click <b>Run Micro Cap Scan</b> to rank micro caps trading within 7% of their 52-week high. Micro caps are the highest-risk category — thin liquidity, sharp swings. Position sizing matters more here than anywhere else.',
            gateNote:  'Hard gates before scoring: price &gt; 200-DMA <i>and</i> weekly close &gt; 30-week MA <i>and</i> tradeable liquidity <i>and</i> <b style="color:var(--text-primary)">within 7% of the 52-week high</b> <i>and</i> <b style="color:var(--text-primary)">market cap &lt; ₹2,000 Cr (verified via Screener.in)</b>.',
            showMcap:  true,
        });
    },

    renderHiddenGems(container) {
        const MKT = 'india_hidden_gems';
        container.innerHTML = `
            <div style="margin-bottom:18px">
                <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">💎 Hidden Gems — Wonder Stock Finder</h2>
                <p style="font-size:13px;color:var(--text-secondary)">A pro-trader fusion scan across the <b>full market</b> that tags each stock with the institutional setup it matches, then ranks by a composite <b>Wonder Score</b>.</p>
            </div>
            <div class="two-col" style="align-items:start;margin-bottom:16px">
                <div class="card" style="border-top:3px solid #16a34a">
                    <div style="font-weight:800;font-size:14px;margin-bottom:8px;color:var(--text-primary)">The three setups it hunts</div>
                    <div style="font-size:12.5px;color:var(--text-secondary);line-height:1.9">
                        <b>🤫 Stealth</b> — quality name grinding up on <i>rising volume</i>, still 8–25% below its high. The coil before the move.<br>
                        <b>🔄 Turnaround</b> — just reclaimed the 200-DMA with momentum turning up. A bottom with proof.<br>
                        <b>🚀 Breakout</b> — strong name pushing to new highs (&lt;5% away) on above-average volume.
                    </div>
                </div>
                <div class="card" style="border-top:3px solid #4f46e5">
                    <div style="font-weight:800;font-size:14px;margin-bottom:8px;color:var(--text-primary)">How the Wonder Score works</div>
                    <div style="font-size:12.5px;color:var(--text-secondary);line-height:1.9">
                        Technical strength <b>+</b> fundamental quality <b>+</b> a bonus for each setup matched. Stocks hitting <b>two or more setups at once</b> (confluence) get an extra boost — that overlap is the strongest tell.<br>
                        <span style="font-size:11.5px">Quality floor: every pick is liquid and above its 200-DMA — no falling knives.</span>
                    </div>
                </div>
            </div>
            <div class="card" style="margin-bottom:16px;display:flex;gap:12px;flex-wrap:wrap;align-items:center;">
                <div style="font-size:12px;color:var(--text-secondary)">Scans the full market (~1700 stocks) with 1 year of data — first run takes <b style="color:var(--text-primary)">3–6 minutes</b>. You can navigate away; results are saved. Click any gem for an AI deep-dive.</div>
                <div style="flex:1"></div>
                <button id="btn-scan-gems" class="btn" style="padding:10px 24px;font-size:14px;font-weight:700">💎 Find Hidden Gems</button>
            </div>
            <div id="gems-status" style="display:none"></div>
            <div id="gems-result"><div style="text-align:center;padding:30px;color:var(--text-secondary)"><div class="spinner"></div></div></div>
            <div style="text-align:center;margin-top:16px;font-size:11px;color:var(--text-secondary)">A high Wonder Score means the stock currently matches more of these setups with strong underlying data — a screening aid, not investment advice. Do your own research.</div>
            <div id="gems-modal" style="display:none;position:fixed;inset:0;background:rgba(15,23,42,0.55);z-index:300;align-items:center;justify-content:center;padding:20px">
                <div style="background:var(--bg-card);border-radius:16px;max-width:620px;width:100%;max-height:85vh;overflow:auto;padding:24px;box-shadow:0 20px 60px rgba(15,23,42,0.3)">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
                        <div id="gems-modal-title" style="font-size:18px;font-weight:800;color:var(--text-primary)"></div>
                        <button id="gems-modal-close" style="background:none;border:none;font-size:22px;cursor:pointer;color:var(--text-secondary)">×</button>
                    </div>
                    <div id="gems-modal-body"></div>
                </div>
            </div>
        `;
        let pollTimer = null;
        const fmt = (v, s='') => (v === null || v === undefined) ? '—' : v + s;

        const scoreColor = (v) => v >= 85 ? 'var(--green)' : v >= 65 ? 'var(--yellow)' : 'var(--text-secondary)';

        const renderResults = (data) => {
            const el = document.getElementById('gems-result');
            if (!el) return;
            let h = '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:18px">';
            h += '<div class="card" style="flex:1;min-width:120px;text-align:center;padding:14px;border-left:4px solid #4f46e5"><div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase">Scanned</div><div style="font-size:20px;font-weight:800;color:var(--text-accent);margin-top:4px">'+data.total_scanned+'</div></div>';
            h += '<div class="card" style="flex:1;min-width:120px;text-align:center;padding:14px;border-left:4px solid var(--green)"><div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase">Matched a setup</div><div style="font-size:20px;font-weight:800;color:var(--green);margin-top:4px">'+data.total_passed+'</div></div>';
            h += '<div class="card" style="flex:1;min-width:120px;text-align:center;padding:14px;border-left:4px solid var(--yellow)"><div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase">Scan time</div><div style="font-size:20px;font-weight:800;color:var(--yellow);margin-top:4px">'+data.scan_time_seconds+'s</div></div>';
            h += '</div>';
            if (data.results && data.results.length) {
                h += '<div style="display:grid;gap:10px">';
                data.results.forEach(s => {
                    const sc = scoreColor(s.wonder_score);
                    const tags = (s.setups || []).map(t => '<span style="background:rgba(79,70,229,0.08);border:1px solid rgba(79,70,229,0.2);color:var(--text-accent);font-size:11px;font-weight:700;padding:3px 9px;border-radius:14px">'+t+'</span>').join(' ');
                    const conf = (s.setups && s.setups.length >= 2) ? '<span style="background:rgba(22,163,74,0.12);color:var(--green);font-size:10px;font-weight:800;padding:3px 8px;border-radius:12px;margin-left:4px">CONFLUENCE</span>' : '';
                    const rsCol = (s.rs_6m||0) >= 0 ? 'var(--green)' : 'var(--red)';
                    h += '<div class="card gem-row" data-ticker="'+s.ticker+'" style="padding:14px 16px;cursor:pointer;transition:transform .15s" title="Click for AI deep-dive">';
                    h += '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">';
                    h += '<div style="display:flex;align-items:center;gap:12px;min-width:200px">';
                    h += '<div style="font-size:18px;font-weight:800;color:var(--text-secondary);width:28px">'+s.rank+'</div>';
                    h += '<div><div style="font-weight:800;color:var(--text-primary);font-size:15px">'+s.ticker+(s.macd_bull?' <span title="MACD bullish" style="font-size:11px">📈</span>':'')+'</div><div style="margin-top:5px;display:flex;gap:5px;flex-wrap:wrap;align-items:center">'+tags+conf+'</div></div>';
                    h += '</div>';
                    h += '<div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap">';
                    h += '<div style="text-align:right"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase">Price</div><div style="font-weight:700">₹'+Number(s.price).toLocaleString("en-IN")+'</div></div>';
                    h += '<div style="text-align:right"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase">RS 6m</div><div style="font-weight:700;color:'+rsCol+'">'+fmt(s.rs_6m,'%')+'</div></div>';
                    h += '<div style="text-align:right"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase">52wH Δ</div><div style="font-weight:700">-'+fmt(s.dist_52wh,'%')+'</div></div>';
                    h += '<div style="text-align:right"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase">Vol surge</div><div style="font-weight:700">'+fmt(s.vol_surge,'×')+'</div></div>';
                    h += '<div style="text-align:right;min-width:90px"><div style="font-size:10px;color:var(--text-secondary);text-transform:uppercase">Wonder</div><div style="display:flex;align-items:center;gap:6px;justify-content:flex-end"><div style="width:44px;height:6px;background:rgba(15,23,42,0.08);border-radius:3px;overflow:hidden"><div style="width:'+Math.min(s.wonder_score,100)+'%;height:100%;background:'+sc+'"></div></div><b style="color:'+sc+'">'+s.wonder_score+'</b></div></div>';
                    h += '</div></div></div>';
                });
                h += '</div>';
            } else {
                h += '<div class="card" style="text-align:center;padding:40px;border-color:var(--yellow)"><div style="font-size:40px;margin-bottom:14px">💎</div><div style="color:var(--yellow);font-weight:800">No gems matched today</div><div style="color:var(--text-secondary);margin-top:8px">In weak or choppy markets, few stocks show a clean accumulation, turnaround, or breakout setup. Try again another day.</div></div>';
            }
            h += '<div style="text-align:right;margin-top:12px;font-size:11px;color:var(--text-secondary);font-style:italic">Last scanned: '+(data.timestamp||'')+'</div>';
            el.innerHTML = h;

            // Wire row clicks -> deep dive
            el.querySelectorAll('.gem-row').forEach(row => {
                row.addEventListener('mouseenter', () => row.style.transform = 'translateY(-2px)');
                row.addEventListener('mouseleave', () => row.style.transform = 'translateY(0)');
                row.addEventListener('click', () => {
                    const t = row.getAttribute('data-ticker');
                    const stock = data.results.find(x => x.ticker === t);
                    if (stock) openDeepDive(stock);
                });
            });
        };

        const openDeepDive = async (stock) => {
            const modal = document.getElementById('gems-modal');
            const title = document.getElementById('gems-modal-title');
            const bodyEl = document.getElementById('gems-modal-body');
            title.innerHTML = '💎 ' + stock.ticker + ' <span style="font-size:12px;color:var(--text-secondary);font-weight:600">· Wonder ' + stock.wonder_score + '</span>';
            bodyEl.innerHTML = '<div style="text-align:center;padding:30px"><div class="spinner"></div><div style="color:var(--text-secondary);font-size:13px;margin-top:10px">Generating institutional deep-dive from live data…</div></div>';
            modal.style.display = 'flex';
            try {
                const r = await api.hiddenGemsDeepDive(stock);
                const txt = (r.analysis || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
                bodyEl.innerHTML = '<div style="font-size:13.5px;line-height:1.7;color:#334155">'+txt+'</div>';
            } catch (e) {
                bodyEl.innerHTML = '<div style="color:var(--red);font-size:13px">Could not generate the deep-dive: '+e.message+'</div>';
            }
        };

        document.getElementById('gems-modal-close').addEventListener('click', () => document.getElementById('gems-modal').style.display='none');
        document.getElementById('gems-modal').addEventListener('click', (e) => { if (e.target.id === 'gems-modal') e.currentTarget.style.display='none'; });

        const startPolling = () => {
            if (pollTimer) clearInterval(pollTimer);
            const btn = document.getElementById('btn-scan-gems');
            const bar = document.getElementById('gems-status');
            if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner" style="vertical-align:middle;margin-right:6px"></span> Scanning full market...'; }
            if (bar) { bar.style.display='block'; bar.innerHTML = '<div class="card" style="padding:12px 16px;background:rgba(22,163,74,0.06);border-color:rgba(22,163,74,0.25);display:flex;align-items:center;gap:12px;margin-bottom:16px"><span class="spinner"></span><span style="color:var(--green);font-weight:600">Hunting for hidden gems (3–6 min)... You can navigate away. Results will be saved.</span></div>'; }
            pollTimer = setInterval(async () => {
                try {
                    const st = await api.getScreenerStatus(MKT);
                    if (st.status === 'done') { clearInterval(pollTimer); pollTimer=null; if (btn){btn.disabled=false;btn.innerHTML='💎 Find Hidden Gems';} if (bar) bar.style.display='none'; loadLast(); }
                    else if (st.status === 'error') { clearInterval(pollTimer); pollTimer=null; if (btn){btn.disabled=false;btn.innerHTML='💎 Find Hidden Gems';} if (bar){bar.style.display='block';bar.innerHTML='<div class="card" style="padding:12px 16px;border-color:var(--red);margin-bottom:16px"><span style="color:var(--red);font-weight:600">Scan failed: '+(st.error||'Unknown error')+'</span></div>';} }
                } catch(e) {}
            }, 6000);
        };

        const loadLast = async () => {
            const el = document.getElementById('gems-result');
            if (!el) return;
            try {
                const data = await api.getScreenerResults(MKT);
                if (data.empty) {
                    el.innerHTML = '<div class="card" style="text-align:center;padding:40px;border-color:rgba(79,70,229,0.2)"><div style="font-size:40px;margin-bottom:14px">💎</div><div style="color:var(--text-accent);font-weight:800;font-size:16px">No scan yet</div><div style="color:var(--text-secondary);margin-top:8px">Click <b>Find Hidden Gems</b> to scan the full market for stealth accumulation, turnarounds, and breakouts. Best run after 4 PM IST.</div></div>';
                } else { renderResults(data); }
            } catch(e) { el.innerHTML = '<div class="card" style="text-align:center;padding:20px;color:var(--text-secondary)">Could not load previous results.</div>'; }
            try { const st = await api.getScreenerStatus(MKT); if (st.status === 'running') startPolling(); } catch(e) {}
        };

        document.getElementById('btn-scan-gems').addEventListener('click', async () => {
            try { await api.startScreenerScan(MKT); startPolling(); }
            catch (err) { alert('Failed to start scan: ' + err.message); }
        });

        loadLast();
    },

    renderHome(container) {
        const owl = `<svg viewBox="0 0 64 64" fill="none" style="width:84px;height:84px;filter:drop-shadow(0 8px 24px rgba(129,140,248,0.3))">
            <path d="M32 6C16 6 10 18 10 32c0 16 10 26 22 26s22-10 22-26C54 18 48 6 32 6Z" fill="#0f172a" stroke="#818cf8" stroke-width="2.5"/>
            <path d="M14 24 L24 17 L40 17 L50 24" fill="none" stroke="#818cf8" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
            <g stroke-linecap="round">
                <line x1="24" y1="25" x2="24" y2="45" stroke="#4ade80" stroke-width="2"/><rect x="20.5" y="29" width="7" height="11" rx="1.6" fill="#4ade80"/>
                <line x1="40" y1="25" x2="40" y2="45" stroke="#f87171" stroke-width="2"/><rect x="36.5" y="29" width="7" height="11" rx="1.6" fill="#f87171"/>
            </g>
            <path d="M32 41 L28 47 L36 47 Z" fill="#fbbf24"/>
        </svg>`;

        const card = (href, color, icon, title, desc, cta, isNew) => `
            <a href="${href}" class="feature-card" style="--card-color: ${color}">
                <div class="feature-icon" style="color:${color}">${icon}</div>
                <div class="feature-title">${title}${isNew ? ' <span style="font-size:10px;font-weight:800;color:#0f172a;background:var(--green);padding:2px 7px;border-radius:20px;vertical-align:middle;margin-left:4px">NEW</span>' : ''}</div>
                <div class="feature-desc">${desc}</div>
                <div class="feature-link">${cta} →</div>
            </a>`;

        const sectionLabel = (t) => `<div style="font-family:'Inter',sans-serif;font-size:12px;letter-spacing:2px;text-transform:uppercase;color:var(--text-accent);font-weight:700;margin:36px 0 14px">${t}</div>`;

        container.innerHTML = `
            <div style="text-align:center;padding:52px 0 8px">
                ${owl}
                <h1 style="font-size:34px;font-weight:900;margin:18px 0 6px;letter-spacing:-1px">
                    <span style="color:var(--text-primary)">Market</span> <span style="color:var(--text-accent)">Wisdom</span>
                </h1>
                <p style="color:var(--text-secondary);font-size:15px;max-width:560px;margin:0 auto">
                    Your personal market intelligence desk — live indices, fast stock research, screeners and Nifty analytics, in one place.
                </p>
                <div style="margin-top:22px">
                    <a href="https://t.me/marketwisdom_official" target="_blank" style="display:inline-flex;align-items:center;gap:8px;background:#2AABEE;color:white;padding:10px 20px;border-radius:24px;text-decoration:none;font-weight:600;font-size:14px;box-shadow:0 4px 15px rgba(42,171,238,0.3)">
                        <span style="font-size:18px">✈️</span> Join our official Telegram channel for live updates
                    </a>
                </div>
            </div>

            ${sectionLabel('Research & Analysis')}
            <div class="feature-grid">
                ${card('#overview', '#34d399', '🧭', 'Stock Overview', 'A fast, accurate snapshot of any stock — live price, CAGR, RSI, fundamentals, shareholding, quarterly results and a 6-month chart.', 'Open Overview', true)}
                ${card('#nifty', '#10b981', '📈', 'Nifty Analysis', "Nifty's 21-EMA & 200-DMA, RSI, day move, weekly/monthly PCR and a VIX-based expected range.", 'Analyze Trend')}
                ${card('#master', '#fbbf24', '🏆', 'Master Screener', 'Ranks the Nifty 1000 on 12 technical + fundamental checks and returns the Top 10 with a full score breakdown.', 'Rank the Market', true)}
                ${card('#smallmid', '#a78bfa', '💎', 'Small/Mid Master', 'Same 12-factor score, applied to the small &amp; mid-cap universe beyond the Nifty 1000. Higher risk, higher potential.', 'Rank Small/Mid', true)}
                ${card('#microcap', '#f472b6', '🔬', 'Micro Cap Scanner', 'Micro caps under ₹2,000 Cr trading within 7% of their 52-week high — the tightest momentum coil, ranked by the same 12-factor score.', 'Scan Micro Caps', true)}
                ${card('#gems', '#16a34a', '💎', 'Hidden Gems', 'The wonder-stock finder: a full-market fusion scan tagging stealth accumulation, turnarounds &amp; breakouts, ranked by Wonder Score with AI deep-dives.', 'Find Gems', true)}
                ${card('#screener', '#60a5fa', '📊', 'Stock Screener', 'Scan the Nifty 500 — and the next 501–1000 — for breakouts by P/E, volume spike and RSI.', 'Run a Scan')}
                ${card('#chartink', '#c084fc', '📋', 'Chartink Comparator', 'Find the stocks that appear in both of your favourite Chartink screeners.', 'Compare')}
            </div>

            ${sectionLabel('Markets & News')}
            <div class="feature-grid">
                ${card('#global', '#f59e0b', '🌍', 'Global Market', "World indices, commodities and FX in separate tables, plus today's biggest moves and live market news.", 'View Markets')}
                ${card('#heatmap', '#10b981', '🗺️', 'Indices Heatmap', "Every live NSE index in one colour-coded grid — spot sector rotation and market breadth at a glance.", 'Open Heatmap', true)}
                ${card('#war-news', '#ef4444', '📰', 'War News', 'Live US–Iran and Russia–Ukraine headlines, newest first, in two columns.', 'Read News')}
                ${card('#action', '#fbbf24', '⚡', 'Stock Action', 'The latest announcements, results and conference-call notes for a company.', 'View Action')}
            </div>

            ${sectionLabel('Your Space')}
            <div class="feature-grid">
                ${card('#watchlist', '#ec4899', '⭐', 'Watchlist', 'Your saved stocks, showing the price when you added them versus the current price.', 'View Saved')}
                ${card('#telegram', '#2AABEE', '💬', 'Telegram Feed', 'Live messages and updates straight from the Market Wisdom Telegram channel.', 'View Feed')}
            </div>

            <footer>Market Wisdom · For informational purposes only · Not investment advice</footer>
        `;
    },

    renderStockOverview(container) {
        container.innerHTML = `
            <div style="margin-bottom:24px">
                <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">🧭 Stock Overview</h2>
                <p style="font-size:13px;color:var(--text-secondary)">Live price · CAGR · RSI · Chart · Shareholding · Quarterly Results · News — sourced from NSE, Screener.in &amp; computed indicators</p>
            </div>
            <div class="card">
                <div style="display:flex;gap:10px;flex-wrap:wrap">
                    <input type="text" id="ovr-company" placeholder="Company name or NSE ticker e.g. Reliance / RELIANCE" style="flex:2;min-width:200px"/>
                    <button class="btn" id="btn-overview">Get Overview</button>
                </div>
                <div style="font-size:11px;color:var(--text-secondary);margin-top:10px">⚡ Fast mode: numbers are pulled from real sources and computed, not AI-generated. Typically 5-10 seconds; repeat lookups are instant.</div>
            </div>
            <div id="overview-result"></div>
        `;

        const run = async () => {
            const raw = document.getElementById('ovr-company').value.trim();
            if (!raw) return alert('Please enter a company name or ticker');
            // If the user typed something that looks like a bare ticker (all caps, no spaces), pass it as ticker.
            const looksLikeTicker = /^[A-Z0-9&.-]{2,20}$/.test(raw) && !raw.includes(' ');
            const company = looksLikeTicker ? raw : raw;
            const ticker  = looksLikeTicker ? raw.toUpperCase() : '';

            const btn = document.getElementById('btn-overview');
            const resDiv = document.getElementById('overview-result');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner" style="vertical-align:middle;margin-right:6px"></span>';
            resDiv.innerHTML = `
                <div class="card" style="text-align:center;padding:40px">
                    <div class="big-spinner"></div>
                    <div style="color:var(--text-accent);font-weight:600">Building overview for ${raw}...</div>
                    <div style="font-size:12px;color:var(--text-secondary);margin-top:8px">Fetching NSE price, Screener.in fundamentals &amp; computing technicals.</div>
                </div>
            `;
            try {
                const data = await api.fetchStockOverview(company, ticker);
                this.renderOverviewResult(resDiv, data);
            } catch (err) {
                resDiv.innerHTML = `<div class="error">❌ ${err.message}</div>`;
            } finally {
                btn.disabled = false;
                btn.textContent = 'Get Overview';
            }
        };

        document.getElementById('btn-overview').addEventListener('click', run);
        document.getElementById('ovr-company').addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
    },

    renderOverviewResult(container, d) {
        let html = '';

        // Header
        html += `
            <div class="card" style="background:linear-gradient(135deg,rgba(16,185,129,0.1),rgba(99,102,241,0.05)); border-color:var(--border-color)">
                <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px">
                    <div style="flex:1;min-width:240px">
                        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">
                            <span style="font-size:20px;font-weight:800">${d.company_name || ''}</span>
                            <span class="badge badge-blue">${d.ticker || ''}</span>
                            <span style="font-size:11px;color:var(--text-secondary)">${d.sector || ''}</span>
                        </div>
                        <p style="font-size:13px;color:var(--text-secondary);line-height:1.7;max-width:520px;margin-bottom:12px">${d.description || ''}</p>
                        ${Components.RatingButtons(d.ticker, d.company_name, d.sector, d.current_price)}
                    </div>
                    <div style="text-align:right">
                        <div style="font-size:28px;font-weight:800">Rs.${d.current_price || 'N/A'}</div>
                        <div style="font-size:12px;margin-top:4px">
                            <span style="color:var(--green)">52W H: Rs.${d.week_52_high || 'N/A'}</span>
                            <span style="color:var(--text-secondary)"> | </span>
                            <span style="color:var(--red)">52W L: Rs.${d.week_52_low || 'N/A'}</span>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // CAGR
        if (d.cagr) {
            html += `<div class="card"><div class="section-title">CAGR Returns <span style="font-size:11px;font-weight:500;color:var(--text-secondary)">· computed from price history</span></div><div class="stat-grid">`;
            Object.entries({ 'YTD': 'ytd', '3 Year': '3yr', '5 Year': '5yr', '10 Year': '10yr' }).forEach(([lbl, key]) => {
                const val = d.cagr[key] || 'N/A';
                const num = parseFloat(val);
                const col = isNaN(num) ? 'var(--text-secondary)' : num >= 0 ? 'var(--green)' : 'var(--red)';
                html += Components.StatCard(lbl, val, col);
            });
            html += `</div></div>`;
        }

        // Technical Analysis (computed)
        if (d.candle_analysis) {
            const ca = d.candle_analysis;
            html += `<div class="card" style="border-color:rgba(16,185,129,0.2)">`;
            html += `<div class="section-title">Technical Analysis <span style="font-size:11px;font-weight:500;color:var(--text-secondary)">· computed</span></div>`;
            html += Components.RsiGauge(ca.rsi_value, ca.rsi_signal);
            if (ca.rsi_note) html += `<p style="font-size:12px;color:var(--text-secondary);margin-bottom:14px">${ca.rsi_note}</p>`;
            html += Components.CheckRow('Trading above 21 EMA', ca.above_21_ema_daily, '', ca.ema_note);
            html += Components.CheckRow('Price-volume breakout', ca.price_volume_breakout, '', ca.breakout_note);
            html += Components.CheckRow('Volume spurt detected', ca.volume_spurt, '', ca.volume_note);
            html += `</div>`;
        }

        // Fundamentals (Screener.in)
        if (d.fundamental_checks) {
            const fc = d.fundamental_checks;
            html += `<div class="card" style="border-color:rgba(251,191,36,0.15)">`;
            html += `<div class="section-title">Fundamental Checks <span style="font-size:11px;font-weight:500;color:var(--text-secondary)">· Screener.in</span></div>`;
            html += Components.CheckRow('ROE > 20%', fc.roe_above_20, fc.roe_value, fc.roe_note);
            html += Components.CheckRow('ROCE > 20%', fc.roce_above_20, fc.roce_value, fc.roce_note);
            html += Components.CheckRow('Sales CAGR (15-20%)', fc.sales_cagr_15_to_20, fc.sales_cagr_value, fc.sales_cagr_note);
            html += `</div>`;
        }

        // Quarterly Results
        if (d.quarterly_results) {
            const qr = d.quarterly_results;
            html += `<div class="card"><div class="section-title">Quarterly Results <span style="font-size:11px;font-weight:500;color:var(--text-secondary)">· Screener.in</span></div><div style="overflow-x:auto"><table style="width:100%;text-align:right"><thead><tr><th style="text-align:left;padding-bottom:8px;color:var(--text-secondary)">Metric</th>`;
            qr.quarters.forEach(q => html += `<th style="padding-bottom:8px;color:var(--text-secondary)">${q}</th>`);
            html += `</tr></thead><tbody>`;
            const addQrRow = (label, dataArr, yoyArr) => {
                if (!dataArr) return;
                html += `<tr><td style="text-align:left;font-weight:600;padding:8px 0;border-bottom:1px solid rgba(15,23,42,0.06)">${label}</td>`;
                dataArr.forEach((v, i) => {
                    const yoy = yoyArr ? yoyArr[i] : null;
                    const yoyHtml = yoy && yoy !== 'N/A' ? `<br><span style="font-size:10px;color:${yoy.startsWith('+') ? 'var(--green)' : 'var(--red)'}">${yoy} YoY</span>` : '';
                    html += `<td style="padding:8px 0;border-bottom:1px solid rgba(15,23,42,0.06)">${v}${yoyHtml}</td>`;
                });
                html += `</tr>`;
            };
            addQrRow('Revenue', qr.revenue, qr.revenue_yoy);
            addQrRow('Net Profit', qr.profit, qr.profit_yoy);
            addQrRow('EPS', qr.eps, qr.eps_yoy);
            html += `</tbody></table></div></div>`;
        }

        // Shareholding
        if (d.holdings) {
            const sh = d.holdings;
            html += `<div class="card"><div class="section-title">Shareholding Pattern <span style="font-size:11px;font-weight:500;color:var(--text-secondary)">· Screener.in</span></div><div style="overflow-x:auto"><table style="width:100%;text-align:right"><thead><tr><th style="text-align:left;padding-bottom:8px;color:var(--text-secondary)">Investor</th>`;
            sh.quarters.forEach(q => html += `<th style="padding-bottom:8px;color:var(--text-secondary)">${q}</th>`);
            html += `</tr></thead><tbody>`;
            const addShRow = (label, dataArr) => {
                if (!dataArr || dataArr[0] === 'N/A') return;
                html += `<tr><td style="text-align:left;font-weight:600;padding:8px 0;border-bottom:1px solid rgba(15,23,42,0.06)">${label}</td>`;
                dataArr.forEach(v => html += `<td style="padding:8px 0;border-bottom:1px solid rgba(15,23,42,0.06)">${v}${v !== 'N/A' ? '%' : ''}</td>`);
                html += `</tr>`;
            };
            addShRow('Promoters', sh.promoter);
            addShRow('FIIs', sh.fii);
            addShRow('DIIs', sh.dii);
            html += `</tbody></table></div></div>`;
        }

        // Chart
        if (d.ohlcv && d.ohlcv.length > 0) {
            html += `
            <div class="card">
                <div class="section-title">6-Month Price Action (Daily)</div>
                <canvas id="overviewCanvas-${d.ticker}" style="width:100%;height:320px;background:#f8fafc;border-radius:6px"></canvas>
                <div style="display:flex;gap:16px;margin-top:10px;font-size:11px;color:var(--text-secondary);flex-wrap:wrap">
                    <span><span style="display:inline-block;width:10px;height:10px;background:var(--green);border-radius:2px;margin-right:4px"></span>Bullish</span>
                    <span><span style="display:inline-block;width:10px;height:10px;background:var(--red);border-radius:2px;margin-right:4px"></span>Bearish</span>
                    <span><span style="display:inline-block;width:20px;height:2px;background:#f59e0b;vertical-align:middle;margin-right:4px"></span>21 EMA</span>
                    <span><span style="display:inline-block;width:20px;height:2px;background:#6366f1;vertical-align:middle;margin-right:4px"></span>200 DMA</span>
                </div>
            </div>`;
            setTimeout(() => app.drawChart(`overviewCanvas-${d.ticker}`, d.ohlcv), 100);
        }

        // News
        if (d.news && d.news.length > 0) {
            html += `<div class="card"><div class="section-title">Recent News</div><div style="display:flex;flex-direction:column;gap:12px">`;
            d.news.slice(0, 5).forEach(n => {
                html += `
                <div style="padding-bottom:12px;border-bottom:1px solid rgba(15,23,42,0.06)">
                    <div style="font-size:14px;font-weight:600;margin-bottom:4px;color:var(--text-primary)">${n.headline}</div>
                    <div style="font-size:11px;color:var(--text-secondary)">
                        <span style="color:var(--text-accent)">${n.source}</span> • ${n.date}
                    </div>
                </div>`;
            });
            html += `</div></div>`;
        }

        // Sources footer
        html += `<div style="font-size:11px;color:var(--text-secondary);text-align:center;margin:8px 0 24px">
            Sources: Price &amp; 52W — NSE / yfinance · Fundamentals &amp; shareholding — Screener.in · RSI / EMA / CAGR — computed · Description — Gemini · News — Google News
        </div>`;

        container.innerHTML = html;
    },

    renderStock(container) {
        container.innerHTML = `
            <div style="margin-bottom:24px">
                <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">Stock Research</h2>
                <p style="font-size:13px;color:var(--text-secondary)">Live price · CAGR · RSI · Chart · Shareholding · Quarterly Results · News</p>
            </div>
            <div class="card">
                <div style="display:flex;gap:10px;flex-wrap:wrap">
                    <input type="text" id="srch-company" placeholder="Company name e.g. Reliance Industries" style="flex:2;min-width:200px"/>
                    <button class="btn" id="btn-analyse">Analyse</button>
                </div>
            </div>
            <div id="stock-result"></div>
        `;

        document.getElementById('btn-analyse').addEventListener('click', async (e) => {
            const company = document.getElementById('srch-company').value;
            const ticker = ""; // Automatically resolved by backend
            if (!company) return alert('Please enter a company name');
            
            const btn = e.target;
            const resDiv = document.getElementById('stock-result');
            
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner" style="vertical-align:middle;margin-right:6px"></span>';
            resDiv.innerHTML = `
                <div class="card" style="text-align:center;padding:40px">
                    <div class="big-spinner"></div>
                    <div style="color:var(--text-accent);font-weight:600">Researching ${company}...</div>
                    <div style="font-size:12px;color:var(--text-secondary);margin-top:8px">This takes 30-90 seconds. Gemini AI is thinking.</div>
                </div>
            `;

            try {
                const data = await api.fetchStock(company, ticker);
                this.renderStockResult(resDiv, data);
            } catch (err) {
                resDiv.innerHTML = `<div class="error">❌ ${err.message}</div>`;
            } finally {
                btn.disabled = false;
                btn.textContent = 'Analyse';
            }
        });
    },

    renderStockAction(container) {
        container.innerHTML = `
            <div style="margin-bottom:24px">
                <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">Stock Action</h2>
                <p style="font-size:13px;color:var(--text-secondary)">PE Ratio · Latest News · Block Deals</p>
            </div>
            <div class="card">
                <div style="display:flex;gap:10px;flex-wrap:wrap">
                    <input type="text" id="action-company" placeholder="Company name e.g. Reliance Industries" style="flex:2;min-width:200px"/>
                    <button class="btn" id="btn-action-analyse">Fetch Action</button>
                </div>
            </div>
            <div id="action-result"></div>
        `;

        document.getElementById('btn-action-analyse').addEventListener('click', async (e) => {
            const company = document.getElementById('action-company').value.trim();
            const ticker = ""; // Automatically resolved by backend
            if (!company) return alert('Please enter a company name');
            
            const btn = e.target;
            const resDiv = document.getElementById('action-result');
            
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner" style="vertical-align:middle;margin-right:6px"></span>';
            resDiv.innerHTML = `
                <div class="card" style="text-align:center;padding:40px">
                    <div class="big-spinner"></div>
                    <div style="color:var(--text-accent);font-weight:600">Fetching Stock Action for ${company}...</div>
                    <div style="font-size:12px;color:var(--text-secondary);margin-top:8px">Using Gemini to summarize block deals and news.</div>
                </div>
            `;

            try {
                const data = await api.fetchStockAction(company, ticker);
                this.renderStockActionResult(resDiv, data, company, ticker);
            } catch (err) {
                resDiv.innerHTML = `<div class="error">❌ ${err.message}</div>`;
            } finally {
                btn.disabled = false;
                btn.textContent = 'Fetch Action';
            }
        });
    },

    renderStockActionResult(container, d, company, ticker) {
        let html = '';
        
        // Header & PE
        html += `
            <div class="card" style="background:linear-gradient(135deg,rgba(245,158,11,0.1),rgba(16,185,129,0.05)); border-color:var(--border-color)">
                <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px">
                    <div>
                        <div style="font-size:20px;font-weight:800">${company}</div>
                        <div style="font-size:13px;color:var(--text-secondary);margin-top:4px">${ticker}</div>
                    </div>
                    <div style="text-align:right">
                        <div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px">P/E Ratio</div>
                        <div style="font-size:28px;font-weight:800;color:var(--yellow)">${d.pe}</div>
                    </div>
                </div>
            </div>
        `;

        // Action Summary
        if (d.action_summary) {
            html += `
            <div class="card" style="border-color:rgba(16,185,129,0.2)">
                <div class="section-title">⚡ Change of Hands & Deals</div>
                <p style="font-size:14px;color:var(--text-primary);line-height:1.6">${d.action_summary}</p>
            </div>`;
        }

        // News
        if (d.news && d.news.length > 0) {
            html += `<div class="card"><div class="section-title">Latest News</div><div style="display:flex;flex-direction:column;gap:12px">`;
            d.news.slice(0, 10).forEach(n => {
                html += `
                <div style="padding-bottom:12px;border-bottom:1px solid rgba(15,23,42,0.06)">
                    <div style="font-size:14px;font-weight:600;margin-bottom:4px;color:var(--text-primary)">${n.headline}</div>
                    <div style="font-size:11px;color:var(--text-secondary)">
                        <span style="color:var(--text-accent)">${n.source}</span> • ${n.date}
                    </div>
                </div>`;
            });
            html += `</div></div>`;
        } else if (d.news && d.news.length === 0) {
            html += `<div class="card"><div class="section-title">Latest News</div><p style="color:var(--text-secondary)">No recent news found.</p></div>`;
        }

        container.innerHTML = html;
    },

    renderStockResult(container, d) {
        // Build out the result HTML using components
        let html = '';
        
        // Header
        html += `
            <div class="card" style="background:linear-gradient(135deg,rgba(99,102,241,0.1),rgba(16,185,129,0.05)); border-color:var(--border-color)">
                <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px">
                    <div style="flex:1">
                        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">
                            <span style="font-size:20px;font-weight:800">${d.company_name || ''}</span>
                            <span class="badge badge-blue">${d.ticker || ''}</span>
                            <span style="font-size:11px;color:var(--text-secondary)">${d.sector || ''}</span>
                        </div>
                        <p style="font-size:13px;color:var(--text-secondary);line-height:1.7;max-width:500px;margin-bottom:12px">${d.description || ''}</p>
                        ${Components.RatingButtons(d.ticker, d.company_name, d.sector, d.current_price)}
                    </div>
                    <div style="text-align:right">
                        <div style="font-size:28px;font-weight:800">Rs.${d.current_price || ''}</div>
                        <div style="font-size:12px;margin-top:4px">
                            <span style="color:var(--green)">52W H: Rs.${d.week_52_high || ''}</span>
                            <span style="color:var(--text-secondary)"> | </span>
                            <span style="color:var(--red)">52W L: Rs.${d.week_52_low || ''}</span>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // CAGR
        if (d.cagr) {
            html += `<div class="card"><div class="section-title">CAGR Returns</div><div class="stat-grid">`;
            Object.entries({ 'YTD': 'ytd', '3 Year': '3yr', '5 Year': '5yr', '10 Year': '10yr' }).forEach(([lbl, key]) => {
                const val = d.cagr[key] || 'N/A';
                const num = parseFloat(val);
                const col = isNaN(num) ? 'var(--text-secondary)' : num >= 0 ? 'var(--green)' : 'var(--red)';
                html += Components.StatCard(lbl, val, col);
            });
            html += `</div></div>`;
        }

        // Analysis
        if (d.candle_analysis) {
            const ca = d.candle_analysis;
            html += `<div class="card" style="border-color:rgba(16,185,129,0.2)">`;
            html += `<div class="section-title">Technical Analysis (Gemini)</div>`;
            html += Components.RsiGauge(ca.rsi_value, ca.rsi_signal);
            if (ca.rsi_note) html += `<p style="font-size:12px;color:var(--text-secondary);margin-bottom:14px">${ca.rsi_note}</p>`;
            
            html += Components.CheckRow('Trading above 21 EMA', ca.above_21_ema_daily, '', ca.ema_note);
            html += Components.CheckRow('Price-volume breakout', ca.price_volume_breakout, '', ca.breakout_note);
            html += Components.CheckRow('Volume spurt detected', ca.volume_spurt, '', ca.volume_note);
            html += `</div>`;
        }

        // Fundamentals
        if (d.fundamental_checks) {
            const fc = d.fundamental_checks;
            html += `<div class="card" style="border-color:rgba(251,191,36,0.15)">`;
            html += `<div class="section-title">Fundamental Checks (Gemini)</div>`;
            html += Components.CheckRow('ROE > 20%', fc.roe_above_20, fc.roe_value, fc.roe_note);
            html += Components.CheckRow('ROCE > 20%', fc.roce_above_20, fc.roce_value, fc.roce_note);
            html += Components.CheckRow('Sales CAGR (15-20%)', fc.sales_cagr_15_to_20, fc.sales_cagr_value, fc.sales_cagr_note);
            html += `</div>`;
        }

        // Quarterly Results
        if (d.quarterly_results) {
            const qr = d.quarterly_results;
            html += `<div class="card"><div class="section-title">Quarterly Results</div><div style="overflow-x:auto"><table style="width:100%;text-align:right"><thead><tr><th style="text-align:left;padding-bottom:8px;color:var(--text-secondary)">Metric</th>`;
            qr.quarters.forEach(q => html += `<th style="padding-bottom:8px;color:var(--text-secondary)">${q}</th>`);
            html += `</tr></thead><tbody>`;

            const addQrRow = (label, dataArr, yoyArr) => {
                html += `<tr><td style="text-align:left;font-weight:600;padding:8px 0;border-bottom:1px solid rgba(15,23,42,0.06)">${label}</td>`;
                dataArr.forEach((v, i) => {
                    const yoy = yoyArr ? yoyArr[i] : null;
                    const yoyHtml = yoy && yoy !== 'N/A' ? `<br><span style="font-size:10px;color:${yoy.startsWith('+') ? 'var(--green)' : 'var(--red)'}">${yoy} YoY</span>` : '';
                    html += `<td style="padding:8px 0;border-bottom:1px solid rgba(15,23,42,0.06)">${v}${yoyHtml}</td>`;
                });
                html += `</tr>`;
            };

            addQrRow('Revenue', qr.revenue, qr.revenue_yoy);
            addQrRow('Net Profit', qr.profit, qr.profit_yoy);
            addQrRow('EPS', qr.eps, qr.eps_yoy);
            html += `</tbody></table></div></div>`;
        }

        // Shareholding
        if (d.holdings) {
            const sh = d.holdings;
            html += `<div class="card"><div class="section-title">Shareholding Pattern</div><div style="overflow-x:auto"><table style="width:100%;text-align:right"><thead><tr><th style="text-align:left;padding-bottom:8px;color:var(--text-secondary)">Investor</th>`;
            sh.quarters.forEach(q => html += `<th style="padding-bottom:8px;color:var(--text-secondary)">${q}</th>`);
            html += `</tr></thead><tbody>`;
            
            const addShRow = (label, dataArr) => {
                if (!dataArr || dataArr[0] === 'N/A') return;
                html += `<tr><td style="text-align:left;font-weight:600;padding:8px 0;border-bottom:1px solid rgba(15,23,42,0.06)">${label}</td>`;
                dataArr.forEach(v => html += `<td style="padding:8px 0;border-bottom:1px solid rgba(15,23,42,0.06)">${v}${v !== 'N/A' ? '%' : ''}</td>`);
                html += `</tr>`;
            };

            addShRow('Promoters', sh.promoter);
            addShRow('FIIs', sh.fii);
            addShRow('DIIs', sh.dii);
            html += `</tbody></table></div></div>`;
        }

        // Chart
        if (d.ohlcv && d.ohlcv.length > 0) {
            html += `
            <div class="card">
                <div class="section-title">6-Month Price Action (Daily)</div>
                <canvas id="stockCanvas-${d.ticker}" style="width:100%;height:320px;background:#f8fafc;border-radius:6px"></canvas>
                <div style="display:flex;gap:16px;margin-top:10px;font-size:11px;color:var(--text-secondary)">
                    <span><span style="display:inline-block;width:10px;height:10px;background:var(--green);border-radius:2px;margin-right:4px"></span>Bullish</span>
                    <span><span style="display:inline-block;width:10px;height:10px;background:var(--red);border-radius:2px;margin-right:4px"></span>Bearish</span>
                    <span><span style="display:inline-block;width:20px;height:2px;background:#f59e0b;vertical-align:middle;margin-right:4px"></span>21 EMA</span>
                </div>
            </div>`;
            setTimeout(() => app.drawChart(`stockCanvas-${d.ticker}`, d.ohlcv), 100);
        }

        // News
        if (d.news && d.news.length > 0) {
            html += `<div class="card"><div class="section-title">Recent News</div><div style="display:flex;flex-direction:column;gap:12px">`;
            d.news.slice(0, 5).forEach(n => {
                html += `
                <div style="padding-bottom:12px;border-bottom:1px solid rgba(15,23,42,0.06)">
                    <div style="font-size:14px;font-weight:600;margin-bottom:4px;color:var(--text-primary)">${n.headline}</div>
                    <div style="font-size:11px;color:var(--text-secondary)">
                        <span style="color:var(--text-accent)">${n.source}</span> • ${n.date}
                    </div>
                </div>`;
            });
            html += `</div></div>`;
        }

        container.innerHTML = html;
    },

    async rateStock(ticker, company, sector, price, rating) {
        if (!ticker) return alert("Ticker is required to rate a stock.");
        try {
            await api.rateStock(ticker, company, sector, price, rating);
            
            // Update UI buttons visually
            const msg = document.getElementById(`rating-msg-${ticker}`);
            const btnContainer = msg.parentElement;
            btnContainer.querySelectorAll('.rating-btn').forEach(btn => btn.classList.remove('active'));
            
            if (rating === 'good') btnContainer.querySelector('.r-good').classList.add('active');
            if (rating === 'average') btnContainer.querySelector('.r-avg').classList.add('active');
            if (rating === 'bad') btnContainer.querySelector('.r-bad').classList.add('active');
            
            msg.textContent = 'Saved!';
            setTimeout(() => msg.textContent = '', 2000);
        } catch (e) {
            alert("Failed to save rating: " + e.message);
        }
    },

    renderFnoDashboard(container) {
        container.innerHTML = `
            <div style="margin-bottom:24px">
                <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">F&O Dashboard</h2>
                <p style="font-size:13px;color:var(--text-secondary)">Live Option Chain from NSE India · PCR · Max Pain</p>
            </div>
            
            <div class="card" style="margin-bottom:20px; display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
                <label style="font-size:14px;font-weight:600;">Select Index:</label>
                <select id="fno-index" class="btn" style="background:var(--bg-card); color:var(--text-primary); border:1px solid var(--border-color);">
                    <option value="NIFTY">NIFTY</option>
                    <option value="BANKNIFTY">BANKNIFTY</option>
                    <option value="FINNIFTY">FINNIFTY</option>
                    <option value="MIDCPNIFTY">MIDCPNIFTY</option>
                </select>
                <button id="btn-fetch-oc" class="btn" style="background:var(--accent-color);color:white">Load Live Data</button>
            </div>
            
            <div id="fno-result"></div>
        `;
        
        document.getElementById('btn-fetch-oc').addEventListener('click', async () => {
            const sym = document.getElementById('fno-index').value;
            const resDiv = document.getElementById('fno-result');
            resDiv.innerHTML = '<div style="padding:40px;text-align:center"><div class="spinner"></div><div style="margin-top:10px;color:var(--text-secondary)">Fetching Live Data from NSE...</div></div>';
            
            try {
                const data = await api.fetchOptionChain(sym);
                if(data.error) throw new Error(data.error);
                
                // Render top stats
                let html = `
                <div class="stat-grid" style="margin-bottom:24px;">
                    <div class="stat-card" style="border-left:4px solid var(--accent-color);">
                        <div class="stat-label">Live PCR</div>
                        <div class="stat-val" style="color: ${data.pcr > 1.2 ? 'var(--green)' : data.pcr < 0.8 ? 'var(--red)' : 'var(--yellow)'}">${data.pcr}</div>
                    </div>
                    <div class="stat-card" style="border-left:4px solid var(--red);">
                        <div class="stat-label">Max Pain Strike</div>
                        <div class="stat-val">${data.max_pain}</div>
                    </div>
                    <div class="stat-card" style="border-left:4px solid var(--green);">
                        <div class="stat-label">Major Support (Put OI)</div>
                        <div class="stat-val">${data.support_strike}</div>
                    </div>
                    <div class="stat-card" style="border-left:4px solid var(--red);">
                        <div class="stat-label">Major Resistance (Call OI)</div>
                        <div class="stat-val">${data.resistance_strike}</div>
                    </div>
                </div>
                
                <h3 style="margin-bottom:12px;font-size:16px;">Option Chain near ATM (${data.underlying})</h3>
                <div style="overflow-x:auto;">
                    <table style="width:100%; border-collapse:collapse; background:var(--bg-card); border-radius:8px; overflow:hidden;">
                        <thead>
                            <tr style="background:rgba(15,23,42,0.06);">
                                <th style="padding:10px;text-align:right;color:var(--text-secondary);">Call OI</th>
                                <th style="padding:10px;text-align:right;color:var(--text-secondary);">LTP</th>
                                <th style="padding:10px;text-align:center;color:white;background:rgba(15,23,42,0.10);">STRIKE</th>
                                <th style="padding:10px;text-align:left;color:var(--text-secondary);">LTP</th>
                                <th style="padding:10px;text-align:left;color:var(--text-secondary);">Put OI</th>
                            </tr>
                        </thead>
                        <tbody>
                `;
                
                data.chain.forEach(row => {
                    const isAtm = Math.abs(row.strike - data.underlying) < 50;
                    const bgRow = isAtm ? 'background:rgba(16, 185, 129, 0.15); font-weight:bold;' : 'border-bottom:1px solid rgba(15,23,42,0.06);';
                    html += `
                        <tr style="${bgRow}">
                            <td style="padding:10px;text-align:right;color:var(--red);">${row.ce_oi.toLocaleString()}</td>
                            <td style="padding:10px;text-align:right;">₹${row.ce_price.toFixed(1)}</td>
                            <td style="padding:10px;text-align:center;background:rgba(15,23,42,0.06);">${row.strike}</td>
                            <td style="padding:10px;text-align:left;">₹${row.pe_price.toFixed(1)}</td>
                            <td style="padding:10px;text-align:left;color:var(--green);">${row.pe_oi.toLocaleString()}</td>
                        </tr>
                    `;
                });
                
                html += `</tbody></table></div>`;
                html += `<div style="text-align:right;margin-top:10px;font-size:11px;color:var(--text-secondary)">Data as of: ${data.timestamp}</div>`;
                
                resDiv.innerHTML = html;
            } catch(e) {
                resDiv.innerHTML = `<div class="error">Failed to load NSE Data: ${e.message}</div>`;
            }
        });
    },

    renderWatchlist(container) {
        const u = (typeof Auth !== 'undefined' && Auth.user) ? Auth.user() : null;
        const isAdmin = !!(u && u.is_admin);
        const adminBanner = isAdmin ? `
            <div id="admin-legacy-banner" class="card" style="margin-bottom:16px;padding:16px;border:1px solid rgba(251,191,36,0.3);background:rgba(251,191,36,0.06)">
                <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
                    <span style="font-size:22px">👑</span>
                    <div style="flex:1;min-width:220px">
                        <div style="font-weight:800;color:var(--yellow);font-size:14px">Admin: Legacy Watchlist</div>
                        <div id="admin-legacy-status" style="font-size:12px;color:var(--text-secondary);margin-top:2px">
                            The pre-auth shared watchlist (from before sign-in was added) is preserved on the server. You can peek at it, then import it into your account if you want it as your starting list.
                        </div>
                    </div>
                    <button id="btn-peek-legacy" class="btn" style="padding:8px 14px;font-size:12px;background:var(--bg-card);border:1px solid var(--border-color);color:var(--text-primary)">👀 Peek</button>
                    <button id="btn-import-legacy" class="btn" style="padding:8px 14px;font-size:12px;background:var(--yellow);color:#1a1a1a;font-weight:700">⬇ Import to my list</button>
                </div>
                <div id="admin-legacy-preview" style="margin-top:12px;display:none"></div>
            </div>` : '';
        container.innerHTML = `
            <div style="margin-bottom:24px;display:flex;justify-content:space-between;align-items:center;">
                <div>
                    <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">⭐ My Watchlist</h2>
                    <p style="font-size:13px;color:var(--text-secondary)">Stocks you have researched and rated${u ? ' — signed in as <b>' + (u.email || '') + '</b>' : ''}.</p>
                </div>
            </div>
            ${adminBanner}
            <div id="watchlist-content">
                <div style="text-align:center;padding:40px"><div class="spinner"></div></div>
            </div>
        `;

        if (isAdmin) {
            const setStatus = (html, color) => {
                const el = document.getElementById('admin-legacy-status');
                if (el) { el.innerHTML = html; if (color) el.style.color = color; }
            };
            document.getElementById('btn-peek-legacy').addEventListener('click', async () => {
                const box = document.getElementById('admin-legacy-preview');
                box.style.display = 'block';
                box.innerHTML = '<div style="color:var(--text-secondary);font-size:12px">Loading…</div>';
                try {
                    const data = await api.fetchLegacyWatchlist();
                    if (!data.count) {
                        box.innerHTML = '<div style="font-size:12px;color:var(--text-secondary);padding:6px 0">The legacy shared watchlist is empty (or never existed on this database).</div>';
                        return;
                    }
                    let h = '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:6px">Found <b style="color:var(--text-primary)">' + data.count + '</b> stock(s):</div>';
                    h += '<div style="max-height:220px;overflow:auto;border:1px solid var(--border-color);border-radius:8px"><table style="width:100%;font-size:12px;border-collapse:collapse">';
                    h += '<thead><tr style="background:rgba(15,23,42,0.03)"><th style="text-align:left;padding:8px 12px;color:var(--text-secondary)">Ticker</th><th style="text-align:left;padding:8px 12px;color:var(--text-secondary)">Company</th><th style="text-align:right;padding:8px 12px;color:var(--text-secondary)">Rating</th></tr></thead><tbody>';
                    data.rows.forEach(r => {
                        h += '<tr style="border-top:1px solid rgba(15,23,42,0.06)"><td style="padding:6px 12px;font-weight:700">' + (r.ticker || '') + '</td><td style="padding:6px 12px;color:var(--text-secondary)">' + (r.company_name || '') + '</td><td style="padding:6px 12px;text-align:right">' + (r.rating || '') + '</td></tr>';
                    });
                    h += '</tbody></table></div>';
                    box.innerHTML = h;
                } catch (err) {
                    box.innerHTML = '<div style="color:var(--red);font-size:12px">Failed: ' + err.message + '</div>';
                }
            });
            document.getElementById('btn-import-legacy').addEventListener('click', async (ev) => {
                if (!confirm('Import the legacy shared watchlist into your account? Safe to run multiple times — duplicates are ignored.')) return;
                ev.currentTarget.disabled = true;
                setStatus('Importing…', 'var(--text-accent)');
                try {
                    const r = await api.importLegacyWatchlist();
                    setStatus('✅ ' + r.message, 'var(--green)');
                    this.loadWatchlistData();
                } catch (err) {
                    setStatus('❌ ' + err.message, 'var(--red)');
                } finally {
                    ev.currentTarget.disabled = false;
                }
            });
        }

        this.loadWatchlistData();
    },

    async loadWatchlistData() {
        try {
            const data = await api.fetchWatchlist();
            const content = document.getElementById('watchlist-content');
            if (!content) return;

            if (data.length === 0) {
                content.innerHTML = `
                    <div class="card" style="text-align:center;padding:40px;color:var(--text-secondary)">
                        No stocks rated yet. Head over to <a href="#stock" style="color:var(--text-accent)">Stock Research</a> to analyze and rate companies!
                    </div>
                `;
                return;
            }

            const getRatingBadge = (r) => {
                if (r === 'good') return '<span class="badge badge-green">✅ Good</span>';
                if (r === 'average') return '<span class="badge" style="background:rgba(251,191,36,0.12);color:var(--yellow);border:1px solid rgba(251,191,36,0.3)">⭐ Average</span>';
                if (r === 'bad') return '<span class="badge" style="background:rgba(248,113,113,0.12);color:var(--red);border:1px solid rgba(248,113,113,0.3)">❌ Bad</span>';
                return r;
            }

            const rows = data.map((s, i) => {
                let livePriceHtml = '';
                if (s.live_price !== "N/A" && s.price !== "N/A") {
                    const savedP = parseFloat(s.price.replace(/,/g, ''));
                    const liveP = parseFloat(s.live_price);
                    if (!isNaN(savedP) && !isNaN(liveP) && savedP > 0) {
                        const pct = ((liveP - savedP) / savedP) * 100;
                        const col = pct >= 0 ? 'var(--green)' : 'var(--red)';
                        const sign = pct >= 0 ? '+' : '';
                        livePriceHtml = `<div style="font-size:11px;color:${col};font-weight:600;margin-top:4px">${sign}${pct.toFixed(2)}%</div>`;
                    }
                }
                
                return `
                    <tr>
                        <td>${i+1}</td>
                        <td><b>${s.company_name}</b><br><span style="font-size:11px;color:var(--text-secondary)">${s.ticker}</span></td>
                        <td>${s.sector}</td>
                        <td>
                            <div style="font-size:11px;color:var(--text-secondary)">Saved: Rs.${s.price}</div>
                            <div style="font-weight:600;margin-top:2px">Live: Rs.${s.live_price}</div>
                            ${livePriceHtml}
                        </td>
                        <td>${getRatingBadge(s.rating)}</td>
                        <td><button onclick="app.removeWatchlist('${s.ticker}')" style="background:rgba(248,113,113,0.2);color:var(--red);border:none;padding:5px 10px;border-radius:5px;cursor:pointer;">Remove</button></td>
                    </tr>
                `;
            });

            content.innerHTML = `
                <div class="card" style="padding:0;overflow:hidden">
                    <table>
                        <thead>
                            <tr><th>#</th><th>Stock</th><th>Sector</th><th>Price</th><th>Rating</th><th>Action</th></tr>
                        </thead>
                        <tbody>${rows.join('')}</tbody>
                    </table>
                </div>
            `;

        } catch (e) {
            document.getElementById('watchlist-content').innerHTML = `<div class="error">Failed to load watchlist: ${e.message}</div>`;
        }
    },

    async removeWatchlist(ticker) {
        if (!confirm('Remove this stock?')) return;
        try {
            await api.removeFromWatchlist(ticker);
            this.loadWatchlistData();
        } catch (e) {
            alert(e.message);
        }
    },

    // Chartink Comparator Rendering
    renderChartink(container) {
        container.innerHTML = `
            <div style="margin-bottom:24px">
                <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">📋 Chartink Comparator</h2>
                <p style="font-size:13px;color:var(--text-secondary)">Compare two screeners, or run a single one. Scans keep running in the background — navigate anywhere and come back for the result.</p>
            </div>

            <div class="card">
                <div class="two-col" style="margin-bottom:12px">
                    <div>
                        <label style="font-size:12px;color:var(--text-secondary);display:block;margin-bottom:4px">Screener 1 URL</label>
                        <input type="text" id="url1" value="https://chartink.com/screener/new-stage-2-new" />
                    </div>
                    <div>
                        <label style="font-size:12px;color:var(--text-secondary);display:block;margin-bottom:4px">Screener 1 Label</label>
                        <input type="text" id="label1" value="New Stage 2" />
                    </div>
                    <div>
                        <label style="font-size:12px;color:var(--text-secondary);display:block;margin-bottom:4px">Screener 2 URL</label>
                        <input type="text" id="url2" value="https://chartink.com/screener/stage-2-new" />
                    </div>
                    <div>
                        <label style="font-size:12px;color:var(--text-secondary);display:block;margin-bottom:4px">Screener 2 Label</label>
                        <input type="text" id="label2" value="Stage 2" />
                    </div>
                </div>
                <button class="btn" id="btn-chartink">🔍 Compare Screeners</button>
            </div>
            <div id="chartink-status" style="display:none"></div>
            <div id="chartink-result"></div>

            <div style="margin-top:36px;margin-bottom:14px">
                <h3 style="font-size:18px;font-weight:800;color:var(--text-primary);margin-bottom:2px">🔎 Run a Single Scanner</h3>
                <p style="font-size:12px;color:var(--text-secondary)">Paste any Chartink screener link — or pick one of your saved scanners. Every scanner you run is saved to your account automatically.</p>
            </div>
            <div class="card">
                <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px">
                    <select id="saved-scanners" style="flex:1;min-width:200px;padding:12px 14px;border-radius:10px;border:1px solid rgba(79,70,229,0.3);background:#fff;color:var(--text-primary);font-size:14px;font-family:inherit">
                        <option value="">— My saved scanners —</option>
                    </select>
                    <button id="btn-del-saved" title="Remove selected from saved" style="background:none;border:1px solid rgba(220,38,38,0.35);color:var(--red);border-radius:10px;padding:11px 14px;cursor:pointer;font-size:13px">🗑</button>
                </div>
                <div style="display:flex;gap:10px;flex-wrap:wrap">
                    <input type="text" id="single-url" placeholder="https://chartink.com/screener/your-scanner" style="flex:2;min-width:220px"/>
                    <button class="btn" id="btn-single-scan">🔎 Search</button>
                </div>
            </div>
            <div id="single-status" style="display:none"></div>
            <div id="single-result"></div>
        `;

        const esc = (s) => String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        const timers = { compare: null, single: null };

        const renderCompare = (data) => {
            const el = document.getElementById('chartink-result');
            if (!el) return;
            const l1 = data.label1 || 'S1', l2 = data.label2 || 'S2';
            let html = '<div class="stat-grid" style="margin-top:16px">';
            html += Components.StatCard(`${l1} Total`, data.count1, '#2563eb');
            html += Components.StatCard('Common', data.common_count, 'var(--green)');
            html += Components.StatCard(`${l2} Total`, data.count2, '#9333ea');
            html += '</div>';
            const mkTable = (title, stocks, color) => {
                let r = `<div class="card" style="padding:0;overflow:hidden"><div style="padding:12px 16px;font-weight:800;font-size:13px;color:${color}">${title} (${stocks.length})</div>`;
                r += stocks.length
                    ? '<div style="max-height:320px;overflow:auto"><table><tbody>' + stocks.map((s,i)=>`<tr><td style="width:34px;color:var(--text-secondary)">${i+1}</td><td style="color:${color};font-weight:600">${esc(s)}</td></tr>`).join('') + '</tbody></table></div>'
                    : '<p style="padding:0 16px 14px;color:var(--text-secondary);font-size:13px">No stocks</p>';
                return r + '</div>';
            };
            html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px">';
            html += mkTable('✅ Common', data.common || [], 'var(--green)');
            html += mkTable(`Only in ${l1}`, data.only_in_1 || [], '#2563eb');
            html += mkTable(`Only in ${l2}`, data.only_in_2 || [], '#9333ea');
            html += '</div>';
            html += `<div style="text-align:right;margin-top:10px;font-size:11px;color:var(--text-secondary);font-style:italic">Last scanned: ${esc(data.timestamp||'')}</div>`;
            el.innerHTML = html;
        };

        const renderSingle = (data) => {
            const el = document.getElementById('single-result');
            if (!el) return;
            const rows = data.rows || (data.stocks || []).map(s => ({ symbol: s, per_chg: null, close: null }));
            let html = `<div class="card" style="margin-top:16px">
                <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:12px">
                    <div style="font-weight:800;font-size:15px">${esc(data.name || 'Scanner')} <span style="color:var(--text-secondary);font-weight:600">— ${data.count} stock(s)</span></div>
                    <a href="${esc(data.url)}" target="_blank" rel="noopener" style="font-size:11px;color:var(--text-accent);text-decoration:none">open on Chartink ↗</a>
                </div>`;
            if (rows.length) {
                const fmtPct = (v) => (v === null || v === undefined) ? '—' : (v > 0 ? '+' : '') + v.toFixed(2) + '%';
                const fmtPr  = (v) => (v === null || v === undefined) ? '—' : '₹' + Number(v).toLocaleString('en-IN');
                html += '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">';
                html += '<thead><tr>'
                     +  '<th style="text-align:left">#</th>'
                     +  '<th style="text-align:left">Symbol</th>'
                     +  '<th style="text-align:right">% Chg</th>'
                     +  '<th style="text-align:right">Price</th>'
                     +  '</tr></thead><tbody>';
                rows.forEach((r, i) => {
                    const c = (r.per_chg === null || r.per_chg === undefined) ? 'var(--text-secondary)'
                              : (r.per_chg >= 0 ? 'var(--green)' : 'var(--red)');
                    html += `<tr>
                        <td style="color:var(--text-secondary);width:34px">${i+1}</td>
                        <td style="font-weight:700;color:var(--text-primary)">${esc(r.symbol)}</td>
                        <td style="text-align:right;font-weight:700;color:${c}">${fmtPct(r.per_chg)}</td>
                        <td style="text-align:right;color:#334155">${fmtPr(r.close)}</td>
                    </tr>`;
                });
                html += '</tbody></table></div>';
                html += '<div style="font-size:11px;color:var(--text-secondary);margin-top:8px">Shown in the scanner\u2019s own order, with the % change at scan time.</div>';
            } else {
                html += '<div style="color:var(--text-secondary);font-size:13px">The scanner returned no stocks right now.</div>';
            }
            html += `<div style="text-align:right;margin-top:12px;font-size:11px;color:var(--text-secondary);font-style:italic">Last scanned: ${esc(data.timestamp||'')}</div>`;
            html += '</div>';
            el.innerHTML = html;
        };

        const ui = {
            compare: { btn:'btn-chartink',    bar:'chartink-status', label:'🔍 Compare Screeners', render: renderCompare, msg:'Comparing screeners' },
            single:  { btn:'btn-single-scan', bar:'single-status',   label:'🔎 Search',            render: renderSingle,  msg:'Running scanner' },
        };

        const setRunning = (mode, on, errText) => {
            const u = ui[mode];
            const btn = document.getElementById(u.btn);
            const bar = document.getElementById(u.bar);
            if (btn) { btn.disabled = on; btn.innerHTML = on ? '<span class="spinner" style="vertical-align:middle;margin-right:6px"></span> Scanning...' : u.label; }
            if (!bar) return;
            if (on) {
                bar.style.display = 'block';
                bar.innerHTML = `<div class="card" style="padding:12px 16px;background:rgba(22,163,74,0.06);border-color:rgba(22,163,74,0.25);display:flex;align-items:center;gap:12px;margin-top:14px"><span class="spinner"></span><span style="color:var(--green);font-weight:600">${u.msg} in the background... You can navigate away — the result will be here when you return.</span></div>`;
            } else if (errText) {
                bar.style.display = 'block';
                bar.innerHTML = `<div class="card" style="padding:12px 16px;border-color:var(--red);margin-top:14px"><span style="color:var(--red);font-weight:600">Scan failed: ${esc(errText)}</span></div>`;
            } else {
                bar.style.display = 'none';
            }
        };

        const poll = (mode) => {
            if (timers[mode]) clearInterval(timers[mode]);
            setRunning(mode, true);
            timers[mode] = setInterval(async () => {
                try {
                    const st = await api.chartinkStatus(mode);
                    if (st.status === 'done') {
                        clearInterval(timers[mode]); timers[mode] = null;
                        setRunning(mode, false);
                        const data = await api.chartinkResults(mode);
                        if (!data.empty) ui[mode].render(data);
                    } else if (st.status === 'error') {
                        clearInterval(timers[mode]); timers[mode] = null;
                        setRunning(mode, false, st.error || 'Unknown error');
                    }
                } catch (e) {}
            }, 5000);
        };

        const resume = async (mode) => {
            try {
                const data = await api.chartinkResults(mode);
                if (!data.empty) {
                    ui[mode].render(data);
                    if (mode === 'compare') {
                        if (data.url1) document.getElementById('url1').value = data.url1;
                        if (data.url2) document.getElementById('url2').value = data.url2;
                        if (data.label1) document.getElementById('label1').value = data.label1;
                        if (data.label2) document.getElementById('label2').value = data.label2;
                    } else if (data.url) {
                        document.getElementById('single-url').value = data.url;
                    }
                }
            } catch (e) {}
            try {
                const st = await api.chartinkStatus(mode);
                if (st.status === 'running') poll(mode);
            } catch (e) {}
        };

        const loadSaved = async () => {
            try {
                const data = await api.chartinkSaved();
                const sel = document.getElementById('saved-scanners');
                if (!sel) return;
                const cur = sel.value;
                sel.innerHTML = '<option value="">— My saved scanners —</option>';
                (data.scanners || []).forEach(s => {
                    const o = document.createElement('option');
                    o.value = s.url;
                    o.textContent = `${s.name || 'Scanner'}  ·  ${s.url.replace('https://chartink.com/screener/','')}`;
                    sel.appendChild(o);
                });
                if (cur) sel.value = cur;
            } catch (e) {}
        };

        document.getElementById('btn-chartink').addEventListener('click', async () => {
            const u1 = document.getElementById('url1').value.trim();
            const u2 = document.getElementById('url2').value.trim();
            if (!u1 || !u2) return alert('Please enter both URLs');
            try {
                await api.chartinkRun({ mode:'compare', url1:u1, url2:u2,
                    label1: document.getElementById('label1').value.trim(),
                    label2: document.getElementById('label2').value.trim() });
                poll('compare');
                loadSaved();
            } catch (err) { alert('Failed to start: ' + err.message); }
        });

        document.getElementById('btn-single-scan').addEventListener('click', async () => {
            const url = document.getElementById('single-url').value.trim();
            if (!url) return alert('Please paste a Chartink screener URL or pick a saved one');
            try {
                await api.chartinkRun({ mode:'single', url });
                poll('single');
                loadSaved();
            } catch (err) { alert('Failed to start: ' + err.message); }
        });

        document.getElementById('saved-scanners').addEventListener('change', (e) => {
            if (e.target.value) document.getElementById('single-url').value = e.target.value;
        });

        document.getElementById('btn-del-saved').addEventListener('click', async () => {
            const sel = document.getElementById('saved-scanners');
            if (!sel.value) return alert('Pick a saved scanner from the dropdown first');
            if (!confirm('Remove this scanner from your saved list?')) return;
            try { await api.chartinkDeleteSaved(sel.value); sel.value=''; loadSaved(); } catch (e) {}
        });

        document.getElementById('single-url').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') document.getElementById('btn-single-scan').click();
        });

        loadSaved();
        resume('compare');
        resume('single');
    },

    // Nifty Analysis Rendering
    renderFiiData(fii) {
        const esc = (s) => String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        const prettyKey = (k) => String(k).replace(/_/g,' ').replace(/([a-z])([A-Z])/g,'$1 $2').replace(/\b\w/g, c=>c.toUpperCase());
        const fmtVal = (v) => {
            if (typeof v === 'number') return v.toLocaleString('en-IN');
            if (typeof v === 'string' && v.trim()!=='' && !isNaN(Number(v.replace(/,/g,'')))) return Number(v.replace(/,/g,'')).toLocaleString('en-IN');
            return esc(v);
        };
        const kvTable = (obj) => {
            let h = '<table style="width:100%;font-size:13px;border-collapse:collapse">';
            Object.entries(obj).forEach(([k,v]) => {
                const valHtml = (v && typeof v === 'object')
                    ? render(v)
                    : '<span style="font-weight:700;color:var(--text-primary)">'+fmtVal(v)+'</span>';
                h += '<tr><td style="padding:8px 12px;border-bottom:1px solid rgba(15,23,42,0.06);color:var(--text-secondary);vertical-align:top;white-space:nowrap">'+esc(prettyKey(k))+'</td>'
                   + '<td style="padding:8px 12px;border-bottom:1px solid rgba(15,23,42,0.06);text-align:right;vertical-align:top">'+valHtml+'</td></tr>';
            });
            return h + '</table>';
        };
        const render = (val) => {
            if (Array.isArray(val)) {
                if (!val.length) return '<span style="color:var(--text-secondary)">—</span>';
                return val.map(item => (item && typeof item === 'object')
                    ? '<div style="margin-bottom:10px;padding:8px;background:#f8fafc;border-radius:8px">'+kvTable(item)+'</div>'
                    : '<div>'+fmtVal(item)+'</div>').join('');
            }
            if (val && typeof val === 'object') return kvTable(val);
            return '<span style="font-weight:700">'+fmtVal(val)+'</span>';
        };
        try {
            return '<div style="overflow-x:auto">'+render(fii)+'</div>';
        } catch(e) {
            return '<div style="font-size:12px;color:var(--text-secondary)">FII data unavailable in a readable format.</div>';
        }
    },

    renderNifty(container) {
        container.innerHTML = `
            <div style="margin-bottom:24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px">
                <div>
                    <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">📈 Nifty Analysis</h2>
                    <p style="font-size:13px;color:var(--text-secondary)">PCR, FII Data, and Technical Charts</p>
                </div>
                <button class="btn" id="btn-nifty" style="padding:8px 16px;font-size:12px">🔄 Refresh</button>
            </div>
            <div id="nifty-content">
                 <div class="card" style="text-align:center;padding:40px">
                    <div class="big-spinner"></div>
                    <div style="color:var(--text-accent);font-weight:600">Fetching live NSE data...</div>
                    <div style="color:var(--text-secondary);font-size:12px;margin-top:8px">Downloading PCR, FII Data & Chart...</div>
                 </div>
            </div>
        `;

        const load = async () => {
            const res = document.getElementById('nifty-content');
            try {
                const data = await api.fetchNifty();
                let html = '';
                
                // DMA + computed metrics
                if (data.chart) {
                    const c = data.chart;
                    const dmaCol = c.above_200dma ? 'var(--green)' : 'var(--red)';
                    html += '<div class="card" style="border-color:rgba(99,102,241,0.3)"><div class="stat-grid">';
                    html += Components.StatCard('Nifty 50', c.current_price);
                    if (c.day_change !== undefined) {
                        const dc = c.day_change_pct;
                        const dcCol = dc > 0 ? 'var(--green)' : dc < 0 ? 'var(--red)' : 'var(--text-secondary)';
                        const sign = dc > 0 ? '+' : '';
                        html += Components.StatCard("Day's Change", `${sign}${c.day_change} (${sign}${dc}%)`, dcCol);
                    }
                    html += Components.StatCard('21 EMA', c.current_ema21, '#f59e0b');
                    html += Components.StatCard('200 DMA', c.current_200dma || 'N/A', '#818cf8');
                    if (c.pct_from_200dma !== undefined) {
                        const pCol = c.pct_from_200dma >= 0 ? 'var(--green)' : 'var(--red)';
                        const ps = c.pct_from_200dma > 0 ? '+' : '';
                        html += Components.StatCard('vs 200 DMA', `${ps}${c.pct_from_200dma}%`, pCol);
                    }
                    if (c.rsi !== undefined) {
                        const rCol = c.rsi >= 70 ? 'var(--red)' : c.rsi <= 30 ? 'var(--green)' : 'var(--yellow)';
                        const rLbl = c.rsi >= 70 ? 'Overbought' : c.rsi <= 30 ? 'Oversold' : 'Neutral';
                        html += Components.StatCard('RSI (14)', `${c.rsi} · ${rLbl}`, rCol);
                    }
                    html += Components.StatCard('DMA Signal', c.above_200dma ? 'Above (Bullish)' : 'Below (Bearish)', dmaCol);
                    html += '</div></div>';
                }

                // Chart Canvas
                if (data.chart && data.chart.ohlcv) {
                    html += `
                    <div class="card">
                        <div class="section-title">Nifty 50 - Daily (Last 1 Year)</div>
                        <canvas id="niftyCanvas" style="width:100%;height:320px;background:#f8fafc;border-radius:6px"></canvas>
                        <div style="display:flex;gap:16px;margin-top:10px;font-size:11px;color:var(--text-secondary)">
                            <span><span style="display:inline-block;width:10px;height:10px;background:var(--green);border-radius:2px;margin-right:4px"></span>Bullish</span>
                            <span><span style="display:inline-block;width:10px;height:10px;background:var(--red);border-radius:2px;margin-right:4px"></span>Bearish</span>
                            <span><span style="display:inline-block;width:24px;height:2px;background:#f59e0b;vertical-align:middle;margin-right:4px"></span>21 EMA</span>
                            <span><span style="display:inline-block;width:24px;height:2px;background:#6366f1;vertical-align:middle;margin-right:4px;border-top:2px dashed #818cf8"></span>200 DMA</span>
                        </div>
                    </div>`;
                }

                // PCR
                const mkPcr = (title, d) => {
                    if(!d) return `<div class="card"><div class="section-title">${title}</div><div style="font-size:12px;color:var(--red)">NSE Data Not Available</div></div>`;
                    const col = d.pcr > 1.2 ? 'var(--green)' : d.pcr < 0.8 ? 'var(--red)' : 'var(--yellow)';
                    return `<div class="card">
                        <div class="section-title">${title}</div>
                        <div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px">Expiry: ${d.expiry}</div>
                        <div style="font-size:32px;font-weight:800;color:${col}">${d.pcr}</div>
                        <div style="margin:8px 0">${Components.CheckRow('Signal', d.pcr > 1.2, d.signal, '')}</div>
                        <div style="display:flex;justify-content:space-between;font-size:12px;margin-top:12px;padding-top:12px;border-top:1px solid rgba(15,23,42,0.06)">
                            <span style="color:var(--green)">PE OI: ${d.pe_oi.toLocaleString('en-IN')}</span>
                            <span style="color:var(--red)">CE OI: ${d.ce_oi.toLocaleString('en-IN')}</span>
                        </div>
                    </div>`;
                };

                html += '<div class="two-col" style="margin-bottom:16px">';
                html += mkPcr('Nifty Weekly PCR', data.nifty_pcr?.weekly);
                html += mkPcr('Nifty Monthly PCR', data.nifty_pcr?.monthly);
                html += '</div>';

                html += '<div class="two-col" style="margin-bottom:16px">';
                html += mkPcr('BankNifty Weekly PCR', data.banknifty_pcr?.weekly);
                html += mkPcr('BankNifty Monthly PCR', data.banknifty_pcr?.monthly);
                html += '</div>';

                // VIX Support & Resistance
                if (data.vix_levels) {
                    const vl = data.vix_levels;
                    html += '<div class="card"><div class="section-title">VIX-Based Expected Range</div>';
                    html += '<div style="overflow-x:auto"><table style="width:100%;text-align:right;font-size:13px"><thead><tr>';
                    html += '<th style="text-align:left;padding-bottom:8px;color:var(--text-secondary)">Level</th>';
                    html += `<th style="padding-bottom:8px;color:var(--text-secondary)">Current (VIX: ${vl.current.vix})</th>`;
                    html += `<th style="padding-bottom:8px;color:var(--text-secondary)">Prev Close (VIX: ${vl.close.vix})</th>`;
                    html += '</tr></thead><tbody>';
                    
                    const addVixRow = (label, curVal, clsVal, col) => {
                        html += `<tr><td style="text-align:left;font-weight:600;padding:8px 0;border-bottom:1px solid rgba(15,23,42,0.06);color:${col}">${label}</td>`;
                        html += `<td style="padding:8px 0;border-bottom:1px solid rgba(15,23,42,0.06)">${curVal}</td>`;
                        html += `<td style="padding:8px 0;border-bottom:1px solid rgba(15,23,42,0.06);color:var(--text-secondary)">${clsVal}</td></tr>`;
                    };
                    addVixRow('Resistance 2', vl.current.r2, vl.close.r2, 'var(--red)');
                    addVixRow('Resistance 1', vl.current.r1, vl.close.r1, 'var(--red)');
                    addVixRow('Support 1', vl.current.s1, vl.close.s1, 'var(--green)');
                    addVixRow('Support 2', vl.current.s2, vl.close.s2, 'var(--green)');
                    html += '</tbody></table></div></div>';
                }
                
                // FII Data Summary (readable, not raw JSON)
                if (data.fii) {
                    html += '<div class="card"><div class="section-title">FII Derivative Activity</div>';
                    html += this.renderFiiData(data.fii);
                    html += '</div>';
                }

                res.innerHTML = html;

                // Bind Chart drawing code
                if (data.chart && data.chart.ohlcv) {
                    setTimeout(() => app.drawNiftyChart(data.chart.ohlcv), 50);
                }

            } catch (err) {
                res.innerHTML = `<div class="error">❌ ${err.message}</div>`;
            }
        };

        const btn = document.getElementById('btn-nifty');
        btn.addEventListener('click', () => {
            document.getElementById('nifty-content').innerHTML = `<div class="card" style="text-align:center;padding:40px"><div class="big-spinner"></div></div>`;
            load();
        });
        load();
    },

    drawChart(canvasId, ohlcv) {
        const cc = document.getElementById(canvasId);
        if(!cc) return;
        
        // Scale for high DPI
        const dpr = window.devicePixelRatio || 1;
        const rect = cc.getBoundingClientRect();
        cc.width = rect.width * dpr;
        cc.height = rect.height * dpr;
        
        const ctx = cc.getContext('2d');
        ctx.scale(dpr, dpr);
        
        const W = rect.width, H = rect.height, PAD = {t:20, b:24, l:10, r:45};
        const n = ohlcv.length;
        if(n<2) return;
        
        const cW = W - PAD.l - PAD.r, cH = H - PAD.t - PAD.b;
        const slot = cW / n, bW = Math.max(1, slot * 0.55);
        
        let minP = Infinity, maxP = -Infinity;
        ohlcv.forEach(d => {
            minP = Math.min(minP, d.low, d.ema21, d.dma200||Infinity);
            maxP = Math.max(maxP, d.high, d.ema21, d.dma200||-Infinity);
        });
        const rng = maxP - minP || 1;
        
        const xOf = i => PAD.l + (i+0.5)*slot;
        const yOf = p => PAD.t + cH - ((p - minP)/rng)*cH;

        ctx.font = '10px monospace'; ctx.fillStyle = 'rgba(15,23,42,0.55)'; ctx.textAlign='left';
        
        // Horizontal grid lines
        for(let gi=0; gi<=5; gi++){
            let p = minP + (rng/5)*gi, y = yOf(p);
            ctx.strokeStyle='rgba(15,23,42,0.08)'; ctx.beginPath(); ctx.moveTo(PAD.l, y); ctx.lineTo(W-PAD.r, y); ctx.stroke();
            ctx.fillText(Math.round(p), W-PAD.r+5, y+3);
        }
        
        // Time labels
        ctx.textAlign='center';
        const step = Math.ceil(n/6);
        for(let gi=0; gi<n; gi+=step) {
            ctx.fillText(ohlcv[gi].date.substring(5), xOf(gi), H-5);
        }

        // Candles
        ohlcv.forEach((d,i)=>{
            const col = d.close >= d.open ? '#22c55e' : '#ef4444', x = xOf(i);
            ctx.strokeStyle = col; ctx.fillStyle = col;
            ctx.beginPath(); ctx.moveTo(x, yOf(d.high)); ctx.lineTo(x, yOf(d.low)); ctx.stroke();
            const t = Math.min(yOf(d.open), yOf(d.close)), h = Math.max(1, Math.abs(yOf(d.open) - yOf(d.close)));
            ctx.fillRect(x - bW/2, t, bW, h);
        });

        // EMA 21
        ctx.strokeStyle = '#f59e0b'; ctx.lineWidth = 1.5; ctx.setLineDash([]); ctx.beginPath();
        ohlcv.forEach((d,i) => i===0 ? ctx.moveTo(xOf(i), yOf(d.ema21)) : ctx.lineTo(xOf(i), yOf(d.ema21))); ctx.stroke();
        
        // DMA 200
        ctx.strokeStyle = '#6366f1'; ctx.lineWidth = 1.2; ctx.setLineDash([5,4]); ctx.beginPath();
        let s = false;
        ohlcv.forEach((d,i) => {
            if(!d.dma200) return;
            s ? ctx.lineTo(xOf(i), yOf(d.dma200)) : (ctx.moveTo(xOf(i), yOf(d.dma200)), s=true);
        }); ctx.stroke();
    }
};

window.onload = () => app.init();
