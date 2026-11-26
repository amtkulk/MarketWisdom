"""Angel One SmartAPI: live NSE / NFO prices, option contracts and broker margin for Paper Trading.

Read-only use of the API: log in, read quotes, ask the margin calculator. It never places,
modifies or cancels a real order.

Credentials come from environment variables (Azure → App Service → Settings → Environment
variables), never from the code or the database:
    ANGEL_API_KEY       the SmartAPI app's API key (smartapi.angelone.in → My Apps)
    ANGEL_CLIENT_CODE   your Angel One client ID
    ANGEL_PIN           your 4-digit Angel One login PIN
    ANGEL_TOTP_SECRET   the TOTP secret (the text behind the QR code) from Enable TOTP
Optional: ANGEL_PUBLIC_IP (the server's outbound IP), ANGEL_LOCAL_IP, ANGEL_MAC.

Limits respected here: quotes up to 50 instruments a request and ~1 request a second; the
margin calculator up to 50 legs a request.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import struct
import threading
import time
import urllib.error
import urllib.request

BASE = "https://apiconnect.angelone.in"
LOGIN = "/rest/auth/angelbroking/user/v1/loginByPassword"
QUOTE = "/rest/secure/angelbroking/market/v1/quote/"
MARGIN = "/rest/secure/angelbroking/margin/v1/batch"
MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
QUOTE_GAP = 1.05            # seconds between quote calls (Angel: ~1 request a second)
QUOTE_MAX = 50
SESSION_HOURS = 6           # log in again after this long (tokens last about a day)

# Index spot tokens on NSE (Angel's list sometimes omits indices); index → (name in Angel's list, fallback token)
INDEX_SPOT = {"NIFTY": ("Nifty 50", "99926000"), "BANKNIFTY": ("Nifty Bank", "99926009"),
              "FINNIFTY": ("Nifty Fin Service", "99926037"), "MIDCPNIFTY": ("NIFTY MID SELECT", "99926074")}
INDEX_NAMES = tuple(INDEX_SPOT)

_lock = threading.RLock()
_quote_lock = threading.Lock()
_sess = {"jwt": None, "at": 0.0, "fail_at": 0.0, "error": None, "last_ok": None}
_qcache = {}                # (exch, token) → (time, data)
_last_quote = [0.0]


def now_ist():
    return dt.datetime.now(IST)


def configured():
    return all(os.environ.get(k) for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"))


# ── TOTP (RFC 6238), so no extra package is needed ──────────────────────────

def totp(secret, t=None, step=30, digits=6):
    s = re.sub(r"[\s-]", "", secret or "").upper()
    key = base64.b32decode(s + "=" * (-len(s) % 8))
    counter = int((time.time() if t is None else t) // step)
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


# ── HTTP ───────────────────────────────────────────────────────────────────

def _headers(jwt=None):
    h = {"Content-Type": "application/json", "Accept": "application/json",
         "X-UserType": "USER", "X-SourceID": "WEB",
         "X-ClientLocalIP": os.environ.get("ANGEL_LOCAL_IP", "127.0.0.1"),
         "X-ClientPublicIP": os.environ.get("ANGEL_PUBLIC_IP", "127.0.0.1"),
         "X-MACAddress": os.environ.get("ANGEL_MAC", "02:00:00:00:00:01"),
         "X-PrivateKey": os.environ.get("ANGEL_API_KEY", "")}
    if jwt:
        h["Authorization"] = "Bearer " + jwt
    return h


def _post(path, body, jwt=None, timeout=12):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=_headers(jwt), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    try:
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        data = {"status": False, "message": "non-JSON reply (HTTP %d)" % code}
    data["_http"] = code
    return data


class AngelError(RuntimeError):
    pass


def _auth_failed(d):
    msg = (str(d.get("message", "")) + " " + str(d.get("errorcode", ""))).lower()
    return d.get("_http") in (401, 403) or "token" in msg or "ag8001" in msg or "ag8002" in msg or "ag8003" in msg


def login(force=False):
    """A session JWT, logging in when needed. Raises AngelError with a short reason."""
    if not configured():
        raise AngelError("Angel One isn't connected yet (ANGEL_* settings missing on the server)")
    with _lock:
        if not force and _sess["jwt"] and time.time() - _sess["at"] < SESSION_HOURS * 3600:
            return _sess["jwt"]
        if time.time() - _sess["fail_at"] < 60:
            raise AngelError(_sess["error"] or "Angel One login failed; retrying shortly")
        d = _post(LOGIN, {"clientcode": os.environ["ANGEL_CLIENT_CODE"], "password": os.environ["ANGEL_PIN"],
                          "totp": totp(os.environ["ANGEL_TOTP_SECRET"])})
        jwt = ((d.get("data") or {}).get("jwtToken")) if d.get("status") else None
        if not jwt:
            _sess.update(fail_at=time.time(), error="Angel One login failed: %s" % (d.get("message") or d.get("errorcode") or "unknown"))
            raise AngelError(_sess["error"])
        _sess.update(jwt=jwt, at=time.time(), error=None, fail_at=0.0)
        return jwt


def _call(path, body):
    jwt = login()
    d = _post(path, body, jwt)
    if not d.get("status") and _auth_failed(d):
        d = _post(path, body, login(force=True))
    if not d.get("status"):
        raise AngelError("Angel One: %s" % (d.get("message") or d.get("errorcode") or "request failed"))
    _sess["last_ok"] = now_ist().strftime("%d %b %H:%M:%S")
    return d.get("data")


# ── quotes ─────────────────────────────────────────────────────────────────

def _norm_quote(q):
    dep = q.get("depth") or {}
    buy = [x for x in (dep.get("buy") or []) if (x.get("price") or 0) > 0]
    sell = [x for x in (dep.get("sell") or []) if (x.get("price") or 0) > 0]
    f = lambda k: float(q.get(k) or 0) if q.get(k) not in (None, "") else None
    return {"ltp": f("ltp"), "open": f("open"), "high": f("high"), "low": f("low"), "close": f("close"),
            "chg": f("netChange"), "chg_pct": f("percentChange"), "oi": f("opnInterest"), "volume": f("tradeVolume"),
            "bid": float(buy[0]["price"]) if buy else None, "ask": float(sell[0]["price"]) if sell else None,
            "upper": f("upperCircuit"), "lower": f("lowerCircuit"), "symbol": q.get("tradingSymbol"),
            "time": q.get("exchFeedTime")}


def quotes(keys, max_age=3.0):
    """{(exch, token): quote} for [(exch, token)], cached for max_age seconds.
    Fetches only what's stale, 50 a request, about one request a second."""
    t0 = time.time()
    out, stale = {}, []
    for k in dict.fromkeys(keys):
        c = _qcache.get(k)
        if c and t0 - c[0] <= max_age:
            out[k] = c[1]
        else:
            stale.append(k)
    for i in range(0, len(stale), QUOTE_MAX):
        part = stale[i:i + QUOTE_MAX]
        by = {}
        for ex, tok in part:
            by.setdefault(ex, []).append(str(tok))
        with _quote_lock:
            wait = QUOTE_GAP - (time.time() - _last_quote[0])
            if wait > 0:
                time.sleep(wait)
            try:
                data = _call(QUOTE, {"mode": "FULL", "exchangeTokens": by}) or {}
            finally:
                _last_quote[0] = time.time()
        now = time.time()
        for q in data.get("fetched") or []:
            k = (q.get("exchange"), str(q.get("symbolToken")))
            v = _norm_quote(q)
            _qcache[k] = (now, v)
            out[k] = v
    if len(_qcache) > 20000:                       # keep the cache bounded
        cut = time.time() - 600
        for k in [k for k, v in _qcache.items() if v[0] < cut]:
            _qcache.pop(k, None)
    return out


