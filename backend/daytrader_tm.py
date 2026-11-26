"""Time Machine for the Day Trader Scanner (NSE): "how often did this trade actually work?"

For every stock the scanner finds, two independent answers:

  history   the SAME rule re-run on the stock's own past — every day the setup fired,
            with the same entry / stop / target rules the scanner prints today — and what
            really happened next: target first, stop first, or neither.
  replays   5,000 simulated futures built from the stock's own recent bars (bootstrap),
            starting at today's entry with today's stop and target.

Rules (shared with the method of trader-timemachine in github.com/Jakeschincariol/day-trader-skill
and its NSE port github.com/ankurgoels/trader-skills-india, both MIT):
  * a bar that touches both the stop and the target counts as the stop (we can't see the order)
  * a gap through the stop exits at the open (worse than the stop), a gap over the target at the open
  * R = (exit − entry) ÷ (entry − stop); costs are taken off in R
  * the verdict compares "target first" with break-even for that target: 1 in (1 + R),
    i.e. 33.3 % for the 2R target
  * one trade at a time per stock and setup: a new signal while a trade is open is ignored

Costs (approximate, NSE equity, discount broker): a swing trade held overnight is a delivery
trade — STT 0.1 % each side + stamp + exchange + GST ≈ 0.22 % of the price round trip;
an intraday trade ≈ 0.08 % (Rs 20/order brokerage on a ~Rs 1 lakh position, STT 0.025 % on the
sell, exchange, stamp, GST). Rates as in github.com/sanT-0/nse-day-trader-skills.

Nothing here predicts. It is a record and a simulation built from past prices.
"""
import datetime as dt
import math

import numpy as np

import daytrader_core as core

HOLD_DAYS = 10                   # daily setups: give the trade up to 10 sessions
REPLAYS = 5000
SEED = 19
DELIVERY_COST = 0.0022
INTRADAY_COST = 0.0008
MIN_TRADES = 8                   # fewer past trades than this: "too few to judge"
WARMUP = 30
LATE_SLOT_TIME = dt.time(14, 30)  # ORB: breakouts after this aren't taken (as on the page)
TARGET_R = 2.0


# ── shared ────────────────────────────────────────────────────────────────

def verdict(t_first, s_first, target_r=TARGET_R, n=None, avg_r=None):
    """One short label from target-first vs stop-first, judged against break-even 1/(1+R),
    then checked against the average result after costs (trades that hit neither side and
    costs can turn a lean around)."""
    if n is not None and n < MIN_TRADES:
        return {"key": "few", "text": f"Too few trades ({n})"}
    decided = t_first + s_first
    if decided <= 0:
        return {"key": "none", "text": "Neither side reached"}
    share = t_first / decided
    d = (share - 1.0 / (1.0 + target_r)) * 100
    if abs(d) < 5:
        key, text = "flip", "Coin flip (near break-even)"
    elif d > 0:
        key, text = ("good", "Leans to target") if d < 15 else ("good_strong", "Strongly to target")
    else:
        key, text = ("bad", "Leans to stop") if d > -15 else ("bad_strong", "Strongly to stop")
    if avg_r is not None:
        if key == "flip" and avg_r <= -0.1:
            key, text = "bad", "Loses after costs"
        elif key == "flip" and avg_r >= 0.1:
            key, text = "good", "Small edge after costs"
        elif key.startswith("good") and avg_r < 0:
            key, text = "flip", "Wins often, but loses after costs"
        elif key.startswith("bad") and avg_r > 0:
            key, text = "flip", "Stopped often, but small gain after costs"
    return {"key": key, "text": text, "edge_pts": round(d, 1)}


def _summ(trades, target_r=TARGET_R):
    n = len(trades)
    if not n:
        return {"trades": 0}
    tf = sum(1 for t in trades if t["out"] == "target")
    sf = sum(1 for t in trades if t["out"] == "stop")
    ne = n - tf - sf
    t1 = sum(1 for t in trades if t.get("t1"))
    rs = [t["r"] for t in trades]
    return {"trades": n, "target_first": tf, "stop_first": sf, "neither": ne,
            "target_pct": round(tf / n * 100, 1), "stop_pct": round(sf / n * 100, 1),
            "neither_pct": round(ne / n * 100, 1), "t1_pct": round(t1 / n * 100, 1),
            "avg_r": round(sum(rs) / n, 2), "win_pct": round(sum(1 for r in rs if r > 0) / n * 100, 1),
            "verdict": verdict(tf, sf, target_r, n, round(sum(rs) / n, 2))}


