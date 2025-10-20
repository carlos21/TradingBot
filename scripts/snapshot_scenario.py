#!/usr/bin/env python3
"""
snapshot_scenario.py

Run the Flask/Socket.IO app headlessly, load a Scenario (tests/scenario_model.py),
add its lines (immediate + scheduled), stream bars at desired TF, and screenshot
the chart including lines and trade markers.

Usage examples are at the bottom of this file.
"""

import argparse
import asyncio
import csv
import importlib
import math
import os
import runpy
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    from dateutil import parser as dtparser
    from dateutil.tz import gettz
except Exception:
    dtparser = None
    gettz = None

from playwright.async_api import async_playwright


APP_URL = "http://127.0.0.1:5001"
DEFAULT_FILE_DTFORMAT = "%d/%m/%Y %H:%M:%S"  # matches sample


# ---------- CSV helpers ----------

def parse_any_dt(s: str) -> datetime:
    if dtparser is None:
        # minimal fallback
        for fmt in ("%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S",
                    "%d/%m/%Y %H:%M:%S",
                    "%m/%d/%Y %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                pass
        raise ValueError(f"Install python-dateutil for flexible parsing: {s!r}")
    return dtparser.parse(s)

def load_csv_times(csv_path: Path, file_dtformat: Optional[str] = DEFAULT_FILE_DTFORMAT) -> List[datetime]:
    dts: List[datetime] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        rdr = csv.DictReader(f, delimiter=";")
        for row in rdr:
            if file_dtformat:
                dt = datetime.strptime(f"{row['Date']} {row['Time']}", file_dtformat)
            else:
                dt = parse_any_dt(f"{row['Date']} {row['Time']}")
            dts.append(dt)
    return dts

def times_to_index_map(dts: List[datetime]) -> Dict[int, int]:
    """Map Unix second -> first index with that second (1m bars -> unique enough)."""
    m: Dict[int, int] = {}
    for i, dt in enumerate(dts):
        ts = int(dt.timestamp())
        if ts not in m:
            m[ts] = i
    return m


# ---------- HTTP helpers ----------

def wait_for_http(url: str, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(url, timeout=2)
            if r.ok:
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise SystemExit(f"App not reachable at {url} within {timeout}s")

def add_line(pair: str, price: float):
    payload = {"pair": pair, "price": float(price)}
    r = requests.post(f"{APP_URL}/api/lines", json=payload, timeout=10)
    if not r.ok:
        raise SystemExit(f"POST /api/lines failed: {r.status_code} {r.text}")


# ---------- Scenario loading ----------

def import_obj(dotted: str) -> Any:
    """
    Import "module:attr" or "module.attr".
    Returns the referenced object.
    """
    if ":" in dotted:
        mod, attr = dotted.split(":", 1)
    else:
        parts = dotted.split(".")
        mod, attr = ".".join(parts[:-1]), parts[-1]
    m = importlib.import_module(mod)
    return getattr(m, attr)

def load_scenario_from_py(path: Path, var_name: str = "SCENARIO") -> Any:
    """
    Execute a Python file and read a variable or a no-arg function that returns Scenario.
    """
    ns = runpy.run_path(str(path))
    obj = ns.get(var_name)
    if obj is None:
        raise SystemExit(f"{path} does not define {var_name}")
    if callable(obj):
        obj = obj()  # assume it returns Scenario
    return obj


# ---------- Schedule helpers ----------

def parse_line_spec(line_row: List[Any]) -> Dict[str, Any]:
    """
    Accept:
      ["short", 19560.0]
      ["L1","short",19560.0]
      ["short",19560.0,"row:3"]
      ["L2","short",19560.0,"2024-07-31T15:12:00-04:00"]
    """
    lid = None
    at_raw = None
    flat = list(line_row)

    # detect at
    for i, x in enumerate(list(flat)):
        if isinstance(x, str) and (x.startswith("row:") or x[:4].isdigit()):
            at_raw = flat.pop(i)
            break

    # detect id (L*)
    for i, x in enumerate(list(flat)):
        if isinstance(x, str) and x.upper().startswith("L"):
            lid = flat.pop(i)
            break

    if len(flat) != 2:
        raise ValueError(f"Bad line spec: {line_row!r}. Expect [dir, level] with optional id/at.")

    a, b = flat
    if isinstance(a, str):
        direction, level = a, float(b)
    else:
        direction, level = b, float(a)

    direction = direction.lower()
    if direction == "buy":  direction = "long"
    if direction == "sell": direction = "short"
    return {"id": lid, "direction": direction, "level": level, "at_raw": at_raw}

def resolve_at_to_epoch(at_raw: Optional[str], dts: List[datetime]) -> Optional[int]:
    if at_raw is None:
        return None
    if isinstance(at_raw, str) and at_raw.startswith("row:"):
        n = int(at_raw.split(":", 1)[1])
        if n <= 0 or n > len(dts):
            raise ValueError(f"{at_raw} out of range 1..{len(dts)}")
        return int(dts[n-1].timestamp())
    # datetime string
    dt = parse_any_dt(at_raw)
    if dt.tzinfo is None and gettz is not None:
        # treat naive as New York like your app alignment
        dt = dt.replace(tzinfo=gettz("America/New_York"))
    return int(dt.timestamp())


# ---------- Playwright ----------

async def take_screenshot(output_png: str, timeframe: str, from_epoch: int, chart_selector: str):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()
        await page.goto(APP_URL + "/", wait_until="domcontentloaded")

        # wire stream_end listener
        await page.evaluate("""
            window.__done = false;
            const sock = (window.io && window.io()) || window.socket;
            if (!sock) { throw new Error("Socket.IO client not found on page"); }
            window.socket = sock;
            sock.on('stream_end', () => { window.__done = true; });
        """)

        # start stream
        await page.evaluate(
            """(tf, fromTs) => { window.socket.emit('start_stream', { timeframe: tf, fromTime: fromTs }); }""",
            timeframe, from_epoch
        )

        # wait chart container
        locator = page.locator(chart_selector)
        await locator.wait_for(state="visible", timeout=20000)

        # wait playback end
        await page.wait_for_function("() => window.__done === true", timeout=120000)
        await page.wait_for_timeout(500)

        # screenshot
        try:
            await locator.screenshot(path=output_png)
        except Exception:
            await page.screenshot(path=output_png, full_page=True)

        await ctx.close()
        await browser.close()


# ---------- Main flow ----------

def main():
    ap = argparse.ArgumentParser(description="Snapshot a Scenario chart (runs app + browser, auto-lines, TF, screenshot).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--scenario", help="Import dotted path 'module:attr' or 'module.attr' that yields a Scenario or factory.")
    g.add_argument("--scenario-py", help="Path to a .py file that defines SCENARIO (object or zero-arg factory).")

    ap.add_argument("--output", required=True, help="Output PNG path.")
    ap.add_argument("--chart-selector", default="#chartContainer", help="CSS selector for the chart container.")
    ap.add_argument("--file-dtformat", default=DEFAULT_FILE_DTFORMAT, help="CSV file Date+Time format ('auto' to autodetect).")
    ap.add_argument("--bars-per-second", default="2000", help="Playback speed (higher = faster).")
    ap.add_argument("--port", default="5001", help="App port.")

    # Optional start/end override (if omitted we use full CSV)
    ap.add_argument("--start", help="Optional start datetime (inclusive). If omitted: start of CSV.")
    ap.add_argument("--end", help="Optional end datetime (inclusive). If omitted: end of CSV.")

    args = ap.parse_args()

    # 1) Load scenario
    if args.scenario:
        obj = import_obj(args.scenario)
        scenario = obj() if callable(obj) else obj
    else:
        scenario = load_scenario_from_py(Path(args.scenario_py))

    # Pull essentials from Scenario
    csv_path: Path = Path(scenario.csv_path)
    tf: str = getattr(scenario, "strategy_tf", None) or "5m"  # default to 5m
    # Note: we will not use scenario.pair to avoid mismatch with app; we'll ask the app.

    # 2) Determine start/end from CSV if not provided
    file_dtformat = None if (args.file_dtformat or "").lower() == "auto" else args.file_dtformat
    all_dts = load_csv_times(csv_path, file_dtformat=file_dtformat)
    if not all_dts:
        raise SystemExit(f"No data rows in {csv_path}")
    csv_start_dt = all_dts[0]
    csv_end_dt = all_dts[-1]

    start_dt = parse_any_dt(args.start) if args.start else csv_start_dt
    end_dt   = parse_any_dt(args.end)   if args.end   else csv_end_dt
    if end_dt < start_dt:
        raise SystemExit("End datetime must be >= start datetime.")
    start_epoch = int(start_dt.timestamp())
    end_epoch   = int(end_dt.timestamp())

    # 3) Launch app with env overrides so it loads the right CSV/time window fast
    env = os.environ.copy()
    env["CSV_FILE"] = str(csv_path.resolve())
    env["START_ISO"] = start_dt.isoformat()
    env["END_ISO"]   = end_dt.isoformat()
    env["BARS_PER_SECOND"] = str(args.bars_per_second)
    env["FLASK_RUN_PORT"] = args.port
    # NOTE: We rely on the small app patch where app.py reads these envs.

    proc = subprocess.Popen([sys.executable, "app.py"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    try:
        # 4) Wait for app up
        wait_for_http(APP_URL + "/api/pair", timeout=30)
        pair_resp = requests.get(APP_URL + "/api/pair", timeout=5)
        pair_resp.raise_for_status()
        pair = pair_resp.json()["pair"]

        # 5) Build line schedule from scenario.lines
        #    immediate: no 'at', scheduled: 'row:N' or datetime string
        idx_map = times_to_index_map(all_dts)
        # Find index of start dt to compute bar offsets
        start_idx = idx_map.get(int(start_dt.timestamp()))
        if start_idx is None:
            # find nearest index >= start_dt
            nearest = min(range(len(all_dts)), key=lambda i: abs((all_dts[i]-start_dt).total_seconds()))
            start_idx = nearest

        # Parse Scenario lines
        immediate: List[Tuple[float]] = []
        scheduled: List[Tuple[int, float]] = []  # (at_ts, price)
        for i, spec in enumerate(getattr(scenario, "lines", []) or []):
            parsed = parse_line_spec(spec)
            price = parsed["level"]
            at_ts = resolve_at_to_epoch(parsed["at_raw"], all_dts)
            if at_ts is None:
                immediate.append((price,))
            else:
                scheduled.append((at_ts, price))

        # 6) Add immediate lines before playback
        for (price,) in immediate:
            add_line(pair, price)

        # 7) Start a scheduler thread to add scheduled lines at the proper wall-clock time during playback
        #    Delay calculation: bars are pushed one per emit_delay (1/BPS).
        #    We compute offset = (index(at_ts) - start_idx) * emit_delay.
        emit_delay = 1.0 / float(args.bars_per_second)

        def schedule_lines():
            for at_ts, price in sorted(scheduled):
                # map at_ts to CSV index (closest >= at_ts)
                at_idx = None
                sec = int(at_ts)
                if sec in idx_map:
                    at_idx = idx_map[sec]
                else:
                    # pick closest index
                    at_idx = min(range(len(all_dts)), key=lambda i: abs((int(all_dts[i].timestamp()) - sec)))
                if at_idx < start_idx:
                    # already in the past for this stream -> add right now
                    delay = 0.0
                else:
                    bars_ahead = at_idx - start_idx
                    delay = max(0.0, bars_ahead * emit_delay)
                time.sleep(delay)
                try:
                    add_line(pair, price)
                except Exception as e:
                    print(f"[scheduler] add_line failed: {e}", file=sys.stderr)

        if scheduled:
            th = threading.Thread(target=schedule_lines, daemon=True)
            th.start()

        # 8) Headless browser: open and start stream, wait for end, screenshot
        asyncio.run(take_screenshot(args.output, tf, int(start_epoch), args.chart_selector))
        print(f"Snapshot saved to {args.output}")

    finally:
        # 9) Stop the app
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

if __name__ == "__main__":
    main()