def margin(positions):
    """Broker margin for a basket of legs.
    positions: [{exchange, token, qty (units), side BUY|SELL, product CARRYFORWARD|INTRADAY|DELIVERY, price}]
    -> {'total': float, 'components': {...}}"""
    total, comps = 0.0, {}
    for i in range(0, len(positions), 50):
        body = {"positions": [{"exchange": p["exchange"], "qty": int(p["qty"]), "price": float(p.get("price") or 0),
                               "productType": p.get("product", "CARRYFORWARD"), "token": str(p["token"]),
                               "tradeType": p["side"]} for p in positions[i:i + 50]]}
        d = _call(MARGIN, body) or {}
        total += float(d.get("totalMarginRequired") or 0)
        for k, v in (d.get("marginComponents") or {}).items():
            try:
                comps[k] = comps.get(k, 0.0) + float(v or 0)
            except (TypeError, ValueError):
                pass
    return {"total": total, "components": comps}


def status():
    return {"configured": configured(), "logged_in": bool(_sess["jwt"]), "error": _sess["error"],
            "last_ok": _sess["last_ok"], "master": master_info()}


# ── instrument list (Angel's scrip master, ~40 MB JSON, read as a stream) ───

_master = {"date": None, "eq": {}, "idx": {}, "fo": {}, "tok": {}, "loaded_at": None, "error": None}
_master_lock = threading.Lock()
_OBJ = re.compile(rb"\{[^{}]*\}")


