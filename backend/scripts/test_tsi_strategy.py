#!/usr/bin/env python3
"""
TSI Cross Strategy Tester — standalone backtest runner.

Runs the TSI Cross strategy over a single date range, captures trades,
generates chart snapshots, and prints a PnL summary identical to run_scenarios.sh.

Usage:
    python scripts/test_tsi_strategy.py --start "2026-02-22 17:00:00" --end "2026-05-22 00:00:00"
    python scripts/test_tsi_strategy.py --start "2026-03-01 08:00:00" --end "2026-03-07 16:58:00" --html-report
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, time as dtime
from multiprocessing import Process, Event
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import requests
from dateutil import parser as dtparser
from playwright.async_api import async_playwright

# -------------------------------------------------------------------------
# PATH SETUP
# -------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_factory import create_app, Repositories
from src.infrastructure.data_sources.csv_datasource import CSVDataSource
from src.financial_calc import FinancialCalc
from src.strategies.base_strategy import BreakevenConfig
from src.strategies.tsi_cross.prod_config import (
    get_tsi_cross_numbers,
    get_tsi_cross_config,
)
from tests.fakes import FakeLineRepository, FakeTradeRepository
from scripts.mode_pnl import per_trade_sim, per_trade_futures, per_trade_cfd
from scripts.html_report import generate_html_report

APP_HOST = "127.0.0.1"

PAIR_TZS = {
    "EURUSD": "Europe/London",
    "MNQ": "Etc/GMT+5",
    "ES": "Etc/GMT+5",
}

BE_THRESHOLD = FinancialCalc.DEFAULT_BE_THRESHOLD_R


# -------------------------------------------------------------------------
# Server Process
# -------------------------------------------------------------------------

def _run_server_inner(
    csv_path: str,
    bars_per_second: float,
    port: int,
    ready_event: Event,
    quiet: bool = False,
    rr_ratio: float = 5.0,
    risk_per_trade: float | None = None,
    risk_pct_per_trade: float | None = None,
    account_balance: float = 100000.0,
    fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
    session_end: str = "16:58",
    session_tz: str = "America/New_York",
    fixed_stop_loss: float | None = None,
    time_range: tuple[str, str] = ("08:00", "15:30"),
    daily_trades_limit: int = 5,
    cross_confirmation_bars: int = 3,
):
    if quiet:
        sys.stdout = open(os.devnull, "w")
        import logging

        logging.disable(logging.CRITICAL)

    # Initialize a temp-file SQLite DB so settings/accounts routes don't 500
    # (in-memory :memory: doesn't work across Flask request threads)
    import tempfile, atexit
    _db_fd, _db_path = tempfile.mkstemp(suffix="_tsi_test.db")
    os.close(_db_fd)
    atexit.register(lambda: os.path.exists(_db_path) and os.remove(_db_path))
    from src.infrastructure.database import database
    database.setup_database(db_url=f"sqlite:///{_db_path}")

    repos = Repositories(lines=FakeLineRepository(), trades=FakeTradeRepository())

    ds = CSVDataSource(
        pair="MNQ",
        filename=csv_path,
        initial_start_time=0,
        initial_end_time=9999999999,
        bars_per_second=bars_per_second,
    )

    numbers = get_tsi_cross_numbers(
        rr_ratio=rr_ratio,
        risk_per_trade=risk_per_trade,
        risk_pct_per_trade=risk_pct_per_trade,
        account_balance=account_balance,
        fixed_stop_loss=fixed_stop_loss,
        fee_per_rt=fee_per_rt,
        close_on_opposite_cross=True,
    )
    config = get_tsi_cross_config(
        time_range=time_range,
        daily_trades_limit=daily_trades_limit,
        skip_rollover=False,
        breakeven=BreakevenConfig(trigger_rr=1.0, move_to_rr=0.05),
        cross_confirmation_bars=cross_confirmation_bars,
    )

    wiring = create_app(
        pair="MNQ",
        data_source=ds,
        repos=repos,
        numbers=numbers,
        strategy_config=config,
        timeframes=["5m"],
        bootstrap_existing_lines=False,
        broker_mode="futures",
        broker_spread=0.0,
        use_fractional_lots=False,
        fee_per_rt=fee_per_rt,
        session_end_time=session_end,
        session_tz=session_tz,
        strategy_name="tsi_cross",
    )

    ready_event.set()

    import logging

    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    # The replay outpaces the headless browser; while the page's main thread
    # is busy it cannot answer engine.io pings, and the default 25s/20s
    # ping_interval/ping_timeout makes the server kill the socket mid-run —
    # the queued tail of the event stream (trades + stream_end) is then lost
    # and results silently truncate. Give the test client ample ping room.
    wiring.socketio.server.eio.ping_interval = 60
    wiring.socketio.server.eio.ping_timeout = 3600

    wiring.socketio.run(
        wiring.app,
        host=APP_HOST,
        port=port,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )


def run_server(
    csv_path: str,
    bars_per_second: float,
    port: int,
    ready_event: Event,
    quiet: bool = False,
    rr_ratio: float = 5.0,
    risk_per_trade: float | None = None,
    risk_pct_per_trade: float | None = None,
    account_balance: float = 100000.0,
    fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
    session_end: str = "16:58",
    session_tz: str = "America/New_York",
    fixed_stop_loss: float | None = None,
    time_range: tuple[str, str] = ("08:00", "15:30"),
    daily_trades_limit: int = 5,
    cross_confirmation_bars: int = 3,
):
    try:
        _run_server_inner(
            csv_path,
            bars_per_second,
            port,
            ready_event,
            quiet,
            rr_ratio,
            risk_per_trade,
            risk_pct_per_trade,
            account_balance,
            fee_per_rt,
            session_end,
            session_tz,
            fixed_stop_loss,
            time_range,
            daily_trades_limit,
            cross_confirmation_bars,
        )
    except Exception as e:
        import traceback

        sys.stderr.write(f"\n❌ Server process crashed: {e}\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------

def wait_http_ok(url: str, timeout: int = 30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(url, timeout=1).ok:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Server at {url} did not start.")


def reset_app_state(base_url: str, start: int | None = None, end: int | None = None):
    payload = {}
    if start:
        payload["start_time"] = start
    if end:
        payload["end_time"] = end
    try:
        r = requests.post(f"{base_url}/__reset_all", json=payload, timeout=10)
        return r.ok
    except Exception:
        return False


# -------------------------------------------------------------------------
# Summary Reporting (adapted from run_scenarios.py)
# -------------------------------------------------------------------------

RST = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
GRAY = "\033[90m"
CYAN = "\033[96m"
WHITE = "\033[97m"
BLUE = "\033[94m"

_ANSI = re.compile(r"\033\[[0-9;]*m")


def _vis(s: str) -> int:
    return len(_ANSI.sub("", s))


def _ljust(s: str, w: int) -> str:
    return s + " " * max(0, w - _vis(s))


def _center(s: str, w: int) -> str:
    pad = max(0, w - _vis(s))
    return " " * (pad // 2) + s + " " * (pad - pad // 2)


def _col(val, txt: str) -> str:
    if val is None:
        return f"{GRAY}{txt}{RST}"
    if val > 0:
        return f"{GREEN}{BOLD}{txt}{RST}"
    if val < 0:
        return f"{RED}{BOLD}{txt}{RST}"
    return txt


def _calc_max_drawdown(balances: list[float]):
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
        if bal < start:
            dd_from_start_usd = start - bal
            dd_from_start_pct = (dd_from_start_usd / start * 100) if start > 0 else 0.0
            if dd_from_start_usd > max_dd_from_start_usd:
                max_dd_from_start_usd = dd_from_start_usd
                max_dd_from_start_pct = dd_from_start_pct
    return max_dd_usd, max_dd_pct, max_dd_from_start_usd, max_dd_from_start_pct


def print_summary(trade_pairs: list, args, pair_tz: ZoneInfo):
    """Print daily/weekly/monthly PnL tables + overall summary."""
    ACCT = args.account
    RISK_PCT = args.risk_pct
    RISK_USD_FIX = args.risk
    NQ_PV = 2.0
    FEE_PER_RT = FinancialCalc.DEFAULT_FEE_PER_RT

    def get_risk(balance: float) -> float:
        if RISK_PCT is not None:
            return balance * RISK_PCT / 100.0
        return RISK_USD_FIX

    mode = args.mode

    def _per_trade_sim(trade, close, balance=ACCT):
        usd, pct, actual_r, comm, _outcome = per_trade_sim(
            trade, close, balance, RISK_USD_FIX, RISK_PCT
        )
        return usd, pct, actual_r, comm

    def _per_trade_real(trade, close, balance=ACCT):
        usd, pct, actual_r, comm, _outcome = per_trade_futures(
            trade, close, balance, RISK_USD_FIX, RISK_PCT, nq_pv=NQ_PV, fee_per_rt=FEE_PER_RT
        )
        return usd, pct, actual_r, comm

    def _per_trade_cfn(trade, close, balance=ACCT):
        cfd_spread = args.cfd_spread
        cfd_commission = args.commission
        if cfd_commission is None:
            cfd_commission = args.cfd_commission
        usd, pct, actual_r, comm, _outcome = per_trade_cfd(
            trade,
            close,
            balance,
            RISK_USD_FIX,
            RISK_PCT,
            nq_pv=NQ_PV,
            cfd_spread=cfd_spread,
            cfd_commission=cfd_commission,
        )
        return usd, pct, actual_r, comm

    pnl_fn = _per_trade_sim
    if mode == "real_futures":
        pnl_fn = _per_trade_real
    elif mode == "real_cfd":
        pnl_fn = _per_trade_cfn

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
            "start_balance": None,
            "trades": 0,
        }

    daily = defaultdict(_new_bucket)
    weekly = defaultdict(_new_bucket)
    monthly = defaultdict(_new_bucket)
    running_balance = ACCT

    for trade, close in trade_pairs:
        if close is not None and close.get("exit_time"):
            exit_dt = datetime.fromtimestamp(close["exit_time"], tz=pair_tz)
            d_key = str(exit_dt.date())
            iso = exit_dt.isocalendar()
            w_key = f"{iso.year}-W{iso.week:02d}"
            m_key = exit_dt.strftime("%Y-%m")
        else:
            # Fallback: use entry time or today
            et = trade.get("entry_time") if trade else None
            if et:
                exit_dt = datetime.fromtimestamp(et, tz=pair_tz)
            else:
                exit_dt = datetime.now(pair_tz)
            d_key = str(exit_dt.date())
            iso = exit_dt.isocalendar()
            w_key = f"{iso.year}-W{iso.week:02d}"
            m_key = exit_dt.strftime("%Y-%m")

        for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
            if bucket[key]["start_balance"] is None:
                bucket[key]["start_balance"] = running_balance

        if close is None:
            for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                bucket[key]["open"] += 1
            continue

        result_type = close.get("result_type", None)
        t_usd, t_pct, actual_r, t_comm = pnl_fn(trade, close, running_balance)
        if t_usd is not None:
            running_balance += t_usd

        if result_type == "SP":
            for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                if t_usd is not None:
                    bucket[key]["usd"] += t_usd
                    bucket[key]["commission"] += t_comm
                bucket[key]["sp"] += 1
            continue

        if result_type == "BE":
            for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                if t_usd is not None:
                    bucket[key]["usd"] += t_usd
                    bucket[key]["commission"] += t_comm
                bucket[key]["be"] += 1
            continue

        is_be = FinancialCalc.is_breakeven_by_r(actual_r, BE_THRESHOLD)
        is_win = actual_r >= BE_THRESHOLD
        for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
            if t_usd is not None:
                bucket[key]["usd"] += t_usd
                bucket[key]["commission"] += t_comm
            if is_be:
                bucket[key]["be"] += 1
            elif is_win:
                bucket[key]["wins"] += 1
            else:
                bucket[key]["losses"] += 1

    def _print_agg(title: str, data: dict):
        if not data:
            return
        COMM_W = 12
        TRADES_W = 8
        WL_W = 16
        lbl_w = max(len(k) for k in data) + 2
        sep = "-" * (lbl_w + 3 + WL_W + 54 + 3 + COMM_W + 3 + TRADES_W)

        print(f"\n{BOLD}{CYAN}{title}{RST}")
        print(
            f"  {'PERIOD':<{lbl_w}} | {'W/L':^{WL_W}} | {'%':>9} | {'$ PnL':>10} | {'$ BALANCE':>11} | {'COMMISSION':>{COMM_W}} | {'TRADES':^{TRADES_W}}"
        )
        print(f"  {sep}")
        balance = ACCT
        for key in sorted(data):
            v = data[key]
            balance += v["usd"]
            sb = v.get("start_balance")
            if sb is None:
                sb = ACCT
            v["pct"] = v["usd"] / sb * 100 if sb else 0.0
            parts = []
            if v["wins"]:
                parts.append(f"{GREEN}{BOLD}{v['wins']}W{RST}")
            if v["losses"]:
                parts.append(f"{RED}{BOLD}{v['losses']}L{RST}")
            if v["be"]:
                parts.append(f"{YELLOW}{v['be']}B{RST}")
            if v["sp"]:
                parts.append(f"{BLUE}{v['sp']}SP{RST}")
            wl_str = _center("/".join(parts) if parts else f"{GRAY}-{RST}", WL_W)
            pct_str = _col(v["pct"], f"{v['pct']:>+8.2f}%")
            usd_str = _col(v["usd"], f"${v['usd']:>+9,.0f}")
            bal_str = _col(balance - ACCT, f"${balance:>10,.0f}")
            comm_val = v.get("commission", 0.0)
            comm_str = f"${comm_val:>10,.2f}" if comm_val > 0 else f"{'--':>{COMM_W}}"
            trades_val = v["wins"] + v["losses"] + v["be"] + v["sp"] + v["open"]
            trades_str = f"{trades_val:^{TRADES_W}}"
            print(
                f"  {key:<{lbl_w}} | {wl_str} | {pct_str} | {usd_str} | {bal_str} | {comm_str} | {trades_str}"
            )

        total_usd = sum(v["usd"] for v in data.values())
        total_pct = total_usd / ACCT * 100 if ACCT else 0.0
        total_w = sum(v["wins"] for v in data.values())
        total_l = sum(v["losses"] for v in data.values())
        total_be = sum(v["be"] for v in data.values())
        total_sp = sum(v["sp"] for v in data.values())
        total_comm = sum(v.get("commission", 0.0) for v in data.values())
        total_trades = sum(v["wins"] + v["losses"] + v["be"] + v["sp"] + v["open"] for v in data.values())
        print(f"  {sep}")
        tot_pct = _col(total_pct, f"{total_pct:>+8.2f}%")
        tot_usd = _col(total_usd, f"${total_usd:>+9,.0f}")
        tot_bal = _col(total_usd, f"${ACCT + total_usd:>10,.0f}")
        tot_comm = f"${total_comm:>10,.2f}" if total_comm > 0 else f"{'--':>{COMM_W}}"
        tot_trades = f"{total_trades:^{TRADES_W}}"
        tot_parts = []
        if total_w:
            tot_parts.append(f"{GREEN}{total_w}W{RST}")
        if total_l:
            tot_parts.append(f"{RED}{total_l}L{RST}")
        if total_be:
            tot_parts.append(f"{YELLOW}{total_be}B{RST}")
        if total_sp:
            tot_parts.append(f"{BLUE}{total_sp}SP{RST}")
        tot_wl = _center("/".join(tot_parts) if tot_parts else f"{GRAY}-{RST}", WL_W)
        print(
            f"  {'TOTAL':<{lbl_w}} | {tot_wl} | {tot_pct} | {tot_usd} | {tot_bal} | {tot_comm} | {tot_trades}"
        )

    risk_desc = (
        f"{RISK_PCT}% of balance" if RISK_PCT is not None else f"${RISK_USD_FIX:,.0f} fixed"
    )
    mode_label = {
        "sim": f"SIM — ${ACCT:,.0f} account, {risk_desc} risk per trade",
        "real_futures": f"REAL FUTURES — MNQ micro futures, ${ACCT:,.0f} account, ~{risk_desc} risk, ${FEE_PER_RT:.2f}/contract RT fees",
        "real_cfd": f"REAL CFD — Nasdaq CFD, ${ACCT:,.0f} account, ~{risk_desc} risk",
    }.get(mode, mode)

    _print_agg(f"DAILY PnL   — {mode_label}", daily)
    _print_agg(f"WEEKLY PnL  — {mode_label}", weekly)
    _print_agg(f"MONTHLY PnL — {mode_label}", monthly)

    # Overall summary
    outcomes = []
    for trade, close in trade_pairs:
        if close is None:
            continue
        result_type = close.get("result_type", None)
        if result_type == "SP":
            outcomes.append("sp")
        elif result_type == "BE":
            outcomes.append("be")
        else:
            actual_r = close.get("result", 0.0)
            if FinancialCalc.is_breakeven_by_r(actual_r, BE_THRESHOLD):
                outcomes.append("be")
            elif actual_r >= BE_THRESHOLD:
                outcomes.append(True)
            else:
                outcomes.append(False)

    total_t = len(outcomes)
    wins = outcomes.count(True)
    losses = outcomes.count(False)
    bes = outcomes.count("be")
    sps = outcomes.count("sp")
    winrate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0

    max_consec_w = max_consec_l = cur_w = cur_l = 0
    for o in outcomes:
        if o is True:
            cur_w += 1
            cur_l = 0
        elif o is False:
            cur_l += 1
            cur_w = 0
        else:
            continue
        max_consec_w = max(max_consec_w, cur_w)
        max_consec_l = max(max_consec_l, cur_l)

    total_usd_all = sum(v["usd"] for v in daily.values())
    equity_balances = [ACCT]
    for key in sorted(daily.keys()):
        equity_balances.append(equity_balances[-1] + daily[key]["usd"])
    max_dd_usd, max_dd_pct, max_dd_start_usd, max_dd_start_pct = _calc_max_drawdown(
        equity_balances
    )

    num_months = len(monthly) if monthly else 1
    avg_monthly_pnl = total_usd_all / num_months if num_months > 0 else 0.0
    avg_monthly_pct = (avg_monthly_pnl / ACCT * 100) if ACCT > 0 else 0.0
    total_comm_all = sum(v.get("commission", 0.0) for v in daily.values())

    print(f"\n{BOLD}{CYAN}OVERALL SUMMARY — {mode_label}{RST}")
    summary_parts = [f"{GREEN}{wins}W{RST}", f"{RED}{losses}L{RST}", f"{YELLOW}{bes}BE{RST}"]
    if sps:
        summary_parts.append(f"{BLUE}{sps}SP{RST}")
    print(f"  Trades  : {total_t}  ({' / '.join(summary_parts)})")
    print(f"  Win Rate: {_col(winrate - 50, f'{winrate:.1f}%')}  (excl. breakevens)")
    print(f"  Max consec. wins  : {GREEN}{BOLD}{max_consec_w}{RST}")
    print(f"  Max consec. losses: {RED}{BOLD}{max_consec_l}{RST}")
    print(
        f"  Max Drawdown (from peak) : {_col(-max_dd_usd, f'${-max_dd_usd:,.0f}')} ({_col(-max_dd_pct, f'{-max_dd_pct:.2f}%')})"
    )
    print(
        f"  Max Drawdown (from start): {_col(-max_dd_start_usd, f'${-max_dd_start_usd:,.0f}')} ({_col(-max_dd_start_pct, f'{-max_dd_start_pct:.2f}%')})"
    )
    print(f"  Net P&L : {_col(total_usd_all, f'${total_usd_all:+,.0f}')}")
    print(f"  Commission: ${total_comm_all:,.2f}")
    print(
        f"  Monthly Avg : {_col(avg_monthly_pnl, f'${avg_monthly_pnl:+,.0f}')} ({_col(avg_monthly_pct, f'{avg_monthly_pct:+.2f}%')})"
    )
    print()


# -------------------------------------------------------------------------
# Progress display (same style as run_scenarios.py)
# -------------------------------------------------------------------------

def _progress_line(done: float, total: float, label: str = "",
                   lo: float = 0.0, hi: float = 100.0) -> None:
    """Redraw the single whole-process bar; [lo, hi] is this phase's slice of it."""
    frac = min(done / total, 1.0) if total else 1.0
    pct = lo + frac * (hi - lo)
    bar_len = 30
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(f"\r  {bar} {int(pct):3d}% {label:<50}", end="", flush=True)


