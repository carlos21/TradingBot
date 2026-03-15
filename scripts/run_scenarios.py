#!/usr/bin/env python3
"""
High-Performance Scenario Runner with Dependency Injection.
"""

import re
import json
import argparse
import asyncio
import csv
import sys
import time
import os
import signal
from pathlib import Path
from typing import Any, Dict, List, Tuple
from datetime import datetime, timezone
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
from src.data_sources.csv_datasource import CSVDataSource
from src.prod_config import (
    get_prod_strategy_numbers,
    get_prod_candle_config,
    get_prod_strategy_options
)
from tests.fakes import FakeLineRepository, FakeTradeRepository
from scripts.html_report import generate_html_report

APP_HOST = "127.0.0.1"

# --- TIMEZONE CONFIGURATION ---
PAIR_TZS = {
    'EURUSD': 'Europe/London',
    'NQ':     'Etc/GMT+5',   # fixed UTC-5, matches TradingView "UTC-5" (no DST shift)
    'ES':     'Etc/GMT+5',
}

# -------------------------------------------------------------------------
# Data Verification Helper
# -------------------------------------------------------------------------
def verify_csv_data(csv_path: Path, pair: str, start_ts: int, end_ts: int):
    print(f"🔍 Verifying data in {csv_path.name}...")
    print(f"   Requested Range: {start_ts} -> {end_ts}")
    
    csv_tz_name = 'America/Chicago' if pair in ('NQ', 'ES') else 'UTC'
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
                except: continue
                
        print(f"   ✅ Found {count} bars in requested range.")
        if count == 0:
            print(f"   ⚠️  WARNING: ZERO bars found!")
            print(f"       Last bar in CSV is at: {last_ts} ({datetime.fromtimestamp(last_ts, tz=utc)})")
            if end_ts > last_ts:
                print(f"       Requested End {end_ts} is AFTER the CSV data ends.")
            else:
                print(f"       Data might be missing (Weekend/Holiday?).")
                
    except Exception as e:
        print(f"   ❌ Could not verify CSV data: {e}")

# -------------------------------------------------------------------------
# Server Process Logic
# -------------------------------------------------------------------------

