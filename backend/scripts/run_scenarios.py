#!/usr/bin/env python3
"""
High-Performance Scenario Runner with Dependency Injection.
"""

import re
import json
import argparse
import asyncio
import csv
import logging
import sys
import tempfile
import time
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from multiprocessing import Process, Event

import yaml
import requests
from collections import defaultdict
from datetime import time as dtime
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

# Use unified BE threshold from FinancialCalc
BE_THRESHOLD = FinancialCalc.DEFAULT_BE_THRESHOLD_R  # Threshold for considering a trade as breakeven (in R)
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from src.strategies.liquidity_v2.prod_config import (
    get_prod_strategy_numbers,
    get_prod_candle_config,
    get_prod_strategy_options,
)
from src.strategies.liquidity_v2.base_strategy import LineRemovalMode
from tests.fakes import FakeLineRepository, FakeTradeRepository
from src.infrastructure.repositories.lines_repository import SQLLineRepository
from src.infrastructure.repositories.trades_repository import SQLTradeRepository
from src.infrastructure.database import database
from scripts.html_report import generate_html_report
from scripts.mode_pnl import per_trade_sim, per_trade_futures, per_trade_cfd
from scripts.report_utils import flatten_trade_records

APP_HOST = "127.0.0.1"

logger = logging.getLogger(__name__)

# --- TIMEZONE CONFIGURATION ---
PAIR_TZS = {
    'EURUSD': 'Europe/London',
    'MNQ':     'Etc/GMT+5',   # fixed UTC-5, matches TradingView "UTC-5" (no DST shift)
    'ES':     'Etc/GMT+5',
}

# -------------------------------------------------------------------------
# Data Verification Helper
# -------------------------------------------------------------------------
def verify_csv_data(csv_path: Path, pair: str, start_ts: int, end_ts: int):
    print(f"🔍 Verifying data in {csv_path.name}...")
    print(f"   Requested Range: {start_ts} -> {end_ts}")
    
    csv_tz_name = 'America/Chicago' if pair in ('MNQ', 'ES') else 'UTC'
    csv_tz = ZoneInfo(csv_tz_name)
    utc = ZoneInfo("UTC")
    
    count = 0
    last_ts = 0
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            sample = f.read(1024); f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
            reader = csv.DictReader(f, dialect=dialect)
            for row in reader:
                try:
                    ts_str = f"{row['Date']} {row['Time']}"
                    try: dt = datetime.strptime(ts_str, '%d/%m/%Y %H:%M:%S')
                    except: dt = dtparser.parse(ts_str)
                    dt = dt.replace(tzinfo=csv_tz)
                    row_ts = int(dt.astimezone(utc).timestamp())
                    
                    if row_ts > last_ts: last_ts = row_ts
                    
                    if start_ts <= row_ts <= end_ts:
                        count += 1
                except Exception as e:
                    logger.warning(f"[ScenarioLoader] Skipping scenario due to error: {e}")
                    continue
                
        print(f"   ✅ Found {count} bars in requested range.")
        if count == 0:
            print("   ⚠️  WARNING: ZERO bars found!")
            print(f"       Last bar in CSV is at: {last_ts} ({datetime.fromtimestamp(last_ts, tz=utc)})")
            if end_ts > last_ts:
                print(f"       Requested End {end_ts} is AFTER the CSV data ends.")
            else:
                print("       Data might be missing (Weekend/Holiday?).")
                
    except Exception as e:
        print(f"   ❌ Could not verify CSV data: {e}")

# -------------------------------------------------------------------------
# Server Process Logic
# -------------------------------------------------------------------------

def run_test_server(csv_path: str, bars_per_second: float, port: int, ready_event: Event, quiet: bool = False, no_breakeven: bool = False, no_reentry_breakeven: bool = False, broker_mode: str = 'futures', broker_spread: float = 0.0, rr_ratio: float = 5.0, persist: bool = False, risk_per_trade: float = None, risk_pct_per_trade: float = None, account_balance: float = 100000.0, use_fractional_lots: bool = False, fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT, session_end: str = "16:58", session_tz: str = "America/New_York", log_path: Optional[str] = None):
    try:
        _run_test_server_inner(csv_path, bars_per_second, port, ready_event, quiet, no_breakeven, no_reentry_breakeven, broker_mode, broker_spread, rr_ratio, persist, risk_per_trade, risk_pct_per_trade, account_balance, use_fractional_lots, fee_per_rt, session_end, session_tz, log_path)
    except Exception as e:
        import traceback
        sys.stderr.write(f"\n❌ Server process crashed: {e}\n")
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()

def _run_test_server_inner(csv_path: str, bars_per_second: float, port: int, ready_event: Event, quiet: bool = False, no_breakeven: bool = False, no_reentry_breakeven: bool = False, broker_mode: str = 'futures', broker_spread: float = 0.0, rr_ratio: float = 5.0, persist: bool = False, risk_per_trade: float = None, risk_pct_per_trade: float = None, account_balance: float = 100000.0, use_fractional_lots: bool = False, fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT, session_end: str = "16:58", session_tz: str = "America/New_York", log_path: Optional[str] = None):
    if log_path:
        # Redirect both stdout and stderr to the log file so crash traces are captured.
        log_file = open(log_path, "w", encoding="utf-8")
        os.dup2(log_file.fileno(), sys.stdout.fileno())
        os.dup2(log_file.fileno(), sys.stderr.fileno())
    if quiet and not log_path:
        sys.stdout = open(os.devnull, 'w')
        import logging
        logging.disable(logging.CRITICAL)

    # Use real SQL repositories if persist flag is set
    if persist:
        database.setup_database()
        trade_repo = SQLTradeRepository()
        trade_repo.clear()  # Clear stale trades from previous runs
        repos = Repositories(
            lines=SQLLineRepository(),
            trades=trade_repo
        )
    else:
        repos = Repositories(
            lines=FakeLineRepository(),
            trades=FakeTradeRepository()
        )

    ds = CSVDataSource(
        pair="MNQ",
        filename=csv_path,
        initial_start_time=0,
        initial_end_time=9999999999,
        bars_per_second=bars_per_second,
    )

    numbers = get_prod_strategy_numbers(rr_ratio=rr_ratio, risk_per_trade=risk_per_trade, risk_pct_per_trade=risk_pct_per_trade)
    # Override account_balance with the value from command line
    from dataclasses import replace
    numbers = replace(numbers, account_balance=account_balance)
    candle_config = get_prod_candle_config()
    options = get_prod_strategy_options(
        numbers.max_bounce,
        numbers.min_cross_depth,
        skip_rollover_days=False,
        reentry_only=False,
        line_removal_mode=LineRemovalMode.ON_EVALUATE,
        max_reentry_attempts=DEFAULT_STRATEGY_OPTIONS.max_reentry_attempts,
    )

    if no_breakeven:
        options.breakeven = None
    if no_reentry_breakeven:
        options.reentry_breakeven = None

    wiring = create_app(
        pair="MNQ",
        data_source=ds,
        repos=repos,
        numbers=numbers,
        options=options,
        candle_config=candle_config,
        timeframes=["3m", "5m", "15m", "30m", "1h"],
        bootstrap_existing_lines=False,
        broker_mode=broker_mode,
        broker_spread=broker_spread,
        use_fractional_lots=use_fractional_lots,
        fee_per_rt=fee_per_rt,
        session_end_time=session_end,
        session_tz=session_tz,
    )

    ready_event.set()

    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    wiring.socketio.run(
        wiring.app, 
        host=APP_HOST, 
        port=port, 
        debug=False, 
        use_reloader=False, 
        allow_unsafe_werkzeug=True
    )

# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------

def sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in ("-","_"," ") else "_" for c in name).strip().replace(" ", "_")

def _parse_yaml_dt(s: str) -> datetime:
    return dtparser.parse(s)

