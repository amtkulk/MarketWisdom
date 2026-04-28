/*
 * Main Application Logic & Router
 */

const app = {
    init() {
        this.bindNav();
        // Load initial route based on hash or default to home
        const initialRoute = window.location.hash.replace('#', '') || 'home';
        this.navigate(initialRoute);
        
        // Handle browser back/forward buttons
        window.addEventListener('hashchange', () => {
            this.navigate(window.location.hash.replace('#', '') || 'home');
        });
    },

    bindNav() {
        document.querySelectorAll('.nav-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const target = e.currentTarget.getAttribute('data-target');
                window.location.hash = target;
            });
        });
    },

    updateNavState(route) {
        document.querySelectorAll('.nav-btn').forEach(btn => {
            if (btn.getAttribute('data-target') === route) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
    },

    navigate(route) {
        this.updateNavState(route);
        const container = document.getElementById('app-container');
        container.innerHTML = ''; // Clear current view

        switch(route) {
            case 'home':
                this.renderHome(container);
                break;
            case 'stock':
                this.renderStock(container);
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

    renderHome(container) {
        container.innerHTML = `
            <div style="text-align:center;padding:60px 0 40px">
                <div style="font-size:11px;letter-spacing:.2em;color:var(--text-secondary);text-transform:uppercase;margin-bottom:12px">
                    Your Personal Market Intelligence
                </div>
                <h1 style="font-size:32px;font-weight:900;background:linear-gradient(135deg,#818cf8,#34d399);
                           -webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:8px">
                    Market Research Hub
                </h1>
                <p style="color:var(--text-secondary);font-size:14px">Powered by Google Gemini · yfinance · Screener.in · Chartink</p>
                <div style="margin-top: 24px;">
                    <a href="#" target="_blank" style="display:inline-flex;align-items:center;gap:8px;background:#2AABEE;color:white;padding:10px 20px;border-radius:24px;text-decoration:none;font-weight:600;font-size:14px;box-shadow: 0 4px 15px rgba(42, 171, 238, 0.3);">
                        <span style="font-size:18px;">✈️</span> This is our Telegram channel, you can visit this link for more details.
                    </a>
                </div>
            </div>
            <div class="two-col">
                <a href="#stock">
                    <div class="card" style="cursor:pointer;border-color:rgba(99,102,241,0.3)">
                        <div style="font-size:32px;margin-bottom:12px">🔍</div>
                        <div style="font-size:17px;font-weight:700;color:var(--text-primary);margin-bottom:8px">Stock Research</div>
                        <div style="font-size:13px;color:var(--text-secondary);line-height:1.6">
                            Enter any company name and get live price, CAGR, RSI, chart patterns,
                            shareholding, quarterly results, fundamentals and news — all in one page.
                        </div>
                        <div style="margin-top:14px;font-size:12px;color:var(--text-accent)">Click to open →</div>
                    </div>
                </a>
                <a href="#nifty">
                    <div class="card" style="cursor:pointer;border-color:rgba(16,185,129,0.3)">
                        <div style="font-size:32px;margin-bottom:12px">📈</div>
                        <div style="font-size:17px;font-weight:700;color:var(--text-primary);margin-bottom:8px">Nifty Analysis</div>
                        <div style="font-size:13px;color:var(--text-secondary);line-height:1.6">
                            Live PCR, FII Data, and technical charts to gauge overall market sentiment.
                        </div>
                        <div style="margin-top:14px;font-size:12px;color:var(--green)">Click to open →</div>
                    </div>
                </a>
                <a href="#action">
                    <div class="card" style="cursor:pointer;border-color:rgba(245,158,11,0.3)">
                        <div style="font-size:32px;margin-bottom:12px">⚡</div>
                        <div style="font-size:17px;font-weight:700;color:var(--text-primary);margin-bottom:8px">Stock Action</div>
                        <div style="font-size:13px;color:var(--text-secondary);line-height:1.6">
                            Get latest PE ratio, recent news, and quick summaries of block deals or change of hands.
                        </div>
                        <div style="margin-top:14px;font-size:12px;color:var(--yellow)">Click to open →</div>
                    </div>
                </a>
            </div>
            <footer>Market Research Hub · For informational purposes only · Not investment advice</footer>
        `;
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
                    <input type="text" id="srch-ticker" placeholder="NSE Ticker e.g. RELIANCE" style="flex:1;min-width:140px"/>
                    <button class="btn" id="btn-analyse">Analyse</button>
                </div>
            </div>
            <div id="stock-result"></div>
        `;

        document.getElementById('btn-analyse').addEventListener('click', async (e) => {
            const company = document.getElementById('srch-company').value;
            const ticker = document.getElementById('srch-ticker').value;
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
                    <input type="text" id="action-ticker" placeholder="NSE Ticker e.g. RELIANCE" style="flex:1;min-width:140px"/>
                    <button class="btn" id="btn-action-analyse">Fetch Action</button>
                </div>
            </div>
            <div id="action-result"></div>
        `;

        document.getElementById('btn-action-analyse').addEventListener('click', async (e) => {
            const company = document.getElementById('action-company').value.trim();
            const ticker = document.getElementById('action-ticker').value.trim();
            if (!company || !ticker) return alert('Please enter both company name and ticker');
            
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
                <div style="padding-bottom:12px;border-bottom:1px solid rgba(255,255,255,0.05)">
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
                        <p style="font-size:13px;color:#94a3b8;line-height:1.7;max-width:500px;margin-bottom:12px">${d.description || ''}</p>
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
                html += `<tr><td style="text-align:left;font-weight:600;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05)">${label}</td>`;
                dataArr.forEach((v, i) => {
                    const yoy = yoyArr ? yoyArr[i] : null;
                    const yoyHtml = yoy && yoy !== 'N/A' ? `<br><span style="font-size:10px;color:${yoy.startsWith('+') ? 'var(--green)' : 'var(--red)'}">${yoy} YoY</span>` : '';
                    html += `<td style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05)">${v}${yoyHtml}</td>`;
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
                html += `<tr><td style="text-align:left;font-weight:600;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05)">${label}</td>`;
                dataArr.forEach(v => html += `<td style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05)">${v}${v !== 'N/A' ? '%' : ''}</td>`);
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
                <canvas id="stockCanvas-${d.ticker}" style="width:100%;height:320px;background:#0d1424;border-radius:6px"></canvas>
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
                <div style="padding-bottom:12px;border-bottom:1px solid rgba(255,255,255,0.05)">
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
            const btnContainer = document.querySelector(`#rating-msg-${ticker}`).parentElement;
            btnContainer.querySelectorAll('.rating-btn').forEach(btn => btn.classList.remove('active'));
            
            if (rating === 'good') btnContainer.querySelector('.r-good').classList.add('active');
            if (rating === 'average') btnContainer.querySelector('.r-avg').classList.add('active');
            if (rating === 'bad') btnContainer.querySelector('.r-bad').classList.add('active');
            
            const msg = document.getElementById(`rating-msg-${ticker}`);
            msg.textContent = 'Saved!';
            setTimeout(() => msg.textContent = '', 2000);
        } catch (e) {
            alert("Failed to save rating: " + e.message);
        }
    },

    renderWatchlist(container) {
        container.innerHTML = `
            <div style="margin-bottom:24px;display:flex;justify-content:space-between;align-items:center;">
                <div>
                    <h2 style="font-size:22px;font-weight:800;color:var(--text-primary);margin-bottom:4px">⭐ My Watchlist</h2>
                    <p style="font-size:13px;color:var(--text-secondary)">Stocks you have researched and rated.</p>
                </div>
            </div>
            <div id="watchlist-content">
                <div style="text-align:center;padding:40px"><div class="spinner"></div></div>
            </div>
        `;

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
                <p style="font-size:13px;color:var(--text-secondary)">Paste two Chartink screener URLs to find common stocks.</p>
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
            <div id="chartink-result"></div>
        `;
        document.getElementById('btn-chartink').addEventListener('click', async (e) => {
            const btn = e.target;
            const res = document.getElementById('chartink-result');
            const u1 = document.getElementById('url1').value.trim();
            const u2 = document.getElementById('url2').value.trim();
            const l1 = document.getElementById('label1').value.trim() || 'S1';
            const l2 = document.getElementById('label2').value.trim() || 'S2';

            if (!u1 || !u2) return alert("Please enter both URLs");
            
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner" style="vertical-align:middle;margin-right:6px"></span> Scanning...';
            res.innerHTML = `
                <div class="card" style="text-align:center;padding:40px">
                    <div class="big-spinner"></div>
                    <div style="color:var(--text-accent);font-weight:600">Scanning Chartink...</div>
                    <div style="color:var(--text-secondary);font-size:12px;margin-top:8px">This takes ~30 seconds to scrape pages</div>
                </div>
            `;

            try {
                const data = await api.compareChartink(u1, l1, u2, l2);
                let html = '<div class="stat-grid" style="margin-top:16px">';
                html += Components.StatCard(`${l1} Total`, data.count1, '#60a5fa');
                html += Components.StatCard('Common', data.common_count, 'var(--green)');
                html += Components.StatCard(`${l2} Total`, data.count2, '#c084fc');
                html += '</div>';

                const mkTable = (title, stocks, color) => {
                    let r = stocks.length ? '<table><tbody>' : '<p style="padding:12px;color:var(--text-secondary)">No stocks</p>';
                    if (stocks.length) {
                        stocks.forEach((s, i) => r += `<tr><td style="width:30px;color:var(--text-secondary)">${i+1}</td><td style="color:${color};font-weight:600">${s}</td></tr>`);
                        r += '</tbody></table>';
                    }
                    return `<div class="card" style="border-color:${color.replace('var(--','rgba(').replace(')','')}">` +
                        `<div class="section-title" style="color:${color}">${title} <span class="badge">${stocks.length}</span></div>` +
                        r + `</div>`;
                };

                html += mkTable('✅ Common Stocks', data.common || [], 'var(--green)');
                html += '<div class="two-col">';
                html += mkTable(`📋 Only in ${l1}`, data.only_in_1 || [], '#60a5fa');
                html += mkTable(`📋 Only in ${l2}`, data.only_in_2 || [], '#c084fc');
                html += '</div>';
                
                res.innerHTML = html;
            } catch (err) {
                res.innerHTML = `<div class="error">❌ ${err.message}</div>`;
            } finally {
                btn.disabled = false;
                btn.textContent = '🔍 Compare Screeners';
            }
        });
    },

    // Nifty Analysis Rendering
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
                
                // DMA
                if (data.chart) {
                    const c = data.chart;
                    const dmaCol = c.above_200dma ? 'var(--green)' : 'var(--red)';
                    html += '<div class="card" style="border-color:rgba(99,102,241,0.3)"><div class="stat-grid">';
                    html += Components.StatCard('Nifty 50', c.current_price);
                    html += Components.StatCard('21 EMA', c.current_ema21, '#f59e0b');
                    html += Components.StatCard('200 DMA', c.current_200dma || 'N/A', '#818cf8');
                    html += Components.StatCard('DMA Signal', c.above_200dma ? 'Above (Bullish)' : 'Below (Bearish)', dmaCol);
                    html += '</div></div>';
                }

                // Chart Canvas
                if (data.chart && data.chart.ohlcv) {
                    html += `
                    <div class="card">
                        <div class="section-title">Nifty 50 - Daily (Last 1 Year)</div>
                        <canvas id="niftyCanvas" style="width:100%;height:320px;background:#0d1424;border-radius:6px"></canvas>
                        <div style="display:flex;gap:16px;margin-top:10px;font-size:11px;color:var(--text-secondary)">
                            <span><span style="display:inline-block;width:10px;height:10px;background:var(--green);border-radius:2px;margin-right:4px"></span>Bullish</span>
                            <span><span style="display:inline-block;width:10px;height:10px;background:var(--red);border-radius:2px;margin-right:4px"></span>Bearish</span>
                            <span><span style="display:inline-block;width:24px;height:2px;background:#f59e0b;vertical-align:middle;margin-right:4px"></span>21 EMA</span>
                            <span><span style="display:inline-block;width:24px;height:2px;background:#818cf8;vertical-align:middle;margin-right:4px;border-top:2px dashed #818cf8"></span>200 DMA</span>
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
                        <div style="display:flex;justify-content:space-between;font-size:12px;margin-top:12px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.05)">
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
                        html += `<tr><td style="text-align:left;font-weight:600;padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);color:${col}">${label}</td>`;
                        html += `<td style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05)">${curVal}</td>`;
                        html += `<td style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);color:var(--text-secondary)">${clsVal}</td></tr>`;
                    };
                    addVixRow('Resistance 2', vl.current.r2, vl.close.r2, 'var(--red)');
                    addVixRow('Resistance 1', vl.current.r1, vl.close.r1, 'var(--red)');
                    addVixRow('Support 1', vl.current.s1, vl.close.s1, 'var(--green)');
                    addVixRow('Support 2', vl.current.s2, vl.close.s2, 'var(--green)');
                    html += '</tbody></table></div></div>';
                }
                
                // FII Data Summary
                if (data.fii) {
                    html += '<div class="card"><div class="section-title">FII Derivative Data (Index Futures)</div>';
                    html += `<pre style="font-size:11px;color:var(--text-secondary);white-space:pre-wrap;background:#0d1424;padding:12px;border-radius:8px">${JSON.stringify(data.fii, null, 2)}</pre></div>`;
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

        ctx.font = '10px monospace'; ctx.fillStyle = 'rgba(255,255,255,0.4)'; ctx.textAlign='left';
        
        // Horizontal grid lines
        for(let gi=0; gi<=5; gi++){
            let p = minP + (rng/5)*gi, y = yOf(p);
            ctx.strokeStyle='rgba(255,255,255,0.05)'; ctx.beginPath(); ctx.moveTo(PAD.l, y); ctx.lineTo(W-PAD.r, y); ctx.stroke();
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
        ctx.strokeStyle = '#818cf8'; ctx.lineWidth = 1.2; ctx.setLineDash([5,4]); ctx.beginPath();
        let s = false;
        ohlcv.forEach((d,i) => {
            if(!d.dma200) return;
            s ? ctx.lineTo(xOf(i), yOf(d.dma200)) : (ctx.moveTo(xOf(i), yOf(d.dma200)), s=true);
        }); ctx.stroke();
    }
};

window.onload = () => app.init();
