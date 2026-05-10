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
        return {"outcome": "open", "usd": 0.0, "pct": 0.0, "r": 0.0, "is_reentry": is_reentry, "commission": 0.0}

    result_type = close.get("result_type", None)
    actual_r = close.get("result", 0.0)

    def _sim_usd(risk, actual_r, be_threshold):
        if abs(actual_r) < be_threshold:
            return risk * actual_r
        elif actual_r > 0:
            return risk * actual_r
        else:
            return -risk

    def _contracts_from_trade_or_compute(trade, risk, sl_pts_price, nq_pv):
        if trade is not None:
            contracts = trade.get("contracts")
            if contracts is not None:
                return contracts
        if sl_pts_price > 0:
            raw = risk / (sl_pts_price * nq_pv)
            return max(1, int(raw + 0.5))
        return 1

    if result_type == "SP":
        commission = 0.0
        if mode == "sim":
            usd = _sim_usd(risk, actual_r, be_threshold)
        else:  # real
            # Use stored pnl_usd if available (single source of truth)
            stored_pnl = close.get("pnl_usd")
            if stored_pnl is not None:
                usd = stored_pnl
                commission = close.get("fees", 0.0)
            else:
                # Fallback: recalculate (legacy path)
                if trade is None:
                    usd = 0.0
                else:
                    entry = trade.get("entry") or trade.get("entry_price")
                    orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
                    # Use stored risk field (ticks) for PnL calculation
                    risk_pts = trade.get("risk")
                    if entry is None or orig_sl is None:
                        usd = 0.0
                    else:
                        sl_pts_price = round(abs(entry - orig_sl), 4)
                        # Use stored risk if available, otherwise fall back to price diff
                        sl_pts = risk_pts if risk_pts is not None else sl_pts_price
                        if sl_pts <= 0:
                            usd = 0.0
                        else:
                            contracts = _contracts_from_trade_or_compute(trade, risk, sl_pts_price, nq_pv)
                            fees = contracts * fee_per_rt
                            commission = fees
                            usd = contracts * actual_r * sl_pts * nq_pv - fees
        pct_base = balance if (risk_pct is not None and balance) else account
        pct = usd / pct_base * 100 if pct_base else 0.0
        return {"outcome": "sp", "usd": usd, "pct": pct, "r": actual_r, "is_reentry": is_reentry, "commission": commission}

    commission = 0.0

    if mode == "sim":
        usd = _sim_usd(risk, actual_r, be_threshold)
    else:  # real
        # Use stored pnl_usd if available (single source of truth)
        stored_pnl = close.get("pnl_usd")
        if stored_pnl is not None:
            usd = stored_pnl
            commission = close.get("fees", 0.0)
        else:
            # Fallback: recalculate (legacy path)
            if trade is None:
                usd = 0.0
            else:
                entry = trade.get("entry") or trade.get("entry_price")
                orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
                # Use stored risk field (ticks) for PnL calculation
                risk_pts = trade.get("risk")
                if entry is None or orig_sl is None:
                    usd = 0.0
                else:
                    sl_pts_price = round(abs(entry - orig_sl), 4)
                    # Use stored risk if available, otherwise fall back to price diff
                    sl_pts = risk_pts if risk_pts is not None else sl_pts_price
                    if sl_pts <= 0:
                        usd = 0.0
                    else:
                        contracts = _contracts_from_trade_or_compute(trade, risk, sl_pts_price, nq_pv)
                        fees = contracts * fee_per_rt
                        commission = fees
                        usd = contracts * actual_r * sl_pts * nq_pv - fees

    pct_base = balance if (risk_pct is not None and balance) else account
    pct = usd / pct_base * 100 if pct_base else 0.0

    if abs(actual_r) < be_threshold:
        outcome = "be"
    elif actual_r >= be_threshold:
        outcome = "win"
    else:
        outcome = "loss"

    return {"outcome": outcome, "usd": usd, "pct": pct, "r": actual_r, "is_reentry": is_reentry, "commission": commission}


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
