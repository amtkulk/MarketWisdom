"""API for the Paper Trading page (/api/paper/*). Signed-in users only; admin endpoints for
fresh-start requests. Real orders are never placed anywhere."""
import sys

from flask import Blueprint, jsonify, request

import angel
import paper

paper_bp = Blueprint("paper", __name__)


def _user():
    mod = sys.modules.get("app") or sys.modules.get("__main__")
    try:
        return mod.current_user() if hasattr(mod, "current_user") else None
    except Exception:
        return None


def _need_user():
    u = _user()
    if not u or not u.get("email"):
        return None, (jsonify({"error": "Please sign in to use Paper Trading."}), 401)
    return u, None


def _need_admin():
    u, err = _need_user()
    if err:
        return None, err
    if not u.get("is_admin"):
        return None, (jsonify({"error": "Admin access required."}), 403)
    return u, None


def _wrap(fn):
    try:
        return jsonify(fn())
    except paper.PaperError as e:
        return jsonify({"error": str(e)}), 400
    except angel.AngelError as e:
        return jsonify({"error": str(e), "data_unavailable": True}), 503
    except Exception as e:
        print(f"[Paper] {request.path}: {e}")
        return jsonify({"error": "Something went wrong: %s" % str(e)[:120]}), 500


def _seg(v):
    v = (v or "EQ").upper()
    return v if v in ("EQ", "FO") else "EQ"


@paper_bp.before_app_request
def _bg():
    paper.ensure_background()


@paper_bp.route("/api/paper/status")
def p_status():
    st = angel.status()
    u = _user()
    out = {"live": st["configured"], "market_open": paper.market_open(), "capital": paper.CAPITAL}
    if u and u.get("is_admin"):
        out["admin"] = st
    return jsonify(out)


@paper_bp.route("/api/paper/account")
def p_account():
    u, err = _need_user()
    if err:
        return err
    return _wrap(lambda: paper.summary(u["email"], _seg(request.args.get("seg")), u.get("name", "")))


@paper_bp.route("/api/paper/quote")
def p_quote():
    u, err = _need_user()
    if err:
        return err
    sym = (request.args.get("symbol") or "").upper().strip()

    def go():
        q = paper.eq_quotes([sym]).get(sym)
        if not q:
            raise paper.PaperError("No price for %s. Use the NSE symbol, e.g. HINDZINC." % sym)
        return dict(q, symbol=sym)
    return _wrap(go)


@paper_bp.route("/api/paper/search")
def p_search():
    u, err = _need_user()
    if err:
        return err
    q, seg = request.args.get("q", ""), _seg(request.args.get("seg"))

    def go():
        if seg == "EQ":
            return {"items": angel.search_equity(q) if angel.configured() else []}
        und = [x for x in angel.underlyings() if x.startswith((q or "").upper())]
        return {"items": und[:15]}
    return _wrap(go)


@paper_bp.route("/api/paper/underlyings")
def p_underlyings():
    u, err = _need_user()
    if err:
        return err
    return _wrap(lambda: {"items": angel.underlyings(), "indices": [i for i in angel.INDEX_NAMES]})


@paper_bp.route("/api/paper/chain")
def p_chain():
    u, err = _need_user()
    if err:
        return err
    a = request.args
    return _wrap(lambda: paper.chain(a.get("und", "NIFTY"), a.get("expiry"), min(25, max(4, int(a.get("n", 12))))))


@paper_bp.route("/api/paper/order", methods=["POST"])
def p_order():
    u, err = _need_user()
    if err:
        return err
    b = request.get_json(silent=True) or {}
    seg = _seg(b.get("seg"))

    def go():
        if seg == "EQ":
            o, _ = paper.place_equity(u["email"], b.get("symbol"), b.get("side"), b.get("qty"), b.get("product"), u.get("name", ""))
        else:
            o, _ = paper.place_fo(u["email"], b.get("legs"), b.get("strategy"), u.get("name", ""))
        return {"order": o, "account": paper.summary(u["email"], seg, u.get("name", ""))}
    return _wrap(go)


@paper_bp.route("/api/paper/close", methods=["POST"])
def p_close():
    u, err = _need_user()
    if err:
        return err
    b = request.get_json(silent=True) or {}
    seg = _seg(b.get("seg"))

    def go():
        if seg == "EQ":
            acc = paper.load(u["email"], "EQ")
            p = acc["positions"].get(b.get("key"))
            if not p:
                raise paper.PaperError("Position not found.")
            paper.place_equity(u["email"], p["symbol"], "SELL" if p["qty"] > 0 else "BUY", abs(p["qty"]), p["product"], u.get("name", ""))
        else:
            paper.close_fo(u["email"], keys=b.get("keys"), strategy=b.get("strategy"), name=u.get("name", ""))
        return {"account": paper.summary(u["email"], seg, u.get("name", ""))}
    return _wrap(go)


@paper_bp.route("/api/paper/strategy", methods=["POST"])
def p_strategy():
    u, err = _need_user()
    if err:
        return err
    b = request.get_json(silent=True) or {}

    def go():
        legs = b.get("legs")
        if not legs:
            legs = paper.build(b.get("und", "NIFTY"), b.get("expiry"), b.get("template"), b.get("lots", 1),
                               b.get("gap", 2), b.get("width", 2))
        pv = paper.preview(legs, b.get("und"))
        pv["template"] = b.get("template")
        pv["name"] = paper.TEMPLATES.get(b.get("template"), "Custom basket")
        return pv
    return _wrap(go)


@paper_bp.route("/api/paper/refill", methods=["POST"])
def p_refill():
    u, err = _need_user()
    if err:
        return err
    seg = _seg((request.get_json(silent=True) or {}).get("seg"))
    return _wrap(lambda: {"request": paper.request_refill(u["email"], seg, u.get("name", ""))})


@paper_bp.route("/api/paper/admin/requests")
def p_admin_requests():
    u, err = _need_admin()
    if err:
        return err
    items = list(reversed(paper.requests_list()))
    return jsonify({"items": items, "pending": sum(1 for r in items if r["status"] == "pending"), "data": angel.status()})


@paper_bp.route("/api/paper/admin/decide", methods=["POST"])
def p_admin_decide():
    u, err = _need_admin()
    if err:
        return err
    b = request.get_json(silent=True) or {}
    return _wrap(lambda: {"request": paper.decide(b.get("id"), bool(b.get("approve")), u["email"])})
