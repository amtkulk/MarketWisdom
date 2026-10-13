"""Fast Chartink screener runner.

A Chartink screener page holds a "scan clause"; Chartink runs it via
POST https://chartink.com/screener/process (with the page's CSRF token + cookies)
and returns every matching stock as JSON.

Speed-ups over fetching each page every time:
  • the scan clause of each screener URL is cached (12 h) — a repeat run is ONE POST;
  • the CSRF token + cookies are cached (20 min) and refreshed automatically on 419/403;
  • results are cached for 45 s, so double-clicks / both users asking at once cost nothing;
  • an empty result is a valid answer (no slow fallback);
  • the browser fallback reads Chartink's own JSON response instead of scraping table pages,
    and learns the scan clause so the next run takes the fast path.
"""
import re
import threading
import time
from urllib.parse import parse_qs

URL_RE = re.compile(r"^https://chartink\.com/screener/[A-Za-z0-9][A-Za-z0-9\-_]*/?(?:\?.*)?$")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
PROCESS_URL = "https://chartink.com/screener/process"
CLAUSE_TTL = 12 * 3600
SESSION_TTL = 20 * 60
RESULT_TTL = 45

_lock = threading.Lock()
_clauses = {}          # url → (clause, ts)
_session = {"csrf": None, "cookies": None, "ts": 0}
_results = {}          # url → (rows, ts)


class ChartinkError(Exception):
    pass


def normalize_url(url):
    u = (url or "").strip()
    if u.startswith("http://"):
        u = "https://" + u[7:]
    if u.startswith("https://www.chartink.com/"):
        u = "https://chartink.com/" + u[len("https://www.chartink.com/"):]
    u = u.split("#")[0]
    return u


def valid_url(url):
    return bool(URL_RE.match(normalize_url(url)))


def _num(v):
    try:
        f = float(str(v).replace(",", "").replace("%", "").strip())
        return round(f, 2) if f == f else None
    except (TypeError, ValueError):
        return None


def parse_rows(payload):
    """Chartink /screener/process JSON → [{symbol, name, per_chg, close, volume}], de-duplicated,
    in Chartink's own order."""
    rows = (payload or {}).get("data") or [] if isinstance(payload, dict) else []
    out, seen = [], set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("nsecode") or r.get("bsecode") or r.get("name") or "").strip()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append({"symbol": sym, "name": str(r.get("name") or sym).strip(),
                    "per_chg": _num(r.get("per_chg")), "close": _num(r.get("close")),
                    "volume": r.get("volume")})
    return out


def extract_page(html):
    """(csrf, scan_clause) from a Chartink screener page."""
    csrf = None
    m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html or "")
    if m:
        csrf = m.group(1)
    clause = None
    for pat in (r'name="scan_clause"[^>]*value="([^"]*)"',
                r'"scan_clause"\s*:\s*"((?:[^"\\]|\\.)*)"',
                r'scan_clause\\?":\\?"((?:[^"\\]|\\.)*)"',
                r':scan-clause="((?:[^"\\]|\\.)*)"',
                r'scan_clause=([^"&\s]+)'):
        mm = re.search(pat, html or "")
        if mm:
            clause = mm.group(1)
            break
    if clause:
        clause = clause.replace("&quot;", '"').replace("&#039;", "'").replace("&lt;", "<") \
                       .replace("&gt;", ">").replace("&amp;", "&")
        if "\\" in clause:
            try:
                clause = clause.encode().decode("unicode_escape")
            except Exception:
                pass
    return csrf, clause


def _get_page(url):
    from curl_cffi import requests as cffi
    r = cffi.get(url, headers={"User-Agent": UA}, impersonate="chrome120", timeout=8)
    if r.status_code != 200:
        raise ChartinkError(f"Chartink page returned HTTP {r.status_code}")
    csrf, clause = extract_page(r.text)
    cookies = dict(r.cookies)
    if csrf and cookies:
        with _lock:
            _session.update({"csrf": csrf, "cookies": cookies, "ts": time.time()})
    return csrf, clause, cookies


