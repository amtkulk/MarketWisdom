"""Paper Trading: fake money, real prices.

Every signed-in user gets two practice accounts:
  EQ  Rs 5,00,000  stocks: Delivery (CNC: buy with cash, sell what you hold) and
                   Intraday (MIS: long or short, 20% margin = 5x like Angel One,
                   squared off automatically at 3:20 PM)
  FO  Rs 10,00,000 index and stock options and futures (NRML), single legs or whole
                   strategies (straddle, strangle, iron condor, spreads) as one basket.
                   Margin for selling and futures is asked from Angel One's margin
                   calculator for the WHOLE portfolio after the trade (span + exposure,
                   hedge benefit included), the same way a broker blocks it. If Angel's
                   calculator can't be reached, an estimate is used and labelled.

Prices: Angel One live quotes; a buy fills at the best ask and a sell at the best bid
(last price if the book is empty). Charges: Angel One brokerage + STT + exchange + SEBI +
stamp + GST at 2026 rates. Expired contracts settle at intrinsic value (options) / the spot
close (futures). When an account runs low the user can ask for a fresh start; an admin
approves it. Nothing here touches a real broker account.
"""
import datetime as dt
import math
import os
import threading
import time
import uuid

import angel

CAPITAL = {"EQ": 500000.0, "FO": 1000000.0}
MIS_MARGIN = 0.20
MIS_SQUAREOFF = dt.time(15, 20)
OPEN, CLOSE = dt.time(9, 15), dt.time(15, 30)
REFILL_BELOW = 0.10           # a fresh start can be requested when available funds < 10% of capital
MAX_ORDERS = 400
MARGIN_TTL = 60               # seconds before an account's blocked margin is recomputed on view
REQ_KEY = "paper_requests"
IST = angel.IST

_locks, _locks_guard = {}, threading.Lock()


class PaperError(ValueError):
    pass


def now_ist():
    return dt.datetime.now(IST).replace(tzinfo=None)


def _lock(key):
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def _key(email, seg):
    return "paper:%s:%s" % (email.lower(), seg)


def is_trading_day(d):
    try:
        import daytrader_core as core
        return core.trading_day(d)[0]
    except Exception:
        return d.weekday() < 5


def market_open(n=None):
    n = n or now_ist()
    return is_trading_day(n.date()) and OPEN <= n.time() < CLOSE


def _check_hours():
    if os.environ.get("PAPER_ALLOW_OFFHOURS") == "1":
        return
    if not market_open():
        raise PaperError("The market is closed. Orders can be placed 9:15 AM – 3:30 PM on NSE trading days.")


# ── storage ────────────────────────────────────────────────────────────────

def _db():
    import database
    return database


def new_account(email, seg, name=""):
    return {"email": email.lower(), "name": name, "seg": seg, "capital": CAPITAL[seg], "cash": CAPITAL[seg],
            "positions": {}, "orders": [], "realized": 0.0, "charges": 0.0, "refills": 0,
            "blocked": 0.0, "blocked_at": 0, "margin_src": None,
            "created": now_ist().strftime("%d %b %Y %H:%M"), "last_sweep": None}


def load(email, seg, name=""):
    acc = _db().paper_get(_key(email, seg))
    if not acc:
        acc = new_account(email, seg, name)
        _db().paper_put(_key(email, seg), acc)
    return acc


def save(acc):
    acc["orders"] = acc["orders"][-MAX_ORDERS:]
    _db().paper_put(_key(acc["email"], acc["seg"]), acc)


# ── prices (swappable in tests) ─────────────────────────────────────────────

def eq_quotes(symbols):
    """{SYMBOL: quote} — Angel live; Yahoo as a fallback for stocks when Angel isn't connected."""
    out = {}
    if angel.configured():
        keys = {}
        for s in symbols:
            e = angel.equity(s)
            if e:
                keys[("NSE", e["token"])] = s
        if keys:
            q = angel.quotes(list(keys))
            for k, s in keys.items():
                if k in q and q[k].get("ltp"):
                    out[s] = dict(q[k], source="Angel One (live)")
        return out
    try:
        import yfinance as yf
        for s in symbols:
            fi = yf.Ticker(s + ".NS").fast_info
            px = float(fi["last_price"])
            prev = float(fi.get("previous_close") or 0) or None
            if px > 0:
                out[s] = {"ltp": px, "bid": None, "ask": None, "close": prev,
                          "chg_pct": round((px / prev - 1) * 100, 2) if prev else None, "source": "Yahoo (may lag)"}
    except Exception as e:
        print(f"[Paper] Yahoo quote failed: {e}")
    return out


def fo_quotes(tokens):
    """{token: quote} for NFO contracts (Angel only)."""
    if not tokens:
        return {}
    q = angel.quotes([("NFO", str(t)) for t in tokens])
    return {k[1]: v for k, v in q.items()}


