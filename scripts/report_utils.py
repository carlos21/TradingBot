#!/usr/bin/env python3
"""
Shared report utility functions used by html_report.py and html_comparison_report.py.
"""


def compute_trade_pnl(trade, close, account, risk, mode, nq_pv, fee_per_rt, be_threshold,
                      risk_pct=None, balance=None):
    """Compute PnL for a single trade+close pair. Returns dict with outcome/usd/pct/r.
    If risk_pct is set, risk is computed as balance * risk_pct / 100."""
    if risk_pct is not None and balance is not None:
        risk = balance * risk_pct / 100.0
    is_reentry = trade.get("is_reentry", False) if trade else False

    if close is None:
        return {"outcome": "open", "usd": 0.0, "pct": 0.0, "r": 0.0, "is_reentry": is_reentry}

    result_type = close.get("result_type", None)
    if result_type == "SP":
        actual_r = close.get("result", 0.0)
        if mode == "sim":
            usd = risk * actual_r if actual_r > 0 else -risk
        else:  # real
            entry = trade.get("entry") or trade.get("entry_price")
            orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
            if entry is None or orig_sl is None:
                usd = 0.0
            else:
                sl_pts = round(abs(entry - orig_sl), 4)
                if sl_pts <= 0:
                    usd = 0.0
                else:
                    contracts = max(1, round(risk / (sl_pts * nq_pv)))
                    fees = contracts * fee_per_rt
                    if actual_r > 0:
                        usd = contracts * (actual_r * sl_pts) * nq_pv - fees
                    else:
                        usd = -(contracts * sl_pts * nq_pv) - fees
        pct_base = balance if (risk_pct is not None and balance) else account
        pct = usd / pct_base * 100 if pct_base else 0.0
        return {"outcome": "sp", "usd": usd, "pct": pct, "r": actual_r, "is_reentry": is_reentry}

    actual_r = close.get("result", 0.0)

    if mode == "sim":
        usd = risk * actual_r if actual_r > 0 else -risk
    else:  # real
        entry = trade.get("entry") or trade.get("entry_price")
        orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
        if entry is None or orig_sl is None:
            usd = 0.0
        else:
            sl_pts = round(abs(entry - orig_sl), 4)
            if sl_pts <= 0:
                usd = 0.0
            else:
                contracts = max(1, round(risk / (sl_pts * nq_pv)))
                fees = contracts * fee_per_rt
                if actual_r > 0:
                    usd = contracts * (actual_r * sl_pts) * nq_pv - fees
                else:
                    usd = -(contracts * sl_pts * nq_pv) - fees

    pct_base = balance if (risk_pct is not None and balance) else account
    pct = usd / pct_base * 100 if pct_base else 0.0

    if actual_r > 0 and actual_r < be_threshold:
        outcome = "be"
    elif actual_r >= be_threshold:
        outcome = "win"
    else:
        outcome = "loss"

    return {"outcome": outcome, "usd": usd, "pct": pct, "r": actual_r, "is_reentry": is_reentry}


def calc_max_dd(balances):
    """Calculate max drawdown from equity curve. Returns (max_dd_usd, max_dd_pct)."""
    if not balances or len(balances) < 2:
        return 0.0, 0.0
    peak = balances[0]
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    for bal in balances[1:]:
        if bal > peak:
            peak = bal
        dd_usd = peak - bal
        dd_pct = (dd_usd / peak * 100) if peak > 0 else 0.0
        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
            max_dd_pct = dd_pct
    return max_dd_usd, max_dd_pct


def fmt_usd(val):
    return f"${val:+,.0f}"


def fmt_pct(val):
    return f"{val:+.2f}%"


def pnl_class(val):
    if val > 0.005:
        return "positive"
    if val < -0.005:
        return "negative"
    return "neutral"


def h(text):
    """HTML-escape."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