def _post(clause, referer, csrf, cookies):
    from curl_cffi import requests as cffi
    r = cffi.post(PROCESS_URL, data={"scan_clause": clause}, cookies=cookies, impersonate="chrome120",
                  timeout=12, headers={"User-Agent": UA, "x-csrf-token": csrf, "x-requested-with": "XMLHttpRequest",
                                       "Referer": referer, "Origin": "https://chartink.com",
                                       "Accept": "application/json, text/javascript, */*; q=0.01",
                                       "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
    return r


def run_fast(url):
    """Run one screener via HTTP. Returns rows (possibly empty). Raises ChartinkError."""
    url = normalize_url(url)
    now = time.time()
    with _lock:
        hit = _results.get(url)
        if hit and now - hit[1] < RESULT_TTL:
            return hit[0]
        cl = _clauses.get(url)
        clause = cl[0] if cl and now - cl[1] < CLAUSE_TTL else None
        sess = dict(_session) if _session["csrf"] and now - _session["ts"] < SESSION_TTL else None

    if not clause or not sess:
        csrf, page_clause, cookies = _get_page(url)       # one GET gives clause + fresh session
        if page_clause:
            clause = page_clause
            with _lock:
                _clauses[url] = (clause, time.time())
        if not clause:
            raise ChartinkError("couldn't read the scan from the Chartink page")
        if not csrf:
            raise ChartinkError("Chartink page had no security token")
        sess = {"csrf": csrf, "cookies": cookies}

    r = _post(clause, url, sess["csrf"], sess["cookies"])
    if r.status_code in (419, 403, 401):                  # stale session → refresh once
        csrf, page_clause, cookies = _get_page(url)
        if page_clause:
            clause = page_clause
            with _lock:
                _clauses[url] = (clause, time.time())
        r = _post(clause, url, csrf, cookies)
    if r.status_code == 429:
        raise ChartinkError("Chartink is rate-limiting requests; try again in a minute")
    if r.status_code != 200:
        raise ChartinkError(f"Chartink returned HTTP {r.status_code}")
    try:
        payload = r.json()
    except Exception:
        raise ChartinkError("Chartink returned an unexpected response")
    if isinstance(payload, dict) and payload.get("scan_error"):
        raise ChartinkError("Chartink: " + str(payload.get("scan_error"))[:160])
    rows = parse_rows(payload)
    with _lock:
        _results[url] = (rows, time.time())
    return rows


def run_browser(url, timeout_s=35):
    """Fallback: open the screener in headless Chromium and read Chartink's own
    /screener/process JSON (all rows, no table paging). Also learns the scan clause
    so the next run of this screener uses the fast path. Returns rows or raises."""
    url = normalize_url(url)
    from playwright.sync_api import sync_playwright
    got = {}

    def on_response(resp):
        try:
            if "/screener/process" in resp.url and "json" not in got and resp.status == 200:
                got["json"] = resp.json()
                pd = resp.request.post_data or ""
                cl = (parse_qs(pd).get("scan_clause") or [None])[0]
                if cl:
                    got["clause"] = cl
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_context(user_agent=UA).new_page()
            page.on("response", on_response)
            try:
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
            except Exception:
                pass
            t0 = time.time()
            clicked = False
            while "json" not in got and time.time() - t0 < timeout_s:
                if not clicked and time.time() - t0 > 6:          # page didn't auto-run → press Run Scan
                    try:
                        btn = page.locator("text='Run Scan'")
                        if btn.count():
                            btn.first.click()
                    except Exception:
                        pass
                    clicked = True
                page.wait_for_timeout(250)
        finally:
            browser.close()
    if "json" not in got:
        raise ChartinkError("Chartink didn't return results in time")
    if got.get("clause"):
        with _lock:
            _clauses[url] = (got["clause"], time.time())
    rows = parse_rows(got["json"])
    with _lock:
        _results[url] = (rows, time.time())
    return rows


def run(url, allow_browser=True):
    """(rows, error, path) — fast path first, browser fallback."""
    try:
        return run_fast(url), None, "fast"
    except Exception as e:
        fast_err = str(e)
    if not allow_browser:
        return None, fast_err, "fast"
    try:
        return run_browser(url), None, "browser"
    except Exception as e:
        return None, f"{fast_err}; browser fallback: {e}", "browser"


def compare(url1, url2, label1, label2, rows1, rows2):
    """Set maths for two screeners, keeping each list in Chartink's order and
    attaching % change / price for every symbol."""
    s1 = [r["symbol"] for r in rows1]
    s2 = [r["symbol"] for r in rows2]
    set1, set2 = set(s1), set(s2)
    info = {}
    for r in rows2 + rows1:                        # screener 1 wins if both have data
        info[r["symbol"]] = {"per_chg": r.get("per_chg"), "close": r.get("close")}
    chg = lambda x: info.get(x, {}).get("per_chg")
    common = sorted([x for x in s1 if x in set2], key=lambda x: (chg(x) is None, -(chg(x) or 0)))   # strongest first
    return {"mode": "compare", "url1": url1, "url2": url2, "label1": label1, "label2": label2,
            "list1": sorted(s1), "list2": sorted(s2),
            "common": common, "only_in_1": [x for x in s1 if x not in set2],
            "only_in_2": [x for x in s2 if x not in set1],
            "count1": len(s1), "count2": len(s2), "common_count": len(common), "info": info}