def spot_quote(und):
    k = angel.spot_key(und)
    q = angel.quotes([k]).get(k) if k else None
    if q and q.get("ltp"):
        return q
    fut = angel.futures(und)                              # index spot missing → nearest future
    if fut:
        f = fo_quotes([fut[0][0]]).get(fut[0][0])
        if f and f.get("ltp"):
            return dict(f, from_future=True)
    return None


def margin_api(legs):
    return angel.margin(legs)["total"]


# ── charges (Angel One brokerage + statutory, 2026) ────────────────────────

def charges(seg, product, side, qty, px, kind=None):
    t = abs(qty) * px
    buy = side == "BUY"
    if seg == "EQ":
        brok = max(5.0, min(20.0, 0.001 * t))
        exch = 0.0000297 * t
        if product == "CNC":
            stt, stamp = 0.001 * t, (0.00015 * t if buy else 0.0)
        else:
            stt, stamp = (0.0 if buy else 0.00025 * t), (0.00003 * t if buy else 0.0)
    else:
        brok = 20.0
        if kind == "FUT":
            stt, exch, stamp = (0.0 if buy else 0.0005 * t), 0.0000173 * t, (0.00002 * t if buy else 0.0)
        else:
            stt, exch, stamp = (0.0 if buy else 0.0015 * t), 0.0003503 * t, (0.00003 * t if buy else 0.0)
    sebi = 0.000001 * t
    gst = 0.18 * (brok + exch + sebi)
    return round(brok + stt + exch + stamp + sebi + gst, 2)


# ── position maths ─────────────────────────────────────────────────────────

def _apply(pos, dq, px):
    """Net a signed trade of dq units at px into pos (dict with qty, avg). -> realized P&L."""
    q0, a0 = pos.get("qty", 0), pos.get("avg", 0.0)
    q1 = q0 + dq
    realized = 0.0
    if q0 and (q0 > 0) != (dq > 0):
        closing = min(abs(q0), abs(dq))
        realized = closing * (px - a0) * (1 if q0 > 0 else -1)
        if q1 == 0:
            a1 = 0.0
        elif (q1 > 0) == (q0 > 0):
            a1 = a0
        else:
            a1 = px
    else:
        a1 = (abs(q0) * a0 + abs(dq) * px) / abs(q1) if q1 else 0.0
    pos["qty"], pos["avg"] = q1, round(a1, 4)
    pos["realized"] = round(pos.get("realized", 0.0) + realized, 2)
    return realized


def _fill_price(q, side):
    if not q:
        return None
    p = q.get("ask") if side == "BUY" else q.get("bid")
    return float(p) if p and p > 0 else (float(q["ltp"]) if q.get("ltp") else None)


def _order(acc, **kw):
    o = {"id": uuid.uuid4().hex[:10], "time": now_ist().strftime("%d %b %Y %H:%M:%S")}
    o.update(kw)
    acc["orders"].append(o)
    return o


def _intr(kind, strike, s):
    return max(0.0, s - strike) if kind == "CE" else max(0.0, strike - s)


# ── equity ─────────────────────────────────────────────────────────────────

def _mis_blocked(acc):
    return round(sum(abs(p["qty"]) * p["avg"] * MIS_MARGIN for p in acc["positions"].values()
                     if p.get("product") == "MIS" and p["qty"]), 2)


def place_equity(email, symbol, side, qty, product, name=""):
    symbol, side, product = (symbol or "").upper().strip(), (side or "").upper(), (product or "CNC").upper()
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        raise PaperError("Quantity must be a whole number of shares.")
    if qty < 1:
        raise PaperError("Quantity must be at least 1 share.")
    if side not in ("BUY", "SELL") or product not in ("CNC", "MIS"):
        raise PaperError("Bad order.")
    _check_hours()
    n = now_ist()
    if product == "MIS" and n.time() >= MIS_SQUAREOFF and os.environ.get("PAPER_ALLOW_OFFHOURS") != "1":
        raise PaperError("Intraday (MIS) orders close at 3:20 PM, when open intraday positions are squared off.")
    q = eq_quotes([symbol]).get(symbol)
    px = _fill_price(q, side)
    if not px:
        raise PaperError("No live price for %s right now. Check the symbol (NSE code, e.g. HINDZINC)." % symbol)
    with _lock(_key(email, "EQ")):
        acc = load(email, "EQ", name)
        sweep(acc)
        pk = "%s|%s" % (symbol, product)
        pos = acc["positions"].get(pk) or {"key": pk, "symbol": symbol, "product": product, "qty": 0, "avg": 0.0,
                                          "realized": 0.0, "opened": n.strftime("%Y-%m-%d")}
        ch = charges("EQ", product, side, qty, px)
        dq = qty if side == "BUY" else -qty
        if product == "CNC":
            if side == "BUY":
                need = qty * px + ch
                avail = acc["cash"] - _mis_blocked(acc)
                if need > avail + 1e-6:
                    raise PaperError("Not enough funds: this needs ₹{:,.2f} (incl. ₹{:,.2f} charges) and you have ₹{:,.2f} available."
                                     .format(need, ch, max(0, avail)))
                acc["cash"] -= need
            else:
                if pos["qty"] < qty:
                    raise PaperError("You hold %d %s in Delivery. To sell shares you don't own, use Intraday (MIS)." % (pos["qty"], symbol))
                acc["cash"] += qty * px - ch
            realized = _apply(pos, dq, px)
        else:
            before = abs(pos["qty"])
            realized = _apply(pos, dq, px)
            cash_after = acc["cash"] + realized - ch
            acc["positions"][pk] = pos
            blocked_after = _mis_blocked(acc)
            if abs(pos["qty"]) > before and cash_after - blocked_after < -1e-6:
                acc["positions"] = load(email, "EQ")["positions"]          # undo
                raise PaperError("Not enough margin: intraday needs 20%% of the value (₹{:,.2f}); you'd be short by ₹{:,.2f}."
                                 .format(abs(pos["qty"]) * px * MIS_MARGIN, blocked_after - cash_after))
            acc["cash"] = cash_after
        acc["realized"] = round(acc["realized"] + realized, 2)
        acc["charges"] = round(acc["charges"] + ch, 2)
        acc["cash"] = round(acc["cash"], 2)
        if pos["qty"]:
            acc["positions"][pk] = pos
        else:
            acc["positions"].pop(pk, None)
        o = _order(acc, seg="EQ", symbol=symbol, side=side, qty=qty, price=round(px, 2), product=product,
                   charges=ch, realized=round(realized, 2), source=(q or {}).get("source"))
        save(acc)
        return o, acc