def _rng(seed):
    return np.random.default_rng(seed)


# ── daily setups ──────────────────────────────────────────────────────────

def _arrays(bars):
    c = np.array([b["c"] for b in bars], float)
    h = np.array([b["h"] if b["h"] is not None else b["c"] for b in bars], float)
    l = np.array([b["l"] if b["l"] is not None else b["c"] for b in bars], float)
    o = np.array([b["o"] if b.get("o") is not None else b["c"] for b in bars], float)
    return o, h, l, c


def _wilder(x, n):
    """Wilder smoothing (SMA seed) of a 1-d array; NaN until n values exist."""
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    a = x[:n].mean()
    out[n - 1] = a
    for i in range(n, len(x)):
        a = (a * (n - 1) + x[i]) / n
        out[i] = a
    return out


def rsi_series(c, n=14):
    d = np.diff(c, prepend=np.nan)
    g = np.where(d > 0, d, 0.0)[1:]
    lo = np.where(d < 0, -d, 0.0)[1:]
    ag, al = _wilder(g, n), _wilder(lo, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(al == 0, 100.0, 100 - 100 / (1 + ag / al))
    return np.concatenate([[np.nan], r])


def atr_series(h, l, c, n=14):
    pc = np.concatenate([[np.nan], c[:-1]])
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)[1:]
    return np.concatenate([[np.nan], _wilder(tr, n)])


def setup_signals(c):
    """{setup: bool array} — True on day t when the scanner's rule fires on closes up to t.
    Same five rules as daytrader_core.find_setups, computed for every day at once."""
    N = len(c)
    sig = {k: np.zeros(N, bool) for k, _ in core.SETUPS}
    if N < 22:
        return sig
    win = np.lib.stride_tricks.sliding_window_view
    hi20 = np.full(N, np.nan)
    hi20[20:] = win(c[:-1], 20).max(axis=1)                 # max of the 20 closes before t
    ma20 = np.full(N, np.nan)
    ma20[19:] = win(c, 20).mean(axis=1)
    ma20_prev = np.concatenate([[np.nan], ma20[:-1]])
    run = np.full(N, np.nan)
    run[15:] = c[10:-5] / c[:-15] - 1                        # c[t-5] / c[t-15] − 1
    rng5 = np.full(N, np.nan)
    w5 = win(c, 5)
    rng5[4:] = (w5.max(axis=1) - w5.min(axis=1)) / c[4:]
    r = rsi_series(c)
    r_prev = np.concatenate([[np.nan], r[:-1]])
    c_prev = np.concatenate([[np.nan], c[:-1]])
    with np.errstate(invalid="ignore"):
        sig["breakout_20d"] = c > hi20
        sig["bull_flag"] = (run > 0.10) & (rng5 < 0.05)
        sig["reclaim_ma20"] = (c_prev < ma20_prev) & (c > ma20)
        sig["oversold"] = r < 30
        sig["oversold_bounce"] = (r_prev < 30) & (c > c_prev)
    for k in sig:
        sig[k][:WARMUP] = False
    return sig


def daily_plan(h_t, l_t, atr_t):
    """The scanner's next-session levels from one day's bar (same as daytrader_core.daily_levels)."""
    if not atr_t or atr_t != atr_t or atr_t <= 0:
        return None
    entry = core.to_tick(h_t + float(core.tick_for(h_t)), "up")
    stop = l_t - float(core.tick_for(l_t))
    if stop < entry - 1.5 * atr_t:
        stop = entry - 1.5 * atr_t
    elif stop > entry - 0.5 * atr_t:
        stop = entry - 0.5 * atr_t
    stop = core.to_tick(stop, "down", entry)
    risk = entry - stop
    if risk <= 0:
        return None
    return {"entry": entry, "stop": stop, "t1": core.to_tick(entry + risk, "near"),
            "t2": core.to_tick(entry + TARGET_R * risk, "near"), "risk": risk}


