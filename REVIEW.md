# MarketWisdom — Architecture / Security / Performance Review

A three-part review (architecture, security, performance) was run on the app. This
document records the findings and, per finding, whether it was **fixed in this branch**
or left as a **recommendation** (larger refactors that need their own PR). Fixes were
applied on `claude/app-review-arch-security-perf-yyqoqm`.

## Reported symptom

> "Site looks a little slow while rendering data on each page."

Root cause was a combination of: two pages scraped live third-party sites on **every**
request with no server cache (Telegram feed, War News), the service worker forced
`no-store` re-downloads of all static assets on every load, no gzip compression, and
no font preconnect. All four are addressed below.

---

## ✅ Fixed in this branch

### Security
| # | Severity | Finding | Fix |
|---|----------|---------|-----|
| S1 | High | **SSRF** in `/api/chartink` — attacker-supplied `url1`/`url2` fetched server-side with no allowlist (could hit cloud metadata `169.254.169.254`, localhost, internal services). | Added `_validate_chartink_url()` — requires `https` + host in `{chartink.com, www.chartink.com}`; applied to both the HTTP and Playwright paths; disabled redirects on the `curl_cffi` GET. |
| S2 | High | **Forgeable sessions** — `SECRET_KEY` fell back to `GEMINI_API_KEY` or a hardcoded literal, so admin/session tokens were forgeable. | Removed the predictable fallback. In production (`FLASK_ENV=production` or `REQUIRE_SECRET_KEY=1`) the app now refuses to start without `SECRET_KEY`; in dev it mints a random per-process key. |
| S3 | Medium | **Wide-open CORS** (`CORS(app)`). | Restricted to `ALLOWED_ORIGINS` env (comma-separated), scoped to `/api/*`. |
| S4 | Medium | **No rate limiting** on expensive/unauthenticated endpoints (Gemini spend, Chromium launches, full-market scans). | Added Flask-Limiter with per-IP defaults + tighter limits on `/api/chartink` (10/min), `/api/screener/start` (6/min), `/api/stock*` (20/min). Degrades to no-op if the lib is absent. |
| S5 | Medium | **XSS** — scraped Telegram text, Gemini output, and news headlines interpolated into `innerHTML` unescaped. | Added a global `esc()` helper (`components.js`) and escaped every server-derived sink in `app.js`/`components.js`. |
| S6 | Medium | **Missing security headers**. | Added `@app.after_request` setting CSP, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, HSTS. |
| S7 | Medium | **Container ran as root**. | Dockerfile now creates and runs as a non-root `appuser`. |
| S8 | Low | **TLS verification disabled** (`verify=False`) on the Telegram fetch — a duplicate, shadowing copy of the function was the one actually served. | Deleted the duplicate; the single verifying, cached copy is now authoritative. |
| S9 | Low | **Internal exception strings leaked** to clients. | Admin endpoints return generic messages + `logger.exception`; added a 500 error handler. |
| S10 | Low | **Debug mode** in the dev entrypoint (`app.run(debug=True)`). | Gated behind `FLASK_DEBUG=1`. |
| S11 | Low | **Static-file existence oracle** via raw `os.path.join` + `os.path.exists`. | Switched to `werkzeug.utils.safe_join` with an absolute frontend dir. |

### Performance
| # | Impact | Finding | Fix |
|---|--------|---------|-----|
| P1 | High | `/api/telegram_feed` scraped t.me on **every** request (no cache). | `@cached(120)` on the fetcher + 60s client cache in `api.js`. |
| P2 | High | `/api/war_news` fetched Google News RSS live on every open. | `@cached(90)` on `fetch_war_news_rss`. |
| P3 | High | Service worker forced `cache: 'no-store'` for **all** assets, re-downloading `app.js`/`style.css`/`logo.jpg` every load and blocking ETag revalidation. | Rewrote `sw.js`: cache-first for images/fonts, network-first **with** HTTP revalidation for HTML/JS/CSS. |
| P4 | Medium | **No gzip** on any response. | Added `flask-compress`. |
| P5 | Medium | **Cache stampede** — expired hot keys were recomputed by every thread at once. | Added per-key single-flight locking to the `cached()` decorator. |
| P6 | Low | Render-blocking Google Fonts with no preconnect; scripts not deferred. | Added `preconnect` for fonts and `defer` on the four app scripts. |

### Dependencies / build
- Pinned `playwright==1.40.0` to match the Docker base image (they were drifting).
- Added `flask-compress`, `flask-limiter`, and explicit `itsdangerous` to `requirements.txt`.
- Added `.dockerignore` so scratch `test_*.py` scripts, caches, and local DB files stay out of the production image.

---

## ⏳ Recommended next (not done here — need their own PR)

These are higher-effort or behavior-changing and were intentionally left out of this
security/perf pass:

1. **Split `app.py` (2,500+ lines) into blueprints/services** — `auth`, `cache`,
   `services/{prices,screener_in,news,gemini,chartink,telegram}`, `routes/*`, with an
   app factory. The duplicate Telegram function existed precisely because there was no
   structure making duplication visible.
2. **De-duplicate Screener.in parsing** — the same table-extraction logic exists in
   ~4 places (two Python copies, one embedded-JS Playwright copy, one in `screener.py`)
   and they have **already diverged** (a `return out` mis-indented inside an `except`
   in the Playwright `get_yoy` drops valid rows). Collapse to one parser.
3. **Persist background-scan state** — `_screener_state` is an in-memory dict; a restart
   mid-scan leaves status stuck on `running` forever and it breaks under multi-process
   (`deploy_backend.sh` runs gunicorn with 3 workers). Store status alongside results.
4. **Cap concurrent Playwright launches** with a semaphore (1–2). 24 waitress threads
   each able to launch Chromium (~300 MB) is an OOM risk.
5. **Batch the watchlist live-price fetch** into one `yf.download([...])` instead of
   N+1 per-ticker calls, and cache negative results briefly.
6. **Move inline `onclick` handlers to `addEventListener` + data attributes** — the
   `onclick="app.rateStock('${ticker}',…)"` pattern breaks on names containing quotes
   and is a residual injection surface.
7. **Pin the remaining dependencies** (lockfile) and enable Dependabot.
8. **Delete the ~30 committed `test_*.py` scratch scripts** (or move to
   `scripts/experiments/`), and add a real `tests/` + CI (ruff + pytest) gate before deploy.
9. **Shrink `logo.jpg` (380 KB → ~5 KB)** and `icon-512.png` (340 KB); consider the
   existing 756-byte `owl-logo.svg`.
10. **Fix `frontend/vercel.json`** — still points at the `http://YOUR_VPS_IP:5000`
    placeholder over plaintext HTTP.
11. **Replace `print()` with structured `logging`** across the codebase and stop
    swallowing exceptions silently (~60 bare `except: pass`).

---

## New env vars introduced

| Var | Purpose | Default |
|-----|---------|---------|
| `ALLOWED_ORIGINS` | Comma-separated CORS allowlist for `/api/*`. | `localhost` dev origins |
| `REQUIRE_SECRET_KEY` / `FLASK_ENV=production` | Force fail-fast if `SECRET_KEY` unset. | off |
| `FLASK_DEBUG` | Enable the Werkzeug debugger (dev only). | off |
| `RATELIMIT_STORAGE_URI` | Rate-limit backend (use Redis for multi-process). | `memory://` |

Set `SECRET_KEY`, `GOOGLE_CLIENT_ID`, `ADMIN_EMAILS`, `MONGODB_URI`, and
`ALLOWED_ORIGINS` on the host for a correct production deploy.
