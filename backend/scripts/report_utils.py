#!/usr/bin/env python3
"""
Shared report utility functions used by html_report.py and html_comparison_report.py.
"""


def compute_trade_pnl(trade, close, account, risk, mode, nq_pv, fee_per_rt, be_threshold,
                      risk_pct=None, balance=None, cfd_spread=None, cfd_commission=None):
    """Compute PnL for a single trade+close pair. Returns dict with outcome/usd/pct/r.
    If risk_pct is set, risk is computed as balance * risk_pct / 100."""
    if risk_pct is not None and balance is not None:
        risk = balance * risk_pct / 100.0
    is_reentry = trade.get("is_reentry", False) if trade else False

    if close is None:
        return {"outcome": "open", "usd": 0.0, "pct": 0.0, "r": 0.0, "is_reentry": is_reentry, "commission": 0.0, "contracts": None}

    result_type = close.get("result_type", None)
    actual_r = close.get("result", 0.0)

    def _sim_usd(risk, actual_r, be_threshold):
        if abs(actual_r) < be_threshold:
            return risk * actual_r
        elif actual_r > 0:
            return risk * actual_r
        else:
            return -risk

    def _contracts_from_trade_or_compute(trade, risk, sl_pts_price, nq_pv, use_fractional=False):
        if trade is not None:
            contracts = trade.get("contracts")
            if contracts is not None:
                return contracts
        if sl_pts_price > 0:
            raw = risk / (sl_pts_price * nq_pv)
            if use_fractional:
                return max(0.01, raw)
            return max(1, int(raw + 0.5))
        return 0.01 if use_fractional else 1

    def _real_mode_pnl(trade, close, risk, nq_pv, fee_per_rt, use_fractional=False):
        """Calculate PnL for real modes (futures or CFD), handling stored vs fallback.
        Returns (usd, fees/commission, contracts)."""
        stored_pnl = close.get("pnl_usd")
        if stored_pnl is not None:
            contracts = trade.get("contracts") if trade else None
            return stored_pnl, close.get("fees", 0.0), contracts

        if trade is None:
            return 0.0, 0.0, None

        entry = trade.get("entry") or trade.get("entry_price")
        orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
        risk_pts = trade.get("risk")
        if entry is None or orig_sl is None:
            return 0.0, 0.0, None

        sl_pts_price = round(abs(entry - orig_sl), 4)
        sl_pts = risk_pts if risk_pts is not None else sl_pts_price
        if sl_pts <= 0:
            return 0.0, 0.0, None

        contracts = _contracts_from_trade_or_compute(trade, risk, sl_pts_price, nq_pv, use_fractional)

        if use_fractional and cfd_spread is not None and cfd_commission is not None:
            spread_cost = contracts * cfd_spread * nq_pv
            commission_cost = contracts * cfd_commission
            total_cost = spread_cost + commission_cost
            usd = contracts * actual_r * sl_pts * nq_pv - total_cost
            return usd, total_cost, contracts
        else:
            fees = contracts * fee_per_rt
            usd = contracts * actual_r * sl_pts * nq_pv - fees
            return usd, fees, contracts

    if result_type == "SP":
        commission = 0.0
        contracts = None
        if mode == "sim":
            usd = _sim_usd(risk, actual_r, be_threshold)
        else:  # real
            usd, commission, contracts = _real_mode_pnl(trade, close, risk, nq_pv, fee_per_rt,
                                                        use_fractional=(mode == "real_cfd"))
        pct_base = balance if (risk_pct is not None and balance) else account
        pct = usd / pct_base * 100 if pct_base else 0.0
        return {"outcome": "sp", "usd": usd, "pct": pct, "r": actual_r, "is_reentry": is_reentry, "commission": commission, "contracts": contracts}

    commission = 0.0
    contracts = None

    if mode == "sim":
        usd = _sim_usd(risk, actual_r, be_threshold)
    else:  # real
        usd, commission, contracts = _real_mode_pnl(trade, close, risk, nq_pv, fee_per_rt,
                                                    use_fractional=(mode == "real_cfd"))

    pct_base = balance if (risk_pct is not None and balance) else account
    pct = usd / pct_base * 100 if pct_base else 0.0

    if abs(actual_r) < be_threshold:
        outcome = "be"
    elif actual_r >= be_threshold:
        outcome = "win"
    else:
        outcome = "loss"

    return {"outcome": outcome, "usd": usd, "pct": pct, "r": actual_r, "is_reentry": is_reentry, "commission": commission, "contracts": contracts}


def calc_max_dd(balances):
    """Calculate max drawdown from equity curve.
    Returns (max_dd_usd, max_dd_pct, max_dd_from_start_usd, max_dd_from_start_pct).
    - max_dd_*: drawdown from peak (running max)
    - max_dd_from_start_*: drawdown from initial balance
    """
    if not balances or len(balances) < 2:
        return 0.0, 0.0, 0.0, 0.0
    start = balances[0]
    peak = balances[0]
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    max_dd_from_start_usd = 0.0
    max_dd_from_start_pct = 0.0
    for bal in balances[1:]:
        if bal > peak:
            peak = bal
        dd_usd = peak - bal
        dd_pct = (dd_usd / peak * 100) if peak > 0 else 0.0
        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
            max_dd_pct = dd_pct
        # Drawdown from initial balance
        if bal < start:
            dd_from_start_usd = start - bal
            dd_from_start_pct = (dd_from_start_usd / start * 100) if start > 0 else 0.0
            if dd_from_start_usd > max_dd_from_start_usd:
                max_dd_from_start_usd = dd_from_start_usd
                max_dd_from_start_pct = dd_from_start_pct
    return max_dd_usd, max_dd_pct, max_dd_from_start_usd, max_dd_from_start_pct


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