def simulate_daily_trade(o, h, l, c, t, plan, hold=HOLD_DAYS):
    """Signal on day t → trade from day t+1. Returns a trade dict, or a skip reason string."""
    N = len(c)
    j = t + 1
    if j >= N:
        return "no_next_day"
    entry, stop, t1, t2 = plan["entry"], plan["stop"], plan["t1"], plan["t2"]
    if h[j] < entry:
        return "not_triggered"
    if o[j] >= t1:
        return "gapped_past"                    # the page says: skip a stock that opens far above
    fill = max(o[j], entry)
    risk = fill - stop
    if risk <= 0:
        return "gapped_past"
    out, exit_px, t1_hit, exit_i = None, None, False, None
    for k in range(j, min(j + hold, N)):
        if k > j and o[k] <= stop:              # gap through the stop
            out, exit_px, exit_i = "stop", o[k], k
            break
        if l[k] <= stop:
            out, exit_px, exit_i = "stop", stop, k
            break
        if k > j and o[k] >= t2:                # gap over the target
            out, exit_px, exit_i = "target", o[k], k
            t1_hit = True
            break
        if h[k] >= t1:
            t1_hit = True
        if h[k] >= t2:
            out, exit_px, exit_i = "target", t2, k
            break
    if out is None:
        last = j + hold - 1
        if last >= N:
            return "still_open"                 # not enough days after it yet
        out, exit_px, exit_i = "neither", c[last], last
    r = (exit_px - fill) / risk - DELIVERY_COST * fill / risk
    return {"out": out, "r": round(float(r), 3), "t1": bool(t1_hit), "exit_i": int(exit_i), "days": int(exit_i - j + 1)}


def daily_history(bars, setups, regime=None):
    """Re-run each setup's rule over the stock's history. -> {setup: {summary, trades}}."""
    o, h, l, c = _arrays(bars)
    sig = setup_signals(c)
    a = atr_series(h, l, c)
    dates = [b["d"] for b in bars]
    out = {}
    for s in setups:
        trades, skips, busy_until = [], {}, -1
        fires = np.nonzero(sig.get(s, np.zeros(len(c), bool)))[0]
        for t in fires:
            if t >= len(c) - 1 or t <= busy_until:
                continue
            plan = daily_plan(h[t], l[t], a[t])
            if not plan:
                continue
            res = simulate_daily_trade(o, h, l, c, t, plan)
            if isinstance(res, str):
                skips[res] = skips.get(res, 0) + 1
                continue
            busy_until = res["exit_i"]
            d = dates[t]
            res.update(date=d.isoformat(), regime=(regime or {}).get(d))
            del res["exit_i"]
            trades.append(res)
        summ = _summ(trades)
        summ["signals"] = int(len(fires))
        summ["skipped"] = skips
        summ["recent"] = [{"date": t["date"], "out": t["out"], "r": t["r"], "days": t["days"]} for t in trades[-6:]][::-1]
        out[s] = {"summary": summ, "trades": trades}
    return out


def daily_replays(bars, entry, stop, target, hold=HOLD_DAYS, n=REPLAYS, seed=SEED, pool_days=250):
    """5,000 futures from the stock's own last ~year of daily bars, starting at today's entry."""
    o, h, l, c = _arrays(bars[-(pool_days + 1):])
    if len(c) < 60 or entry <= stop:
        return None
    pc = c[:-1]
    ro, rh, rl, rc = o[1:] / pc, h[1:] / pc, l[1:] / pc, c[1:] / pc
    rng = _rng(seed)
    idx = rng.integers(0, len(rc), size=(n, hold))
    prev = np.full(n, float(entry))
    risk = entry - stop
    result = np.zeros(n)                       # R per path
    done = np.zeros(n, bool)
    outcome = np.zeros(n, int)                 # 1 target, -1 stop, 0 neither
    for k in range(hold):
        i = idx[:, k]
        op, hi, lo, cl = prev * ro[i], prev * rh[i], prev * rl[i], prev * rc[i]
        live = ~done
        gap_stop = live & (op <= stop) & (k > 0)
        hit_stop = live & ~gap_stop & (lo <= stop)
        gap_tgt = live & ~gap_stop & ~hit_stop & (op >= target) & (k > 0)
        hit_tgt = live & ~gap_stop & ~hit_stop & ~gap_tgt & (hi >= target)
        result[gap_stop] = (op[gap_stop] - entry) / risk
        result[hit_stop] = -1.0
        result[gap_tgt] = (op[gap_tgt] - entry) / risk
        result[hit_tgt] = (target - entry) / risk
        outcome[gap_stop | hit_stop] = -1
        outcome[gap_tgt | hit_tgt] = 1
        done |= gap_stop | hit_stop | gap_tgt | hit_tgt
        prev = cl
    still = ~done
    result[still] = (prev[still] - entry) / risk
    result -= DELIVERY_COST * entry / risk
    tf, sf = int((outcome == 1).sum()), int((outcome == -1).sum())
    return {"replays": n, "seed": seed, "days": hold,
            "target_pct": round(tf / n * 100, 1), "stop_pct": round(sf / n * 100, 1),
            "neither_pct": round((n - tf - sf) / n * 100, 1),
            "avg_r": round(float(result.mean()), 2),
            "p10_r": round(float(np.percentile(result, 10)), 2), "p90_r": round(float(np.percentile(result, 90)), 2),
            "verdict": verdict(tf, sf, avg_r=float(result.mean()))}