def _progress_done(label: str) -> None:
    print(f"\r  {'█' * 30} 100% {label:<45}")


# Wait until the chart has actually painted: lightweight-charts schedules its
# redraw via requestAnimationFrame, so two rAF ticks guarantee the frame with
# our mutations is on screen. Replaces the old fixed 300/200/500ms sleeps.
_PAINT_WAIT = ("() => new Promise(r => requestAnimationFrame("
               "() => requestAnimationFrame(() => r())))")
_PAINT_SETTLE_MS = 50  # small safety margin after the double rAF


# -------------------------------------------------------------------------
# Main Async Runner
# -------------------------------------------------------------------------

async def run_test(args: argparse.Namespace):
    t0 = time.monotonic()
    quiet = args.quiet
    csv_path = Path(args.csv_file)
    if not csv_path.exists():
        print(f"❌ CSV file not found: {csv_path}")
        return

    base_url = f"http://{APP_HOST}:{args.port}"
    server_ready = Event()

    risk_per_trade = None if args.risk_pct is not None else args.risk
    server_proc = Process(
        target=run_server,
        args=(
            str(csv_path.resolve()),
            args.bars_per_second,
            args.port,
            server_ready,
            quiet,
            args.rr,
            risk_per_trade,
            args.risk_pct,
            args.account,
            FinancialCalc.DEFAULT_FEE_PER_RT,
            args.session_end,
            args.session_tz,
            args.fixed_stop_loss,
            (args.time_range_start, args.time_range_end),
            args.daily_trades_limit,
            args.cross_confirmation_bars,
        ),
        # Daemon so a crashed/aborted run can never hang on exit joining the
        # Flask child — multiprocessing terminates daemonic children at exit.
        daemon=True,
    )
    server_proc.start()

    if not server_ready.wait(timeout=30):
        print("❌ Server failed to start within timeout.")
        server_proc.terminate()
        return

    try:
        wait_http_ok(f"{base_url}/api/pair")
    except TimeoutError:
        print("❌ Server process started but HTTP not reachable.")
        server_proc.terminate()
        return

    pair_resp = requests.get(f"{base_url}/api/pair", timeout=10).json()
    pair_name = pair_resp["pair"]
    pair_tz = ZoneInfo(PAIR_TZS.get(pair_name, "UTC"))
    if not quiet:
        print(f"✅ Test Server running ({pair_name}) at {base_url}. Timezone: {pair_tz}")

    def get_epoch(dt_str: str) -> int:
        dt = dtparser.parse(dt_str)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        dt = dt.replace(tzinfo=pair_tz)
        return int(dt.timestamp())

    start_ts = get_epoch(args.start)
    end_ts = get_epoch(args.end)
    start_date_label = dtparser.parse(args.start).strftime("%Y-%m-%d")
    end_date_label = dtparser.parse(args.end).strftime("%Y-%m-%d")
    overview_label = (
        start_date_label if start_date_label == end_date_label
        else f"{start_date_label}_to_{end_date_label}"
    )
    outdir = Path(args.outdir) / pair_name
    outdir.mkdir(parents=True, exist_ok=True)

    if args.snapshot:
        for old_png in outdir.rglob("*.png"):
            old_png.unlink()

    WARMUP_SECONDS = 2 * 3600
    warmup_start_ts = start_ts - WARMUP_SECONDS

    if not reset_app_state(base_url, start=warmup_start_ts, end=start_ts):
        print("❌ Reset failed")
        server_proc.terminate()
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()

        if not quiet:
            def _on_console(msg):
                if msg.type in ("warning", "error"):
                    print(f"   [BROWSER {msg.type.upper()}] {msg.text}")

            page.on("console", _on_console)
        page.on("pageerror", lambda exc: print(f"   [BROWSER ERROR] {exc}"))

        await page.goto(
            f"{base_url}/?pair={pair_name}&start_time={start_ts}&keep_lines=true&keep_closed_trades=true&tf=5m&show_tsi=true",
            wait_until="domcontentloaded",
        )

        try:
            await page.wait_for_function("() => window.__chartReady === true", timeout=10000)
        except Exception as e:
            if not quiet:
                print(f"⚠️ Timeout waiting for chart init: {e}")

        await page.evaluate("""
            window.__done = false;
            window.__trades = [];
            window.__closes = {};
            window.__extraLines = {};

            const sock = window.chartViewer.socket;
            if (!sock) throw new Error("ChartViewer socket not found");

            sock.on('trade_open', (t) => {
                window.__trades.push(t);
                const n = window.__trades.length;
                if (window.chartViewer && window.chartViewer.priceSeries) {
                    const sl = t.stop_loss ?? t.sl ?? t.stopLoss;
                    if (typeof sl === 'number') {
                        const line = window.chartViewer.priceSeries.createPriceLine({
                            price: sl,
                            color: '#ff5252',
                            lineWidth: 1,
                            lineStyle: 1,
                            axisLabelVisible: true,
                            title: 'Orig SL #' + n
                        });
                        window.__extraLines[t.trade_id] = line;
                    }
                }
            });
            sock.on('trade_close', (c) => { window.__closes[String(c.trade_id)] = c; });
            sock.on('stream_end', () => { window.__done = true; });
        """)

        # Extend stream to session end
        session_end_time = dtime.fromisoformat(args.session_end)
        session_end_dt = datetime.combine(
            dtparser.parse(args.start).date(), session_end_time, tzinfo=pair_tz
        )
        session_end_ts = int(session_end_dt.timestamp())
        stream_stop_at = max(end_ts, session_end_ts)

        await page.evaluate(
            """(p) => {
                window.chartViewer.socket.emit('join_instrument', { pair: p.pair });
                window.chartViewer.socket.emit('start_stream', { pair: p.pair, timeframe: p.tf, fromTime: p.start, stopAt: p.end, paceBps: p.pace });
            }""",
            {"pair": pair_name, "tf": "5m", "start": start_ts, "end": stream_stop_at,
             "pace": args.bars_per_second},
        )

        # Poll for stream end, showing ingest progress (the page's lastTime
        # tracks how far the replay has been consumed). One bar covers the
        # whole process: streaming owns [0, stream_share], snapshots the rest.
        stream_span = max(1, stream_stop_at - start_ts)
        stream_share = 50.0 if args.snapshot else 100.0
        stream_deadline = time.monotonic() + 600
        stream_ok = False
        while time.monotonic() < stream_deadline:
            try:
                if await page.evaluate("() => window.__done === true"):
                    stream_ok = True
                    break
                cur = await page.evaluate(
                    "() => (window.chartViewer && isFinite(window.chartViewer.lastTime))"
                    " ? window.chartViewer.lastTime : null"
                )
            except Exception as e:
                print(f"\n❌ Stream wait failed: {e}")
                break
            if cur:
                label = datetime.fromtimestamp(cur, tz=pair_tz).strftime("%Y-%m-%d %H:%M")
                _progress_line(min(cur, stream_stop_at) - start_ts, stream_span,
                               f"streaming {label}", 0.0, stream_share)
            await asyncio.sleep(1.0)
        if stream_ok:
            if not args.snapshot:
                _progress_done("stream complete")
        elif time.monotonic() >= stream_deadline:
            print("\n❌ Timeout waiting for stream end (600s)")

        captured_trades = await page.evaluate("window.__trades")
        captured_closes = await page.evaluate("window.__closes")

        # Build trade pairs
        trade_pairs = []
        trade_ids_seen = set()
        for t in (captured_trades or []):
            tid = str(t.get("trade_id", ""))
            trade_ids_seen.add(tid)
            close = (captured_closes or {}).get(tid)
            trade_pairs.append((t, close))
        for tid, close in (captured_closes or {}).items():
            if tid not in trade_ids_seen:
                trade_pairs.append((None, close))

        # Snapshots
        if args.snapshot:
            try:
                await page.wait_for_function(
                    "() => window.chartViewer.priceSeries.data().length > 0",
                    timeout=5000,
                )
                await page.evaluate(
                    """(range) => {
                        const viewer = window.chartViewer;
                        viewer.chart.timeScale().applyOptions({
                            shiftVisibleRangeOnNewBar: false,
                            rightOffset: 0,
                            fixLeftEdge: false,
                            fixRightEdge: false,
                        });
                        viewer.chart.timeScale().setVisibleRange({ from: range.start, to: range.end });
                    }""",
                    {"start": start_ts, "end": end_ts},
                )
                await page.evaluate(_PAINT_WAIT)
                await page.wait_for_timeout(_PAINT_SETTLE_MS)
                chart_locator = page.locator("#chartContainer")
                if await chart_locator.count() == 0:
                    chart_locator = page.locator("body")
                await chart_locator.wait_for(state="visible", timeout=2000)
                await chart_locator.screenshot(path=str(outdir / f"{overview_label}_5m_overview.png"))

                # Per-trade snapshots — grouped by trade date
                total_snaps = len(captured_trades or []) + 1  # +1 overview
                _progress_line(1, total_snaps, "snapshot overview",
                               stream_share, 100.0)
                for idx, t in enumerate(captured_trades or [], start=1):
                    entry_ts = t.get("entry_time")
                    trade_id = t.get("trade_id")
                    if entry_ts and trade_id:
                        trade_dt = datetime.fromtimestamp(entry_ts, tz=pair_tz)
                        trade_date_label = trade_dt.strftime("%Y-%m-%d")
                        trade_sdir = outdir / trade_date_label
                        trade_sdir.mkdir(parents=True, exist_ok=True)

                        zoom = args.snapshot_zoom
                        await page.evaluate(
                            """(p) => {
                                const viewer = window.chartViewer;
                                if (viewer && viewer.showOnlyTrade) viewer.showOnlyTrade(p.tid);
                                if (window.__extraLines) {
                                    Object.entries(window.__extraLines).forEach(([tid, line]) => {
                                        if (viewer && viewer.priceSeries) {
                                            viewer.priceSeries.removePriceLine(line);
                                        }
                                    });
                                    window.__extraLines = {};
                                }
                                viewer.chart.timeScale().setVisibleRange({ from: p.start, to: p.end });
                            }""",
                            {"tid": trade_id, "start": entry_ts - zoom, "end": entry_ts + zoom},
                        )
                        await page.evaluate(_PAINT_WAIT)
                        await page.wait_for_timeout(_PAINT_SETTLE_MS)
                        await chart_locator.screenshot(
                            path=str(trade_sdir / f"{trade_date_label}_5m_trade_{idx}.png")
                        )
                        _progress_line(idx + 1, total_snaps,
                                       f"snapshot {trade_date_label} #{idx} of {total_snaps - 1}",
                                       stream_share, 100.0)
                _progress_done(f"done — {total_snaps} snapshots")
            except Exception as e:
                if not quiet:
                    print(f"   ⚠️ Snapshot failed: {e}")

        await browser.close()

    # Print summary
    print_summary(trade_pairs, args, pair_tz)

    # HTML report
    if args.html_report:
        html_path = Path(args.outdir) / "report.html"
        html_kwargs = dict(
            summary_results=[
                {
                    "name": f"{pair_name} {date_label}",
                    "status": "PASS",
                    "reason": "",
                    "values": "",
                    "trade": trade_pairs[0][0] if trade_pairs else None,
                    "close": trade_pairs[0][1] if trade_pairs else None,
                    "won": None,
                    "trade_pairs": trade_pairs,
                    "date": args.start,
                    "velocity": None,
                }
            ],
            account=args.account,
            risk=args.risk,
            mode=args.mode,
            output_path=str(html_path),
            nq_pv=2.0,
            fee_per_rt=FinancialCalc.DEFAULT_FEE_PER_RT,
            be_threshold=BE_THRESHOLD,
            risk_pct=args.risk_pct,
        )
        if args.mode == "real_cfd":
            html_kwargs["cfd_spread"] = args.cfd_spread
            html_kwargs["cfd_commission"] = args.commission or args.cfd_commission
        generate_html_report(**html_kwargs)
        print(f"\n📄 HTML report: {html_path.resolve()}")

    if not quiet:
        print("🛑 Terminating Test Server...")
    server_proc.terminate()
    server_proc.join()

    elapsed = time.monotonic() - t0
    print(f"⏱️  Total time: {int(elapsed // 60)}m {int(elapsed % 60):02d}s")


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="TSI Cross Strategy Tester")
    ap.add_argument("--start", required=True, help="Start date (YYYY-MM-DD HH:MM:SS)")
    ap.add_argument("--end", required=True, help="End date (YYYY-MM-DD HH:MM:SS)")
    ap.add_argument("--csv-file", default="csvs/NQ_live.csv", help="CSV data source")
    ap.add_argument("--outdir", default="./tsi_test_out", help="Output directory for snapshots")
    ap.add_argument("--port", type=int, default=5002, help="Flask server port")
    ap.add_argument("--bars-per-second", type=float, default=800,
                    help="Replay pace; also caps the backtest burst rate so the headless browser is not flooded")
    ap.add_argument(
        "--snapshot", action="store_true", default=True, help="Take chart snapshots"
    )
    ap.add_argument(
        "--no-snapshot", dest="snapshot", action="store_false", help="Skip chart snapshots"
    )
    ap.add_argument(
        "--mode",
        choices=["sim", "real_futures", "real_cfd", "both"],
        default="real_futures",
        help="PnL mode",
    )
    ap.add_argument("--rr", type=float, default=5.0, help="Risk:Reward ratio")
    ap.add_argument("--risk", type=float, default=1000.0, help="Fixed $ risk per trade")
    ap.add_argument("--risk-pct", type=float, default=None, help="Risk %% of balance")
    ap.add_argument("--account", type=float, default=100_000.0, help="Simulated account size")
    ap.add_argument("--cfd-spread", type=float, default=1.5, help="CFD spread in points")
    ap.add_argument("--cfd-commission", type=float, default=5.0,
                    help="CFD commission per round-trip lot in USD")
    ap.add_argument("--commission", type=float, default=None,
                    help="Override round-trip commission per lot/contract for ANY mode")
    ap.add_argument("--session-end", type=str, default="16:58", help="Session end time")
    ap.add_argument("--session-tz", type=str, default="America/New_York", help="Session timezone")
    ap.add_argument("--html-report", action="store_true", default=False, help="Generate HTML report")
    ap.add_argument("--quiet", action="store_true", default=False, help="Suppress verbose output")

    # Snapshot zoom: seconds before/after trade entry to show (default 1 hour = 3600)
    ap.add_argument("--snapshot-zoom", type=int, default=3600,
                    help="Per-trade snapshot zoom in seconds before/after entry (default: 3600)")

    # TSI-specific toggles
    ap.add_argument("--fixed-stop-loss", type=float, default=None, help="Fixed SL distance in points")
    ap.add_argument("--time-range-start", type=str, default="08:00", help="Session start filter (HH:MM)")
    ap.add_argument("--time-range-end", type=str, default="15:30", help="Session end filter (HH:MM)")
    ap.add_argument("--daily-trades-limit", type=int, default=5, help="Max trades per day")
    ap.add_argument(
        "--cross-confirmation-bars",
        type=int,
        default=3,
        help="Min consecutive bars TSI must be on the 'from' side before a cross (default: 3)",
    )

    args = ap.parse_args()

    asyncio.run(run_test(args))


if __name__ == "__main__":
    main()