def parse_line_spec(line_row: Any) -> Dict[str, Any]:
    if isinstance(line_row, dict):
        return {
            "id": line_row.get("id"),
            "direction": line_row.get("direction", "long"),
            "level": float(line_row.get("price") or line_row.get("level", 0)),
            "at_raw": line_row.get("at")
        }

    flat = list(line_row)
    lid, at_raw = None, None

    for i, x in enumerate(list(flat)):
        if isinstance(x, str) and x.upper().startswith("L") and len(x) < 10:
            lid = flat.pop(i); break
    
    for i, x in enumerate(list(flat)):
        if isinstance(x, str) and (x.startswith("row:") or x[0].isdigit()):
            at_raw = flat.pop(i); break

    direction, level = "long", 0.0
    if len(flat) == 2:
        a, b = flat
        if isinstance(a, str): direction, level = a, float(b)
        else: direction, level = b, float(a)
    elif len(flat) == 1:
        level = float(flat[0])
    
    return {"id": lid, "direction": direction.lower(), "level": level, "at_raw": at_raw}

def wait_http_ok(url, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(url, timeout=1).ok: return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Server at {url} did not start.")

def add_line_http(base_url: str, pair: str, price: float, creation_time: float = None):
    payload = {"pair": pair, "price": float(price)}
    if creation_time is not None:
        payload["creation_time"] = creation_time

    try:
        r = requests.post(f"{base_url}/api/lines", json=payload, timeout=5)
        return r.ok
    except Exception as e:
        print(f"⚠️ Exception adding line {price}: {e}")
        return False

def reset_app_state(base_url: str, start=None, end=None, seed_lines=None, max_retries: int = 3):
    payload = {}
    if start: payload['start_time'] = start
    if end:   payload['end_time'] = end
    if seed_lines: payload['seed_lines'] = seed_lines
    for attempt in range(max_retries):
        try:
            r = requests.post(f"{base_url}/__reset_all", json=payload, timeout=10)
            if r.ok:
                return True
        except Exception:
            pass
        time.sleep(0.2 * (attempt + 1))
    return False

def _check_trade(label: str, expect: Dict, trade: Dict, tol: float) -> List[str]:
    """Check a single trade against expected values. Returns list of error strings."""
    checks = {
        "entry": ["entry", "entry_price"],
        "sl":    ["orig_sl", "stop_loss", "stopLoss", "sl"],
        "tp":    ["take_profit", "takeProfit", "tp"]
    }
    errors = []
    prefix = f"{label} " if label else ""
    for yaml_key, trade_keys in checks.items():
        if yaml_key in expect:
            target = float(expect[yaml_key])
            actual = None
            for k in trade_keys:
                if k in trade:
                    actual = trade[k]
                    break
            if actual is None:
                errors.append(f"{prefix}{yaml_key} missing")
                continue
            if abs(actual - target) > tol:
                errors.append(f"{prefix}{yaml_key}: got {actual}, want {target}")
    return errors


def check_expectations(expect: Dict, trades: List[Dict]) -> Tuple[str, str, str]:
    if expect and expect.get("none") is True:
        if not trades:
            return "PASS", "Correctly had no trades", ""
        else:
            t = trades[0]
            entry = t.get("entry") or t.get("entry_price")
            return "FAIL", f"Expected NO trades, but got {len(trades)}", f"(Got trade @ {entry})"

    if not trades:
        return "FAIL", "No trades opened", ""

    trade = trades[0]
    entry = trade.get("entry") or trade.get("entry_price")
    orig_sl = trade.get("orig_sl") or trade.get("stop_loss")

    values_str = f"(Entry: {entry}, Orig SL: {orig_sl}, TP: {trade.get('take_profit')})"

    if not expect:
        return "PASS", "Matches expectations", values_str

    tol = 0.25
    errors = _check_trade("", expect, trade, tol)

    # Check re-entry trade if expected
    if "reentry" in expect:
        if len(trades) < 2:
            errors.append("reentry expected but only 1 trade")
        else:
            errors += _check_trade("reentry", expect["reentry"], trades[1], tol)

    if errors:
        return "FAIL", ", ".join(errors), values_str

    return "PASS", "Matches expectations", values_str


def compute_trade_pnl(trade):
    """Returns (direction, pnl_pts, risk_pts, pnl_pct, r_multiple) assuming TP is hit."""
    if not trade:
        return None
    entry = trade.get("entry") or trade.get("entry_price")
    sl    = trade.get("orig_sl") or trade.get("stop_loss")
    tp    = trade.get("take_profit")
    if entry is None or sl is None or tp is None:
        return None
    entry, sl, tp = float(entry), float(sl), float(tp)
    direction = "short" if sl > entry else "long"
    pnl_pts   = (tp - entry) if direction == "long" else (entry - tp)
    risk_pts  = abs(entry - sl)
    pnl_pct   = pnl_pts / entry * 100
    r_multiple = pnl_pts / risk_pts if risk_pts > 0 else 0.0
    return direction, pnl_pts, risk_pts, pnl_pct, r_multiple

# -------------------------------------------------------------------------
# Log Printer
# -------------------------------------------------------------------------

def print_detailed_summary(logs: List[Dict], pair_tz: ZoneInfo):
    # ANSI colours
    RST   = '\033[0m';  BOLD  = '\033[1m'
    GREEN = '\033[92m'; RED   = '\033[91m'; YELLOW = '\033[93m'
    CYAN  = '\033[96m'; BLUE  = '\033[94m'; GRAY   = '\033[90m'
    WHITE = '\033[97m'
    W = 108  # line width

    def ts_str(ts):
        return datetime.fromtimestamp(ts, tz=pair_tz).strftime("%m-%d %H:%M:%S")

    def row(t, tf, evt, det, color="", bold=False):
        b = BOLD if bold else ""
        print(f"   {color}{b}{t:<14}  {(tf or '--'):<3}  {evt:<15}  {det}{RST}")

    if not logs:
        print("   ℹ️  No decision logs recorded.")
        return

    # Group entries by line_id, preserving insertion order
    from collections import defaultdict
    lines_map: Dict[str, List[Dict]] = defaultdict(list)
    for entry in logs:
        lines_map[entry.get("line_id", "?")].append(entry)
    line_order = list(dict.fromkeys(e.get("line_id", "?") for e in logs))

    print(f"\n   {BOLD}{'═' * W}{RST}")
    print(f"   {BOLD}  SCENARIO DECISION LOG{RST}")
    print(f"   {BOLD}{'═' * W}{RST}\n")

    entry_count = 0
    filter_block_count = 0
    last_event = None

    for lid in line_order:
        entries = lines_map[lid]
        print(f"   {CYAN}{BOLD}── LINE: {lid}  {'─' * (W - 10)}{RST}")

        i = 0
        while i < len(entries):
            e = entries[i]
            t   = ts_str(e.get("time", 0))
            tf  = e.get("tf") or "--"
            evt = e.get("event", "")
            det = e.get("details", "")
            last_event = e

            # Collapse consecutive TSI_CHECK (verbose, collapse all but first+last)
            if evt == "TSI_CHECK":
                j = i + 1
                while j < len(entries) and entries[j].get("event") == "TSI_CHECK":
                    j += 1
                skipped = j - i - 1
                row(t, tf, evt, det, GRAY)
                if skipped:
                    last_det = entries[j - 1].get("details", "")
                    last_t   = ts_str(entries[j - 1].get("time", 0))
                    print(f"   {GRAY}               ... ({skipped} more, no cross)"
                          f"  last @ {last_t}: {last_det}{RST}")
                i = j
                continue

            # Collapse consecutive VAT_REGIME with the same regime label
            if evt == "VAT_REGIME":
                def _regime(d):
                    return d.split("→")[1].split("|")[0].strip() if "→" in d else d
                cur_regime = _regime(det)
                j = i + 1
                while j < len(entries) and entries[j].get("event") == "VAT_REGIME" \
                        and _regime(entries[j].get("details", "")) == cur_regime:
                    j += 1
                skipped = j - i - 1
                row(t, tf, evt, det, BLUE)
                if skipped:
                    print(f"   {BLUE}               ... ({skipped} more bars, same regime){RST}")
                i = j
                continue

            # Key milestone events
            if evt == "ENTRY":
                entry_count += 1
                print(f"   {GREEN}{BOLD}{'─' * W}{RST}")
                print(f"   {GREEN}{BOLD}  ✅ ENTRY   {t}  {tf:<3}  {det}{RST}")
                print(f"   {GREEN}{BOLD}{'─' * W}{RST}")

            elif evt == "FILTER_BLOCK":
                filter_block_count += 1
                print(f"   {YELLOW}{BOLD}  ⚠  FILTER_BLOCK  {t}  {tf:<3}  {det}{RST}")

            elif evt in ("REMOVE", "TSI_INVALID", "REENTRY_CANCEL"):
                row(t, tf, evt, det, RED)

            elif evt == "REENTRY_WATCH":
                row(t, tf, evt, det, YELLOW, bold=True)

            elif evt in ("VAT_CROSS_1", "VAT_RESET", "VAT_CROSS_2",
                         "TSI_CROSS", "TSI_RESCUE", "TSI_SWEEP", "TSI_FAST"):
                row(t, tf, evt, det, CYAN, bold=True)

            elif "FAIL" in evt:
                row(t, tf, evt, det, GRAY)

            else:
                row(t, tf, evt, det, WHITE)

            i += 1

        print()

    # Outcome summary
    print(f"   {BOLD}{'═' * W}{RST}")
    if entry_count > 0:
        print(f"   {GREEN}{BOLD}  OUTCOME: {entry_count} trade(s) opened{RST}")
    else:
        if filter_block_count > 0:
            reason = f"trigger fired {filter_block_count}x but blocked by filters every time"
        elif last_event:
            reason = (f"no trigger fired — last event: "
                      f"{last_event.get('event')} | {last_event.get('details', '')}")
        else:
            reason = "no events recorded"
        print(f"   {RED}{BOLD}  OUTCOME: NO TRADE | {reason}{RST}")
    print(f"   {BOLD}{'═' * W}{RST}\n")

# -------------------------------------------------------------------------
# Test Runner
# -------------------------------------------------------------------------

def _server_args(args, csv_path: Path, port: int, server_ready: Event, log_path: Optional[str] = None) -> tuple:
    """Build positional args for run_test_server."""
    no_breakeven = args.no_breakeven
    no_reentry_breakeven = args.no_reentry_breakeven
    mode = args.mode
    broker_mode = 'cfd' if mode in ('real_cfd',) else 'futures'
    broker_spread = args.cfd_spread if broker_mode == 'cfd' else 0.0
    use_fractional_lots = mode in ('real_cfd',)
    fee_per_rt = args.commission
    if fee_per_rt is None:
        fee_per_rt = args.cfd_commission if mode == 'real_cfd' else FinancialCalc.DEFAULT_FEE_PER_RT
    risk_per_trade = None if args.risk_pct is not None else args.risk
    session_end = args.session_end
    session_tz = args.session_tz
    return (
        str(csv_path.resolve()), args.bars_per_second, port, server_ready,
        args.quiet, no_breakeven, no_reentry_breakeven,
        broker_mode, broker_spread, args.rr, args.persist, risk_per_trade,
        args.risk_pct, args.account, use_fractional_lots,
        fee_per_rt, session_end, session_tz, log_path,
    )


def _start_test_server(args, csv_path: Path, port: int) -> tuple[Process, Event, Path]:
    """Start the in-memory test server and return (process, ready_event, log_path)."""
    server_ready = Event()
    log_path = Path(tempfile.mkstemp(suffix=".log", prefix="run_scenarios_server_")[1])
    proc = Process(
        target=run_test_server,
        args=_server_args(args, csv_path, port, server_ready, str(log_path)),
    )
    proc.start()
    return proc, server_ready, log_path


def _stop_server(proc: Process, log_path: Path) -> None:
    """Terminate server process and dump its log on failure."""
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
    if proc.is_alive():
        proc.kill()
        proc.join(timeout=5)
    if log_path.exists():
        log_path.unlink(missing_ok=True)


def _ensure_server_running(
    base_url: str,
    args,
    csv_path: Path,
    port: int,
    quiet: bool,
    server_proc: Process,
    server_ready: Event,
    log_path: Path,
) -> tuple[Process, Event, Path]:
    """Check server health; restart it if it has crashed or is unresponsive."""
    try:
        r = requests.get(f"{base_url}/api/pair", timeout=5)
        if r.ok:
            return server_proc, server_ready, log_path
    except Exception:
        pass

    if not quiet:
        print("\n🔄 Server unresponsive; restarting...")

    # Dump last crash log for diagnostics before removing it.
    if log_path.exists():
        try:
            crash_log = log_path.read_text(encoding="utf-8", errors="replace").strip()
            if crash_log and not quiet:
                last_lines = "\n".join(crash_log.splitlines()[-20:])
                print(f"   Last server log lines:\n{last_lines}")
        except Exception:
            pass

    _stop_server(server_proc, log_path)
    return _start_test_server(args, csv_path, port)


async def run_suite(args, scenarios: List[Dict], csv_path: Path):
    quiet = args.quiet
    if not quiet:
        print(f"🚀 Launching In-Memory Test Server with {csv_path}...")

    base_url = f"http://{APP_HOST}:{args.port}"

    server_proc, server_ready, log_path = _start_test_server(args, csv_path, args.port)

    if not server_ready.wait(timeout=30):
        print("❌ Server failed to start within timeout.")
        _stop_server(server_proc, log_path)
        return

    try:
        wait_http_ok(f"{base_url}/api/pair")
    except TimeoutError:
        print("❌ Server process started but HTTP not reachable.")
        _stop_server(server_proc, log_path)
        return

    pair_resp = requests.get(f"{base_url}/api/pair", timeout=10).json()
    pair_name = pair_resp['pair']
    pair_tz = ZoneInfo(PAIR_TZS.get(pair_name, 'UTC'))
    if not quiet:
        print(f"✅ Test Server running ({pair_name}) at {base_url}. Timezone: {pair_tz}")

    def get_epoch(dt_str):
        dt = dtparser.parse(dt_str)
        # Strip any existing timezone suffix (e.g. Z) and treat the wall-clock time
        # as belonging to the pair's local timezone.
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        dt = dt.replace(tzinfo=pair_tz)
        return int(dt.timestamp())

    try:
        summary_results = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
            page = await ctx.new_page()

            if not quiet:
                page.on("console", lambda msg: print(f"   [BROWSER] {msg.text}"))
            page.on("pageerror", lambda exc: print(f"   [BROWSER ERROR] {exc}"))

            total = len(scenarios)
            for i, sc in enumerate(scenarios):
                name = sc.get("name", f"scenario_{i}")
                pct = int((i / total) * 100)
                bar_len = 30
                filled = int(bar_len * i / total)
                bar = "█" * filled + "░" * (bar_len - filled)
                print(f"\r  {bar} {pct:3d}% ({i}/{total}) {name:<40}", end="", flush=True)

                # Make sure the backend server is still alive before each scenario.
                server_proc, server_ready, log_path = _ensure_server_running(
                    base_url, args, csv_path, args.port, quiet,
                    server_proc, server_ready, log_path,
                )
                
                pair_name_val = sc.get("pair", "unknown")
                date_label = dtparser.parse(sc["start"]).strftime("%Y-%m-%d")
                sdir = Path(args.outdir) / pair_name_val
                # With --group all (or no group), keep snapshots under each
                # scenario's own group subdir to avoid same-date collisions.
                snap_group = args.group
                if snap_group in (None, "all"):
                    snap_group = sc.get("_group")
                if snap_group:
                    sdir = sdir / snap_group
                sdir = sdir / date_label
                sdir.mkdir(parents=True, exist_ok=True)
                if args.snapshot:
                    # Clean up previous snapshots for this scenario
                    for old_png in sdir.glob("*.png"):
                        old_png.unlink()
                
                start_ts = get_epoch(sc["start"])
                end_ts   = get_epoch(sc["end"])
                tf       = sc.get("tf", "5m")

                if not quiet:
                    verify_csv_data(csv_path, pair_name, start_ts, end_ts)
                    print(f"   [DEBUG] Scenario Start: {start_ts} | End: {end_ts}")

                # Pre-seed 2 hours of warmup so 5m/15m TSI is fully warmed up by start.
                # If scenario lines are created earlier than that, extend the warmup so
                # their full lifetime (and any pre-session removal by filters) is replayed.
                WARMUP_SECONDS = 2 * 3600
                warmup_start_ts = start_ts - WARMUP_SECONDS

                # Parse scenario lines and prepare seed lines so they are present during warmup
                lines = [parse_line_spec(l) for l in sc.get("lines", [])]
                seed_lines = []
                earliest_line_ts = None
                for i, l in enumerate(lines):
                    c_ts = 0.0
                    if l["at_raw"]:
                        c_ts = get_epoch(l["at_raw"])
                        if earliest_line_ts is None or c_ts < earliest_line_ts:
                            earliest_line_ts = c_ts
                    seed_lines.append({
                        "id": f"sc_line_{i}",
                        "price": float(l["level"]),
                        "creation_ts": c_ts,
                    })

                if earliest_line_ts is not None:
                    warmup_start_ts = min(warmup_start_ts, earliest_line_ts - WARMUP_SECONDS)

                if not reset_app_state(base_url, start=warmup_start_ts, end=start_ts, seed_lines=seed_lines):
                    print(f"❌ [{name}] Reset failed")
                    continue

                show_tsi = "true" if sc.get("show_tsi", False) else "false"
                await page.goto(f"{base_url}/?start_time={start_ts}&keep_lines=true&keep_closed_trades=true&tf={tf}&show_tsi={show_tsi}", wait_until="domcontentloaded")

                try:
                    await page.wait_for_function("() => window.__chartReady === true", timeout=10000)
                except Exception as e:
                    if not quiet:
                        print(f"⚠️ Timeout waiting for chart init: {e}")

                await page.evaluate("""
                    window.__done = false;
                    window.__trades = [];
                    window.__closes = {};
                    window.__extraLines = {};  // trade_id -> priceLine

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

                # Extend stream to session end so open trades get closed
                scenario_date = dtparser.parse(sc["start"]).date()
                session_end_time = dtime.fromisoformat(args.session_end)
                session_end_tz = ZoneInfo(args.session_tz)
                session_end_dt = datetime.combine(scenario_date, session_end_time, tzinfo=session_end_tz)
                session_end_ts = int(session_end_dt.timestamp())
                stream_stop_at = max(end_ts, session_end_ts)

                await page.evaluate(
                    """(p) => window.chartViewer.socket.emit('start_stream', { timeframe: p.tf, fromTime: p.start, stopAt: p.end })""",
                    {"tf": tf, "start": start_ts, "end": stream_stop_at}
                )

                try:
                    await page.wait_for_function("() => window.__done === true", timeout=120000)
                except Exception as e:
                    print(f"❌ [{name}] Timeout waiting for stream end: {e}")

                captured_trades = await page.evaluate("window.__trades")
                captured_closes = await page.evaluate("window.__closes")
                # Build (trade, close) pairs for every trade in this scenario
                trade_pairs = []
                trade_ids_seen = set()
                # First, add all trades with their closes
                for t in (captured_trades or []):
                    tid = str(t.get("trade_id", ""))
                    trade_ids_seen.add(tid)
                    close = (captured_closes or {}).get(tid)
                    trade_pairs.append((t, close))
                # Second, add any closes that don't have matching trades
                # (trades that opened before scenario started)
                for tid, close in (captured_closes or {}).items():
                    if tid not in trade_ids_seen:
                        trade_pairs.append((None, close))
                # Net R across all closed trades → determines won/lost for the scenario
                closed_results = [c["result"] for _, c in trade_pairs if c is not None]
                net_result = sum(closed_results) if closed_results else None
                won = (net_result > 0) if net_result is not None else None
                # First trade is still used for expectation checks (YAML expects first trade)
                trade      = captured_trades[0] if captured_trades else None
                close_data = trade_pairs[0][1]  if trade_pairs   else None
                status, reason, values = check_expectations(sc.get("expect"), captured_trades)
                summary_results.append({
                    "name": name,
                    "status": status,
                    "reason": reason,
                    "values": values,
                    "trade": trade,
                    "close": close_data,
                    "won": won,
                    "trade_pairs": trade_pairs,   # all trades for PnL accounting
                    "date": sc["start"],
                    "velocity": None,
                })

                # Always fetch logs to extract velocity
                try:
                    logs = requests.get(f"{base_url}/api/debug/logs", timeout=2).json()
                    for log_entry in logs:
                        if log_entry.get("event") == "VAT_REGIME":
                            details = log_entry.get("details", "")
                            # Parse "vel=3.45 pts/min → MODERATE | ..."
                            m = re.search(r'vel=([\d.]+)\s*pts/min\s*→\s*(\w+)', details)
                            if m:
                                summary_results[-1]["velocity"] = float(m.group(1))
                                summary_results[-1]["velocity_regime"] = m.group(2)
                            break
                    if args.decision_log:
                        print_detailed_summary(logs, pair_tz)
                except Exception as e:
                    if args.decision_log:
                        print(f"   ⚠️ Failed to fetch summary logs: {e}")

                if args.snapshot:
                    try:
                        # 1. Wait for data
                        await page.wait_for_function(
                            "() => window.chartViewer.priceSeries.data().length > 0", 
                            timeout=5000
                        )
                        
                        # 2. Center chart on scenario range
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
                            {"start": start_ts, "end": end_ts}
                        )
                        
                        # 3. Buffer for repaint
                        await page.wait_for_timeout(500) 
                        
                        # 4. Capture the chart container specifically
                        chart_locator = page.locator("#chartContainer")
                        
                        if await chart_locator.count() == 0:
                            print("   ⚠️ #chartContainer not found, defaulting to body capture")
                            chart_locator = page.locator("body")
                            
                        await chart_locator.wait_for(state="visible", timeout=2000)
                        await chart_locator.screenshot(path=str(sdir / f"{date_label}_{tf}.png"))
                        
                        # 5. Zoomed-in snapshot(s)
                        # Prefer reentry (2nd trade) if present, otherwise first trade
                        zoom_targets = []
                        if len(captured_trades) >= 2:
                            zoom_targets.append((captured_trades[1], "reentry"))
                        if captured_trades:
                            zoom_targets.append((captured_trades[0], "entry"))
                        
                        for zt_trade, zt_label in zoom_targets:
                            entry_ts = zt_trade.get("entry_time")
                            trade_id = zt_trade.get("trade_id")
                            if entry_ts:
                                # Show only this trade's lines (entry/SL/TP)
                                if trade_id:
                                    await page.evaluate(
                                        """(tid) => { if (window.chartViewer && window.chartViewer.showOnlyTrade) window.chartViewer.showOnlyTrade(tid); }""",
                                        trade_id
                                    )
                                    await page.wait_for_timeout(300)
                                # Remove all injected Orig SL lines from zoomed snapshots
                                # (showOnlyTrade now draws the original SL itself)
                                await page.evaluate("""() => {
                                    if (window.__extraLines) {
                                        Object.entries(window.__extraLines).forEach(([tid, line]) => {
                                            if (window.chartViewer && window.chartViewer.priceSeries) {
                                                window.chartViewer.priceSeries.removePriceLine(line);
                                            }
                                        });
                                        window.__extraLines = {};
                                    }
                                }""")
                                await page.wait_for_timeout(200)
                                await page.evaluate(
                                    """(range) => { window.chartViewer.chart.timeScale().setVisibleRange({ from: range.start, to: range.end }); }""",
                                    {"start": entry_ts - 3600, "end": entry_ts + 3600}
                                )
                                await page.wait_for_timeout(500)
                                await chart_locator.screenshot(path=str(sdir / f"{date_label}_{tf}_{zt_label}.png"))
                        

                    except Exception as e:
                        if not quiet:
                            print(f"   ⚠️ Snapshot failed: {e}")

                # Brief cooldown so background threads/ZMQ sockets can settle before reset.
                await asyncio.sleep(0.2)

            print(f"\r  {'█' * 30} 100% ({total}/{total}){' ' * 50}")
            await browser.close()

    finally:
        if not quiet:
            print("🛑 Terminating Test Server...")
        _stop_server(server_proc, log_path)

    # ── ANSI helpers ──────────────────────────────────────────────────────────
    RST    = '\033[0m';  BOLD   = '\033[1m'
    GREEN  = '\033[92m'; RED    = '\033[91m'
    YELLOW = '\033[93m'; GRAY   = '\033[90m'
    CYAN   = '\033[96m'; WHITE  = '\033[97m'
    BLUE   = '\033[94m'
    ACCT         = args.account   # simulated starting balance (configurable via --account)
    RISK_PCT     = args.risk_pct  # percentage risk per trade (e.g. 1.0 = 1%)
    RISK_USD_FIX = args.risk      # fixed risk per trade in USD (configurable via --risk)
    NQ_PV        = 2.0       # $ per point, MNQ micro contract
    FEE_PER_RT   = FinancialCalc.DEFAULT_FEE_PER_RT
    # BE_THRESHOLD is now imported from FinancialCalc (unified single source of truth)

    def get_risk(balance):
        """Return risk amount in USD — either fixed or percentage of current balance."""
        if RISK_PCT is not None:
            return balance * RISK_PCT / 100.0
        return RISK_USD_FIX

    _ANSI = re.compile(r'\033\[[0-9;]*m')
    def _vis(s):      return len(_ANSI.sub('', s))
    def _ljust(s, w): return s + ' ' * max(0, w - _vis(s))
    def _center(s, w):
        pad = max(0, w - _vis(s))
        return ' ' * (pad // 2) + s + ' ' * (pad - pad // 2)

    def _col(val, txt):
        """Colour text green if val > 0, red if < 0, gray if 0 / None."""
        if val is None:  return f"{GRAY}{txt}{RST}"
        if val > 0:      return f"{GREEN}{BOLD}{txt}{RST}"
        if val < 0:      return f"{RED}{BOLD}{txt}{RST}"
        return txt

    def _calc_max_drawdown(balances):
        """Calculate max drawdown from a list of balance values.
        Returns (max_dd_usd, max_dd_pct, max_dd_from_start_usd, max_dd_from_start_pct)."""
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

    # pnl_fn parameter in _print_results is unused; keep a stub for compatibility
    _unused_pnl_fn = lambda r, balance=None: None

    def _print_results(mode_label, pnl_fn, per_trade_fn):
        """
        per_trade_fn(trade, close) → (usd, pct, actual_r, commission)
        Used so that W/L bucketing counts each individual trade, not the net scenario outcome.
        """
        # ── Aggregate by day / week / month (per individual trade) ─────────────
        def _new_bucket():
            return {"usd": 0.0, "pct": 0.0, "commission": 0.0, "wins": 0, "losses": 0, "be": 0, "sp": 0, "open": 0,
                    "reentry_win": 0, "reentry_loss": 0, "reentry_be": 0, "all_passed": True,
                    "velocity": None, "start_balance": None, "trades": 0,
                    "entry_sl_sum": 0.0, "entry_sl_count": 0,
                    "entry_risk_sum": 0.0, "entry_risk_count": 0,
                    "reentry_sl_sum": 0.0, "reentry_sl_count": 0,
                    "reentry_risk_sum": 0.0, "reentry_risk_count": 0,
                    "contracts": []}

        daily   = defaultdict(_new_bucket)
        weekly  = defaultdict(_new_bucket)
        monthly = defaultdict(_new_bucket)

        # Order-independent per-day pass: scenario status and velocity.
        for r in summary_results:
            scenario_date = dtparser.parse(r["date"]).date()
            if r["status"] != "PASS":
                daily[str(scenario_date)]["all_passed"] = False
            if r.get("velocity") is not None:
                daily[str(scenario_date)]["velocity"] = r["velocity"]

        # Chronological per-trade pass: % risk compounding follows the running
        # balance in true time order, even when merged groups interleave.
        running_balance = ACCT

        for rec in flatten_trade_records(summary_results):
            r = rec["result"]
            trade, close = rec["trade"], rec["close"]
            scenario_date = dtparser.parse(r["date"]).date()
            # Use trade exit time for bucketing (not scenario date)
            if close is not None and close.get("exit_time"):
                exit_dt = datetime.fromtimestamp(close["exit_time"], tz=pair_tz)
                d_key = str(exit_dt.date())
                iso = exit_dt.isocalendar()
                w_key = f"{iso.year}-W{iso.week:02d}"
                m_key = exit_dt.strftime("%Y-%m")
            else:
                # Fallback to scenario date for open trades
                d_key = str(scenario_date)
                iso = scenario_date.isocalendar()
                w_key = f"{iso.year}-W{iso.week:02d}"
                m_key = scenario_date.strftime("%Y-%m")

            for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                if bucket[key]["start_balance"] is None:
                    bucket[key]["start_balance"] = running_balance

            # Track SL points and exact risk dollars for entry/reentry
            if trade:
                sl_pts = trade.get("risk")
                risk_dollars = trade.get("risk_dollars")
                is_reentry_trade = trade.get("is_reentry", False)
                if sl_pts is not None and risk_dollars is not None:
                    for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                        if is_reentry_trade:
                            bucket[key]["reentry_sl_sum"] += sl_pts
                            bucket[key]["reentry_sl_count"] += 1
                            bucket[key]["reentry_risk_sum"] += risk_dollars
                            bucket[key]["reentry_risk_count"] += 1
                        else:
                            bucket[key]["entry_sl_sum"] += sl_pts
                            bucket[key]["entry_sl_count"] += 1
                            bucket[key]["entry_risk_sum"] += risk_dollars
                            bucket[key]["entry_risk_count"] += 1

            if close is None:
                for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                    bucket[key]["open"] += 1
                continue
            result_type = close.get("result_type", None)
            t_usd, t_pct, actual_r, t_comm = per_trade_fn(trade, close, running_balance)
            if t_usd is not None:
                running_balance += t_usd
            is_reentry = trade.get("is_reentry", False) if trade else False

            # Track contracts used for this trade
            trade_contracts = trade.get("contracts") if trade else None
            if trade_contracts is not None:
                for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                    bucket[key]["contracts"].append(trade_contracts)

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
                    if is_reentry:
                        bucket[key]["reentry_be"] += 1
                continue

            # Use unified BE threshold from FinancialCalc
            is_be = FinancialCalc.is_breakeven_by_r(actual_r, BE_THRESHOLD)
            is_win = actual_r >= BE_THRESHOLD
            for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                if t_usd is not None:
                    bucket[key]["usd"] += t_usd
                    bucket[key]["commission"] += t_comm
                if is_be:    bucket[key]["be"]     += 1
                elif is_win: bucket[key]["wins"]   += 1
                else:        bucket[key]["losses"] += 1
                if is_reentry:
                    if is_be:
                        bucket[key]["reentry_be"] += 1
                    elif is_win:
                        bucket[key]["reentry_win"] += 1
                    else:
                        bucket[key]["reentry_loss"] += 1

        CHECKMARK = "\u2713"
        CROSSMARK = "\u2717"

        def _print_agg(title, data, show_passed=False, show_velocity=False, show_sl_risk=False, show_contracts=False):
            if not data:
                return
            has_reentry = any(v["reentry_win"] + v["reentry_loss"] + v["reentry_be"] > 0 for v in data.values())
            has_velocity = show_velocity and any(v.get("velocity") is not None for v in data.values())
            COMM_W = 12
            TRADES_W = 8
            CONTRACTS_W = 14
            WL_W  = 16
            RE_W  = 12
            PAS_W = 8
            VEL_W = 12
            SL_RISK_W = 20
            lbl_w = max(len(k) for k in data) + 2
            sep   = "-" * (lbl_w + 3 + WL_W + 54 + 3 + COMM_W + 3 + TRADES_W + (3 + CONTRACTS_W if show_contracts else 0) + (3 + SL_RISK_W if show_sl_risk else 0) + (3 + RE_W if has_reentry else 0) + (3 + PAS_W if show_passed else 0) + (3 + VEL_W if has_velocity else 0))
            hdr_re = f" | {'RE-ENTRY':^{RE_W}}" if has_reentry else ""
            hdr_pas = f" | {'PASSED':^{PAS_W}}" if show_passed else ""
            hdr_vel = f" | {'VELOCITY':^{VEL_W}}" if has_velocity else ""
            hdr_trades = f" | {'TRADES':^{TRADES_W}}"
            hdr_contracts = f" | {'CONTRACTS':^{CONTRACTS_W}}" if show_contracts else ""
            hdr_sl_risk = f" | {'SL/RISK':^{SL_RISK_W}}" if show_sl_risk else ""
            print(f"\n{BOLD}{CYAN}{title}{RST}")
            print(f"  {'PERIOD':<{lbl_w}} | {'W/L':^{WL_W}} | {'%':>9} | {'$ PnL':>10} | {'$ BALANCE':>11} | {'COMMISSION':>{COMM_W}}{hdr_trades}{hdr_contracts}{hdr_sl_risk}{hdr_re}{hdr_pas}{hdr_vel}")
            print(f"  {sep}")
            balance = ACCT
            for key in sorted(data):
                v        = data[key]
                balance += v["usd"]
                sb = v.get("start_balance")
                if sb is None:
                    sb = ACCT
                v["pct"] = v["usd"] / sb * 100 if sb else 0.0
                parts = []
                if v["wins"]:    parts.append(f"{GREEN}{BOLD}{v['wins']}W{RST}")
                if v["losses"]:  parts.append(f"{RED}{BOLD}{v['losses']}L{RST}")
                if v["be"]:      parts.append(f"{YELLOW}{v['be']}B{RST}")
                if v["sp"]:      parts.append(f"{BLUE}{v['sp']}SP{RST}")
                wl_str  = _center("/".join(parts) if parts else f"{GRAY}-{RST}", WL_W)
                pct_str = _col(v["pct"], f"{v['pct']:>+8.2f}%")
                usd_str = _col(v["usd"], f"${v['usd']:>+9,.0f}")
                bal_str = _col(balance - ACCT, f"${balance:>10,.0f}")
                sl_risk_str = ""
                if show_sl_risk:
                    def _fmt_sl_risk(sl_sum, sl_count, risk_sum, risk_count):
                        if sl_count == 0:
                            return ""
                        sl_avg = sl_sum / sl_count
                        risk_avg = risk_sum / risk_count if risk_count > 0 else 0
                        return f"{sl_avg:.0f}/${risk_avg:.0f}"
                    entry_str = _fmt_sl_risk(v["entry_sl_sum"], v["entry_sl_count"], v["entry_risk_sum"], v["entry_risk_count"])
                    reentry_str = _fmt_sl_risk(v["reentry_sl_sum"], v["reentry_sl_count"], v["reentry_risk_sum"], v["reentry_risk_count"])
                    sl_parts = []
                    if entry_str:
                        sl_parts.append(f"E:{entry_str}")
                    if reentry_str:
                        sl_parts.append(f"R:{reentry_str}")
                    sl_risk_val = " ".join(sl_parts) if sl_parts else f"{GRAY}-{RST}"
                    sl_risk_str = f" | {_center(sl_risk_val, SL_RISK_W)}"
                re_str = ""
                if has_reentry:
                    rw, rl, rb = v["reentry_win"], v["reentry_loss"], v["reentry_be"]
                    if rw + rl + rb > 0:
                        re_parts = []
                        if rw: re_parts.append(f"{GREEN}{rw}W{RST}")
                        if rl: re_parts.append(f"{RED}{rl}L{RST}")
                        if rb: re_parts.append(f"{YELLOW}{rb}B{RST}")
                        re_str = f" | {_center('/'.join(re_parts), RE_W)}"
                    else:
                        re_str = f" | {_center(f'{GRAY}-{RST}', RE_W)}"
                pas_str = ""
                if show_passed:
                    if v.get("all_passed", True):
                        pas_str = f" | {_center(f'{GREEN}{CHECKMARK}{RST}', PAS_W)}"
                    else:
                        pas_str = f" | {_center(f'{RED}{CROSSMARK}{RST}', PAS_W)}"
                vel_str = ""
                if has_velocity:
                    vel_val = v.get("velocity")
                    if vel_val is not None:
                        vel_str = f" | {f'{vel_val:.2f} pts/m':^{VEL_W}}"
                    else:
                        vel_str = f" | {'-':^{VEL_W}}"
                comm_val = v.get("commission", 0.0)
                comm_str = f"${comm_val:>10,.2f}" if comm_val > 0 else f"{'--':>{COMM_W}}"
                trades_val = v["wins"] + v["losses"] + v["be"] + v["sp"] + v["open"]
                trades_str = f"{trades_val:^{TRADES_W}}"
                contracts_str = ""
                if show_contracts:
                    contracts_list = v.get("contracts", [])
                    if contracts_list:
                        contracts_val = " / ".join(f"{c:g}" for c in contracts_list)
                    else:
                        contracts_val = f"{GRAY}-{RST}"
                    contracts_str = f" | {_center(contracts_val, CONTRACTS_W)}"
                print(f"  {key:<{lbl_w}} | {wl_str} | {pct_str} | {usd_str} | {bal_str} | {comm_str} | {trades_str}{contracts_str}{sl_risk_str}{re_str}{pas_str}{vel_str}")
            total_usd = sum(v["usd"]    for v in data.values())
            total_pct = total_usd / ACCT * 100 if ACCT else 0.0
            total_w   = sum(v["wins"]   for v in data.values())
            total_l   = sum(v["losses"] for v in data.values())
            total_be  = sum(v["be"]     for v in data.values())
            total_sp  = sum(v["sp"]     for v in data.values())
            total_comm = sum(v.get("commission", 0.0) for v in data.values())
            total_rw  = sum(v["reentry_win"]  for v in data.values())
            total_rl  = sum(v["reentry_loss"] for v in data.values())
            total_rb  = sum(v["reentry_be"]   for v in data.values())
            total_trades = sum(v["wins"] + v["losses"] + v["be"] + v["sp"] + v["open"] for v in data.values())
            print(f"  {sep}")
            tot_pct  = _col(total_pct, f"{total_pct:>+8.2f}%")
            tot_usd  = _col(total_usd, f"${total_usd:>+9,.0f}")
            tot_bal  = _col(total_usd, f"${ACCT + total_usd:>10,.0f}")
            tot_comm = f"${total_comm:>10,.2f}" if total_comm > 0 else f"{'--':>{COMM_W}}"
            tot_trades = f"{total_trades:^{TRADES_W}}"
            tot_contracts = f" | {'':^{CONTRACTS_W}}" if show_contracts else ""
            tot_parts = []
            if total_w:   tot_parts.append(f"{GREEN}{total_w}W{RST}")
            if total_l:   tot_parts.append(f"{RED}{total_l}L{RST}")
            if total_be:  tot_parts.append(f"{YELLOW}{total_be}B{RST}")
            if total_sp:  tot_parts.append(f"{BLUE}{total_sp}SP{RST}")
            tot_wl = _center("/".join(tot_parts) if tot_parts else f"{GRAY}-{RST}", WL_W)
            tot_re = ""
            if has_reentry:
                tot_re_parts = []
                if total_rw: tot_re_parts.append(f"{GREEN}{total_rw}W{RST}")
                if total_rl: tot_re_parts.append(f"{RED}{total_rl}L{RST}")
                if total_rb: tot_re_parts.append(f"{YELLOW}{total_rb}B{RST}")
                tot_re = f" | {_center('/'.join(tot_re_parts) if tot_re_parts else f'{GRAY}-{RST}', RE_W)}"
            tot_pas = ""
            if show_passed:
                all_ok = all(v.get("all_passed", True) for v in data.values())
                if all_ok:
                    tot_pas = f" | {_center(f'{GREEN}{CHECKMARK}{RST}', PAS_W)}"
                else:
                    failed = sum(1 for v in data.values() if not v.get("all_passed", True))
                    tot_pas = f" | {_center(f'{RED}{failed}{CROSSMARK}{RST}', PAS_W)}"
            tot_sl_risk = f" | {'':^{SL_RISK_W}}" if show_sl_risk else ""
            tot_vel = f" | {'':^{VEL_W}}" if has_velocity else ""
            print(f"  {'TOTAL':<{lbl_w}} | {tot_wl} | {tot_pct} | {tot_usd} | {tot_bal} | {tot_comm} | {tot_trades}{tot_contracts}{tot_sl_risk}{tot_re}{tot_pas}{tot_vel}")

        _print_agg(f"DAILY PnL   — {mode_label}", daily, show_passed=True, show_velocity=True, show_sl_risk=True, show_contracts=True)
        _print_agg(f"WEEKLY PnL  — {mode_label}", weekly)
        _print_agg(f"MONTHLY PnL — {mode_label}", monthly)

        # ── Overall summary (count each individual trade) ─────────────────────
        outcomes = []
        for rec in flatten_trade_records(summary_results):
            r = rec["result"]
            close = rec["close"]
            scenario_date = dtparser.parse(r["date"]).date()
            if close is None:
                continue
            exit_ts = close.get("exit_time")
            if exit_ts:
                date_str = datetime.fromtimestamp(exit_ts, tz=pair_tz).strftime("%Y-%m-%d")
            else:
                date_str = str(scenario_date)
            result_type = close.get("result_type", None)
            if result_type == "SP":
                outcomes.append(("sp", date_str))
            elif result_type == "BE":
                outcomes.append(("be", date_str))
            else:
                actual_r = close.get("result", 0.0)
                if FinancialCalc.is_breakeven_by_r(actual_r, BE_THRESHOLD):
                    outcomes.append(("be", date_str))
                elif actual_r >= BE_THRESHOLD:
                    outcomes.append((True, date_str))
                else:
                    outcomes.append((False, date_str))

        total_t = len(outcomes)
        wins    = sum(1 for o, _ in outcomes if o is True)
        losses  = sum(1 for o, _ in outcomes if o is False)
        bes     = sum(1 for o, _ in outcomes if o == "be")
        sps     = sum(1 for o, _ in outcomes if o == "sp")
        winrate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0

        max_consec_w = max_consec_l = cur_w = cur_l = 0
        max_consec_l_start = max_consec_l_end = ""
        cur_l_start = cur_l_end = ""
        for o, d in outcomes:
            if o is True:
                cur_w += 1; cur_l = 0
            elif o is False:
                if cur_l == 0:
                    cur_l_start = d
                cur_l += 1; cur_w = 0
                cur_l_end = d
            else:
                continue
            max_consec_w = max(max_consec_w, cur_w)
            if cur_l > max_consec_l:
                max_consec_l = cur_l
                max_consec_l_start = cur_l_start
                max_consec_l_end = cur_l_end

        total_usd_all = sum(v["usd"] for v in daily.values())

        # Calculate max drawdown from equity curve
        equity_balances = [ACCT]
        for key in sorted(daily.keys()):
            equity_balances.append(equity_balances[-1] + daily[key]["usd"])
        max_dd_usd, max_dd_pct, max_dd_start_usd, max_dd_start_pct = _calc_max_drawdown(equity_balances)

        # Calculate monthly average profit
        num_months = len(monthly) if monthly else 1
        avg_monthly_pnl = total_usd_all / num_months if num_months > 0 else 0.0
        avg_monthly_pct = (avg_monthly_pnl / ACCT * 100) if ACCT > 0 else 0.0

        print(f"\n{BOLD}{CYAN}OVERALL SUMMARY — {mode_label}{RST}")
        summary_parts = [f"{GREEN}{wins}W{RST}", f"{RED}{losses}L{RST}", f"{YELLOW}{bes}BE{RST}"]
        if sps:
            summary_parts.append(f"{BLUE}{sps}SP{RST}")
        print(f"  Trades  : {total_t}  ({' / '.join(summary_parts)})")
        print(f"  Win Rate: {_col(winrate - 50, f'{winrate:.1f}%')}  (excl. breakevens)")
        print(f"  Max consec. wins  : {GREEN}{BOLD}{max_consec_w}{RST}")
        max_l_period = ""
        if max_consec_l > 0 and max_consec_l_start:
            if max_consec_l_start == max_consec_l_end:
                max_l_period = f" ({max_consec_l_start})"
            else:
                max_l_period = f" ({max_consec_l_start} → {max_consec_l_end})"
        print(f"  Max consec. losses: {RED}{BOLD}{max_consec_l}{RST}{max_l_period}")
        print(f"  Max Drawdown (from peak) : {_col(-max_dd_usd, f'${-max_dd_usd:,.0f}')} ({_col(-max_dd_pct, f'{-max_dd_pct:.2f}%')})")
        print(f"  Max Drawdown (from start): {_col(-max_dd_start_usd, f'${-max_dd_start_usd:,.0f}')} ({_col(-max_dd_start_pct, f'{-max_dd_start_pct:.2f}%')})")
        total_comm_all = sum(v.get("commission", 0.0) for v in daily.values())
        print(f"  Net P&L : {_col(total_usd_all, f'${total_usd_all:+,.0f}')}")
        print(f"  Commission: ${total_comm_all:,.2f}")
        print(f"  Monthly Avg : {_col(avg_monthly_pnl, f'${avg_monthly_pnl:+,.0f}')} ({_col(avg_monthly_pct, f'{avg_monthly_pct:+.2f}%')})")
        total_rw = sum(v["reentry_win"]  for v in monthly.values())
        total_rl = sum(v["reentry_loss"] for v in monthly.values())
        total_rb = sum(v["reentry_be"]   for v in monthly.values())
        if total_rw + total_rl + total_rb > 0:
            re_total = total_rw + total_rl + total_rb
            re_wl = total_rw + total_rl
            re_rate = total_rw / re_wl * 100 if re_wl > 0 else 0.0
            re_parts = [f"{GREEN}{total_rw}W{RST}", f"{RED}{total_rl}L{RST}"]
            if total_rb:
                re_parts.append(f"{YELLOW}{total_rb}B{RST}")
            print(f"  Re-entries: {re_total}  ({' / '.join(re_parts)})  success rate: {_col(re_rate - 50, f'{re_rate:.1f}%')}")
        print()

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

    risk_desc = f"{RISK_PCT}% of balance" if RISK_PCT is not None else f"${RISK_USD_FIX:,.0f} fixed"
    mode = args.mode
    if mode in ('sim', 'both'):
        _print_results(f"SIM — ${ACCT:,.0f} account, {risk_desc} risk per trade", _unused_pnl_fn, _per_trade_sim)
    if mode in ('real_futures', 'both'):
        _print_results(
            f"REAL FUTURES — MNQ micro futures, ${ACCT:,.0f} account, ~{risk_desc} risk, ${FEE_PER_RT:.2f}/contract RT fees (Tradovate)",
            _unused_pnl_fn,
            _per_trade_real,
        )
    if mode in ('real_cfd', 'both'):
        cfd_spread = args.cfd_spread
        # Respect --commission override in CFD report, same as server startup logic
        cfd_commission = args.commission
        if cfd_commission is None:
            cfd_commission = args.cfd_commission

        def _per_trade_cfd(trade, close, balance=ACCT):
            usd, pct, actual_r, comm, _outcome = per_trade_cfd(
                trade, close, balance, RISK_USD_FIX, RISK_PCT,
                nq_pv=NQ_PV, cfd_spread=cfd_spread, cfd_commission=cfd_commission
            )
            return usd, pct, actual_r, comm

        _print_results(
            f"REAL CFD — Nasdaq CFD, ${ACCT:,.0f} account, ~{risk_desc} risk, {cfd_spread}pt spread, ${cfd_commission:.2f}/lot commission",
            _unused_pnl_fn,
            _per_trade_cfd,
        )

    if args.results_json:
        out = {
            "config": {
                "account": ACCT,
                "risk": RISK_USD_FIX,
                "risk_pct": RISK_PCT,
                "mode": mode,
                "rr": args.rr,
                "no_breakeven": args.no_breakeven,
                "no_reentry_breakeven": args.no_reentry_breakeven,
            },
            "results": [
                {
                    "name": r["name"],
                    "status": r["status"],
                    "date": r.get("date", ""),
                    "trade_pairs": [
                        [t, c] for t, c in (r.get("trade_pairs") or [])
                    ],
                }
                for r in summary_results
            ],
        }
        Path(args.results_json).write_text(json.dumps(out, indent=2, default=str))

    if args.html_report:
        # For "both" mode, default HTML report to real_futures (the more realistic scenario)
        html_mode = "real_futures" if mode == "both" else mode
        report_name = "report.html"
        if args.group:
            report_name = f"{sanitize(args.group)}_report.html"
        html_path = Path(args.outdir) / report_name
        html_kwargs = dict(
            summary_results=summary_results,
            account=ACCT,
            risk=RISK_USD_FIX,
            mode=html_mode,
            output_path=str(html_path),
            nq_pv=NQ_PV,
            fee_per_rt=FEE_PER_RT,
            be_threshold=BE_THRESHOLD,
            risk_pct=RISK_PCT,
        )
        if html_mode == "real_cfd":
            html_kwargs["cfd_spread"] = args.cfd_spread
            html_kwargs["cfd_commission"] = args.commission or args.cfd_commission
        generate_html_report(**html_kwargs)
        print(f"\n📄 HTML report: {html_path.resolve()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", action="append", required=True,
                    help="Scenario yaml file(s). Repeat --yaml to merge multiple groups")
    ap.add_argument("--group", default=None,
                    help="Scenario group (e.g. ny, london). Snapshots go to <outdir>/<pair>/<group>/<date>/. "
                         "Default: derived from the yaml path when it lives in a scenarios/ directory")
    ap.add_argument("--source-csv", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--bars-per-second", type=int, default=5000)
    
    # We default to "main" to capture the flex container holding both charts
    ap.add_argument("--chart-selector", default="main") 
    
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--mode", choices=["sim", "real_futures", "real_cfd", "both"], default="real_futures",
                    help="Simulation mode: sim=fixed risk, real_futures=MNQ contracts+fees, real_cfd=CFD with spread+commission, both=show all")
    ap.add_argument("--risk", type=float, default=1000.0,
                    help="Fixed risk per trade in USD (default: 1000). Ignored if --risk-pct is set")
    ap.add_argument("--risk-pct", type=float, default=None,
                    help="Risk per trade as %% of current balance (e.g. 1 = 1%%). Overrides --risk")
    ap.add_argument("--account", type=float, default=100_000.0,
                    help="Simulated account size in USD (default: 100000)")
    ap.add_argument("--quiet", action="store_true",
                    help="Suppress verbose app output; print only per-scenario results and final summary")
    ap.add_argument("--snapshot", action="store_true", default=True,
                    help="Take a chart snapshot for each scenario (default: true)")
    ap.add_argument("--no-snapshot", dest="snapshot", action="store_false",
                    help="Skip chart snapshots")
    ap.add_argument("--results-json", default=None,
                    help="If set, write scenario results as JSON to this path after all scenarios run")
    ap.add_argument("--html-report", action="store_true", default=False,
                    help="Generate an HTML report with monthly view in the output directory")
    ap.add_argument("--decision-log", action="store_true", default=False,
                    help="Print detailed decision log for each scenario")
    ap.add_argument("--no-breakeven", action="store_true", default=False,
                    help="Disable breakeven logic (SL stays at original level, never moves to entry)")
    ap.add_argument("--no-reentry-breakeven", action="store_true", default=False,
                    help="Disable breakeven logic for re-entry trades only")
    ap.add_argument("--cfd-spread", type=float, default=1.5,
                    help="CFD spread in points (default: 1.5 for Nasdaq)")
    ap.add_argument("--cfd-commission", type=float, default=5.0,
                    help="CFD commission per round-trip lot in USD (default: 5.0)")
    ap.add_argument("--commission", type=float, default=None,
                    help="Override round-trip commission per lot/contract for ANY mode (default: None — uses mode defaults: $1.50 for futures, $5.00 for CFD)")
    ap.add_argument("--rr", type=float, default=4.0,
                    help="Risk:Reward ratio for TP calculation (default: 4.0)")
    ap.add_argument("--persist", action="store_true", default=False,
                    help="Persist scenario trades to the database (default: false, uses in-memory storage)")
    ap.add_argument("--session-end", type=str, default="16:58",
                    help="Session end time HH:MM for closing open trades (default: 16:58)")
    ap.add_argument("--session-tz", type=str, default="America/New_York",
                    help="Timezone for session end time (default: America/New_York)")
    args = ap.parse_args()

    # Snapshots are grouped per session: explicit --group, or derived from the
    # yaml path (…/scenarios/<group>.yaml). No group → flat <pair>/<date> layout.
    if args.group is None and len(args.yaml) == 1 and Path(args.yaml[0]).parent.name == "scenarios":
        args.group = Path(args.yaml[0]).stem

    scenarios = []
    for yaml_arg in args.yaml:
        yaml_path = Path(yaml_arg)
        if not yaml_path.exists():
            print(f"❌ YAML file not found: {yaml_path}")
            return

        if not args.quiet:
            print(f"📂 Loading scenarios from {yaml_path}...")
        try:
            ydoc = yaml.safe_load(yaml_path.read_text())
        except Exception as e:
            print(f"❌ Error parsing YAML {yaml_path}: {e}")
            return

        if not ydoc:
            continue

        for sc in ydoc.get("scenarios", []):
            sc["_group"] = yaml_path.stem
            scenarios.append(sc)

    if not scenarios:
        print("⚠️  No 'scenarios' found in the given YAML file(s).")
        return

    # Run scenarios in chronological order so merged groups interleave correctly.
    scenarios.sort(key=lambda sc: _parse_yaml_dt(sc["start"]))

    if not args.quiet:
        print(f"✅ Found {len(scenarios)} scenarios. Starting runner...")
    asyncio.run(run_suite(args, scenarios, Path(args.source_csv)))

if __name__ == "__main__":
    main()