# ── F&O ────────────────────────────────────────────────────────────────────

def _fo_pos(contract_tuple, und):
    tok, tsym, kind, exp, strike, lot, tick, cls = contract_tuple
    return {"key": tok, "token": tok, "tsym": tsym, "und": und, "kind": kind, "expiry": exp, "strike": strike,
            "lot": lot, "cls": cls, "qty": 0, "avg": 0.0, "realized": 0.0}


def _approx_margin(positions, spots):
    """Fallback SPAN-like estimate per underlying+expiry: the worst loss at expiry over a
    ±scan range (12% index / 20% stock) using the legs' payoffs, plus 2% / 3.5% exposure on
    short notional. Defined-risk strategies come out near their max loss, like a broker."""
    groups = {}
    for p in positions:
        if p["qty"]:
            groups.setdefault((p["und"], p["expiry"]), []).append(p)
    total = 0.0
    for (und, exp), legs in groups.items():
        s = spots.get(und)
        if not s:
            continue
        idx = legs[0]["cls"] == "IDX"
        rng, expo = (0.12, 0.02) if idx else (0.20, 0.035)
        shorts = [p for p in legs if p["qty"] < 0 or p["kind"] == "FUT"]
        if not shorts:
            continue
        lo, hi = s * (1 - rng), s * (1 + rng)
        grid = [lo + (hi - lo) * i / 80 for i in range(81)] + [p["strike"] for p in legs if p["strike"] and lo <= p["strike"] <= hi]
        def value(S):
            v = 0.0
            for p in legs:
                if p["kind"] == "FUT":
                    v += p["qty"] * (S - s)
                else:
                    v += p["qty"] * (_intr(p["kind"], p["strike"], S) - _intr(p["kind"], p["strike"], s))
            return v
        worst = max(0.0, -min(value(S) for S in grid))
        short_notional = sum(abs(p["qty"]) * s for p in shorts if p["qty"] < 0 or p["kind"] == "FUT")
        total += worst + expo * short_notional
    return round(total, 2)


def margin_required(positions, spots=None, ltps=None):
    """Margin blocked for the open F&O positions -> (amount, source).
    Angel's calculator on the whole basket, minus what it charges for the long options alone
    (their premium is already paid in cash), so hedges get their benefit."""
    pos = [p for p in positions if p["qty"]]
    risky = [p for p in pos if p["kind"] == "FUT" or p["qty"] < 0]
    if not risky:
        return 0.0, "none"
    ltps = ltps or {}
    legs = [{"exchange": "NFO", "token": p["token"], "qty": abs(p["qty"]), "side": "BUY" if p["qty"] > 0 else "SELL",
             "product": "CARRYFORWARD", "price": ltps.get(p["token"]) or p["avg"]} for p in pos]
    try:
        if not angel.configured():
            raise angel.AngelError("not configured")
        total = margin_api(legs)
        longs = [l for l, p in zip(legs, pos) if p["qty"] > 0 and p["kind"] != "FUT"]
        prem = margin_api(longs) if longs else 0.0
        return round(max(0.0, total - prem), 2), "Angel One margin calculator"
    except Exception as e:
        print(f"[Paper] margin API unavailable, using an estimate: {e}")
        return _approx_margin(pos, spots or {}), "estimate (Angel's calculator unavailable)"


