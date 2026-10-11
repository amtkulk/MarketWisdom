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
        window.dispatchEvent(new CustomEvent('mw-auth-changed'));
    },

    async handleCredential(response) {
        try {
            const res = await fetch(`${API_BASE_URL}/auth/google`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ credential: response.credential })
            });
            const data = await res.json();
            if (res.ok && data.consent_required) { this.showConsent(data); return; }   // accept first
            if (!res.ok || !data.token) throw new Error(data.error || 'Sign-in failed');
            this.save(data.token, data.user);
            this.render();
            window.dispatchEvent(new CustomEvent('mw-auth-changed'));
        } catch (err) {
            alert('Google sign-in failed: ' + err.message);
        }
    },

    // ── Disclaimer consent: no access until the user accepts ──────────
    showConsent(data) {
        // Not signed in until they accept: drop any stored session.
        if (this.session()) { localStorage.removeItem(this.KEY); this.render(); window.dispatchEvent(new CustomEvent('mw-auth-changed')); }
        const old = document.getElementById('mw-consent'); if (old) old.remove();
        const esc = (t) => String(t || '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
        const box = document.createElement('div');
        box.id = 'mw-consent';
        box.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,0.55);z-index:3000;display:flex;align-items:center;justify-content:center;padding:16px';
        box.innerHTML = `
            <div role="dialog" aria-modal="true" aria-labelledby="mw-consent-title" style="background:#fff;max-width:520px;width:100%;border-radius:14px;padding:24px 22px;box-shadow:0 20px 60px rgba(0,0,0,0.25)">
                <h2 id="mw-consent-title" style="font-size:20px;font-weight:800;color:#0f172a;margin:0 0 6px">Disclaimer</h2>
                <div style="font-size:12.5px;color:#64748b;margin-bottom:12px">Signing in as <b>${esc(data.user && data.user.email)}</b></div>
                <p style="font-size:14.5px;line-height:1.65;color:#334155;margin:0 0 14px">${esc(data.disclaimer)}</p>
                <p style="font-size:12.5px;color:#64748b;margin:0 0 18px">You need to accept this to use Market Wisdom. Your choice is recorded against your email address.</p>
                <div id="mw-consent-err" style="display:none;color:#dc2626;font-size:13px;margin-bottom:10px"></div>
                <div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end">
                    <button id="mw-consent-no" style="padding:10px 18px;border-radius:10px;border:1px solid #cbd5e1;background:#fff;color:#334155;font-weight:700;cursor:pointer">I do not accept</button>
                    <button id="mw-consent-yes" class="btn" style="padding:10px 22px;font-weight:700">I accept</button>
                </div>
            </div>`;
        document.body.appendChild(box);
        const decide = async (decision) => {
            const err = document.getElementById('mw-consent-err');
            document.getElementById('mw-consent-yes').disabled = true;
            document.getElementById('mw-consent-no').disabled = true;
            try {
                const res = await fetch(`${API_BASE_URL}/auth/consent`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ consent_token: data.consent_token, decision })
                });
                const out = await res.json();
                if (!res.ok) throw new Error(out.error || 'Could not record your choice');
                box.remove();
                if (out.accepted && out.token) {
                    this.save(out.token, out.user);
                    this.render();
                    window.dispatchEvent(new CustomEvent('mw-auth-changed'));
                } else {
                    this.logout();
                    alert('You have not accepted the disclaimer, so you remain signed out. You can sign in again any time and accept it to use Market Wisdom.');
                }
            } catch (e) {
                err.style.display = 'block'; err.textContent = e.message;
                document.getElementById('mw-consent-yes').disabled = false;
                document.getElementById('mw-consent-no').disabled = false;
            }
        };
        document.getElementById('mw-consent-yes').addEventListener('click', () => decide('accept'));
        document.getElementById('mw-consent-no').addEventListener('click', () => decide('decline'));
    },

    // On page load: check the stored session is still valid and consented.
    async verify() {
        if (!this.session()) return;
        try {
            const res = await fetch(`${API_BASE_URL}/auth/me`, { headers: this.headers() });
            if (res.status === 401) { this.logout(); return; }
            const data = await res.json();
            if (data.consent_required) { this.showConsent(data); return; }
            if (data.user) {
                const s = this.session();
                const before = JSON.stringify(s.user);
                this.save(s.token, Object.assign({}, s.user, data.user));
                if (JSON.stringify(this.user()) !== before) { this.render(); window.dispatchEvent(new CustomEvent('mw-auth-changed')); }
            }
        } catch (e) { /* offline: keep the session; the server still enforces consent */ }
    },

    _refreshWatchlistIfOpen() {
        // Kept for backward compatibility; the router handles this now via mw-auth-changed.
    },

    render() {
        // Admin-only menu item (the server also refuses non-admins).
        const adminLink = document.getElementById('nav-admin');
        if (adminLink) adminLink.style.display = (this.user() && this.user().is_admin) ? '' : 'none';
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

document.addEventListener('DOMContentLoaded', () => { Auth.render(); Auth.verify(); });
