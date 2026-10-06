"""
Amit Multibagger Early-Signal Scanner — v2 (115-point model)
============================================================
Built from six case studies (Wheels India, Spectrum Electrical, Fermenta,
Indo Amines, Sterlite Technologies, Mukand). The common denominator:

    "Something changes that makes the market revise its estimate of the
     company's future earnings."

So every stock gets THREE scores instead of one:

  A. Business Change      /50  — is the business fundamentally changing?
  B. Future Growth        /35  — can it continue? (order inflow & visibility,
                                  structural theme, new product/capacity,
                                  large orders)
  C. Market Recognition   /30  — has the market started discovering it?
                                  (volume, breakout, RS/trend, FII/DII/MF,
                                  promoter, strategic capital)

  Total /115, plus a separate valuation gauge, plus a RISK OVERRIDE: red
  flags beat any score.

Stage (how far the market has discovered the story):
  Stage 1 · Hidden     — Market Recognition < 15   (best hunting zone)
  Stage 2 · Discovery  — Market Recognition 15-22  (STL at ₹180)
  Stage 3 · Momentum   — Market Recognition ≥ 23   (strong, don't chase)
  "Not yet"            — Business Change < 25
  "Red flag"           — any major risk flag
Each stage also shows whether Amit's exact bar is met (e.g. Hidden needs
BC ≥ 35 and FG ≥ 20). Points, not rigid filters — Fermenta had a weak
quarter but a strong future-growth thesis.

Data: price/volume (Yahoo), financials & shareholding (Screener.in), and
BSE exchange filings (order wins, press releases for capex / approvals /
launches / fund raises / negative events, analyst meets). "Order book" is
approximated by disclosed order inflow + order-book figures read from
results press releases; no free feed publishes order books.
"""

import concurrent.futures
import datetime as dt
import re
import time

import screener as S
import orders as O

MCAP_MIN_CR = 500                 # Amit's first filter
FILINGS_CACHE_KEY = "mb_filings_cache"
FILINGS_TTL = 6 * 3600
ORDER_DAYS, PRESS_DAYS, MEET_DAYS = 90, 90, 45
UNCHECKED = ["Auditor qualification", "Promoter pledge %", "Related-party transactions"]


# ══════════════════════════════════════════════════════════════
#  small helpers
# ══════════════════════════════════════════════════════════════

def _clean(seq):
    return [x for x in (seq or []) if x is not None]


def _yoy(series, back=1):
    return S._mb_yoy(series, back)


def _ttm_growth(series):
    s = series or []
    if len(s) < 8 or any(x is None for x in s[-8:]):
        return None
    a, b = sum(s[-4:]), sum(s[-8:-4])
    return (a / b - 1) * 100 if b > 0 else None


def _pct(a, b):
    return round(a / b * 100, 1) if (a is not None and b) else None


def _r(x, d=1):
    return None if x is None else round(x, d)


class _Card:
    """Collects the parts of one of the three scores."""

    def __init__(self, mx):
        self.mx, self.parts = mx, []

    def add(self, key, label, frac, w, note, na=False):
        self.parts.append({"key": key, "label": label, "max": w,
                           "pts": None if na else round(max(0.0, min(1.0, frac)) * w, 1), "note": note})

    def earned(self):
        return sum(p["pts"] for p in self.parts if p["pts"] is not None)

    def avail(self):
        return sum(p["max"] for p in self.parts if p["pts"] is not None)


def _accel_frac(series, hi, mid, two_q_bonus=True):
    """Fraction 0-1 for an accelerating growth line + a note."""
    g0, turn = _yoy(series, 1)
    g1, _ = _yoy(series, 2)
    gT = _ttm_growth(series)
    if g0 is None:
        if turn:
            return 0.8, "turned profitable vs year-ago loss", None
        return None, "n/a", None
    acc = g1 is not None and g0 > g1
    if g0 >= hi:
        f = 1.0 if (acc or (two_q_bonus and g1 is not None and g1 >= hi)) else 0.75
    elif g0 >= mid:
        f = 0.65 if acc else 0.5
    elif g0 > 0 and acc:
        f = 0.25
    else:
        f = 0.0
    if gT is not None and gT >= hi and f < 0.5:      # strong year, one soft quarter (Fermenta-type)
        f = 0.5
    note = f"YoY {g0:+.0f}%" + (f" (prev qtr {g1:+.0f}%)" if g1 is not None else "") + \
           (f", TTM {gT:+.0f}%" if gT is not None else "")
    return f, note, g0


# ══════════════════════════════════════════════════════════════
#  structural themes (from Screener's business description + industry)
# ══════════════════════════════════════════════════════════════

THEMES = [
    ("AI / data centre", r"data\s*cent(?:re|er)|hyperscal|optical\s+fib(?:re|er)|fib(?:re|er)\s+optic|optical\s+(?:connectivity|interconnect)|"
                         r"artificial\s+intelligence|\bai\b|\bgpus?\b|servers?|liquid\s+cooling|precision\s+cooling"),
    ("Defence & aerospace", r"defen[cs]e|aerospace|missile|ammunition|explosives?|radar|avionic|naval|warships?|shipbuild"),
    ("Power T&D / energy transition", r"transformers?|power\s+transmission|switchgear|smart\s+meter|renewable|solar|wind\s+(?:energy|turbine|power)|"
                                      r"green\s+hydrogen|electrolys|energy\s+storage|\bbess\b|substation|hvdc|conductors?|power\s+cables?"),
    ("EV & batteries", r"electric\s+vehicle|\bevs?\b|lithium|battery|batteries|charging\s+infra|e-?mobility"),
    ("Railways & metro", r"railways?|wagons?|locomotive|metro\s+rail|vande\s+bharat|rolling\s+stock|kavach"),
    ("Electronics & semiconductors", r"electronics\s+manufactur|\bems\b|printed\s+circuit|\bpcbs?\b|semiconductor|\bosat\b|chips?\b"),
    ("Infra & capex cycle", r"\bepc\b|infrastructure|capital\s+goods|heavy\s+engineering|water\s+treatment|irrigation|pumps?\b|valves?\b"),
    ("Pharma CDMO / specialty chem", r"\bcdmo\b|\bcrams\b|contract\s+(?:development|research|manufactur)|active\s+pharmaceutical|"
                                      r"specialty\s+chemicals?|fluoro|agrochemical\s+intermediates?"),
    ("Telecom & digital infra", r"telecom|\b5g\b|broadband|ftth|network\s+infra"),
    ("PLI / China+1 manufacturing", r"\bpli\b|production[\s-]+linked|china\s*\+\s*1"),
]
_THEME_RX = [(name, re.compile(rx, re.I)) for name, rx in THEMES]


