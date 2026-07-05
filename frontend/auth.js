// ═══════════════════════════════════════════════════════════════
//  Google Sign-In for Market Wisdom
//  SETUP (one time): paste your OAuth Client ID from Google Cloud
//  Console below, and set GOOGLE_CLIENT_ID + SECRET_KEY on the
//  backend host. Until then the sign-in button simply stays hidden.
// ═══════════════════════════════════════════════════════════════
const GOOGLE_CLIENT_ID = "397446530885-afn5vtqb7h6of9c76bjnb50dk917mjj3.apps.googleusercontent.com";

const Auth = {
    KEY: 'mw_auth',

    session() {
        try { return JSON.parse(localStorage.getItem(this.KEY)) || null; }
        catch (e) { return null; }
    },

    user() { const s = this.session(); return s ? s.user : null; },

    headers() {
        const s = this.session();
        return (s && s.token) ? { 'Authorization': 'Bearer ' + s.token } : {};
    },

    save(token, user) { localStorage.setItem(this.KEY, JSON.stringify({ token, user })); },

    logout() {
        localStorage.removeItem(this.KEY);
        try { google.accounts.id.disableAutoSelect(); } catch (e) {}
        this.render();
        this._refreshWatchlistIfOpen();
    },

    async handleCredential(response) {
        try {
            const res = await fetch(`${API_BASE_URL}/auth/google`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ credential: response.credential })
            });
            const data = await res.json();
            if (!res.ok || !data.token) throw new Error(data.error || 'Sign-in failed');
            this.save(data.token, data.user);
            this.render();
            this._refreshWatchlistIfOpen();
        } catch (err) {
            alert('Google sign-in failed: ' + err.message);
        }
    },

    _refreshWatchlistIfOpen() {
        // Watchlist contents differ per user — re-render it if it's the open page.
        try {
            if ((location.hash || '').replace('#', '') === 'watchlist' && window.app) {
                const c = document.getElementById('app-container') || document.querySelector('main');
                if (c && app.renderWatchlist) app.renderWatchlist(c);
            }
        } catch (e) {}
    },

    render() {
        const slot = document.getElementById('auth-slot');
        if (!slot) return;
        const u = this.user();
        if (u) {
            const first = (u.name || u.email || '').split(' ')[0];
            slot.innerHTML = `
                <span style="display:inline-flex;align-items:center;gap:8px;background:rgba(129,140,248,0.1);border:1px solid rgba(129,140,248,0.3);border-radius:20px;padding:4px 10px 4px 4px">
                    ${u.picture ? `<img src="${u.picture}" referrerpolicy="no-referrer" style="width:24px;height:24px;border-radius:50%">` : '👤'}
                    <span style="font-size:12px;font-weight:700;color:var(--text-primary)">${first}</span>
                    <button onclick="Auth.logout()" title="Sign out" style="background:none;border:none;color:var(--text-secondary);cursor:pointer;font-size:12px;padding:0 2px">✕</button>
                </span>`;
            return;
        }
        // Not signed in → render the Google button (when configured + script loaded)
        if (GOOGLE_CLIENT_ID.startsWith('PASTE_')) { slot.innerHTML = ''; return; }
        slot.innerHTML = '<div id="gsi-btn"></div>';
        let tries = 0;
        const boot = () => {
            if (window.google && google.accounts && google.accounts.id) {
                try {
                    google.accounts.id.initialize({
                        client_id: GOOGLE_CLIENT_ID,
                        callback: (r) => Auth.handleCredential(r),
                    });
                    google.accounts.id.renderButton(
                        document.getElementById('gsi-btn'),
                        { theme: 'filled_black', size: 'medium', shape: 'pill', text: 'signin_with' }
                    );
                } catch (e) { console.warn('GSI init failed', e); }
            } else if (tries++ < 40) {
                setTimeout(boot, 250);       // wait for the gsi script (up to ~10s)
            }
        };
        boot();
    },
};

document.addEventListener('DOMContentLoaded', () => Auth.render());
