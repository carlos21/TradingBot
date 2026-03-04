#!/usr/bin/env python3
"""
High-Performance Scenario Runner with Dependency Injection.
"""

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

APP_HOST = "127.0.0.1"

# --- TIMEZONE CONFIGURATION ---
PAIR_TZS = {
    'EURUSD': 'Europe/London',
    'NQ':     'America/New_York', 
    'ES':     'America/New_York',
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

def run_test_server(csv_path: str, bars_per_second: float, port: int, ready_event: Event):
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

    numbers = get_prod_strategy_numbers()
    candle_config = get_prod_candle_config()
    options = get_prod_strategy_options(numbers.max_bounce)

    wiring = create_app(
        pair="NQ",
        data_source=ds,
        repos=repos,
        numbers=numbers,
        options=options,
        candle_config=candle_config,
        timeframes=["3m", "5m", "15m", "30m", "1h"],
        bootstrap_existing_lines=False,
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

            elif evt in ("REMOVE", "TSI_INVALID"):
                row(t, tf, evt, det, RED)

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
    print(f"🚀 Launching In-Memory Test Server with {csv_path}...")

    base_url = f"http://{APP_HOST}:{args.port}"
    server_ready = Event()

    server_proc = Process(
        target=run_test_server, 
        args=(str(csv_path.resolve()), args.bars_per_second, args.port, server_ready)
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

            page.on("console", lambda msg: print(f"   [BROWSER] {msg.text}"))
            page.on("pageerror", lambda exc: print(f"   [BROWSER ERROR] {exc}"))

            for i, sc in enumerate(scenarios):
                name = sc.get("name", f"scenario_{i}")
                print(f"▶️  Running: {name}")
                
                sdir = Path(args.outdir) / sanitize(name)
                sdir.mkdir(parents=True, exist_ok=True)
                
                start_ts = get_epoch(sc["start"])
                end_ts   = get_epoch(sc["end"])
                tf       = sc.get("tf", "5m")

                verify_csv_data(csv_path, pair_name, start_ts, end_ts)

                print(f"   [DEBUG] Scenario Start: {start_ts} | End: {end_ts}")

                # Pre-seed 2 hours of warmup so 5m/15m TSI is fully warmed up by start
                WARMUP_SECONDS = 2 * 3600
                warmup_start_ts = start_ts - WARMUP_SECONDS
                if not reset_app_state(base_url, start=warmup_start_ts, end=start_ts):
                    print("❌ Reset failed")
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
                    print(f"⚠️ Timeout waiting for chart init: {e}")

                await page.evaluate("""
                    window.__done = false;
                    window.__trades = [];
                    
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
                    sock.on('stream_end', () => { window.__done = true; });
                """)

                await page.evaluate(
                    """(p) => window.chartViewer.socket.emit('start_stream', { timeframe: p.tf, fromTime: p.start, stopAt: p.end })""",
                    {"tf": tf, "start": start_ts, "end": end_ts}
                )

                try:
                    await page.wait_for_function("() => window.__done === true", timeout=120000)
                except Exception as e:
                    print(f"❌ Timeout waiting for stream end: {e}")

                captured_trades = await page.evaluate("window.__trades")
                status, reason, values = check_expectations(sc.get("expect"), captured_trades)
                summary_results.append({"name": name, "status": status, "reason": reason, "values": values})
                print(f"   [{status}] {reason} {values}")

                if sc.get("export_summary", False):
                    try:
                        logs = requests.get(f"{base_url}/api/debug/logs", timeout=2).json()
                        print_detailed_summary(logs, pair_tz)
                    except Exception as e:
                        print(f"   ⚠️ Failed to fetch summary logs: {e}")

                if sc.get("snapshot", True):
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
                        print(f"   ⚠️ Snapshot failed: {e}")

            await browser.close()

    finally:
        print("🛑 Terminating Test Server...")
        server_proc.terminate()
        server_proc.join()

    print("\n" + "="*100)
    print(f"{'SCENARIO':<35} | {'STATUS':<6} | {'DETAILS'}")
    print("-" * 100)
    for r in summary_results:
        print(f"{r['name']:<35} | {r['status']:<6} | {r['reason']} {r['values']}")
    print("="*100 + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", required=True)
    ap.add_argument("--source-csv", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--bars-per-second", type=int, default=5000)
    
    # We default to "main" to capture the flex container holding both charts
    ap.add_argument("--chart-selector", default="main") 
    
    ap.add_argument("--port", type=int, default=5001)
    args = ap.parse_args()

    yaml_path = Path(args.yaml)
    if not yaml_path.exists():
        print(f"❌ YAML file not found: {yaml_path}")
        return

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

    print(f"✅ Found {len(scenarios)} scenarios. Starting runner...")
    asyncio.run(run_suite(args, scenarios, Path(args.source_csv)))

if __name__ == "__main__":
    main()