def run_test_server(csv_path: str, bars_per_second: float, port: int, ready_event: Event, quiet: bool = False, no_breakeven: bool = False, broker_mode: str = 'futures', broker_spread: float = 0.0, rr_ratio: float = 4.0):
    if quiet:
        sys.stdout = open(os.devnull, 'w')
        import logging
        logging.disable(logging.CRITICAL)

    os.environ["LINE_REMOVAL_MODE"] = "ON_EVALUATE"

    repos = Repositories(
        lines=FakeLineRepository(),
        trades=FakeTradeRepository()
    )

    ds = CSVDataSource(
        pair="NQ",
        filename=csv_path,
        initial_start_time=0,
        initial_end_time=9999999999,
        bars_per_second=bars_per_second,
    )

    numbers = get_prod_strategy_numbers(rr_ratio=rr_ratio)
    candle_config = get_prod_candle_config()
    options = get_prod_strategy_options(numbers.max_bounce, numbers.min_cross_depth)

    if no_breakeven:
        options.breakeven = None

    wiring = create_app(
        pair="NQ",
        data_source=ds,
        repos=repos,
        numbers=numbers,
        options=options,
        candle_config=candle_config,
        timeframes=["3m", "5m", "15m", "30m", "1h"],
        bootstrap_existing_lines=False,
        broker_mode=broker_mode,
        broker_spread=broker_spread,
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
        except: pass
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

def reset_app_state(base_url: str, start=None, end=None):
    payload = {}
    if start: payload['start_time'] = start
    if end:   payload['end_time'] = end
    try:
        r = requests.post(f"{base_url}/__reset_all", json=payload, timeout=10)
        return r.ok
    except Exception:
        return False

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

    tol = float(expect.get("tolerance", 1.0))
    errors = []

    checks = {
        "entry": ["entry", "entry_price"],
        "sl":    ["orig_sl", "stop_loss", "stopLoss", "sl"],
        "tp":    ["take_profit", "takeProfit", "tp"]
    }

    for yaml_key, trade_keys in checks.items():
        if yaml_key in expect:
            target = float(expect[yaml_key])
            actual = None
            for k in trade_keys:
                if k in trade:
                    actual = trade[k]
                    break
            
            if actual is None:
                errors.append(f"{yaml_key} missing")
                continue
            
            if abs(actual - target) > tol:
                errors.append(f"{yaml_key}: got {actual}, want {target}")

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

async def run_suite(args, scenarios: List[Dict], csv_path: Path):
    quiet = getattr(args, 'quiet', False)
    if not quiet:
        print(f"🚀 Launching In-Memory Test Server with {csv_path}...")

    base_url = f"http://{APP_HOST}:{args.port}"
    server_ready = Event()

    no_breakeven = getattr(args, 'no_breakeven', False)
    # Determine broker mode for trade manager
    mode = getattr(args, 'mode', 'real_futures')
    broker_mode = 'cfd' if mode in ('real_cfd', ) else 'futures'
    broker_spread = getattr(args, 'cfd_spread', 0.0) if broker_mode == 'cfd' else 0.0

    server_proc = Process(
        target=run_test_server,
        args=(str(csv_path.resolve()), args.bars_per_second, args.port, server_ready, quiet, no_breakeven, broker_mode, broker_spread, args.rr)
    )
    server_proc.start()

    if not server_ready.wait(timeout=10):
        print("❌ Server failed to start within timeout.")
        server_proc.terminate()
        return

    try:
        wait_http_ok(f"{base_url}/api/pair")
    except TimeoutError:
        print("❌ Server process started but HTTP not reachable.")
        server_proc.terminate()
        return

    pair_resp = requests.get(f"{base_url}/api/pair").json()
    pair_name = pair_resp['pair']
    pair_tz = ZoneInfo(PAIR_TZS.get(pair_name, 'UTC'))
    if not quiet:
        print(f"✅ Test Server running ({pair_name}) at {base_url}. Timezone: {pair_tz}")

    def get_epoch(dt_str):
        dt = dtparser.parse(dt_str)
        dt = dt.replace(tzinfo=pair_tz)  # always interpret in pair's local TZ
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

            for i, sc in enumerate(scenarios):
                name = sc.get("name", f"scenario_{i}")
                if not quiet:
                    print(f"▶️  Running: {name}")
                
                sdir = Path(args.outdir) / sanitize(name)
                sdir.mkdir(parents=True, exist_ok=True)
                
                start_ts = get_epoch(sc["start"])
                end_ts   = get_epoch(sc["end"])
                tf       = sc.get("tf", "5m")

                if not quiet:
                    verify_csv_data(csv_path, pair_name, start_ts, end_ts)
                    print(f"   [DEBUG] Scenario Start: {start_ts} | End: {end_ts}")

                # Pre-seed 2 hours of warmup so 5m/15m TSI is fully warmed up by start
                WARMUP_SECONDS = 2 * 3600
                warmup_start_ts = start_ts - WARMUP_SECONDS
                if not reset_app_state(base_url, start=warmup_start_ts, end=start_ts):
                    print(f"❌ [{name}] Reset failed")
                    continue
                
                lines = [parse_line_spec(l) for l in sc.get("lines", [])]
                for l in lines:
                    c_ts = None
                    if l["at_raw"]:
                        c_ts = get_epoch(l["at_raw"])
                    add_line_http(base_url, pair_name, l["level"], creation_time=c_ts)

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

                    const sock = window.chartViewer.socket;
                    if (!sock) throw new Error("ChartViewer socket not found");

                    sock.on('trade_open', (t) => {
                        window.__trades.push(t);
                        if (window.chartViewer && window.chartViewer.series) {
                            const sl = t.stop_loss ?? t.sl ?? t.stopLoss;
                            if (typeof sl === 'number') {
                                window.chartViewer.series.createPriceLine({
                                    price: sl,
                                    color: '#ff5252',
                                    lineWidth: 1,
                                    lineStyle: 1,
                                    axisLabelVisible: true,
                                    title: 'Orig SL'
                                });
                            }
                        }
                    });
                    sock.on('trade_close', (c) => { window.__closes[String(c.trade_id)] = c; });
                    sock.on('stream_end', () => { window.__done = true; });
                """)

                # Extend stream to NY session end (15:00) so open trades get closed
                ny_tz = ZoneInfo("America/New_York")
                scenario_date = dtparser.parse(sc["start"]).date()
                session_end_dt = datetime.combine(scenario_date, dtime(15, 0), tzinfo=ny_tz)
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
                trade_pairs = [
                    (t, (captured_closes or {}).get(str(t.get("trade_id", ""))))
                    for t in (captured_trades or [])
                ]
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
                })
                date_label = dtparser.parse(sc["start"]).strftime("%Y-%m-%d")
                print(f"   [{status}] {date_label}  {reason} {values}")

                if args.decision_log:
                    try:
                        logs = requests.get(f"{base_url}/api/debug/logs", timeout=2).json()
                        print_detailed_summary(logs, pair_tz)
                    except Exception as e:
                        print(f"   ⚠️ Failed to fetch summary logs: {e}")

                if args.snapshot:
                    try:
                        # 1. Wait for data
                        await page.wait_for_function(
                            "() => window.chartViewer.series.data().length > 0", 
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
                        await chart_locator.screenshot(path=str(sdir / f"snapshot_{tf}.png"))
                    except Exception as e:
                        if not quiet:
                            print(f"   ⚠️ Snapshot failed: {e}")

            await browser.close()

    finally:
        if not quiet:
            print("🛑 Terminating Test Server...")
        server_proc.terminate()
        server_proc.join()

    # ── ANSI helpers ──────────────────────────────────────────────────────────
    RST    = '\033[0m';  BOLD   = '\033[1m'
    GREEN  = '\033[92m'; RED    = '\033[91m'
    YELLOW = '\033[93m'; GRAY   = '\033[90m'
    CYAN   = '\033[96m'; WHITE  = '\033[97m'
    ACCT         = args.account   # simulated starting balance (configurable via --account)
    RISK_USD     = args.risk      # fixed risk per trade in USD (configurable via --risk)
    NQ_PV        = 2.0       # $ per point, MNQ micro contract
    FEE_PER_RT   = 1.50      # $ round-trip per contract (Tradovate monthly + CME micro exchange fees)
    BE_THRESHOLD = 0.5       # R below this is considered breakeven

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
        """Calculate max drawdown (amount and %) from a list of balance values."""
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

    def _actual_pnl_sim(r):
        """$1,000 fixed risk per trade — sums across all trades in the scenario."""
        trade_pairs = r.get("trade_pairs") or []
        if not trade_pairs:
            return None, None, None, ""
        total_usd = 0.0
        has_closed = False
        for _trade, close in trade_pairs:
            if close is None:
                continue
            has_closed = True
            actual_r = close.get("result", 0.0)
            total_usd += RISK_USD * actual_r if actual_r > 0 else -RISK_USD
        if not has_closed:
            return None, None, None, ""
        actual_pct = total_usd / ACCT * 100
        n = len([c for _, c in trade_pairs if c is not None])
        extra = f"{n} trade(s)" if n > 1 else ""
        return actual_pct, total_usd, actual_pct, extra

    def _actual_pnl_real(r):
        """Realistic NQ futures: integer contracts ($20/pt), round-trip fees included. Sums all trades."""
        trade_pairs = r.get("trade_pairs") or []
        if not trade_pairs:
            return None, None, None, ""
        total_usd = 0.0
        has_closed = False
        detail_parts = []
        for trade, close in trade_pairs:
            if close is None:
                continue
            entry   = trade.get("entry") or trade.get("entry_price")
            orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
            if entry is None or orig_sl is None:
                continue
            sl_pts = round(abs(entry - orig_sl), 4)
            if sl_pts <= 0:
                continue
            contracts = max(1, round(RISK_USD / (sl_pts * NQ_PV)))
            fees = contracts * FEE_PER_RT
            actual_r = close.get("result", 0.0)
            if actual_r > 0:
                trade_usd = contracts * (actual_r * sl_pts) * NQ_PV - fees
            else:
                trade_usd = -(contracts * sl_pts * NQ_PV) - fees
            total_usd += trade_usd
            has_closed = True
            detail_parts.append(f"{contracts}c@{sl_pts:.0f}pt")
        if not has_closed:
            return None, None, None, ""
        actual_pct = total_usd / ACCT * 100
        extra = "  ".join(detail_parts)
        return actual_pct, total_usd, actual_pct, extra

    def _print_results(mode_label, pnl_fn, per_trade_fn):
        """
        per_trade_fn(trade, close) → (usd, pct, actual_r)
        Used so that W/L bucketing counts each individual trade, not the net scenario outcome.
        """
        # ── Per-scenario table ─────────────────────────────────────────────────
        W = 135
        print(f"\n{'=' * W}")
        print(f"  {BOLD}{CYAN}MODE: {mode_label}{RST}")
        print(f"{'=' * W}")
        hdr = f"{'SCENARIO':<40} | {'STATUS':^8} | {'RESULT':^14} | {'%':>9} | {'$ PnL':>12} | DETAILS"
        print(hdr)
        print("-" * W)
        for r in summary_results:
            actual_pct, actual_usd, actual_acct_pct, extra = pnl_fn(r)

            # Build per-trade result labels (W/L/B/E per trade)
            trade_pairs = r.get("trade_pairs") or []
            trade_labels = []
            for trade, close in trade_pairs:
                if close is None:
                    trade_labels.append(f"{YELLOW}OPEN?{RST}")
                    continue
                r_val = close.get("result", 0.0)
                if r_val > 0 and r_val < BE_THRESHOLD:
                    trade_labels.append(f"{YELLOW}{BOLD}B/E{RST}")
                elif r_val >= BE_THRESHOLD:
                    trade_labels.append(f"{GREEN}{BOLD}W{RST}")
                else:
                    trade_labels.append(f"{RED}{BOLD}L{RST}")

            if len(trade_labels) == 1:
                res_str = _center(trade_labels[0], 14)
            elif trade_labels:
                res_str = _center(" + ".join(trade_labels), 14)
            else:
                res_str = _center(f"{GRAY}–{RST}", 14)

            if actual_pct is not None:
                pct_str = _col(actual_pct, f"{actual_pct:>+8.2f}%")
                usd_str = _col(actual_usd, f"{actual_usd:>+11,.0f}")
            else:
                pct_str = f"{GRAY}{'N/A':>9}{RST}"
                usd_str = f"{GRAY}{'N/A':>12}{RST}"

            status_vis = f"{'PASS':^8}" if r['status'] == 'PASS' else f"{'FAIL':^8}"
            status_col = f"{GREEN}{BOLD}{status_vis}{RST}" if r['status'] == 'PASS' else f"{RED}{BOLD}{status_vis}{RST}"
            details = f"{r['reason']} {r['values']}"
            if extra:
                details = f"{details}  {GRAY}[{extra}]{RST}"
            print(f"{r['name']:<40} | {status_col} | {res_str} | {pct_str} | {usd_str} | {details}")
        print("=" * W)

        # ── Aggregate by day / week / month (per individual trade) ─────────────
        def _new_bucket():
            return {"usd": 0.0, "pct": 0.0, "wins": 0, "losses": 0, "be": 0, "open": 0}

        daily   = defaultdict(_new_bucket)
        weekly  = defaultdict(_new_bucket)
        monthly = defaultdict(_new_bucket)

        for r in summary_results:
            date  = dtparser.parse(r["date"]).date()
            d_key = str(date)
            iso   = date.isocalendar()
            w_key = f"{iso.year}-W{iso.week:02d}"
            m_key = date.strftime("%Y-%m")
            for trade, close in (r.get("trade_pairs") or []):
                if close is None:
                    for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                        bucket[key]["open"] += 1
                    continue
                t_usd, t_pct, actual_r = per_trade_fn(trade, close)
                is_be  = actual_r > 0 and actual_r < BE_THRESHOLD
                is_win = actual_r >= BE_THRESHOLD
                for bucket, key in [(daily, d_key), (weekly, w_key), (monthly, m_key)]:
                    if t_usd is not None:
                        bucket[key]["usd"] += t_usd
                        bucket[key]["pct"] += t_pct
                    if is_be:    bucket[key]["be"]     += 1
                    elif is_win: bucket[key]["wins"]   += 1
                    else:        bucket[key]["losses"] += 1

        def _print_agg(title, data):
            if not data:
                return
            WL_W  = 10
            lbl_w = max(len(k) for k in data) + 2
            sep   = "-" * (lbl_w + 3 + WL_W + 54)
            print(f"\n{BOLD}{CYAN}{title}{RST}")
            print(f"  {'PERIOD':<{lbl_w}} | {'W/L':^{WL_W}} | {'%':>9} | {'$ PnL':>10} | {'$ BALANCE':>11}")
            print(f"  {sep}")
            balance = ACCT
            for key in sorted(data):
                v        = data[key]
                balance += v["usd"]
                parts = []
                if v["wins"]:    parts.append(f"{GREEN}{BOLD}{v['wins']}W{RST}")
                if v["losses"]:  parts.append(f"{RED}{BOLD}{v['losses']}L{RST}")
                if v["be"]:      parts.append(f"{YELLOW}{v['be']}B{RST}")
                wl_str  = _center("/".join(parts) if parts else f"{GRAY}-{RST}", WL_W)
                pct_str = _col(v["pct"], f"{v['pct']:>+8.2f}%")
                usd_str = _col(v["usd"], f"${v['usd']:>+9,.0f}")
                bal_str = _col(balance - ACCT, f"${balance:>10,.0f}")
                print(f"  {key:<{lbl_w}} | {wl_str} | {pct_str} | {usd_str} | {bal_str}")
            total_usd = sum(v["usd"]    for v in data.values())
            total_pct = sum(v["pct"]    for v in data.values())
            total_w   = sum(v["wins"]   for v in data.values())
            total_l   = sum(v["losses"] for v in data.values())
            total_be  = sum(v["be"]     for v in data.values())
            print(f"  {sep}")
            tot_pct  = _col(total_pct, f"{total_pct:>+8.2f}%")
            tot_usd  = _col(total_usd, f"${total_usd:>+9,.0f}")
            tot_bal  = _col(total_usd, f"${ACCT + total_usd:>10,.0f}")
            tot_parts = []
            if total_w:   tot_parts.append(f"{GREEN}{total_w}W{RST}")
            if total_l:   tot_parts.append(f"{RED}{total_l}L{RST}")
            if total_be:  tot_parts.append(f"{YELLOW}{total_be}B{RST}")
            tot_wl = _center("/".join(tot_parts) if tot_parts else f"{GRAY}-{RST}", WL_W)
            print(f"  {'TOTAL':<{lbl_w}} | {tot_wl} | {tot_pct} | {tot_usd} | {tot_bal}")

        _print_agg(f"DAILY PnL   — {mode_label}", daily)
        _print_agg(f"WEEKLY PnL  — {mode_label}", weekly)
        _print_agg(f"MONTHLY PnL — {mode_label}", monthly)

        # ── Overall summary (count each individual trade) ─────────────────────
        outcomes = []
        for r in summary_results:
            for trade, close in (r.get("trade_pairs") or []):
                if close is None:
                    continue
                actual_r = close.get("result", 0.0)
                is_be    = actual_r > 0 and actual_r < BE_THRESHOLD
                if is_be:
                    outcomes.append("be")
                elif actual_r >= BE_THRESHOLD:
                    outcomes.append(True)
                else:
                    outcomes.append(False)

        total_t = len(outcomes)
        wins    = outcomes.count(True)
        losses  = outcomes.count(False)
        bes     = outcomes.count("be")
        winrate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0

        max_consec_w = max_consec_l = cur_w = cur_l = 0
        for o in outcomes:
            if o is True:
                cur_w += 1; cur_l = 0
            elif o is False:
                cur_l += 1; cur_w = 0
            else:
                continue
            max_consec_w = max(max_consec_w, cur_w)
            max_consec_l = max(max_consec_l, cur_l)

        total_usd_all = sum(v["usd"] for v in daily.values())

        # Calculate max drawdown from equity curve
        equity_balances = [ACCT]
        for key in sorted(daily.keys()):
            equity_balances.append(equity_balances[-1] + daily[key]["usd"])
        max_dd_usd, max_dd_pct = _calc_max_drawdown(equity_balances)

        # Calculate monthly average profit
        num_months = len(monthly) if monthly else 1
        avg_monthly_pnl = total_usd_all / num_months if num_months > 0 else 0.0
        avg_monthly_pct = avg_monthly_pnl / ACCT * 100

        print(f"\n{BOLD}{CYAN}OVERALL SUMMARY — {mode_label}{RST}")
        print(f"  Trades  : {total_t}  ({GREEN}{wins}W{RST} / {RED}{losses}L{RST} / {YELLOW}{bes}BE{RST})")
        print(f"  Win Rate: {_col(winrate - 50, f'{winrate:.1f}%')}  (excl. breakevens)")
        print(f"  Max consec. wins  : {GREEN}{BOLD}{max_consec_w}{RST}")
        print(f"  Max consec. losses: {RED}{BOLD}{max_consec_l}{RST}")
        print(f"  Max Drawdown  : {_col(-max_dd_usd, f'${-max_dd_usd:,.0f}')} ({_col(-max_dd_pct, f'{-max_dd_pct:.2f}%')})")
        print(f"  Net P&L : {_col(total_usd_all, f'${total_usd_all:+,.0f}')}")
        print(f"  Monthly Avg : {_col(avg_monthly_pnl, f'${avg_monthly_pnl:+,.0f}')} ({_col(avg_monthly_pct, f'{avg_monthly_pct:+.2f}%')})")
        print()

    def _per_trade_sim(trade, close):
        actual_r = close.get("result", 0.0)
        usd = RISK_USD * actual_r if actual_r > 0 else -RISK_USD
        return usd, usd / ACCT * 100, actual_r

    def _per_trade_real(trade, close):
        entry   = trade.get("entry") or trade.get("entry_price")
        orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
        if entry is None or orig_sl is None:
            return None, None, 0.0
        sl_pts = round(abs(entry - orig_sl), 4)
        if sl_pts <= 0:
            return None, None, 0.0
        contracts = max(1, round(RISK_USD / (sl_pts * NQ_PV)))
        fees = contracts * FEE_PER_RT
        actual_r = close.get("result", 0.0)
        usd = (contracts * (actual_r * sl_pts) * NQ_PV - fees) if actual_r > 0 else -(contracts * sl_pts * NQ_PV) - fees
        return usd, usd / ACCT * 100, actual_r

    mode = getattr(args, 'mode', 'both')
    if mode in ('sim', 'both'):
        _print_results(f"SIM — ${ACCT:,.0f} account, ${RISK_USD:,.0f} fixed risk per trade", _actual_pnl_sim, _per_trade_sim)
    if mode in ('real_futures', 'both'):
        _print_results(
            f"REAL FUTURES — MNQ micro futures, ${ACCT:,.0f} account, ~${RISK_USD:,.0f} target risk, ${FEE_PER_RT:.2f}/contract RT fees (Tradovate)",
            _actual_pnl_real,
            _per_trade_real,
        )
    if mode in ('real_cfd', 'both'):
        cfd_spread = getattr(args, 'cfd_spread', 0.5)
        cfd_commission = getattr(args, 'cfd_commission', 5.0)

        def _actual_pnl_cfd(r):
            """CFD mode: accounts for spread and commission costs."""
            trade_pairs = r.get("trade_pairs") or []
            if not trade_pairs:
                return None, None, None, ""
            total_usd = 0.0
            has_closed = False
            detail_parts = []
            for trade, close in trade_pairs:
                if close is None:
                    continue
                entry   = trade.get("entry") or trade.get("entry_price")
                orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
                if entry is None or orig_sl is None:
                    continue
                sl_pts = round(abs(entry - orig_sl), 4)
                if sl_pts <= 0:
                    continue
                contracts = max(1, round(RISK_USD / (sl_pts * NQ_PV)))
                # CFD costs: round-trip spread + commission
                spread_cost = contracts * cfd_spread * NQ_PV
                commission_cost = contracts * cfd_commission
                total_cost = spread_cost + commission_cost
                actual_r = close.get("result", 0.0)
                if actual_r > 0:
                    trade_usd = contracts * (actual_r * sl_pts) * NQ_PV - total_cost
                else:
                    trade_usd = -(contracts * sl_pts * NQ_PV) - total_cost
                total_usd += trade_usd
                has_closed = True
                detail_parts.append(f"{contracts}c@{sl_pts:.0f}pt")
            if not has_closed:
                return None, None, None, ""
            actual_pct = total_usd / ACCT * 100
            extra = "  ".join(detail_parts)
            return actual_pct, total_usd, actual_pct, extra

        def _per_trade_cfd(trade, close):
            """Per-trade CFD P&L calculation."""
            entry   = trade.get("entry") or trade.get("entry_price")
            orig_sl = trade.get("orig_sl") or trade.get("stop_loss")
            if entry is None or orig_sl is None:
                return None, None, 0.0
            sl_pts = round(abs(entry - orig_sl), 4)
            if sl_pts <= 0:
                return None, None, 0.0
            contracts = max(1, round(RISK_USD / (sl_pts * NQ_PV)))
            spread_cost = contracts * cfd_spread * NQ_PV
            commission_cost = contracts * cfd_commission
            total_cost = spread_cost + commission_cost
            actual_r = close.get("result", 0.0)
            usd = (contracts * (actual_r * sl_pts) * NQ_PV - total_cost) if actual_r > 0 else -(contracts * sl_pts * NQ_PV) - total_cost
            return usd, usd / ACCT * 100, actual_r

        _print_results(
            f"REAL CFD — Nasdaq CFD, ${ACCT:,.0f} account, ~${RISK_USD:,.0f} target risk, {cfd_spread}pt spread, ${cfd_commission:.2f}/lot commission",
            _actual_pnl_cfd,
            _per_trade_cfd,
        )

    if getattr(args, 'results_json', None):
        out = [
            {
                "name": r["name"],
                "status": r["status"],
                "trades": [t for t, _ in (r.get("trade_pairs") or [])],
            }
            for r in summary_results
        ]
        Path(args.results_json).write_text(json.dumps(out, indent=2))

    if getattr(args, 'html_report', False):
        # For "both" mode, default HTML report to real_futures (the more realistic scenario)
        html_mode = "real_futures" if mode == "both" else mode
        html_path = Path(args.outdir) / "report.html"
        generate_html_report(
            summary_results,
            account=ACCT,
            risk=RISK_USD,
            mode=html_mode,
            output_path=str(html_path),
            nq_pv=NQ_PV,
            fee_per_rt=FEE_PER_RT,
            be_threshold=BE_THRESHOLD,
        )
        print(f"\n📄 HTML report: {html_path.resolve()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", required=True)
    ap.add_argument("--source-csv", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--bars-per-second", type=int, default=5000)
    
    # We default to "main" to capture the flex container holding both charts
    ap.add_argument("--chart-selector", default="main") 
    
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--mode", choices=["sim", "real_futures", "real_cfd", "both"], default="real_futures",
                    help="Simulation mode: sim=fixed risk, real_futures=MNQ contracts+fees, real_cfd=CFD with spread+commission, both=show all")
    ap.add_argument("--risk", type=float, default=1000.0,
                    help="Fixed risk per trade in USD (default: 1000)")
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
    ap.add_argument("--cfd-spread", type=float, default=0.5,
                    help="CFD spread in points (default: 0.5 for Nasdaq)")
    ap.add_argument("--cfd-commission", type=float, default=5.0,
                    help="CFD commission per round-trip lot in USD (default: 5.0)")
    ap.add_argument("--rr", type=float, default=4.0,
                    help="Risk:Reward ratio for TP calculation (default: 4.0)")
    args = ap.parse_args()

    yaml_path = Path(args.yaml)
    if not yaml_path.exists():
        print(f"❌ YAML file not found: {yaml_path}")
        return

    if not args.quiet:
        print(f"📂 Loading scenarios from {yaml_path}...")
    try:
        ydoc = yaml.safe_load(yaml_path.read_text())
    except Exception as e:
        print(f"❌ Error parsing YAML: {e}")
        return

    if not ydoc:
        print(f"⚠️  YAML file is empty or invalid.")
        return

    scenarios = ydoc.get("scenarios", [])
    if not scenarios:
        print(f"⚠️  No 'scenarios' key found in YAML or list is empty.")
        return

    if not args.quiet:
        print(f"✅ Found {len(scenarios)} scenarios. Starting runner...")
    asyncio.run(run_suite(args, scenarios, Path(args.source_csv)))

if __name__ == "__main__":
    main()