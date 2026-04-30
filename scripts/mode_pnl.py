#!/usr/bin/env python3
"""
Reusable PnL calculators and aggregation logic for scenario results.
Extracted from run_scenarios.py so both the runner and comparison reports
use the exact same math.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Dict, List, Tuple, Any
from zoneinfo import ZoneInfo

from dateutil import parser as dtparser

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.financial_calc import FinancialCalc

BE_THRESHOLD = FinancialCalc.DEFAULT_BE_THRESHOLD_R

PAIR_TZS = {
    "EURUSD": "Europe/London",
    "MNQ": "Etc/GMT+5",
    "ES": "Etc/GMT+5",
}


def _resolve_risk(balance: float, risk_usd_fix: float, risk_pct: float | None) -> float:
    if risk_pct is not None:
        return balance * risk_pct / 100.0
    return risk_usd_fix


def _detect_pair(name: str) -> str:
    """Infer pair from scenario name (e.g. 'MNQ - 2025-05-01' -> 'MNQ')."""
    for key in ("MNQ", "ES", "EURUSD"):
        if name.upper().startswith(key):
            return key
    return "MNQ"


def per_trade_sim(
    trade: dict,
    close: dict,
    balance: float,
    risk_usd_fix: float,
    risk_pct: float | None,
) -> Tuple[float | None, float | None, float, float, str]:
    """Sim mode: fixed risk per trade. Returns (usd, pct, actual_r, commission, outcome)."""
    if close is None:
        return 0.0, 0.0, 0.0, 0.0, "open"
    risk = _resolve_risk(balance, risk_usd_fix, risk_pct)
    actual_r = close.get("result", 0.0)
    usd = risk * actual_r if actual_r > 0 else -risk
    pct = usd / balance * 100 if balance else 0.0
    if FinancialCalc.is_breakeven_by_r(actual_r, BE_THRESHOLD):
        outcome = "be"
    elif actual_r >= BE_THRESHOLD:
        outcome = "win"
    else:
        outcome = "loss"
    return usd, pct, actual_r, 0.0, outcome


def per_trade_futures(
    trade: dict,
    close: dict,
    balance: float,
    risk_usd_fix: float,
    risk_pct: float | None,
    nq_pv: float = 2.0,
    fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
) -> Tuple[float | None, float | None, float, float, str]:
    """Real futures mode: integer contracts + fees."""
    if close is None:
        return 0.0, 0.0, 0.0, 0.0, "open"

    result_type = close.get("result_type", None)
    actual_r = close.get("result", 0.0)

    if result_type == "SP":
        stored_pnl = close.get("pnl_usd")
        commission = close.get("fees", 0.0)
        if stored_pnl is not None:
            usd = stored_pnl
        else:
            if trade is None:
                usd = 0.0
            else:
                entry = trade.get("entry") or trade.get("entry_price")
                orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
                risk_pts = trade.get("risk")
                sl_pts_price = round(abs(entry - orig_sl), 4) if entry and orig_sl else 0.0
                sl_pts = risk_pts if risk_pts is not None else sl_pts_price
                contracts = max(1, round(_resolve_risk(balance, risk_usd_fix, risk_pct) / (sl_pts_price * nq_pv))) if sl_pts_price > 0 else 1
                fees = contracts * fee_per_rt
                usd = FinancialCalc.pnl_usd(contracts, actual_r, sl_pts, nq_pv, fees)
                commission = fees
        pct = usd / balance * 100 if balance else 0.0
        return usd, pct, actual_r, commission, "sp"

    if result_type == "BE":
        stored_pnl = close.get("pnl_usd")
        commission = close.get("fees", 0.0)
        if stored_pnl is not None:
            usd = stored_pnl
        else:
            usd = 0.0
        pct = usd / balance * 100 if balance else 0.0
        return usd, pct, actual_r, commission, "be"

    # Use stored pnl_usd from strategy if available (single source of truth)
    stored_pnl = close.get("pnl_usd")
    stored_fees = close.get("fees") or 0.0
    if stored_pnl is not None:
        usd = stored_pnl
        commission = stored_fees
    else:
        if trade is None:
            return None, None, actual_r, 0.0, "loss"
        entry = trade.get("entry") or trade.get("entry_price")
        orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
        if entry is None or orig_sl is None:
            return None, None, actual_r, 0.0, "loss"
        sl_pts_price = round(abs(entry - orig_sl), 4)
        if sl_pts_price <= 0:
            return None, None, actual_r, 0.0, "loss"
        risk_pts = trade.get("risk")
        sl_pts = risk_pts if risk_pts is not None else sl_pts_price
        risk = _resolve_risk(balance, risk_usd_fix, risk_pct)
        contracts = FinancialCalc.contracts(risk, sl_pts_price * nq_pv)
        fees = FinancialCalc.fees(contracts, fee_per_rt)
        usd = FinancialCalc.pnl_usd(contracts, actual_r, sl_pts, nq_pv, fees)
        commission = fees

    pct = usd / balance * 100 if balance else 0.0
    is_be = FinancialCalc.is_breakeven_by_r(actual_r, BE_THRESHOLD)
    is_win = actual_r >= BE_THRESHOLD
    if is_be:
        outcome = "be"
    elif is_win:
        outcome = "win"
    else:
        outcome = "loss"
    return usd, pct, actual_r, commission, outcome


def per_trade_cfd(
    trade: dict,
    close: dict,
    balance: float,
    risk_usd_fix: float,
    risk_pct: float | None,
    nq_pv: float = 2.0,
    cfd_spread: float = 1.5,
    cfd_commission: float = 5.0,
) -> Tuple[float | None, float | None, float, float, str]:
    """Real CFD mode: fractional lots + spread + commission."""
    if close is None:
        return 0.0, 0.0, 0.0, 0.0, "open"

    result_type = close.get("result_type", None)
    actual_r = close.get("result", 0.0)

    if result_type == "SP":
        if trade is None:
            usd = 0.0
            commission = 0.0
        else:
            entry = trade.get("entry") or trade.get("entry_price")
            orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
            if entry is None or orig_sl is None:
                usd = 0.0
                commission = 0.0
            else:
                sl_pts = round(abs(entry - orig_sl), 4)
                if sl_pts <= 0:
                    usd = 0.0
                    commission = 0.0
                else:
                    risk = _resolve_risk(balance, risk_usd_fix, risk_pct)
                    lots = FinancialCalc.lots(risk, sl_pts * nq_pv)
                    spread_cost = lots * cfd_spread * nq_pv
                    commission_cost = lots * cfd_commission
                    total_cost = spread_cost + commission_cost
                    usd = FinancialCalc.pnl_usd(lots, actual_r, sl_pts, nq_pv, total_cost)
                    commission = total_cost
        pct = usd / balance * 100 if balance else 0.0
        return usd, pct, actual_r, commission, "sp"

    if result_type == "BE":
        if trade is None:
            usd = 0.0
            commission = 0.0
        else:
            entry = trade.get("entry") or trade.get("entry_price")
            orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
            if entry is None or orig_sl is None:
                usd = 0.0
                commission = 0.0
            else:
                sl_pts = round(abs(entry - orig_sl), 4)
                if sl_pts <= 0:
                    usd = 0.0
                    commission = 0.0
                else:
                    risk = _resolve_risk(balance, risk_usd_fix, risk_pct)
                    lots = FinancialCalc.lots(risk, sl_pts * nq_pv)
                    spread_cost = lots * cfd_spread * nq_pv
                    commission_cost = lots * cfd_commission
                    total_cost = spread_cost + commission_cost
                    usd = FinancialCalc.pnl_usd(lots, actual_r, sl_pts, nq_pv, total_cost)
                    commission = total_cost
        pct = usd / balance * 100 if balance else 0.0
        return usd, pct, actual_r, commission, "be"

    if trade is None:
        return None, None, actual_r, 0.0, "loss"

    entry = trade.get("entry") or trade.get("entry_price")
    orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
    if entry is None or orig_sl is None:
        return None, None, actual_r, 0.0, "loss"
    sl_pts = round(abs(entry - orig_sl), 4)
    if sl_pts <= 0:
        return None, None, actual_r, 0.0, "loss"

    risk = _resolve_risk(balance, risk_usd_fix, risk_pct)
    lots = FinancialCalc.lots(risk, sl_pts * nq_pv)
    spread_cost = lots * cfd_spread * nq_pv
    commission_cost = lots * cfd_commission
    total_cost = spread_cost + commission_cost
    usd = FinancialCalc.pnl_usd(lots, actual_r, sl_pts, nq_pv, total_cost)
    commission = total_cost

    pct = usd / balance * 100 if balance else 0.0
    is_be = FinancialCalc.is_breakeven_by_r(actual_r, BE_THRESHOLD)
    is_win = actual_r >= BE_THRESHOLD
    if is_be:
        outcome = "be"
    elif is_win:
        outcome = "win"
    else:
        outcome = "loss"
    return usd, pct, actual_r, commission, outcome


def aggregate_breakdown(
    results: List[dict],
    per_trade_fn: Callable,
    account: float,
    risk_usd_fix: float,
    risk_pct: float | None,
    pair_tz_name: str = "Etc/GMT+5",
) -> Tuple[dict, dict, dict, dict]:
    """
    Aggregate scenario results into daily, weekly, and monthly buckets.

    Returns (daily, weekly, monthly, stats) where each bucket dict is keyed by
    period string and contains: wins, losses, be, sp, open, usd, commission,
    start_balance, reentry_win, reentry_loss, reentry_be.

    stats contains overall summary metrics.
    """

    def _new_bucket():
        return {
            "usd": 0.0,
            "pct": 0.0,
            "commission": 0.0,
            "wins": 0,
            "losses": 0,
            "be": 0,
            "sp": 0,
            "open": 0,
            "reentry_win": 0,
            "reentry_loss": 0,
            "reentry_be": 0,
            "start_balance": None,
        }

    daily = defaultdict(_new_bucket)
    weekly = defaultdict(_new_bucket)
    monthly = defaultdict(_new_bucket)
    running_balance = account
    pair_tz = ZoneInfo(pair_tz_name)

    for r in results:
        scenario_date = dtparser.parse(r["date"]).date()
        for trade, close in (r.get("trade_pairs") or []):
            if close is not None and close.get("exit_time"):
                exit_dt = datetime.fromtimestamp(close["exit_time"], tz=pair_tz)
                d_key = str(exit_dt.date())
                iso = exit_dt.isocalendar()
                w_key = f"{iso.year}-W{iso.week:02d}"
                m_key = exit_dt.strftime("%Y-%m")
            else:
                d_key = str(scenario_date)
                iso = scenario_date.isocalendar()
                w_key = f"{iso.year}-W{iso.week:02d}"
                m_key = scenario_date.strftime("%Y-%m")

            for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                if bucket[key]["start_balance"] is None:
                    bucket[key]["start_balance"] = running_balance

            if close is None:
                for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                    bucket[key]["open"] += 1
                continue

            t_usd, t_pct, actual_r, t_comm, outcome = per_trade_fn(trade, close, running_balance)
            if t_usd is not None:
                running_balance += t_usd

            is_reentry = trade.get("is_reentry", False) if trade else False

            for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                if t_usd is not None:
                    bucket[key]["usd"] += t_usd
                    bucket[key]["commission"] += t_comm
                if outcome == "sp":
                    bucket[key]["sp"] += 1
                elif outcome == "be":
                    bucket[key]["be"] += 1
                    if is_reentry:
                        bucket[key]["reentry_be"] += 1
                elif outcome == "win":
                    bucket[key]["wins"] += 1
                    if is_reentry:
                        bucket[key]["reentry_win"] += 1
                elif outcome == "loss":
                    bucket[key]["losses"] += 1
                    if is_reentry:
                        bucket[key]["reentry_loss"] += 1

    # Compute overall stats
    outcomes = []
    for r in results:
        for trade, close in (r.get("trade_pairs") or []):
            if close is None:
                continue
            _, _, _, _, outcome = per_trade_fn(trade, close, account)
            if outcome != "open":
                outcomes.append(outcome)

    total_t = len(outcomes)
    total_w = outcomes.count("win")
    total_l = outcomes.count("loss")
    total_be = outcomes.count("be")
    total_sp = outcomes.count("sp")
    winrate = (total_w / (total_w + total_l) * 100) if (total_w + total_l) > 0 else 0.0
    net_usd = sum(v["usd"] for v in daily.values())
    net_pct = net_usd / account * 100 if account else 0.0

    # Max consecutive
    max_cw = max_cl = cw = cl = 0
    for o in outcomes:
        if o == "win":
            cw += 1
            cl = 0
        elif o == "loss":
            cl += 1
            cw = 0
        else:
            continue
        max_cw = max(max_cw, cw)
        max_cl = max(max_cl, cl)

    # Equity curve for drawdown
    equity_balances = [account]
    for key in sorted(daily.keys()):
        equity_balances.append(equity_balances[-1] + daily[key]["usd"])

    peak = account
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    for bal in equity_balances[1:]:
        if bal > peak:
            peak = bal
        dd_usd = peak - bal
        dd_pct = (dd_usd / peak * 100) if peak > 0 else 0.0
        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
            max_dd_pct = dd_pct

    num_months = len(monthly) if monthly else 1
    avg_monthly_usd = net_usd / num_months if num_months > 0 else 0.0
    total_commission = sum(v.get("commission", 0.0) for v in daily.values())

    stats = {
        "total_trades": total_t,
        "wins": total_w,
        "losses": total_l,
        "be": total_be,
        "sp": total_sp,
        "winrate": winrate,
        "net_usd": net_usd,
        "net_pct": net_pct,
        "max_cw": max_cw,
        "max_cl": max_cl,
        "max_dd_usd": max_dd_usd,
        "max_dd_pct": max_dd_pct,
        "avg_monthly_usd": avg_monthly_usd,
        "total_commission": total_commission,
        "equity_balances": equity_balances,
    }

    return dict(daily), dict(weekly), dict(monthly), stats