def _resolve_legs(legs):
    out = []
    for l in legs or []:
        und, c = angel.contract(l.get("token"))
        if not c:
            raise PaperError("Unknown contract %s; refresh the option chain." % l.get("token"))
        side = (l.get("side") or "").upper()
        try:
            lots = int(l.get("lots"))
        except (TypeError, ValueError):
            raise PaperError("Lots must be a whole number.")
        if side not in ("BUY", "SELL") or lots < 1:
            raise PaperError("Each leg needs BUY or SELL and at least 1 lot.")
        if c[3] < now_ist().date().isoformat():
            raise PaperError("%s has expired." % c[1])
        out.append({"und": und, "c": c, "side": side, "lots": lots, "qty": lots * c[5]})
    if not out:
        raise PaperError("No legs to trade.")
    if len(out) > 8:
        raise PaperError("A basket can have at most 8 legs.")
    return out


def fo_marks(acc, extra_tokens=()):
    """Quotes for all open positions (+extra tokens) and the spots of their underlyings."""
    toks = [p["token"] for p in acc["positions"].values()] + [str(t) for t in extra_tokens]
    q = fo_quotes(list(dict.fromkeys(toks)))
    spots = {}
    for und in {p["und"] for p in acc["positions"].values()}:
        sq = spot_quote(und)
        if sq:
            spots[und] = sq["ltp"]
    return q, spots


def _fo_numbers(positions, ltp):
    short_liab = long_val = fut_mtm = unreal = 0.0
    for p in positions:
        if not p["qty"]:
            continue
        px = ltp.get(p["token"], p.get("last") or p["avg"])
        if p["kind"] == "FUT":
            fut_mtm += p["qty"] * (px - p["avg"])
        elif p["qty"] > 0:
            long_val += p["qty"] * px
        else:
            short_liab += -p["qty"] * px
        unreal += p["qty"] * (px - p["avg"])
    return short_liab, long_val, fut_mtm, unreal


def place_fo(email, legs, strategy=None, name=""):
    _check_hours()
    rl = _resolve_legs(legs)
    with _lock(_key(email, "FO")):
        acc = load(email, "FO", name)
        sweep(acc)
        q, spots = fo_marks(acc, [l["c"][0] for l in rl])
        for l in rl:
            if l["und"] not in spots:
                sq = spot_quote(l["und"])
                if sq:
                    spots[l["und"]] = sq["ltp"]
        positions = {k: dict(v) for k, v in acc["positions"].items()}
        cash, realized_tot, ch_tot, fills, reduce_only = acc["cash"], 0.0, 0.0, [], True
        gid = uuid.uuid4().hex[:8] if len(rl) > 1 or strategy else None
        for l in rl:
            c = l["c"]
            px = _fill_price(q.get(c[0]), l["side"])
            if not px:
                raise PaperError("No live price for %s right now; try again in a moment." % c[1])
            pos = positions.get(c[0]) or _fo_pos(c, l["und"])
            before = pos["qty"]
            dq = l["qty"] if l["side"] == "BUY" else -l["qty"]
            if not before or (before > 0) == (dq > 0) or abs(dq) > abs(before):
                reduce_only = False
            r = _apply(pos, dq, px)
            ch = charges("FO", "NRML", l["side"], l["qty"], px, c[2])
            if c[2] == "FUT":
                cash += r - ch
            else:
                cash += (l["qty"] * px if l["side"] == "SELL" else -l["qty"] * px) - ch
            pos["last"] = px
            if gid and not pos.get("strategy"):
                pos["strategy"], pos["strategy_name"] = gid, strategy or "Basket"
            positions[c[0]] = pos
            realized_tot += r
            ch_tot += ch
            fills.append({"token": c[0], "tsym": c[1], "side": l["side"], "lots": l["lots"], "qty": l["qty"],
                          "price": round(px, 2), "charges": ch, "realized": round(r, 2)})
        open_pos = [p for p in positions.values() if p["qty"]]
        ltp = {t: (v.get("ltp") or 0) for t, v in q.items()}
        blocked, src = margin_required(open_pos, spots, ltp)
        short_liab, long_val, fut_mtm, _ = _fo_numbers(open_pos, ltp)
        available = cash - short_liab + fut_mtm - blocked
        if available < -1e-6 and not reduce_only:
            raise PaperError("Not enough margin: after this trade the account would need ₹{:,.0f} margin ({}) and be short by ₹{:,.0f}."
                             .format(blocked, src, -available))
        acc["positions"] = {p["key"]: p for p in open_pos}
        acc["cash"] = round(cash, 2)
        acc["realized"] = round(acc["realized"] + realized_tot, 2)
        acc["charges"] = round(acc["charges"] + ch_tot, 2)
        acc["blocked"], acc["blocked_at"], acc["margin_src"] = blocked, time.time(), src
        o = _order(acc, seg="FO", strategy=strategy, group=gid, legs=fills, charges=round(ch_tot, 2),
                   realized=round(realized_tot, 2), margin_after=blocked, margin_src=src)
        save(acc)
        return o, acc


