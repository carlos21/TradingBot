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
        timeframes=["5m", "15m"],
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
    
    values_str = f"(Entry: {entry}, Orig SL: {trade.get('stop_loss')}, TP: {trade.get('take_profit')})"

    if not expect:
        return "PASS", "Matches expectations", values_str
    
    tol = float(expect.get("tolerance", 1.0))
    errors = []

    checks = {
        "entry": ["entry", "entry_price"],
        "sl":    ["stop_loss", "stopLoss", "sl"],
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
    if not logs:
        print("   ℹ️  No decision logs recorded.")
        return

    print("\n   📋 SCENARIO DECISION LOG:")
    print(f"   {'TIME (UTC)':<20} | {'TF':<4} | {'LINE':<5} | {'EVENT':<15} | {'DETAILS'}")
    print("   " + "-"*110)

    for log in logs:
        ts = log.get("time", 0)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        t_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        
        tf = log.get("tf", "--")
        lid = log.get("line_id", "")
        evt = log.get("event", "")
        det = log.get("details", "")

        if evt == "ENTRY":
            evt = f"\033[92m{evt}\033[0m" # Green
        elif evt == "FILTER_BLOCK":
            evt = f"\033[93m{evt}\033[0m" # Yellow
        elif evt == "REMOVE":
            evt = f"\033[91m{evt}\033[0m" # Red
        elif "FAIL" in evt:
            evt = f"\033[90m{evt}\033[0m" # Gray

        print(f"   {t_str:<20} | {tf:<4} | {lid:<5} | {evt:<24} | {det}")
    print("\n")

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
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pair_tz)
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

                if not reset_app_state(base_url, start=start_ts, end=start_ts):
                    print("❌ Reset failed")
                    continue
                
                lines = [parse_line_spec(l) for l in sc.get("lines", [])]
                for l in lines:
                    c_ts = None
                    if l["at_raw"]:
                        c_ts = get_epoch(l["at_raw"])
                    add_line_http(base_url, pair_name, l["level"], creation_time=c_ts)

                await page.goto(f"{base_url}/?start_time={start_ts}&keep_lines=true&keep_closed_trades=true&tf={tf}&show_tsi=true", wait_until="domcontentloaded")

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
                        
                        # 2. FORCE Alignment on SINGLE chart
                        await page.evaluate(
                            """(range) => {
                                console.log("Setting visible range:", range);
                                
                                const viewer = window.chartViewer;
                                
                                // Explicitly lock options to remove offset and prevent drift
                                const opts = {
                                    shiftVisibleRangeOnNewBar: false,
                                    rightOffset: 0,
                                    fixLeftEdge: true,
                                    fixRightEdge: true
                                };
                                
                                // Apply to the single chart instance
                                viewer.chart.timeScale().applyOptions(opts);

                                // Set range
                                const rangeObj = { from: range.start, to: range.end };
                                viewer.chart.timeScale().setVisibleRange(rangeObj);
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