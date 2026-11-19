"""
API for the Day Trader Scanner page. Separate from the other screeners: its own
endpoints, its own background-scan state and its own saved-results keys.

  POST /api/daytrader/start?mode=daily|orb    start a background scan
  GET  /api/daytrader/status?mode=daily|orb   idle / running / done / error
  GET  /api/daytrader/results?mode=daily|orb  the last saved scan
"""

import threading
import traceback

from flask import Blueprint, jsonify, request

from database import get_screener_results, save_screener_results

daytrader_bp = Blueprint("daytrader", __name__)

MODES = {"daily": "daytrader_daily", "orb": "daytrader_orb"}   # storage keys, distinct from every screener
_state = {}
_lock = threading.Lock()


def _mode():
    m = (request.args.get("mode") or "daily").lower()
    return m if m in MODES else None


def _run(mode):
    try:
        import daytrader
        data = daytrader.run_daytrader_daily() if mode == "daily" else daytrader.run_daytrader_orb()
        save_screener_results(MODES[mode], data)
        with _lock:
            _state[mode] = {"status": "done"}
        print(f"[DayTrader] {mode} scan saved: {len(data.get('results', []))} hits")
    except Exception as e:
        print(f"[DayTrader] {mode} scan failed: {traceback.format_exc()}")
        with _lock:
            _state[mode] = {"status": "error", "error": str(e) or "scan failed"}


@daytrader_bp.route("/api/daytrader/start", methods=["POST"])
def daytrader_start():
    mode = _mode()
    if not mode:
        return jsonify({"error": "mode must be daily or orb"}), 400
    with _lock:
        if _state.get(mode, {}).get("status") == "running":
            return jsonify({"status": "already_running"})
        _state[mode] = {"status": "running"}
    threading.Thread(target=_run, args=(mode,), daemon=True).start()
    return jsonify({"status": "started"})


@daytrader_bp.route("/api/daytrader/status")
def daytrader_status():
    mode = _mode()
    if not mode:
        return jsonify({"error": "mode must be daily or orb"}), 400
    with _lock:
        return jsonify(_state.get(mode, {"status": "idle"}))


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
