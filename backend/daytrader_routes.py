"""
API for the Day Trader Scanner page. Separate from the other screeners: its own
endpoints, its own background-scan state and its own saved-results keys.

  POST /api/daytrader/start?mode=daily|orb&universe=all|nifty500   start a background scan
  GET  /api/daytrader/status?mode=daily|orb   idle / running (with progress) / done / error
  GET  /api/daytrader/results?mode=daily|orb  the last saved scan

A scan of every NSE stock takes several minutes, so:
  * only one scan per mode runs at a time, and a new one can't start within
    COOLDOWN of the last (admins can override) — everyone shares the saved result;
  * the server runs the scans itself on trading days: the daily scan after the
    close (AUTO_DAILY, for the next session's levels) and the opening-range scan
    in the morning (AUTO_ORB), so most visits just read a fresh saved scan.
"""

import datetime
import os
import threading
import time
import traceback

from flask import Blueprint, jsonify, request

from database import get_screener_results, save_screener_results

daytrader_bp = Blueprint("daytrader", __name__)

MODES = {"daily": "daytrader_daily", "orb": "daytrader_orb"}   # storage keys, distinct from every screener
UNIVERSES = ("all", "nifty500")
COOLDOWN = 10 * 60
AUTO_DAILY = datetime.time(16, 20)       # IST: after the close, NSE's last prices are in
AUTO_ORB = (datetime.time(9, 50), datetime.time(11, 0))
_state = {}
_lock = threading.Lock()
_sched = {"started": False, "ran": set()}


def _mode():
    m = (request.args.get("mode") or "daily").lower()
    return m if m in MODES else None


def _set(mode, **kw):
    with _lock:
        st = dict(_state.get(mode) or {})
        st.update(kw)
        _state[mode] = st


def _run(mode, universe, who="user"):
    def progress(stage, done, total):
        _set(mode, stage=stage, done=int(done or 0), total=int(total or 0))
    try:
        import daytrader
        fn = daytrader.run_daytrader_daily if mode == "daily" else daytrader.run_daytrader_orb
        data = fn(universe=universe, progress=progress)
        data["run_by"] = who
        save_screener_results(MODES[mode], data)
        _set(mode, status="done", finished=time.time(), stage=None)
        print(f"[DayTrader] {mode} scan saved: {len(data.get('results', []))} hits in {data.get('elapsed_s')} s")
    except Exception as e:
        print(f"[DayTrader] {mode} scan failed: {traceback.format_exc()}")
        _set(mode, status="error", error=str(e) or "scan failed", finished=time.time(), stage=None)


def start_scan(mode, universe="all", who="user", force=False):
    """-> 'started' | 'already_running' | 'recent'."""
    with _lock:
        st = _state.get(mode) or {}
        if st.get("status") == "running":
            return "already_running"
        if not force and st.get("status") == "done" and time.time() - (st.get("finished") or 0) < COOLDOWN \
                and st.get("universe") == universe:
            return "recent"
        _state[mode] = {"status": "running", "universe": universe, "started": time.time(),
                        "stage": "Starting", "done": 0, "total": 0}
    threading.Thread(target=_run, args=(mode, universe, who), daemon=True, name=f"daytrader-{mode}").start()
    return "started"


@daytrader_bp.route("/api/daytrader/start", methods=["POST"])
def daytrader_start():
    mode = _mode()
    if not mode:
        return jsonify({"error": "mode must be daily or orb"}), 400
    universe = (request.args.get("universe") or "all").lower()
    if universe not in UNIVERSES:
        return jsonify({"error": "universe must be all or nifty500"}), 400
    force = False
    try:
        import sys
        mod = sys.modules.get("app") or sys.modules.get("__main__")      # waitress: app:app; dev: python app.py
        u = mod.current_user() if hasattr(mod, "current_user") else None
        force = bool(u and u.get("is_admin"))
    except Exception:
        pass
    return jsonify({"status": start_scan(mode, universe, "admin" if force else "user", force)})


@daytrader_bp.route("/api/daytrader/status")
def daytrader_status():
    mode = _mode()
    if not mode:
        return jsonify({"error": "mode must be daily or orb"}), 400
    with _lock:
        st = dict(_state.get(mode, {"status": "idle"}))
    if st.get("started") and st.get("status") == "running":
        st["elapsed_s"] = int(time.time() - st["started"])
    return jsonify(st)


@daytrader_bp.route("/api/daytrader/results")
def daytrader_results():
    mode = _mode()
    if not mode:
        return jsonify({"error": "mode must be daily or orb"}), 400
    data, updated_at = get_screener_results(MODES[mode])
    if data is None:
        return jsonify({"empty": True})
    data["timestamp"] = updated_at
    data["empty"] = False
    return jsonify(data)


# ── automatic scans on trading days ───────────────────────────────────────

def _scheduler():
    import daytrader_core as core
    while True:
        try:
            now = core.to_ist(datetime.datetime.utcnow())
            d = now.date()
            if core.trading_day(d)[0]:
                plan = [("daily", AUTO_DAILY)] + [("orb", t) for t in AUTO_ORB]
                for mode, at in plan:
                    key = (mode, d.isoformat(), at.isoformat())
                    if key in _sched["ran"]:
                        continue
                    t0 = datetime.datetime.combine(d, at)
                    if t0 <= now < t0 + datetime.timedelta(minutes=40):
                        _sched["ran"].add(key)
                        start_scan(mode, "all", who="auto", force=True)
            _sched["ran"] = {k for k in _sched["ran"] if k[1] >= (d - datetime.timedelta(days=2)).isoformat()}
        except Exception as e:
            print(f"[DayTrader] scheduler: {e}")
        time.sleep(60)


@daytrader_bp.before_app_request
def _ensure_scheduler():
    if _sched["started"] or os.environ.get("MW_NO_POLLERS") == "1":
        return
    with _lock:
        if _sched["started"]:
            return
        _sched["started"] = True
    threading.Thread(target=_scheduler, daemon=True, name="daytrader-auto").start()