def detect_themes(raw, name=""):
    txt = " ".join(x for x in (name, raw.get("name") or "", raw.get("industry") or "", raw.get("about") or "") if x)
    return [n for n, rx in _THEME_RX if rx.search(txt)]


# ══════════════════════════════════════════════════════════════
#  filings → company facts
# ══════════════════════════════════════════════════════════════

_NEG_TYPES = [
    ("EHS incident", r"\bfire\b|explosion|blast|accident|gas\s+leak|fatal|casualt|death"),
    ("Regulatory action", r"warning\s+letter|import\s+alert|\boai\b|\bban\b|suspension\s+of\s+(?:licen|operations|production)|"
                          r"cancell?ation\s+of\s+licen|show[\s-]*cause|penalty"),
    ("Fraud / investigation", r"fraud|forensic|\braid\b|search\s+(?:and\s+seizure|operation)|enforcement\s+directorate|\bcbi\b"),
    ("Auditor resignation", r"resignation\s+of\s+(?:the\s+)?(?:statutory\s+)?auditor"),
    ("Default / insolvency", r"default(?:ed)?\s+(?:on|in)|insolvency|\bnclt\b.*(?:admit|petition)|\bcirp\b"),
]
_NEG_RX = [(n, re.compile(rx, re.I)) for n, rx in _NEG_TYPES]
_RESULTS_RX = re.compile(r"result|order\s*book|business\s+update|operational\s+update|investor\s+presentation|"
                         r"earnings|performance|q[1-4]\s*fy", re.I)
_OB_RX = re.compile(r"order\s*book|order\s*backlog|unexecuted\s+orders?|outstanding\s+orders?", re.I)


def extract_order_book(text):
    """First ₹ amount after an 'order book' phrase → {'cr', 'snippet'} or None."""
    best = None
    for m in _OB_RX.finditer(text or ""):
        window = text[m.start(): m.start() + 200]
        cut = None
        for rx in (O._MONEY_A, O._MONEY_B):
            mm = rx.search(window)
            if mm and (cut is None or mm.end() < cut):
                cut = mm.end()
        if cut is None:
            continue
        v = O.parse_order_value("order " + window[:cut], require_kw=False)
        if v and v["cr"] >= 1 and (best is None or v["cr"] > best["cr"]):
            best = {"cr": v["cr"], "snippet": " ".join(window[:cut + 20].split())[:160]}
    return best


def normalize_filing(r, feed):
    """BSE row → compact filing with a first read (headline only)."""
    it = O.normalize(r)
    if not it:
        return None
    it["feed"] = feed
    h = it["headline"] or ""
    if feed == "orders":
        cls, tags = O.classify_order(h)
        it.update({"cls": cls, "tags": tags})
        v = O.parse_order_value(h, require_kw=False)
    elif feed == "press":
        it.update({"cls": O.classify_press(h), "tags": []})
        v = O.parse_order_value(h, require_kw=False)
        neg = next((n for n, rx in _NEG_RX if rx.search(h)), None)
        if neg:
            it["cls"], it["neg_type"] = "negative", neg
    else:
        it.update({"cls": "meet", "tags": []})
        v = None
    if v:
        it["value_cr"] = v["cr"]
    for k in ("subject", "size_kb"):
        it.pop(k, None)
    return it