# ── opening-range breakouts (5-minute bars) ───────────────────────────────

N_RANGE = 3                       # 15-minute opening range = three 5-minute bars


def _slot_of(t):
    return int(((t.hour * 60 + t.minute) - (core.OPEN.hour * 60 + core.OPEN.minute)) // core.BAR_MIN)


def full_sessions(series, before_day):
    """{day: bars} for complete 9:15–15:30 sessions (75 bars) before `before_day`."""
    days = sorted({core.ts_to_ist(ts).date() for ts in series["t"]})
    out = {}
    for d in days:
        if d >= before_day or not core.trading_day(d)[0]:
            continue
        close_dt = dt.datetime.combine(d, core.CLOSE)
        bars = core.session_bars(series, d, close_dt)
        if core.usable(bars, d, close_dt, N_RANGE) and all(b["h"] is not None and b["l"] is not None for b in bars):
            out[d] = bars
    return out


def orb_trade(bars, exit_by, stop_rule="or-mid"):
    """The page's ORB rule on one full session → trade dict, or None if no (valid) breakout."""
    rng = bars[:N_RANGE]
    oh, ol = max(b["h"] for b in rng), min(b["l"] for b in rng)
    last_slot = _slot_of(exit_by) - 1          # the bar that ends at exit_by
    for k in range(N_RANGE, len(bars)):
        b = bars[k]
        if b["c"] <= oh:
            continue
        if b["start"].time() >= LATE_SLOT_TIME or k >= last_slot:
            return None                        # breakout too late to take
        entry = b["c"]
        stop = core.to_tick((oh + ol) / 2 if stop_rule == "or-mid" else ol, "down", entry)
        risk = entry - stop
        if risk <= 0:
            return None
        t1, t2 = entry + risk, entry + TARGET_R * risk
        t1_hit = False
        for x in bars[k + 1:last_slot + 1]:
            if x["l"] <= stop:
                return _orb_res("stop", stop, entry, risk, t1_hit, b)
            if x["h"] >= t1:
                t1_hit = True
            if x["h"] >= t2:
                return _orb_res("target", t2, entry, risk, True, b)
        return _orb_res("neither", bars[last_slot]["c"], entry, risk, t1_hit, b)
    return None


def _orb_res(out, px, entry, risk, t1, bar):
    r = (px - entry) / risk - INTRADAY_COST * entry / risk
    return {"out": out, "r": round(float(r), 3), "t1": bool(t1), "at": bar["start"].strftime("%H:%M")}


def orb_history(sessions, exit_by):
    trades = []
    for d in sorted(sessions):
        t = orb_trade(sessions[d], exit_by)
        if t:
            t["date"] = d.isoformat()
            trades.append(t)
    summ = _summ(trades)
    summ["sessions"] = len(sessions)
    summ["recent"] = [{"date": t["date"], "out": t["out"], "r": t["r"], "at": t["at"]} for t in trades[-6:]][::-1]
    return {"summary": summ, "trades": trades}


def orb_replays(sessions, entry, stop, target, trigger_slot, exit_by, n=REPLAYS, seed=SEED, window=2):
    """Rebuild the rest of the day 5,000 times from past bars at the same time of day (± window bars)."""
    if len(sessions) < 20 or entry <= stop:
        return None
    by_slot = {}
    for bars in sessions.values():
        for k in range(1, len(bars)):
            pc = bars[k - 1]["c"]
            if pc:
                by_slot.setdefault(k, []).append((bars[k]["h"] / pc, bars[k]["l"] / pc, bars[k]["c"] / pc))
    last_slot = _slot_of(exit_by) - 1
    slots = list(range(trigger_slot + 1, last_slot + 1))
    if not slots:
        return None
    rng = _rng(seed)
    risk = entry - stop
    prev = np.full(n, float(entry))
    result = np.zeros(n)
    done = np.zeros(n, bool)
    outcome = np.zeros(n, int)
    for s in slots:
        pool = [x for k in range(s - window, s + window + 1) for x in by_slot.get(k, [])]
        if not pool:
            continue
        arr = np.array(pool)
        pick = arr[rng.integers(0, len(arr), size=n)]
        hi, lo, cl = prev * pick[:, 0], prev * pick[:, 1], prev * pick[:, 2]
        live = ~done
        hs = live & (lo <= stop)
        ht = live & ~hs & (hi >= target)
        result[hs] = -1.0
        result[ht] = (target - entry) / risk
        outcome[hs], outcome[ht] = -1, 1
        done |= hs | ht
        prev = cl
    still = ~done
    result[still] = (prev[still] - entry) / risk
    result -= INTRADAY_COST * entry / risk
    tf, sf = int((outcome == 1).sum()), int((outcome == -1).sum())
    return {"replays": n, "seed": seed, "until": exit_by.strftime("%H:%M"),
            "target_pct": round(tf / n * 100, 1), "stop_pct": round(sf / n * 100, 1),
            "neither_pct": round((n - tf - sf) / n * 100, 1), "avg_r": round(float(result.mean()), 2),
            "p10_r": round(float(np.percentile(result, 10)), 2), "p90_r": round(float(np.percentile(result, 90)), 2),
            "verdict": verdict(tf, sf, avg_r=float(result.mean()))}


# ── market regime + report card ──────────────────────────────────────────

def regime_map(nifty_bars):
    """{date: 'up'|'down'} — Nifty 50 above / below its 50-day average that day."""
    if not nifty_bars or len(nifty_bars) < 60:
        return {}
    c = np.array([b["c"] for b in nifty_bars], float)
    ma50 = np.full(len(c), np.nan)
    ma50[49:] = np.lib.stride_tricks.sliding_window_view(c, 50).mean(axis=1)
    return {b["d"]: ("up" if c[i] > ma50[i] else "down") for i, b in enumerate(nifty_bars) if ma50[i] == ma50[i]}


def regime_now(nifty_bars):
    """Today's Nifty trend in plain words."""
    if not nifty_bars or len(nifty_bars) < 60:
        return None
    c = np.array([b["c"] for b in nifty_bars], float)
    ma20, ma50 = c[-20:].mean(), c[-50:].mean()
    ma20_prev = c[-25:-5].mean()
    last = c[-1]
    if last > ma50 and ma20 > ma20_prev:
        key, text = "up", "Uptrend: Nifty is above its 50-day average and the 20-day average is rising"
    elif last < ma50 and ma20 < ma20_prev:
        key, text = "down", "Downtrend: Nifty is below its 50-day average and the 20-day average is falling"
    else:
        key, text = "side", "Sideways: Nifty's trend is mixed"
    return {"key": key, "text": text, "nifty": round(float(last), 2), "ma20": round(float(ma20), 2),
            "ma50": round(float(ma50), 2), "date": nifty_bars[-1]["d"].isoformat()}


def report_card(all_trades, by_key="setup"):
    """Pool every stock's past trades: {group: summary (+ by Nifty regime for daily)}."""
    groups = {}
    for t in all_trades:
        groups.setdefault(t[by_key], []).append(t)
    out = {}
    for g, ts in groups.items():
        s = _summ(ts)
        if any(t.get("regime") for t in ts):
            s["by_regime"] = {r: _summ([t for t in ts if t.get("regime") == r]) for r in ("up", "down")}
        out[g] = s
    return out


def orb_time_bucket(at):
    h, m = map(int, at.split(":"))
    mins = h * 60 + m
    return "9:30–10:30" if mins < 10 * 60 + 30 else ("10:30–12:30" if mins < 12 * 60 + 30 else "12:30–2:30")


def rank_score(summary):
    """Sort key: average R after costs, shrunk toward 0 for small samples."""
    n = summary.get("trades") or 0
    if not n:
        return -9.0
    return summary["avg_r"] * n / (n + 10)