def _parse_record(r, eq, idx, fo):
    ex, it, sym = r.get("exch_seg"), r.get("instrumenttype") or "", r.get("symbol") or ""
    try:
        tick = float(r.get("tick_size") or 0) / 100.0 or 0.05
    except ValueError:
        tick = 0.05
    if ex == "NSE":
        if sym.endswith("-EQ"):
            eq[r.get("name") or sym[:-3]] = (str(r["token"]), sym, tick)
        elif it == "AMXIDX":
            idx[(r.get("name") or sym).upper()] = str(r["token"])
    elif ex == "NFO" and it in ("OPTIDX", "OPTSTK", "FUTIDX", "FUTSTK"):
        try:
            exp = dt.datetime.strptime(r.get("expiry") or "", "%d%b%Y").date().isoformat()
            lot = int(float(r.get("lotsize") or 0))
        except ValueError:
            return
        kind = "FUT" if it.startswith("FUT") else ("CE" if sym.endswith("CE") else "PE" if sym.endswith("PE") else None)
        if not kind or lot <= 0:
            return
        strike = round(float(r.get("strike") or 0) / 100.0, 2) if kind != "FUT" else None
        fo.setdefault(r.get("name"), []).append((str(r["token"]), sym, kind, exp, strike, lot, tick,
                                                  "IDX" if it.endswith("IDX") else "STK"))


def parse_master_stream(chunks):
    """Iterate the master file's bytes in chunks → (eq, idx, fo). Memory stays small."""
    eq, idx, fo = {}, {}, {}
    buf = b""
    for chunk in chunks:
        buf += chunk
        last = 0
        for m in _OBJ.finditer(buf):
            try:
                _parse_record(json.loads(m.group(0)), eq, idx, fo)
            except (ValueError, KeyError):
                pass
            last = m.end()
        buf = buf[last:]
    return eq, idx, fo


