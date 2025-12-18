#!/usr/bin/env python3
"""
High-Performance Scenario Runner.

Optimizations:
1. Starts Flask App ONCE (loads CSV once).
2. Starts Browser ONCE (reuses context).
3. Resets state via API between scenarios.
4. Validates expectations (Original SL/TP) and prints a summary.
5. Draws persistent "Original SL" lines for visualization.
6. Timezone-aware: Syncs YAML/CSV times with the Pair's specific timezone.
"""

import argparse
import asyncio
import csv
import sys
import time
import threading
import subprocess
import signal
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml
import requests
from dateutil import parser as dtparser
from playwright.async_api import async_playwright

APP_HOST = "127.0.0.1"
APP_URL = f"http://{APP_HOST}:5001"

# Timezones must match src/data_sources/csv_datasource.py
PAIR_TZS = {
    'EURUSD': 'Europe/London',
    'NQ':     'America/Chicago',
}

# ---------------- helpers ----------------

def sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in ("-","_"," ") else "_" for c in name).strip().replace(" ", "_")

def _parse_dt_flexible(date_str: str, time_str: str) -> datetime:
    s = f"{date_str} {time_str}".strip()
    try:
        return datetime.strptime(s, "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return dtparser.parse(s)

def _parse_yaml_dt(s: str) -> datetime:
    """Parses YAML datetime string. Returns naive datetime."""
    return dtparser.parse(s).replace(tzinfo=None)

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

def add_line_http(pair: str, price: float):
    try:
        r = requests.post(f"{APP_URL}/api/lines", json={"pair": pair, "price": float(price)}, timeout=5)
        if not r.ok:
            print(f"⚠️ Failed to add line {price}: {r.status_code} {r.text}")
            return False
        return True
    except Exception as e:
        print(f"⚠️ Exception adding line {price}: {e}")
        return False

def reset_app_state(start=None, end=None):
    payload = {}
    if start: payload['start_time'] = start
    if end:   payload['end_time'] = end
    try:
        # Increased timeout to 10s to allow for strategy warmup
        r = requests.post(f"{APP_URL}/__reset_all", json=payload, timeout=10)
        if not r.ok:
            print(f"⚠️ Reset failed: {r.status_code} {r.text}")
            return False
        return True
    except Exception as e:
        print(f"⚠️ Reset exception: {e}")
        return False

def check_expectations(expect: Dict, trades: List[Dict]) -> Tuple[str, str, str]:
    """Returns (Status, Reason, FormattedValues)"""
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
                errors.append(f"{yaml_key} missing in trade data")
                continue
            
            if abs(actual - target) > tol:
                errors.append(f"{yaml_key}: got {actual}, want {target}")

    if errors:
        return "FAIL", ", ".join(errors), values_str
    
    return "PASS", "Matches expectations", values_str

# ---------------- async runner ----------------

async def run_suite(args, scenarios: List[Dict], csv_path: Path):
    print(f"🚀 Launching persistent App with {csv_path}...")
    
    env = os.environ.copy()
    env["CSV_FILE"] = str(csv_path.resolve())
    env["BARS_PER_SECOND"] = str(args.bars_per_second)
    env["START_ISO"] = "2000-01-01" 
    env["END_ISO"]   = "2099-01-01"
    env["FLASK_RUN_PORT"] = str(args.port)
    env["LINE_REMOVAL_MODE"] = "NEVER"

    proc = subprocess.Popen([sys.executable, "app.py"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # 1. Read CSV (Naive)
    print("📂 Reading CSV...")
    all_dts_naive = []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            sample = f.read(4096); f.seek(0)
            try: dialect = csv.Sniffer().sniff(sample, delimiters=",;")
            except: dialect = csv.excel(); dialect.delimiter=";"
            rdr = csv.DictReader(f, dialect=dialect)
            for row in rdr:
                all_dts_naive.append(_parse_dt_flexible(
                    (row.get("Date") or row.get("date") or "").strip(),
                    (row.get("Time") or row.get("time") or "").strip()
                ))
    except Exception as e:
        print(f"⚠️ Error reading CSV: {e}")
        proc.kill()
        return

    try:
        summary_results = []

        # 2. Wait for App & Get Pair
        wait_http_ok(f"{APP_URL}/api/pair")
        pair_resp = requests.get(f"{APP_URL}/api/pair").json()
        pair_name = pair_resp['pair']
        
        # 3. Determine Timezone
        pair_tz = ZoneInfo(PAIR_TZS.get(pair_name, 'UTC'))
        print(f"✅ App running ({pair_name}). Timezone: {pair_tz}")

        # 4. Build Index Map (Aware)
        # Convert naive CSV times to Pair TZ -> UTC Epoch
        idx_map = {}
        for i, dt_naive in enumerate(all_dts_naive):
            dt_aware = dt_naive.replace(tzinfo=pair_tz)
            ts = int(dt_aware.timestamp())
            idx_map[ts] = i

        def resolve_at(at_str):
            if str(at_str).startswith("row:"):
                n = int(at_str.split(":",1)[1])
                return max(0, min(n-1, len(all_dts_naive)-1))
            
            # Parse YAML string as naive, attach Pair TZ, get epoch
            dt_naive = _parse_yaml_dt(str(at_str))
            dt_aware = dt_naive.replace(tzinfo=pair_tz)
            sec = int(dt_aware.timestamp())
            return idx_map.get(sec, 0)

        def get_epoch(dt_str):
            dt_naive = _parse_yaml_dt(dt_str)
            dt_aware = dt_naive.replace(tzinfo=pair_tz)
            return int(dt_aware.timestamp())

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
            page = await ctx.new_page()

            for i, sc in enumerate(scenarios):
                name = sc.get("name", f"scenario_{i}")
                print(f"▶️  Running: {name}")
                
                sdir = Path(args.outdir) / sanitize(name)
                sdir.mkdir(parents=True, exist_ok=True)
                
                # Use Timezone-Aware Epochs
                start_ts = get_epoch(sc["start"])
                end_ts   = get_epoch(sc["end"])
                tf       = sc.get("tf", "5m")

                # Reset app state with a buffer (e.g. 2 days history + scenario duration)
                if not reset_app_state(start=start_ts - 172800, end=end_ts + 3600):
                    print("❌ Skipping scenario due to reset failure")
                    continue
                
                # --- ADD ALL LINES BEFORE PAGE LOAD ---
                lines = [parse_line_spec(l) for l in sc.get("lines", [])]
                for l in lines:
                    # We add ALL lines immediately, ignoring l.get("at_raw") check
                    if not add_line_http(pair_name, l["level"]):
                        print(f"❌ Failed to add line {l['level']}")
                # --------------------------------------------

                # Calculate a view buffer (e.g. 24 hours) to limit history load on frontend
                view_start_ts = start_ts - 86400
                
                # Pass start_time to frontend to prevent loading full history
                # Pass keep_lines=true to prevent line removal on trade trigger
                await page.goto(f"{APP_URL}/?start_time={view_start_ts}&keep_lines=true&tf={tf}", wait_until="domcontentloaded")

                await page.evaluate("""
                    window.__done = false;
                    window.__trades = [];
                    
                    const sock = (window.io && window.io()) || window.socket;
                    if (!sock) throw new Error("Socket.IO not found");
                    window.socket = sock;
                    
                    if (window.chartViewer) {
                        window.chartViewer.keepClosedTradeLines = true;
                    }

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

                # Wait for chart to be ready (bars loaded, lines loaded)
                try:
                    await page.wait_for_function("() => window.__chartReady === true", timeout=10000)
                except Exception as e:
                    print(f"⚠️ Warning: Chart did not report ready: {e}")

                stop_sched = threading.Event()
                
                # REMOVED SCHEDULER THREAD COMPLETELY

                await page.evaluate(
                    """(p) => window.socket.emit('start_stream', { timeframe: p.tf, fromTime: p.start, stopAt: p.end })""",
                    {"tf": tf, "start": start_ts, "end": end_ts}
                )

                try:
                    await page.wait_for_function("() => window.__done === true", timeout=120000)
                    await page.wait_for_timeout(1000)
                except Exception as e:
                    print(f"❌ Timeout/Error waiting for stream end: {e}")

                stop_sched.set()

                captured_trades = await page.evaluate("window.__trades")
                status, reason, values = check_expectations(sc.get("expect"), captured_trades)
                summary_results.append({"name": name, "status": status, "reason": reason, "values": values})
                print(f"   [{status}] {reason} {values}")

                if sc.get("snapshot", True):
                    png_path = sdir / f"snapshot_{tf}.png"
                    try:
                        chart = page.locator(args.chart_selector)
                        await chart.wait_for(state="visible", timeout=2000)
                        await chart.screenshot(path=str(png_path))
                    except:
                        await page.screenshot(path=str(png_path), full_page=True)

            await browser.close()

    finally:
        print("🛑 Shutting down App...")
        proc.send_signal(signal.SIGINT)
        proc.wait()

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
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--bars-per-second", type=int, default=5000)
    ap.add_argument("--chart-selector", default="#chartContainer")
    args = ap.parse_args()

    ydoc = yaml.safe_load(Path(args.yaml).read_text())
    scenarios = ydoc.get("scenarios", [])
    if not scenarios: return

    asyncio.run(run_suite(args, scenarios, Path(args.source_csv)))

if __name__ == "__main__":
    main()