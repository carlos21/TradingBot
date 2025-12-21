#!/usr/bin/env python3
"""
High-Performance Scenario Runner with Dependency Injection.

Features:
1. Injects FakeLineRepository and FakeTradeRepository (In-Memory).
2. Uses EXACTLY the same StrategyOptions/CandleConfig as Production (via src.prod_config).
3. Spawns a dedicated server process per test suite to ensure clean state.
4. Uses Playwright for end-to-end verification.
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
from datetime import datetime
from zoneinfo import ZoneInfo
from multiprocessing import Process, Event

import yaml
import requests
from dateutil import parser as dtparser
from playwright.async_api import async_playwright

# -------------------------------------------------------------------------
# PATH SETUP: Ensure we can import from project root
# -------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# --- Project Imports ---
from app_factory import create_app, Repositories
from src.data_sources.csv_datasource import CSVDataSource
from src.prod_config import (
    get_prod_strategy_numbers,
    get_prod_candle_config,
    get_prod_strategy_options
)
# Use Fakes instead of SQL
from tests.fakes import FakeLineRepository, FakeTradeRepository

APP_HOST = "127.0.0.1"

# Timezones must match src/data_sources/csv_datasource.py
PAIR_TZS = {
    'EURUSD': 'Europe/London',
    'NQ':     'America/Chicago',
}

# -------------------------------------------------------------------------
# Server Process Logic (The "App" in Test Mode)
# -------------------------------------------------------------------------

def run_test_server(csv_path: str, bars_per_second: float, port: int, ready_event: Event):
    """
    Entry point for the background process.
    Constructs the app with Mock Repos + Real Strategy Logic.
    """
    # 1. Configure Environment for Test Mode
    # We want the BACKEND to remove the line after evaluation so it doesn't trigger twice.
    # The FRONTEND will ignore this removal command because of ?keep_lines=true.
    os.environ["LINE_REMOVAL_MODE"] = "ON_EVALUATE"

    # 2. Mocks (InMemory)
    repos = Repositories(
        lines=FakeLineRepository(),
        trades=FakeTradeRepository()
    )

    # 3. Data Source (Real CSV logic)
    ds = CSVDataSource(
        pair="NQ",
        filename=csv_path,
        initial_start_time=0, 
        initial_end_time=9999999999,
        bars_per_second=bars_per_second,
    )

    # 4. Strategy Logic (EXACT Match with Production)
    numbers = get_prod_strategy_numbers()
    candle_config = get_prod_candle_config()
    options = get_prod_strategy_options(numbers.max_bounce)

    # 5. Build App
    wiring = create_app(
        pair="NQ",
        data_source=ds,
        repos=repos,
        numbers=numbers,
        options=options,
        candle_config=candle_config,
        timeframes=["5m", "15m"],
        bootstrap_existing_lines=False, # No DB lines to load
    )

    # Signal parent that we are about to start
    ready_event.set()

    # Disable generic Flask logs to keep console clean
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    # allow_unsafe_werkzeug=True is required for non-debug mode in recent Flask-SocketIO
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
    # FIX: Do not strip tzinfo here; let get_epoch handle it
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

# FIX: Updated to accept creation_time
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
    if not trades:
        return "FAIL", "No trades opened", ""
    
    trade = trades[0]
    entry = trade.get("entry") or trade.get("entry_price")
    sl    = trade.get("stop_loss") or trade.get("stopLoss") or trade.get("sl")
    tp    = trade.get("take_profit") or trade.get("takeProfit") or trade.get("tp")
    
    values_str = f"(Entry: {entry}, Orig SL: {sl}, TP: {tp})"

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
# Test Runner
# -------------------------------------------------------------------------

async def run_suite(args, scenarios: List[Dict], csv_path: Path):
    print(f"🚀 Launching In-Memory Test Server with {csv_path}...")

    # Construct Base URL based on args
    base_url = f"http://{APP_HOST}:{args.port}"

    # Event to know when Flask is ready
    server_ready = Event()

    # Spawn the server process
    server_proc = Process(
        target=run_test_server, 
        args=(str(csv_path.resolve()), args.bars_per_second, args.port, server_ready)
    )
    server_proc.start()

    # Wait for ready signal
    if not server_ready.wait(timeout=10):
        print("❌ Server failed to start within timeout.")
        server_proc.terminate()
        return

    # Wait for HTTP 200 (double check)
    try:
        wait_http_ok(f"{base_url}/api/pair")
    except TimeoutError:
        print("❌ Server process started but HTTP not reachable.")
        server_proc.terminate()
        return

    # 1. Prepare Timezone logic
    pair_resp = requests.get(f"{base_url}/api/pair").json()
    pair_name = pair_resp['pair']
    pair_tz = ZoneInfo(PAIR_TZS.get(pair_name, 'UTC'))
    print(f"✅ Test Server running ({pair_name}) at {base_url}. Timezone: {pair_tz}")

    # 2. Naive CSV read for index mapping (client-side helper)
    all_dts_naive = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            sample = f.read(4096); f.seek(0)
            try: dialect = csv.Sniffer().sniff(sample, delimiters=",;")
            except: dialect = csv.excel(); dialect.delimiter=";"
            rdr = csv.DictReader(f, dialect=dialect)
            for row in rdr:
                all_dts_naive.append(datetime.strptime(
                    f"{row['Date']} {row['Time']}".strip(), 
                    "%d/%m/%Y %H:%M:%S"
                ))
    except Exception as e:
        print(f"⚠️ Error reading CSV for index map: {e}")

    # 3. Build Index Map
    idx_map = {}
    for i, dt_naive in enumerate(all_dts_naive):
        dt_aware = dt_naive.replace(tzinfo=pair_tz)
        ts = int(dt_aware.timestamp())
        idx_map[ts] = i

    def get_epoch(dt_str):
        dt = _parse_yaml_dt(dt_str)
        # FIX: Only attach pair_tz if the string didn't specify one
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pair_tz)
        return int(dt.timestamp())

    try:
        summary_results = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
            page = await ctx.new_page()

            for i, sc in enumerate(scenarios):
                name = sc.get("name", f"scenario_{i}")
                print(f"▶️  Running: {name}")
                
                sdir = Path(args.outdir) / sanitize(name)
                sdir.mkdir(parents=True, exist_ok=True)
                
                start_ts = get_epoch(sc["start"])
                end_ts   = get_epoch(sc["end"])
                tf       = sc.get("tf", "5m")

                # Reset the In-Memory App
                if not reset_app_state(base_url, start=start_ts - 172800, end=start_ts - 1):
                    print("❌ Reset failed")
                    continue
                
                # Add Lines (HTTP)
                lines = [parse_line_spec(l) for l in sc.get("lines", [])]
                for l in lines:
                    # FIX: Parse 'at' time using the same timezone logic as start/end
                    c_ts = None
                    if l["at_raw"]:
                        c_ts = get_epoch(l["at_raw"])
                        print(f"   -> Line {l['level']} scheduled at {l['at_raw']} (Epoch: {c_ts})")
                    
                    if not add_line_http(base_url, pair_name, l["level"], creation_time=c_ts):
                        print(f"❌ Failed to add line {l['level']}")

                # Load Page with keep_closed_trades=true
                view_start_ts = start_ts - 86400
                # FIX: Added &keep_closed_trades=true
                await page.goto(f"{base_url}/?start_time={view_start_ts}&keep_lines=true&keep_closed_trades=true&tf={tf}", wait_until="domcontentloaded")

                # Inject Test Listeners
                await page.evaluate("""
                    window.__done = false;
                    window.__trades = [];
                    const sock = (window.io && window.io()) || window.socket;
                    if (!sock) throw new Error("Socket.IO not found");
                    window.socket = sock;

                    sock.on('trade_open', (t) => {
                        window.__trades.push(t);
                        
                        // Optional: Draw persistent "Orig SL" line for debugging
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

                # Start Stream
                await page.evaluate(
                    """(p) => window.socket.emit('start_stream', { timeframe: p.tf, fromTime: p.start, stopAt: p.end })""",
                    {"tf": tf, "start": start_ts, "end": end_ts}
                )

                # Wait for completion
                try:
                    await page.wait_for_function("() => window.__done === true", timeout=120000)
                except Exception as e:
                    print(f"❌ Timeout waiting for stream end: {e}")

                # Check Results
                captured_trades = await page.evaluate("window.__trades")
                status, reason, values = check_expectations(sc.get("expect"), captured_trades)
                summary_results.append({"name": name, "status": status, "reason": reason, "values": values})
                print(f"   [{status}] {reason} {values}")

                if sc.get("snapshot", True):
                    try:
                        chart = page.locator(args.chart_selector)
                        await chart.wait_for(state="visible", timeout=2000)
                        await chart.screenshot(path=str(sdir / f"snapshot_{tf}.png"))
                    except:
                        pass

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
    ap.add_argument("--chart-selector", default="#chartContainer")
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
        print(f"   Found keys: {list(ydoc.keys())}")
        return

    print(f"✅ Found {len(scenarios)} scenarios. Starting runner...")
    asyncio.run(run_suite(args, scenarios, Path(args.source_csv)))

if __name__ == "__main__":
    main()