def _download_master():
    req = urllib.request.Request(MASTER_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        def gen():
            while True:
                b = r.read(1 << 20)
                if not b:
                    return
                yield b
        return parse_master_stream(gen())


def _cache_path(d):
    return os.path.join(os.environ.get("TMPDIR", "/tmp"), "angel_master_%s.json" % d)


def load_master(force=False):
    """The instrument list for today (memory → today's file → download). Refreshes daily after 8 AM IST."""
    n = now_ist()
    want = (n.date() if n.hour >= 8 else n.date() - dt.timedelta(days=1)).isoformat()
    with _master_lock:
        if not force and _master["date"] == want and _master["fo"]:
            return _master
        p = _cache_path(want)
        try:
            if not force and os.path.exists(p):
                with open(p) as f:
                    d = json.load(f)
                fo = {k: [tuple(x) for x in v] for k, v in d["fo"].items()}
                _master.update(date=want, eq={k: tuple(v) for k, v in d["eq"].items()}, idx=d["idx"], fo=fo,
                               loaded_at=d.get("loaded_at"), error=None, tok=_tok_index(fo))
                return _master
        except Exception:
            pass
        try:
            eq, idx, fo = _download_master()
            if not fo:
                raise RuntimeError("the list had no F&O contracts")
            for v in fo.values():
                v.sort(key=lambda x: (x[3], x[2], x[4] or 0))
            loaded = n.strftime("%d %b %Y %H:%M")
            _master.update(date=want, eq=eq, idx=idx, fo=fo, loaded_at=loaded, error=None, tok=_tok_index(fo))
            try:
                with open(p, "w") as f:
                    json.dump({"eq": eq, "idx": idx, "fo": fo, "loaded_at": loaded}, f)
            except Exception:
                pass
        except Exception as e:
            _master["error"] = "instrument list download failed: %s" % str(e)[:100]
            if not _master["fo"]:
                raise AngelError(_master["error"])
        return _master


def _tok_index(fo):
    return {c[0]: (name, c) for name, lst in fo.items() for c in lst}


def master_info():
    return {"date": _master["date"], "loaded_at": _master["loaded_at"], "equities": len(_master["eq"]),
            "fo_underlyings": len(_master["fo"]), "error": _master["error"]}


# ── lookups used by Paper Trading ───────────────────────────────────────────

def equity(symbol):
    """NSE equity → {exch, token, symbol, tick} or None."""
    m = load_master()
    v = m["eq"].get((symbol or "").upper())
    return {"exch": "NSE", "token": v[0], "tsym": v[1], "tick": v[2], "name": symbol.upper()} if v else None


def search_equity(q, limit=12):
    m = load_master()
    q = (q or "").upper().strip()
    if not q:
        return []
    starts = sorted(k for k in m["eq"] if k.startswith(q))
    rest = sorted(k for k in m["eq"] if q in k and not k.startswith(q))
    return (starts + rest)[:limit]


def underlyings():
    m = load_master()
    names = sorted(m["fo"])
    idx = [n for n in INDEX_NAMES if n in m["fo"]]
    return idx + [n for n in names if n not in idx]


def spot_key(underlying):
    """(exch, token) for the underlying's spot price."""
    m = load_master()
    u = underlying.upper()
    if u in INDEX_SPOT:
        name, fallback = INDEX_SPOT[u]
        return ("NSE", m["idx"].get(name.upper(), fallback))
    v = m["eq"].get(u)
    return ("NSE", v[0]) if v else None


def expiries(underlying, today=None):
    today = (today or now_ist().date()).isoformat()
    m = load_master()
    return sorted({c[3] for c in m["fo"].get(underlying.upper(), []) if c[2] != "FUT" and c[3] >= today})


def futures(underlying, today=None):
    today = (today or now_ist().date()).isoformat()
    m = load_master()
    return [c for c in m["fo"].get(underlying.upper(), []) if c[2] == "FUT" and c[3] >= today]


def chain_contracts(underlying, expiry):
    """{strike: {'CE': contract, 'PE': contract}} for one expiry."""
    m = load_master()
    out = {}
    for c in m["fo"].get(underlying.upper(), []):
        if c[3] == expiry and c[2] in ("CE", "PE"):
            out.setdefault(c[4], {})[c[2]] = c
    return out


def contract(token):
    """Look a contract up by token (for orders sent from the page)."""
    m = load_master()
    return (m.get("tok") or {}).get(str(token), (None, None))
