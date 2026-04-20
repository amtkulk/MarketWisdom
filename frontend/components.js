const Components = {
    RatingButtons: (ticker, company, sector, price, currentRating = null) => {
        const btnGoodClass = currentRating === 'good' ? 'active' : '';
        const btnAvgClass = currentRating === 'average' ? 'active' : '';
        const btnBadClass = currentRating === 'bad' ? 'active' : '';

        return `
            <div class="rating-container" style="display:flex; align-items:center; gap:8px;">
                <span class="text-secondary" style="font-size:12px">Rate:</span>
                <button class="rating-btn r-good ${btnGoodClass}" onclick="app.rateStock('${ticker}', '${company}', '${sector}', '${price}', 'good')">✅ Good</button>
                <button class="rating-btn r-avg ${btnAvgClass}" onclick="app.rateStock('${ticker}', '${company}', '${sector}', '${price}', 'average')">⭐ Average</button>
                <button class="rating-btn r-bad ${btnBadClass}" onclick="app.rateStock('${ticker}', '${company}', '${sector}', '${price}', 'bad')">❌ Bad</button>
                <span id="rating-msg-${ticker}" style="font-size:12px; color:var(--green)"></span>
            </div>
        `;
    },

    RsiGauge: (val, signal) => {
        const v = Math.min(100, Math.max(0, parseFloat(val) || 50));
        const col = v >= 70 ? 'var(--red)' : v <= 30 ? 'var(--green)' : 'var(--yellow)';
        const lbl = v >= 70 ? 'Overbought' : v <= 30 ? 'Oversold' : 'Neutral';
        return `
            <div style="margin-bottom:14px">
                <div style="display:flex;justify-content:space-between;margin-bottom:6px">
                    <span style="font-size:11px;color:var(--text-secondary)">0 Oversold</span>
                    <span style="font-size:14px;font-weight:700;color:${col}">${val} - ${lbl}</span>
                    <span style="font-size:11px;color:var(--text-secondary)">Overbought 100</span>
                </div>
                <div style="height:10px;border-radius:5px;background:rgba(255,255,255,0.06);position:relative">
                    <div style="position:absolute;left:0;width:30%;height:100%;background:rgba(34,197,94,0.13);border-radius:5px 0 0 5px"></div>
                    <div style="position:absolute;right:0;width:30%;height:100%;background:rgba(239,68,68,0.13);border-radius:0 5px 5px 0"></div>
                    <div style="position:absolute;top:-3px;left:calc(${v}% - 6px);width:12px;height:16px;background:${col};border-radius:3px"></div>
                </div>
                <div style="display:flex;justify-content:space-between;margin-top:3px">
                    <span style="font-size:10px;color:rgba(34,197,94,.5)">30</span>
                    <span style="font-size:10px;color:rgba(239,68,68,.5)">70</span>
                </div>
            </div>
        `;
    },

    CheckRow: (label, val, metric, note) => {
        const isY = val === true, isN = val === false;
        const bg = isY ? 'rgba(34,197,94,0.07)' : isN ? 'rgba(239,68,68,0.07)' : 'rgba(255,255,255,0.02)';
        const bdr = isY ? '1px solid rgba(34,197,94,0.2)' : isN ? '1px solid rgba(239,68,68,0.2)' : '1px solid rgba(99,102,241,0.1)';
        const badge = isY 
            ? '<span class="badge badge-green">YES</span>' 
            : isN 
            ? '<span class="badge" style="background:rgba(239,68,68,0.12);color:var(--red);border:1px solid rgba(239,68,68,0.3)">NO</span>' 
            : '<span class="badge" style="background:rgba(100,116,139,0.12);color:#94a3b8;border:1px solid rgba(100,116,139,0.3)">N/A</span>';
            
        const mh = (metric && metric !== 'N/A') ? `<span style="color:#60a5fa;font-weight:600;margin-right:8px">${metric}</span>` : '';
        const nh = (note && note !== 'N/A') ? `<div class="check-note">${note}</div>` : '';
        
        return `
            <div class="check-row" style="background:${bg};border:${bdr}">
                <div>
                    <div class="check-label">${label}</div>
                    ${nh}
                </div>
                <div style="display:flex;align-items:center;gap:8px">
                    ${mh}${badge}
                </div>
            </div>
        `;
    },

    StatCard: (label, value, valueColor = 'var(--text-primary)') => {
        return `
            <div class="stat-card">
                <div class="stat-label">${label}</div>
                <div class="stat-val" style="color:${valueColor}">${value}</div>
            </div>
        `;
    },
    
    // Simplistic rendering of HTML Table for stock screening
    Table: (headers, rows, colorMapper) => {
        let html = '<div style="overflow-x:auto"><table><thead><tr>';
        headers.forEach(h => html += `<th>${h}</th>`);
        html += '</tr></thead><tbody>';
        
        rows.forEach(row => {
            html += '<tr>';
            row.forEach((cell, idx) => {
                const style = colorMapper && colorMapper(idx, cell) ? `style="${colorMapper(idx, cell)}"` : '';
                html += `<td ${style}>${cell}</td>`;
            });
            html += '</tr>';
        });
        
        html += '</tbody></table></div>';
        return html;
    }
};