def close_fo(email, keys=None, strategy=None, name=""):
    """Square off positions (by token keys, or every leg of a strategy group)."""
    acc = load(email, "FO", name)
    pos = [p for p in acc["positions"].values() if (keys and p["key"] in keys) or (strategy and p.get("strategy") == strategy)]
    if not pos:
        raise PaperError("Nothing to close.")
    legs = [{"token": p["token"], "side": "SELL" if p["qty"] > 0 else "BUY", "lots": abs(p["qty"]) // p["lot"]} for p in pos]
    return place_fo(email, legs, strategy=("Exit: " + pos[0].get("strategy_name", "")) if strategy else None, name=name)


# ── settlement: intraday square-off + expiry ───────────────────────────────

def sweep(acc, n=None):
    """Square off old/late intraday positions and settle expired contracts. Mutates acc."""
    n = n or now_ist()
    changed = False
    if acc["seg"] == "EQ":
        due = [p for p in acc["positions"].values() if p.get("product") == "MIS" and p["qty"] and
               (p.get("opened", "") < n.date().isoformat() or n.time() >= MIS_SQUAREOFF)]
        if due:
            q = eq_quotes([p["symbol"] for p in due])
            for p in due:
                px = (q.get(p["symbol"]) or {}).get("ltp") or p.get("last") or p["avg"]
                side = "SELL" if p["qty"] > 0 else "BUY"
                qty = abs(p["qty"])
                ch = charges("EQ", "MIS", side, qty, px)
                r = _apply(p, -p["qty"], px)
                acc["cash"] = round(acc["cash"] + r - ch, 2)
                acc["realized"] = round(acc["realized"] + r, 2)
                acc["charges"] = round(acc["charges"] + ch, 2)
                acc["positions"].pop(p["key"], None)
                _order(acc, seg="EQ", symbol=p["symbol"], side=side, qty=qty, price=round(px, 2), product="MIS",
                       charges=ch, realized=round(r, 2), note="Auto square-off (intraday, 3:20 PM)")
                changed = True
    else:
        today = n.date().isoformat()
        due = [p for p in acc["positions"].values() if p["expiry"] < today or (p["expiry"] == today and n.time() >= CLOSE)]
        if due:
            spots = {}
            for und in {p["und"] for p in due}:
                try:
                    sq = spot_quote(und)
                    if sq:
                        spots[und] = sq["ltp"]
                except Exception:
                    pass
            for p in due:
                s = spots.get(p["und"])
                if s is None:
                    if p["expiry"] == today:
                        continue                                  # try again when a price is available
                    s = p.get("last_spot")
                if s is None:
                    val = p.get("last") or 0.0
                else:
                    val = s if p["kind"] == "FUT" else _intr(p["kind"], p["strike"], s)
                q0 = p["qty"]
                r = _apply(p, -q0, val)
                # futures: the profit or loss is paid; options: a long receives qty × value, a short pays it
                acc["cash"] = round(acc["cash"] + (r if p["kind"] == "FUT" else q0 * val), 2)
                acc["realized"] = round(acc["realized"] + r, 2)
                acc["positions"].pop(p["key"], None)
                _order(acc, seg="FO", legs=[{"token": p["token"], "tsym": p["tsym"], "side": "EXPIRY", "qty": 0,
                                             "price": round(val, 2), "realized": round(r, 2)}],
                       charges=0.0, realized=round(r, 2), note="Expiry settlement at %s" % ("intrinsic value" if p["kind"] != "FUT" else "spot close"))
                changed = True
            if changed:
                acc["blocked_at"] = 0
    acc["last_sweep"] = n.strftime("%Y-%m-%d %H:%M")
    return changed


# ── summaries ──────────────────────────────────────────────────────────────

def summary(email, seg, name=""):
    with _lock(_key(email, seg)):
        acc = load(email, seg, name)
        if sweep(acc):
            save(acc)
        if seg == "EQ":
            return _eq_summary(acc)
        return _fo_summary(acc)


def _eq_summary(acc):
    syms = sorted({p["symbol"] for p in acc["positions"].values()})
    q = eq_quotes(syms) if syms else {}
    rows, hold_val, hold_cost, mis_unreal, day = [], 0.0, 0.0, 0.0, 0.0
    for p in acc["positions"].values():
        qq = q.get(p["symbol"]) or {}
        ltp = qq.get("ltp") or p.get("last") or p["avg"]
        pnl = p["qty"] * (ltp - p["avg"])
        if qq.get("ltp"):
            p["last"] = ltp
        if p["product"] == "CNC":
            hold_val += p["qty"] * ltp
            hold_cost += p["qty"] * p["avg"]
            if qq.get("close"):
                day += p["qty"] * (ltp - qq["close"])
        else:
            mis_unreal += pnl
        rows.append({"key": p["key"], "symbol": p["symbol"], "product": p["product"], "qty": p["qty"], "avg": round(p["avg"], 2),
                     "ltp": round(ltp, 2), "value": round(p["qty"] * ltp, 2), "pnl": round(pnl, 2),
                     "pnl_pct": round((ltp / p["avg"] - 1) * 100 * (1 if p["qty"] > 0 else -1), 2) if p["avg"] else None,
                     "chg_pct": qq.get("chg_pct"), "live": bool(qq.get("ltp")), "source": qq.get("source")})
    rows.sort(key=lambda r: (r["product"], r["symbol"]))
    blocked = _mis_blocked(acc)
    available = acc["cash"] - blocked + min(0.0, mis_unreal)
    value = acc["cash"] + hold_val + mis_unreal
    return _common(acc, {"positions": rows, "holdings_value": round(hold_val, 2), "invested": round(hold_cost, 2),
                         "mis_unrealized": round(mis_unreal, 2), "day_pnl": round(day, 2), "blocked": blocked,
                         "available": round(available, 2), "account_value": round(value, 2)})


def _fo_summary(acc):
    pos = list(acc["positions"].values())
    q, spots = fo_marks(acc) if pos else ({}, {})
    ltp = {t: (v.get("ltp") or 0) for t, v in q.items()}
    for p in pos:
        if ltp.get(p["token"]):
            p["last"] = ltp[p["token"]]
        if spots.get(p["und"]):
            p["last_spot"] = spots[p["und"]]
    if pos and time.time() - (acc.get("blocked_at") or 0) > MARGIN_TTL:
        acc["blocked"], acc["margin_src"] = margin_required(pos, spots, ltp)
        acc["blocked_at"] = time.time()
        save(acc)
    elif not pos:
        acc["blocked"] = 0.0
    short_liab, long_val, fut_mtm, unreal = _fo_numbers(pos, ltp)
    rows = []
    for p in pos:
        px = ltp.get(p["token"]) or p.get("last") or p["avg"]
        qq = q.get(p["token"]) or {}
        rows.append({"key": p["key"], "tsym": p["tsym"], "und": p["und"], "kind": p["kind"], "expiry": p["expiry"],
                     "strike": p["strike"], "lot": p["lot"], "lots": p["qty"] // p["lot"] if p["lot"] else None,
                     "qty": p["qty"], "avg": round(p["avg"], 2), "ltp": round(px, 2), "pnl": round(p["qty"] * (px - p["avg"]), 2),
                     "chg_pct": qq.get("chg_pct"), "live": bool(qq.get("ltp")), "strategy": p.get("strategy"),
                     "strategy_name": p.get("strategy_name"), "spot": spots.get(p["und"])})
    rows.sort(key=lambda r: (r["strategy"] or "~", r["und"], r["expiry"], r["kind"], r["strike"] or 0))
    blocked = acc.get("blocked") or 0.0
    value = acc["cash"] + long_val - short_liab + fut_mtm
    available = acc["cash"] - short_liab + fut_mtm - blocked
    return _common(acc, {"positions": rows, "blocked": round(blocked, 2), "margin_src": acc.get("margin_src"),
                         "unrealized": round(unreal, 2), "long_value": round(long_val, 2), "short_liability": round(short_liab, 2),
                         "available": round(available, 2), "account_value": round(value, 2)})


def _common(acc, d):
    pend = [r for r in requests_list() if r["email"] == acc["email"] and r["seg"] == acc["seg"] and r["status"] == "pending"]
    d.update({"seg": acc["seg"], "capital": acc["capital"], "cash": round(acc["cash"], 2), "realized": acc["realized"],
              "charges": acc["charges"], "refills": acc["refills"], "created": acc["created"],
              "total_pnl": round(d["account_value"] - acc["capital"], 2),
              "orders": list(reversed(acc["orders"][-60:])), "market_open": market_open(),
              "can_request": d["available"] < acc["capital"] * REFILL_BELOW and not pend,
              "pending_request": pend[0] if pend else None, "refill_below": acc["capital"] * REFILL_BELOW})
    return d


# ── fresh-start requests (admin approves) ──────────────────────────────────

def requests_list():
    doc = _db().paper_get(REQ_KEY) or {"items": []}
    return doc["items"]


def request_refill(email, seg, name=""):
    s = summary(email, seg, name)
    if s["pending_request"]:
        raise PaperError("You already have a request waiting for approval.")
    if not s["can_request"]:
        raise PaperError("You can ask for a fresh start once your available funds fall below ₹{:,.0f}.".format(s["refill_below"]))
    doc = _db().paper_get(REQ_KEY) or {"items": []}
    item = {"id": uuid.uuid4().hex[:10], "email": email.lower(), "name": name, "seg": seg,
            "at": now_ist().strftime("%d %b %Y %H:%M"), "status": "pending",
            "account_value": s["account_value"], "available": s["available"], "refills_so_far": s["refills"]}
    doc["items"].append(item)
    doc["items"] = doc["items"][-500:]
    _db().paper_put(REQ_KEY, doc)
    return item


def decide(req_id, approve, admin_email):
    doc = _db().paper_get(REQ_KEY) or {"items": []}
    item = next((r for r in doc["items"] if r["id"] == req_id), None)
    if not item:
        raise PaperError("Request not found.")
    if item["status"] != "pending":
        raise PaperError("This request was already %s." % item["status"])
    item.update(status="approved" if approve else "rejected", decided_by=admin_email,
                decided_at=now_ist().strftime("%d %b %Y %H:%M"))
    if approve:
        reset(item["email"], item["seg"], admin_email)
    _db().paper_put(REQ_KEY, doc)
    return item


def reset(email, seg, by):
    """Fresh start: open positions are closed at the last price (no charges), cash back to full capital."""
    with _lock(_key(email, seg)):
        acc = load(email, seg)
        closed = len(acc["positions"])
        acc["positions"] = {}
        acc["cash"] = CAPITAL[seg]
        acc["capital"] = CAPITAL[seg]
        acc["blocked"] = 0.0
        acc["refills"] = acc.get("refills", 0) + 1
        acc["realized"], acc["charges"] = 0.0, 0.0
        _order(acc, seg=seg, note="Fresh start approved by admin: ₹{:,.0f} (refill #{}, {} open position(s) closed)".format(
            CAPITAL[seg], acc["refills"], closed), charges=0.0, realized=0.0)
        save(acc)
        return acc


# ── option chain + strategies ───────────────────────────────────────────────

def chain(und, expiry=None, n=12):
    und = und.upper()
    exps = angel.expiries(und)
    if not exps:
        raise PaperError("No F&O contracts for %s." % und)
    expiry = expiry if expiry in exps else exps[0]
    cons = angel.chain_contracts(und, expiry)
    strikes = sorted(cons)
    sq = spot_quote(und)
    spot = sq["ltp"] if sq else None
    if not strikes:
        raise PaperError("No strikes for %s %s." % (und, expiry))
    atm = min(strikes, key=lambda k: abs(k - spot)) if spot else strikes[len(strikes) // 2]
    i = strikes.index(atm)
    pick = strikes[max(0, i - n): i + n + 1]
    toks = [cons[k][t][0] for k in pick for t in ("CE", "PE") if t in cons[k]]
    q = fo_quotes(toks)
    rows = []
    for k in pick:
        row = {"strike": k, "atm": k == atm}
        for t in ("CE", "PE"):
            c = cons[k].get(t)
            if c:
                qq = q.get(c[0]) or {}
                row[t] = {"token": c[0], "tsym": c[1], "lot": c[5], "ltp": qq.get("ltp"), "chg": qq.get("chg"),
                          "chg_pct": qq.get("chg_pct"), "oi": qq.get("oi"), "bid": qq.get("bid"), "ask": qq.get("ask"),
                          "volume": qq.get("volume")}
        rows.append(row)
    fut = angel.futures(und)
    fq = fo_quotes([fut[0][0]]).get(fut[0][0]) if fut else None
    lot = next((c[5] for k in pick for c in cons[k].values()), None)
    return {"und": und, "expiry": expiry, "expiries": exps, "spot": spot, "spot_chg_pct": (sq or {}).get("chg_pct"),
            "spot_from_future": bool((sq or {}).get("from_future")), "atm": atm, "lot": lot, "rows": rows,
            "future": {"token": fut[0][0], "tsym": fut[0][1], "expiry": fut[0][3], "lot": fut[0][5],
                       "ltp": (fq or {}).get("ltp"), "chg_pct": (fq or {}).get("chg_pct")} if fut else None,
            "at": now_ist().strftime("%H:%M:%S"), "market_open": market_open()}


TEMPLATES = {
    "long_straddle": "Long straddle (buy ATM call + put)", "short_straddle": "Short straddle (sell ATM call + put)",
    "long_strangle": "Long strangle (buy OTM call + put)", "short_strangle": "Short strangle (sell OTM call + put)",
    "iron_condor": "Iron condor (sell OTM call + put, buy wings)", "bull_call_spread": "Bull call spread",
    "bear_put_spread": "Bear put spread",
}


def build(und, expiry, template, lots=1, gap=2, width=2):
    """Legs for a template: gap = strikes from ATM for the short/OTM legs, width = wing distance in strikes."""
    if template not in TEMPLATES:
        raise PaperError("Unknown strategy.")
    und = und.upper()
    cons = angel.chain_contracts(und, expiry)
    strikes = sorted(cons)
    sq = spot_quote(und)
    if not strikes or not sq:
        raise PaperError("No live price for %s right now." % und)
    i = strikes.index(min(strikes, key=lambda k: abs(k - sq["ltp"])))
    at = lambda j, t: cons[strikes[max(0, min(len(strikes) - 1, j))]].get(t)
    lots, gap, width = max(1, int(lots)), max(0, int(gap)), max(1, int(width))
    S, B = "SELL", "BUY"
    spec = {
        "long_straddle": [(i, "CE", B), (i, "PE", B)], "short_straddle": [(i, "CE", S), (i, "PE", S)],
        "long_strangle": [(i + gap, "CE", B), (i - gap, "PE", B)], "short_strangle": [(i + gap, "CE", S), (i - gap, "PE", S)],
        "iron_condor": [(i + gap, "CE", S), (i + gap + width, "CE", B), (i - gap, "PE", S), (i - gap - width, "PE", B)],
        "bull_call_spread": [(i, "CE", B), (i + width, "CE", S)], "bear_put_spread": [(i, "PE", B), (i - width, "PE", S)],
    }[template]
    legs = []
    for j, t, side in spec:
        c = at(j, t)
        if not c:
            raise PaperError("A strike needed for this strategy isn't listed; try a smaller gap or width.")
        legs.append({"token": c[0], "side": side, "lots": lots})
    return legs


def preview(legs, und=None):
    """Live prices, net premium, broker margin for the basket on its own, and the payoff at expiry."""
    rl = _resolve_legs(legs)
    q = fo_quotes([l["c"][0] for l in rl])
    und = und or rl[0]["und"]
    sq = spot_quote(und)
    spot = sq["ltp"] if sq else None
    out, net = [], 0.0
    pos = []
    for l in rl:
        c = l["c"]
        px = _fill_price(q.get(c[0]), l["side"]) or 0.0
        sign = 1 if l["side"] == "BUY" else -1
        net += -sign * l["qty"] * px if c[2] != "FUT" else 0.0
        out.append({"token": c[0], "tsym": c[1], "kind": c[2], "strike": c[4], "expiry": c[3], "side": l["side"],
                    "lots": l["lots"], "lot": c[5], "qty": l["qty"], "price": round(px, 2),
                    "ltp": (q.get(c[0]) or {}).get("ltp")})
        p = _fo_pos(c, l["und"])
        p["qty"], p["avg"] = sign * l["qty"], px
        pos.append(p)
    m, src = margin_required(pos, {und: spot} if spot else {}, {c["token"]: c["price"] for c in out})
    ch = sum(charges("FO", "NRML", o["side"], o["qty"], o["price"], o["kind"]) for o in out)
    return {"legs": out, "spot": spot, "net_premium": round(net, 2), "margin": m, "margin_src": src,
            "charges": round(ch, 2), "payoff": payoff(pos, spot) if spot else None}


def payoff(pos, spot):
    """P&L at expiry over a price range: max profit / loss (or unlimited), breakevens, curve points."""
    lo, hi = spot * 0.6, spot * 1.4
    strikes = sorted({p["strike"] for p in pos if p["strike"]})
    grid = sorted(set([lo + (hi - lo) * i / 200 for i in range(201)] + [k for k in strikes if lo <= k <= hi]))
    def pl(S):
        v = 0.0
        for p in pos:
            end = S if p["kind"] == "FUT" else _intr(p["kind"], p["strike"], S)
            v += p["qty"] * (end - p["avg"])
        return v
    vals = [pl(S) for S in grid]
    slope_lo = vals[1] - vals[0]
    slope_hi = vals[-1] - vals[-2]
    mx, mn = max(vals), min(vals)
    be = []
    for i in range(1, len(grid)):
        a, b = vals[i - 1], vals[i]
        if (a < 0 <= b) or (a >= 0 > b):
            x = grid[i - 1] + (grid[i] - grid[i - 1]) * (0 - a) / ((b - a) or 1)
            be.append(round(x, 2))
    # the chart zooms to where the strikes and breakevens are (the wide range is only for max profit / loss)
    pts = strikes + be + [spot]
    span = max(max(abs(x - spot) for x in pts) * 1.6, spot * 0.04)
    clo, chi = max(lo, spot - span), min(hi, spot + span)
    cg = sorted(set([clo + (chi - clo) * i / 80 for i in range(81)] + [k for k in strikes if clo <= k <= chi]))
    return {"max_profit": None if (slope_hi > 1e-6 or slope_lo < -1e-6) else round(mx, 2),
            "max_loss": None if (slope_hi < -1e-6 or slope_lo > 1e-6) else round(mn, 2),
            "breakevens": be, "at_spot": round(pl(spot), 2),
            "curve": [[round(x, 2), round(pl(x), 2)] for x in cg]}


# ── background: square-off + expiry for everyone ───────────────────────────

_bg = {"started": False}


def _bg_loop():
    while True:
        try:
            n = now_ist()
            if is_trading_day(n.date()) and dt.time(15, 20) <= n.time() <= dt.time(16, 30):
                for k in _db().paper_keys("paper:"):
                    parts = k.split(":")
                    if len(parts) != 3:
                        continue
                    _, email, seg = parts
                    if seg == "EQ" and n.time() > dt.time(15, 45):
                        continue
                    with _lock(k):
                        acc = _db().paper_get(k)
                        if acc and acc.get("positions") and sweep(acc, n):
                            save(acc)
        except Exception as e:
            print(f"[Paper] background sweep: {e}")
        time.sleep(60)


def ensure_background():
    if _bg["started"] or os.environ.get("MW_NO_POLLERS") == "1":
        return
    _bg["started"] = True
    threading.Thread(target=_bg_loop, daemon=True, name="paper-sweep").start()