def company_facts(filings, ttm_sales, mcap, today):
    """Aggregate a company's recent filings into the facts the scores need."""
    d = lambda days: (today - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    biz = [f for f in filings if f["feed"] == "orders" and f["cls"] == "business" and f["date"] >= d(ORDER_DAYS)]
    reg = [f for f in filings if f["feed"] == "orders" and f["cls"] == "regulatory" and f["date"] >= d(120)]
    press = [f for f in filings if f["feed"] == "press" and f["date"] >= d(120)]
    meets = [f for f in filings if f["feed"] == "meets" and f["date"] >= d(MEET_DAYS)]
    vals = [f["value_cr"] for f in biz if f.get("value_cr")]
    inflow = round(sum(vals), 2) if vals else None
    biggest = max(vals) if vals else None
    tags = {t for f in biz for t in f.get("tags", [])}
    kinds = {}
    for f in press:
        kinds.setdefault(f["cls"], []).append(f)
    negs = [f for f in press if f["cls"] == "negative"]
    fund = [f for f in filings if f["feed"] == "press" and f["cls"] == "fundraise" and f["date"] >= d(180)]
    catalysts = sorted([f for f in biz] + [f for f in press if f["cls"] in ("capex", "approval", "product", "order",
                                                                              "partnership", "acquisition", "fundraise", "negative")]
                       + meets[:2], key=lambda f: f["dt"], reverse=True)[:6]
    return {
        "n_orders": len(biz), "order_inflow_cr": inflow, "largest_order_cr": biggest,
        "order_inflow_pct_sales": _pct(inflow, ttm_sales) if inflow else None,
        "order_inflow_pct_mcap": _pct(inflow, mcap) if inflow else None,
        "largest_order_pct_sales": _pct(biggest, ttm_sales) if biggest else None,
        "govt_or_export": bool(tags & {"Govt/PSU", "Export"}),
        "press_kinds": {k: len(v) for k, v in kinds.items()},
        "negatives": [{"type": f.get("neg_type") or "Negative event", "date": f["date"], "headline": f["headline"]} for f in negs],
        "reg_orders": [{"date": f["date"], "headline": f["headline"], "value_cr": f.get("value_cr")} for f in reg],
        "n_meets": len(meets), "fundraise": len(fund) > 0,
        "results_press": [f for f in sorted(press, key=lambda f: f["dt"], reverse=True) if _RESULTS_RX.search(f["headline"] or "")],
        "catalysts": [{"date": f["date"], "feed": f["feed"], "cls": f["cls"], "headline": f["headline"],
                       "value_cr": f.get("value_cr"), "attachment": f.get("attachment")} for f in catalysts],
    }


# ══════════════════════════════════════════════════════════════
#  THE 115-POINT SCORE
# ══════════════════════════════════════════════════════════════

def score_v2(sig, raw, facts, order_book=None, name=""):
    """sig = screener._mb_price_signals(..., strict=False); raw = Screener page
    data ({} if unavailable); facts = company_facts(...). Returns the full read."""
    raw = raw or {}
    facts = facts or company_facts([], None, None, dt.date.today())
    qs, qe = raw.get("q_sales"), raw.get("q_ebitda")
    qp = raw.get("q_pat") or raw.get("q_eps")
    ttm_sales = round(sum(_clean(qs)[-4:]), 1) if len(_clean(qs)) >= 4 else None
    mcap = raw.get("mcap")
    m = {}                                                       # metrics shown on the card

    # ── A. BUSINESS CHANGE /50 ────────────────────────────────
    A = _Card(50)
    f, n, m["rev_yoy"] = _accel_frac(qs, 20, 15)
    A.add("rev", "Revenue acceleration", f or 0, 8, n, na=f is None)
    f, n, m["ebitda_yoy"] = _accel_frac(qe, 25, 20)
    A.add("ebitda", "EBITDA acceleration", f or 0, 8, n, na=f is None)
    f, n, m["pat_yoy"] = _accel_frac(qp, 20, 15)
    if f is not None and m["pat_yoy"] is not None and m["rev_yoy"] is not None and m["pat_yoy"] > m["rev_yoy"] + 5 and m["pat_yoy"] >= 15:
        f, n = min(1.0, f + 0.2), n + " · operating leverage"
    A.add("pat", "PAT / EPS acceleration", f or 0, 8, n, na=f is None)

    # margin expansion: TTM OPM vs previous TTM, and latest quarter vs year-ago quarter
    opm_d = None
    if len(_clean(qs)) >= 8 and len(_clean(qe)) >= 8 and all(x is not None for x in (qs[-8:] + qe[-8:])):
        s1, s0 = sum(qs[-4:]), sum(qs[-8:-4])
        if s1 > 0 and s0 > 0:
            opm_d = sum(qe[-4:]) / s1 * 100 - sum(qe[-8:-4]) / s0 * 100
    qo = raw.get("q_opm") or []
    q_d = (qo[-1] - qo[-5]) if (len(qo) >= 5 and qo[-1] is not None and qo[-5] is not None) else None
    best_d = max([x for x in (opm_d, q_d) if x is not None], default=None)
    m["opm_delta"] = _r(best_d)
    if best_d is None:
        A.add("margin", "Margin expansion", 0, 6, "n/a", na=True)
    else:
        A.add("margin", "Margin expansion", 1.0 if best_d >= 2.5 else 0.7 if best_d >= 1 else 0.4 if best_d > 0 else 0, 6,
              (f"TTM OPM {opm_d:+.1f}pp" if opm_d is not None else "") + (f"; latest qtr {q_d:+.1f}pp vs yr-ago" if q_d is not None else ""))

    rh = _clean(raw.get("roce_hist"))
    if len(rh) >= 2:
        lvl, dlt = rh[-1], rh[-1] - rh[-2]
        f = (0.5 if lvl >= 20 else 0.35 if lvl >= 15 else 0.25 if lvl >= 12 else 0.05) + \
            (0.5 if dlt >= 3 else 0.35 if dlt >= 1 else 0.2 if dlt > 0 else 0)
        A.add("roce", "ROCE level & improvement", f, 6, f"{rh[-2]:.0f}% → {rh[-1]:.0f}%")
        m["roce"] = rh[-1]
    else:
        A.add("roce", "ROCE level & improvement", 0, 6, "n/a", na=True)

    oc, yp = _clean(raw.get("ocf")), _clean(raw.get("y_pat"))
    if len(oc) >= 2:
        f = (0.4 if oc[-1] > 0 else 0) + (0.3 if oc[-1] > 0 and oc[-1] >= oc[-2] else 0)
        conv = (oc[-1] / yp[-1]) if (yp and yp[-1] and yp[-1] > 0) else None
        if conv is not None and conv >= 0.7:
            f += 0.3
        m["ocf_to_pat"] = _r(conv, 2)
        A.add("ocf", "Operating cash flow quality", f, 7,
              f"OCF ₹{oc[-2]:,.0f} → ₹{oc[-1]:,.0f} Cr" + (f", {conv:.1f}× PAT" if conv is not None else ""))
    else:
        A.add("ocf", "Operating cash flow quality", 0, 7, "n/a", na=True)

    br, eq, rs_ = _clean(raw.get("borrowings")), _clean(raw.get("equity_capital")), _clean(raw.get("reserves"))
    de = None
    if br and eq and rs_ and (eq[-1] + rs_[-1]) > 0:
        de = br[-1] / (eq[-1] + rs_[-1])
    m["debt_equity"] = _r(de, 2)
    if de is not None:
        f = 0.5 if de < 0.3 else 0.35 if de < 0.6 else 0.2 if de < 1.0 else 0
        if len(br) >= 2 and (br[-1] <= 1 or br[-1] <= br[-2] * 0.9):
            f += 0.3
        dd = _clean(raw.get("debtor_days"))
        if len(dd) >= 2 and dd[-1] <= dd[-2] * 1.25:
            f += 0.2
        A.add("debt", "Debt & balance sheet", f, 7, f"Debt/Equity {de:.2f}" + (", debt falling" if len(br) >= 2 and br[-1] < br[-2] else ""))
    else:
        A.add("debt", "Debt & balance sheet", 0, 7, "n/a", na=True)
    bc = A.earned()

    # ── B. FUTURE GROWTH /35 ──────────────────────────────────
    # Order inflow 8 · Book-to-bill / order-to-mcap 5 · Large orders 4 ·
    # Structural theme 8 · New product / approval 5 · Capacity expansion 5
    B = _Card(35)
    order_driven = facts["n_orders"] > 0 or order_book is not None
    ip = facts["order_inflow_pct_sales"]
    if facts["n_orders"]:
        if ip is not None:
            f = 1.0 if ip >= 50 else 0.8 if ip >= 25 else 0.6 if ip >= 10 else 0.4 if ip >= 5 else 0.2
            note = f"₹{facts['order_inflow_cr']:,.0f} Cr won in {ORDER_DAYS}d = {ip:.0f}% of annual sales"
        else:
            f = 0.5 if facts["n_orders"] >= 3 else 0.3
            note = f"{facts['n_orders']} order wins in {ORDER_DAYS}d (values not disclosed)"
        B.add("orders", "Order inflow vs revenue", f, 8, note)
    elif order_book is not None:
        B.add("orders", "Order inflow vs revenue", 0.3, 8, "order book disclosed, no new order filings")
    else:
        B.add("orders", "Order inflow vs revenue", 0, 8, "not an order-driven business (no order filings)", na=True)

    btb = (order_book["cr"] / ttm_sales) if (order_book and ttm_sales) else None
    obm = (order_book["cr"] / mcap * 100) if (order_book and mcap) else None
    m.update({"order_book_cr": order_book["cr"] if order_book else None, "book_to_bill": _r(btb, 2),
              "order_book_pct_mcap": _r(obm)})
    vis_frac, vis_note = None, ""
    if btb is not None:
        vis_frac = 1.0 if btb >= 1.5 else 0.6 if btb >= 1.0 else 0.3
        vis_note = f"Order book ₹{order_book['cr']:,.0f} Cr = {btb:.1f}× annual sales" + (f", {obm:.0f}% of mkt cap" if obm else "")
    imc = facts["order_inflow_pct_mcap"]
    if imc is not None:
        f2 = 1.0 if imc >= 25 else 0.6 if imc >= 10 else 0.3 if imc >= 3 else 0.1
        if vis_frac is None or f2 > vis_frac:
            vis_frac = f2
        vis_note = (vis_note + "; " if vis_note else "") + f"orders won = {imc:.0f}% of market cap"
    if vis_frac is None:
        B.add("visibility", "Book-to-bill / order-to-market-cap", 0, 5,
              "n/a" if not order_driven else "no order values disclosed", na=not order_driven)
    else:
        B.add("visibility", "Book-to-bill / order-to-market-cap", vis_frac, 5, vis_note)

    lp = facts["largest_order_pct_sales"]
    if facts["n_orders"]:
        f = 1.0 if (lp is not None and lp >= 5) else 0.6 if facts["govt_or_export"] else 0.4 if facts["n_orders"] >= 3 else 0.1
        B.add("large", "Large customers / orders", f, 4,
              (f"largest order {lp:.0f}% of sales" if lp is not None else f"{facts['n_orders']} orders") +
              (" · Govt/PSU or export" if facts["govt_or_export"] else ""))
    else:
        B.add("large", "Large customers / orders", 0, 4, "n/a", na=True)

    themes = detect_themes(raw, name)
    m["themes"] = themes
    pk = facts["press_kinds"]
    theme_catalyst = facts["n_orders"] > 0 or any(pk.get(k) for k in ("capex", "approval", "product", "order"))
    if themes:
        B.add("theme", "Structural industry theme", 1.0 if theme_catalyst else 0.6, 8,
              ", ".join(themes[:2]) + (" + live catalyst in filings" if theme_catalyst else ""))
    else:
        B.add("theme", "Structural industry theme", 0, 8, "no structural theme detected" if raw.get("about") else "business description unavailable")

    prod = [k for k in ("approval", "product", "partnership", "acquisition") if pk.get(k)]
    pf = 1.0 if "approval" in prod else 0.8 if "product" in prod else 0.5 if prod else 0
    B.add("product", "New product / approval", pf, 5,
          ", ".join(O.PRESS_LABEL.get(x, x) for x in prod) if prod else "none filed in last 120 days")
    B.add("capacity", "Capacity expansion", 1.0 if pk.get("capex") else 0, 5,
          f"{pk['capex']} capex / expansion filing(s)" if pk.get("capex") else "none filed in last 120 days")
    # Order items only apply to order-driven businesses. For the rest (consumer,
    # pharma, chemicals…) future growth is judged on theme + product + capacity.
    missing = sum(p["max"] for p in B.parts if p["pts"] is None)
    fg = B.earned() * 35 / B.avail() if B.avail() else 0
    fg_scaled = missing > 0

    # ── C. MARKET RECOGNITION /30 ─────────────────────────────
    C = _Card(30)
    vr = sig.get("vol_ratio") if sig else None
    if vr is None:
        C.add("volume", "Volume vs 20-day average", 0, 5, "n/a", na=True)
    else:
        C.add("volume", "Volume vs 20-day average", 1.0 if vr >= 3 else 0.8 if vr >= 2 else 0.5 if vr >= 1.5 else 0.2 if vr >= 1.2 else 0, 5,
              f"{vr:.1f}× average")
    st = sig.get("breakout_state") if sig else None
    if st is None:
        C.add("breakout", "Base breakout", 0, 5, "n/a", na=True)
    else:
        C.add("breakout", "Base breakout", {"breakout": 1.0, "at_resistance": 0.8, "coiling": 0.5, "extended_breakout": 0.4}.get(st, 0), 5,
              (sig.get("notes") or {}).get("breakout", st))
    if sig and sig.get("dist_52wh") is not None:
        f = (0.2 if sig.get("above_50dma") else 0) + (0.2 if sig.get("above_200dma") else 0) + \
            (0.3 if (sig.get("rs_6m") or -1) > 0 else 0) + (0.3 if sig["dist_52wh"] <= 15 else 0)
        C.add("trend", "Relative strength & trend", f, 5,
              (f"RS {sig['rs_6m']:+.0f}pp vs Nifty, " if sig.get("rs_6m") is not None else "") + f"{sig['dist_52wh']:.0f}% below 52wH")
    else:
        C.add("trend", "Relative strength & trend", 0, 5, "n/a", na=True)
    fi, di, pr = _clean(raw.get("sh_fii")), _clean(raw.get("sh_dii")), _clean(raw.get("sh_prom"))
    if len(fi) >= 3 and len(di) >= 3:
        dlt = (fi[-1] + di[-1]) - (fi[-3] + di[-3])
        f = 1.0 if dlt >= 2 else 0.7 if dlt >= 1 else 0.4 if dlt > 0.3 else 0.2 if dlt > 0 else 0
        if facts["n_meets"] >= 2:
            f = min(1.0, f + 0.2)
        C.add("inst", "FII / DII / MF accumulation", f, 5, f"FII+DII {dlt:+.2f}pp over 2 qtrs ({fi[-1] + di[-1]:.1f}% now)"
              + (f"; {facts['n_meets']} investor meets" if facts["n_meets"] else ""))
        m["inst_now"] = _r(fi[-1] + di[-1])
    else:
        C.add("inst", "FII / DII / MF accumulation", 0, 5, "n/a", na=True)
    eqc0 = _clean(raw.get("equity_capital"))
    dil_ratio = (eqc0[-2] / eqc0[-1]) if (len(eqc0) >= 2 and eqc0[-1] > eqc0[-2] * 1.02) else None

    def promoter_change(back):
        """Promoter change over `back` quarters, ignoring the mechanical drop from new shares."""
        if len(pr) <= back:
            return None
        d_ = pr[-1] - pr[-1 - back]
        if dil_ratio and d_ < 0 and pr[-1] >= pr[-1 - back] * dil_ratio - 0.5:
            return 0.0                                     # diluted by an issue, not selling
        return d_
    if len(pr) >= 3:
        dp = promoter_change(2)
        f = 1.0 if dp >= 0.5 else 0.7 if dp > 0.1 else (0.6 if pr[-1] >= 50 else 0.4 if pr[-1] >= 35 else 0.2) if dp >= -0.5 else 0
        C.add("promoter", "Promoter activity", f, 5, f"{pr[-1]:.1f}% ({dp:+.2f}pp over 2 qtrs)")
        m["promoter"] = pr[-1]
    else:
        C.add("promoter", "Promoter activity", 0, 5, "n/a", na=True)
    eqc, rsv = _clean(raw.get("equity_capital")), _clean(raw.get("reserves"))
    new_money = len(eqc) >= 2 and eqc[-1] > eqc[-2] * 1.02 and (len(rsv) < 2 or rsv[-1] >= rsv[-2])
    # new money (not a bonus issue: a bonus moves reserves into equity capital, so reserves fall)
    dil_years = sum(1 for i in range(1, min(4, len(eqc)))
                    if eqc[-i] > eqc[-i - 1] * 1.02 and (len(rsv) <= i or rsv[-i] >= rsv[-i - 1]))
    inst_up = len(fi) >= 3 and len(di) >= 3 and (fi[-1] + di[-1]) > (fi[-3] + di[-3])
    if facts["fundraise"] or new_money:
        f = 1.0 if inst_up else 0.5
        if dil_years >= 2:
            f = 0.2
        C.add("capital", "Strategic capital (QIP / preferential)", f, 5,
              ("QIP / preferential / warrants filed" if facts["fundraise"] else "new equity raised") +
              (" with institutions buying" if inst_up else "") + (" · but frequent dilution" if dil_years >= 2 else ""))
    else:
        C.add("capital", "Strategic capital (QIP / preferential)", 0, 5, "no recent raise")
    mr = C.earned()

    # ── VALUATION (gauge, not in the 115) ─────────────────────
    pe = raw.get("pe")
    gs = [g for g in (_ttm_growth(qp), m["pat_yoy"]) if g is not None]
    g_exp = min(100.0, sum(gs) / len(gs)) if gs else None
    peg = (pe / g_exp) if (pe and pe > 0 and g_exp and g_exp > 0) else None
    if peg is None:
        val = {"label": "n/a", "peg": None, "pe": pe, "note": "loss-making or no growth" if pe else "P/E unavailable"}
    else:
        lab = "Cheap" if peg <= 1 else "Fair" if peg <= 1.8 else "Rich" if peg <= 3 else "Expensive"
        val = {"label": lab, "peg": round(peg, 2), "pe": pe, "note": f"P/E {pe:.0f} ÷ growth {g_exp:.0f}%"}
    if facts["order_inflow_pct_mcap"] is not None:
        val["order_inflow_pct_mcap"] = facts["order_inflow_pct_mcap"]

    # ── RISK OVERRIDE ─────────────────────────────────────────
    major, caution = [], []
    for n_ in facts["negatives"]:
        major.append(f"{n_['type']}: {n_['headline'][:90]} ({n_['date']})")
    for r_ in facts["reg_orders"]:
        if r_.get("value_cr") and mcap:
            share = r_["value_cr"] / mcap * 100
            if share >= 2:
                major.append(f"Tax / regulatory demand ₹{r_['value_cr']:,.0f} Cr = {share:.1f}% of market cap ({r_['date']})")
            elif share >= 0.5:
                caution.append(f"Tax / regulatory demand ₹{r_['value_cr']:,.0f} Cr ({r_['date']})")
    if len(pr) >= 5:
        drop = -(promoter_change(4) or 0)
        if drop >= 5:
            major.append(f"Promoter holding fell {drop:.1f}pp in a year (selling or pledge invocation?)")
        elif drop >= 2:
            caution.append(f"Promoter holding down {drop:.1f}pp in a year")
    dd = _clean(raw.get("debtor_days"))
    if len(dd) >= 2 and dd[-1] >= 90 and dd[-1] >= dd[-2] * 1.3:
        caution.append(f"Receivables stretching: debtor days {dd[-2]:.0f} → {dd[-1]:.0f}")
    idd = _clean(raw.get("inventory_days"))
    if len(idd) >= 2 and idd[-1] >= 120 and idd[-1] >= idd[-2] * 1.3:
        caution.append(f"Inventory piling up: inventory days {idd[-2]:.0f} → {idd[-1]:.0f}")
    if len(br) >= 2 and len(yp) >= 2 and br[-2] > 0 and yp[-2] > 0 and de is not None and de >= 0.5:
        dg, pg = (br[-1] / br[-2] - 1) * 100, (yp[-1] / yp[-2] - 1) * 100
        if dg > pg + 25:
            caution.append(f"Debt growing faster than profit (debt {dg:+.0f}%, PAT {pg:+.0f}%)")
    if de is not None and de >= 1.0:
        caution.append(f"Debt/Equity {de:.2f} (above 1.0)")
    if dil_years >= 2:
        caution.append(f"Frequent equity dilution ({dil_years} of last 3 years)")
    qoi, qpbt = raw.get("q_other_income") or [], raw.get("q_pbt") or []
    if len(_clean(qoi)) >= 4 and len(_clean(qpbt)) >= 4:
        oi, pbt = sum(_clean(qoi)[-4:]), sum(_clean(qpbt)[-4:])
        if pbt > 0 and oi / pbt > 0.4:
            caution.append(f"Profit quality: other income is {oi / pbt * 100:.0f}% of pre-tax profit")
    qex = _clean(raw.get("q_exceptional"))
    if len(qex) >= 1 and len(_clean(qpbt)) >= 1 and _clean(qpbt)[-1] > 0 and abs(qex[-1]) / _clean(qpbt)[-1] > 0.3:
        caution.append("One-off (exceptional) item is a big part of the latest quarter's profit")
    if len(oc) >= 2 and oc[-1] < 0 and oc[-2] < 0 and yp and yp[-1] > 0:
        caution.append("Operating cash flow negative two years running despite profits")
    if peg is not None and peg > 3:
        caution.append(f"Priced for perfection (PEG {peg:.1f})")
    if len(caution) >= 4:                                   # Amit's "automatic red flags" when they stack up
        major.append(f"Multiple warning signs ({len(caution)}): " + "; ".join(c.split(":")[0] for c in caution[:4]))
    penalty = min(9, 3 * len(caution))

    # ── STAGE ─────────────────────────────────────────────────
    total_raw = bc + fg + mr
    total = max(0.0, total_raw - penalty)
    if major:
        stage = "redflag"
    elif not raw:
        stage = "nodata"                                   # can't judge the business without financials
    elif bc < 25:
        stage = "weak"
    elif mr < 15:
        stage = "hidden"
    elif mr <= 22:
        stage = "discovery"
    else:
        stage = "momentum"
    bar = {"hidden": bc >= 35 and fg >= 20 and mr < 15,
           "discovery": bc >= 35 and fg >= 25 and 15 <= mr <= 22,
           "momentum": bc >= 40 and fg >= 30 and mr >= 23}.get(stage, False)
    conviction = "A" if (bc >= 35 and fg >= 25) else "B" if (bc >= 30 and fg >= 18) else "C"
    return {
        "bc": round(bc, 1), "fg": round(fg, 1), "mr": round(mr, 1),
        "total": round(total, 1), "total_raw": round(total_raw, 1), "penalty": penalty,
        "parts": {"bc": A.parts, "fg": B.parts, "mr": C.parts}, "fg_scaled": fg_scaled,
        "valuation": val, "risk": {"major": major, "caution": caution, "unchecked": UNCHECKED},
        "stage": stage, "meets_bar": bar, "conviction": conviction,
        "metrics": m, "ttm_sales": ttm_sales, "mcap": mcap,
        "order_inflow_cr": facts["order_inflow_cr"], "order_inflow_pct_sales": facts["order_inflow_pct_sales"],
        "order_inflow_pct_mcap": facts["order_inflow_pct_mcap"], "n_orders": facts["n_orders"],
        "catalysts": facts["catalysts"],
        "fund_ok": bool(raw),
    }


STAGE_LABEL = {"hidden": "🟢 Stage 1 · Hidden", "discovery": "🟢 Stage 2 · Discovery", "momentum": "🟡 Stage 3 · Momentum",
               "weak": "⚪ Not yet · business not changing", "redflag": "🚨 Red flag",
               "nodata": "❔ Not enough data"}
STAGE_RANK = {"discovery": 4, "hidden": 4, "momentum": 2, "weak": 1, "nodata": 0.5, "redflag": 0}


# ══════════════════════════════════════════════════════════════
#  BSE filings for the whole market (cached 6 h) and per company
# ══════════════════════════════════════════════════════════════

def _db_get(key):
    try:
        from database import get_screener_results
        return get_screener_results(key)[0]
    except Exception:
        return None


def _db_put(key, data):
    try:
        from database import save_screener_results
        save_screener_results(key, data)
    except Exception as e:
        print(f"[MB2] cache save failed: {e}")


def fetch_market_filings(force=False):
    """Orders (90d), press releases (90d) and analyst meets (45d) for all
    BSE equity companies. Returns (filings, info)."""
    cached = _db_get(FILINGS_CACHE_KEY)
    if not force and isinstance(cached, dict) and time.time() - (cached.get("at") or 0) < FILINGS_TTL:
        return cached.get("filings") or [], {"source": "cache", "counts": cached.get("counts"), "at": cached.get("at")}
    today = O._now_ist().date()
    smap = dict((cached or {}).get("subcat_map") or {})
    out, counts, errors = [], {}, []
    client = O.BseClient(allow_browser=True)
    try:
        for feed, days, pages in (("orders", ORDER_DAYS, 40), ("press", PRESS_DAYS, 90), ("meets", MEET_DAYS, 25)):
            try:
                rows, sc = O.fetch_feed(client, feed, today - dt.timedelta(days=days), today, pages, smap)
                if sc:
                    smap[feed] = sc
                got = [f for f in (normalize_filing(r, feed) for r in rows) if f]
                out += got
                counts[feed] = len(got)
            except Exception as e:
                errors.append(f"{feed}: {e}")
                counts[feed] = 0
    finally:
        client.close()
    if out:
        _db_put(FILINGS_CACHE_KEY, {"at": time.time(), "filings": out, "subcat_map": smap, "counts": counts})
    elif isinstance(cached, dict) and cached.get("filings"):
        return cached["filings"], {"source": "stale cache (BSE unreachable)", "counts": cached.get("counts"), "errors": errors}
    return out, {"source": f"live ({client.mode})", "counts": counts, "errors": errors}


def fetch_company_filings(code, days=120):
    """One company's filings straight from BSE (3 small calls)."""
    if not code:
        return []
    today = O._now_ist().date()
    cached = _db_get(FILINGS_CACHE_KEY) or {}
    smap = cached.get("subcat_map") or {}
    out = []
    client = O.BseClient(allow_browser=False)
    try:
        for feed in ("orders", "press", "meets"):
            sc = smap.get(feed) or O.FEEDS[feed]["subcats"][0]
            try:
                rows = O.fetch_bse(client, O.CATEGORY, sc, today - dt.timedelta(days=days), today, 3, scrip=code)
                out += [f for f in (normalize_filing(r, feed) for r in rows) if f]
            except Exception as e:
                print(f"[MB2] company filings {code}/{feed}: {e}")
    finally:
        client.close()
    if not out:                                            # fall back to the market cache
        out = [f for f in (cached.get("filings") or []) if f.get("code") == str(code)]
    return out


def order_book_from_filings(facts, max_pdfs=1):
    """Read the latest results / business-update press release PDF for an order-book figure."""
    for f in (facts.get("results_press") or [])[:max_pdfs]:
        if not f.get("attachment"):
            continue
        text, _base = O.fetch_pdf_text(f["attachment"], max_pages=6, max_bytes=O.PDF_MAX_BYTES)
        ob = extract_order_book(text) if text else None
        if ob:
            ob["date"] = f["date"]
            return ob
    for f in facts.get("results_press") or []:            # sometimes the headline says it
        ob = extract_order_book(f.get("headline") or "")
        if ob:
            ob["date"] = f["date"]
            return ob
    return None


# ══════════════════════════════════════════════════════════════
#  result row + verdict
# ══════════════════════════════════════════════════════════════

def build_row(sym, sig, raw, sc, name=None, found_via=None, order_book=None):
    sig = sig or {}
    return {
        "ticker": sym, "name": raw.get("name") if raw else name, "bse_code": (raw or {}).get("bse_code"),
        "price": sig.get("price"),
        "bc": sc["bc"], "fg": sc["fg"], "mr": sc["mr"], "total": sc["total"], "total_raw": sc["total_raw"],
        "score": sc["total"], "penalty": sc["penalty"],
        "stage": sc["stage"], "stage_label": STAGE_LABEL[sc["stage"]], "meets_bar": sc["meets_bar"],
        "conviction": sc["conviction"], "valuation": sc["valuation"], "risk": sc["risk"],
        "parts": sc["parts"], "fg_scaled": sc["fg_scaled"], "metrics": sc["metrics"],
        "order_inflow_cr": sc["order_inflow_cr"], "order_inflow_pct_sales": sc["order_inflow_pct_sales"],
        "order_inflow_pct_mcap": sc["order_inflow_pct_mcap"], "n_orders": sc["n_orders"],
        "order_book": order_book, "catalysts": sc["catalysts"], "mcap": sc["mcap"], "ttm_sales": sc["ttm_sales"],
        "fund_ok": sc["fund_ok"], "found_via": sorted(found_via or []),
        "breakout_state": sig.get("breakout_state"), "base_hi": sig.get("base_hi"), "ext_pct": sig.get("ext_pct"),
        "ran_pct": sig.get("ran_pct"), "vol_ratio": sig.get("vol_ratio"), "rs_6m": sig.get("rs_6m"),
        "dist_52wh": sig.get("dist_52wh"), "turnover_cr": sig.get("turnover_cr"),
    }


def _gaps(sc, n=3):
    allp = [p for grp in ("bc", "fg", "mr") for p in sc["parts"][grp] if p["pts"] is not None and p["pts"] < p["max"]]
    return [p["label"] for p in sorted(allp, key=lambda p: p["max"] - p["pts"], reverse=True)[:n]]


def verdict(sc):
    st, bar = sc["stage"], sc["meets_bar"]
    bc, fg, mr = sc["bc"], sc["fg"], sc["mr"]
    gaps = ", ".join(_gaps(sc))
    nums = f"Business Change {bc:.0f}/50 · Future Growth {fg:.0f}/35 · Market Recognition {mr:.0f}/30."
    if st == "redflag":
        return {"head": "🚨 Red flag — resolve before considering",
                "detail": sc["risk"]["major"][0] + ". A high score doesn't override this. " + nums}
    if st == "nodata":
        return {"head": "❔ Not enough data to judge the business",
                "detail": "Couldn't read the company's financials from Screener.in, so only the chart and filings were "
                          "checked. Try again in a few minutes."}
    if st == "weak":
        return {"head": "⚪ Not yet — the business isn't changing enough",
                "detail": nums + (f" Biggest gaps: {gaps}." if gaps else "")}
    if st == "hidden":
        return {"head": "🟢 Stage 1 · Hidden" + (" — best hunting zone" if bar else " (below your bar)"),
                "detail": nums + (" The business is changing and future visibility is building, but the market hasn't "
                                  "noticed yet. Check the catalysts by hand." if bar else
                                  f" Market hasn't noticed, but your Stage-1 bar needs BC ≥ 35 and FG ≥ 20. Gaps: {gaps}.")}
    if st == "discovery":
        return {"head": "🟢 Stage 2 · Discovery" + (" — the market is starting to notice" if bar else " (below your bar)"),
                "detail": nums + (" This is where STL was at ₹180: recognition beginning, re-rating not done." if bar else
                                  f" Your Stage-2 bar needs BC ≥ 35 and FG ≥ 25. Gaps: {gaps}.")}
    return {"head": "🟡 Stage 3 · Momentum — strong, but already discovered",
            "detail": nums + " Don't chase blindly; a fresh base or pullback is the better entry."}


# ══════════════════════════════════════════════════════════════
#  MARKET-WIDE SCAN
# ══════════════════════════════════════════════════════════════

def run_multibagger_v2():
    start = time.time()
    today = O._now_ist().date()
    try:
        u1 = S.get_nifty1000_tickers()
    except Exception:
        u1 = []
    try:
        u2 = S.get_small_mid_cap_tickers()
    except Exception:
        u2 = []
    tickers = list(dict.fromkeys(list(u1) + list(u2)))
    print(f"[MB2] universe {len(tickers)}")

    # 1) prices for everyone + filings for everyone, in parallel
    price_data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        f_fil = ex.submit(fetch_market_filings)
        futs = [ex.submit(S._bulk_download_ohlc, tickers[i:i + 100], "1y") for i in range(0, len(tickers), 100)]
        futs.append(ex.submit(S._bulk_download_ohlc, ["^NSEI"], "1y"))
        for f in concurrent.futures.as_completed(futs):
            try:
                price_data.update(f.result() or {})
            except Exception:
                pass
        try:
            filings, finfo = f_fil.result()
        except Exception as e:
            filings, finfo = [], {"source": f"failed: {e}", "counts": {}}
    nifty = [r[1] for r in price_data.pop("^NSEI", [])]

    sigs, excluded = {}, {"penny": 0, "illiquid": 0}
    for t, rows in price_data.items():
        sg = S._mb_price_signals(rows, nifty, strict=False)
        if not sg:
            continue
        if sg.get("gate") in ("penny", "illiquid"):
            excluded[sg["gate"]] += 1
            continue
        sigs[t] = sg

    by_code, slug_code = {}, {}
    for f in filings:
        by_code.setdefault(f["code"], []).append(f)
        if f.get("bse_slug"):
            slug_code.setdefault(f["bse_slug"], f["code"])

    # 2) candidates: chart triggers + filing catalysts + quiet quality (Stage-1 hunting)
    reasons = {}
    chart = sorted([t for t, g in sigs.items() if g["vol_ratio"] >= 1.5 or g["breakout_state"] in ("breakout", "at_resistance")],
                   key=lambda t: sigs[t]["price_pts"], reverse=True)[:80]
    for t in chart:
        reasons.setdefault(t, set()).add("chart")
    cat_pts = {}
    for slug, code in slug_code.items():
        t = slug + ".NS"
        if t not in sigs:
            continue
        p = 0
        for f in by_code.get(code, []):
            if f["feed"] == "orders" and f["cls"] == "business":
                p += 2 + (1 if f.get("value_cr") else 0)
            elif f["feed"] == "press" and f["cls"] in ("capex", "approval", "product"):
                p += 2
            elif f["feed"] == "press" and f["cls"] in ("order", "partnership", "acquisition", "fundraise"):
                p += 1
            elif f["feed"] == "meets":
                p += 1
        if p:
            cat_pts[t] = p
    for t in sorted(cat_pts, key=cat_pts.get, reverse=True)[:90]:
        reasons.setdefault(t, set()).add("filings")
    quiet = sorted([t for t, g in sigs.items() if t not in reasons and g.get("above_200dma") and (g.get("dist_52wh") or 99) <= 25
                    and (g.get("rs_6m") or -1) > 0 and (g.get("ran_pct") or 0) < 60],
                   key=lambda t: sigs[t].get("rs_6m") or 0, reverse=True)[:40]
    for t in quiet:
        reasons.setdefault(t, set()).add("quiet")
    finalists = list(reasons)[:230]
    print(f"[MB2] {len(sigs)} priced · {len(filings)} filings · {len(finalists)} finalists "
          f"(chart {len(chart)}, filings {len(cat_pts)}, quiet {len(quiet)})")

    # 3) deep read: Screener + filings + order book
    def enrich(t):
        sym = t.replace(".NS", "").replace(".BO", "")
        raw = S._fetch_screener_deep(sym)
        code = (raw or {}).get("bse_code") or slug_code.get(sym)
        fl = by_code.get(code, []) if code else []
        qs = _clean((raw or {}).get("q_sales"))
        ttm = sum(qs[-4:]) if len(qs) >= 4 else None
        facts = company_facts(fl, ttm, (raw or {}).get("mcap"), today)
        ob = order_book_from_filings(facts) if facts["results_press"] else None
        sc = score_v2(sigs[t], raw, facts, ob, sym)
        return t, sym, raw, sc, ob

    rows_out, fund_ok, ob_found = [], 0, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(lambda t: _safe(enrich, t), finalists):
            if not res:
                continue
            t, sym, raw, sc, ob = res
            fund_ok += 1 if raw else 0
            ob_found += 1 if ob else 0
            if sc["mcap"] is not None and sc["mcap"] < MCAP_MIN_CR:
                continue
            rows_out.append(build_row(sym, sigs[t], raw, sc, found_via=reasons[t], order_book=ob))

    rows_out.sort(key=lambda r: (STAGE_RANK[r["stage"]], r["meets_bar"], r["total"]), reverse=True)
    stage_counts = {}
    for r in rows_out:
        stage_counts[r["stage"]] = stage_counts.get(r["stage"], 0) + 1
    results = rows_out[:30]
    for i, r in enumerate(results):
        r["rank"] = i + 1
    elapsed = round(time.time() - start, 1)
    print(f"[MB2] done in {elapsed}s — {len(rows_out)} scored, stages {stage_counts}")
    return {
        "market": "Multibagger Early Signal v2", "model": "115",
        "total_scanned": len(tickers), "total_priced": len(sigs) + sum(excluded.values()),
        "excluded": excluded, "deep_fetched": len(finalists), "fund_fetched": fund_ok,
        "total_passed": len(rows_out), "stage_counts": stage_counts, "order_books_found": ob_found,
        "filings": {"source": finfo.get("source"), "counts": finfo.get("counts") or {}, "total": len(filings)},
        "unchecked": UNCHECKED, "results": results, "scan_time_seconds": elapsed,
    }


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        print(f"[MB2] enrich failed for {a}: {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  SINGLE-STOCK CHECK (same engine)
# ══════════════════════════════════════════════════════════════

def analyze_stock_v2(symbol):
    sym = (symbol or "").upper().strip().replace(".NS", "").replace(".BO", "")
    if not sym:
        return None
    suffixes = [".BO"] if sym.isdigit() else [".NS", ".BO"]
    rows, used = None, None
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        f_n = ex.submit(S._bulk_download_ohlc, ["^NSEI"], "1y")
        f_raw = ex.submit(S._fetch_screener_deep, sym)
        for suf in suffixes:
            try:
                d = S._bulk_download_ohlc([sym + suf], "1y")
            except Exception:
                d = {}
            if d.get(sym + suf) and len(d[sym + suf]) >= 20:
                rows, used = d[sym + suf], sym + suf
                break
        try:
            nifty = [r[1] for r in (f_n.result() or {}).get("^NSEI", [])]
        except Exception:
            nifty = []
        try:
            raw = f_raw.result() or {}
        except Exception:
            raw = {}
    if not rows:
        return None
    sig = S._mb_price_signals(rows, nifty, strict=False)
    if sig is None:
        return {"ticker": sym, "name": raw.get("name"), "error": "short_history",
                "message": f"Only {len(rows)} trading days of history — the check needs about 6 months "
                           f"(130 sessions). Likely a recent listing."}
    code = raw.get("bse_code") or (sym if sym.isdigit() else None)
    filings = fetch_company_filings(code) if code else []
    today = O._now_ist().date()
    qs = _clean(raw.get("q_sales"))
    facts = company_facts(filings, sum(qs[-4:]) if len(qs) >= 4 else None, raw.get("mcap"), today)
    ob = order_book_from_filings(facts, max_pdfs=2) if facts["results_press"] else None
    sc = score_v2(sig, raw, facts, ob, sym)
    row = build_row(sym, sig, raw, sc, order_book=ob)
    row["exchange"] = "NSE" if used.endswith(".NS") else "BSE"
    warnings = []
    gate = sig.get("gate")
    if gate == "penny":
        warnings.append(f"Price is below ₹{S.MB_MIN_PRICE}; the market scan skips penny stocks.")
    elif gate == "illiquid":
        warnings.append(f"Average daily turnover is only ₹{sig['turnover_cr']} Cr; the market scan skips illiquid stocks.")
    if sc["mcap"] is not None and sc["mcap"] < MCAP_MIN_CR:
        warnings.append(f"Market cap ₹{sc['mcap']:,.0f} Cr is under your ₹{MCAP_MIN_CR} Cr floor.")
    if not raw:
        warnings.append("Couldn't read Screener.in, so Business Change and part of Market Recognition are missing.")
    if not filings:
        warnings.append("No BSE filings found (or BSE didn't respond), so order and catalyst signals are empty.")
    row.update({"warnings": warnings, "verdict": verdict(sc),
                "eligible": not gate and not (sc["mcap"] is not None and sc["mcap"] < MCAP_MIN_CR)
                and sc["stage"] not in ("redflag", "weak", "nodata")})
    return row
