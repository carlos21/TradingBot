#!/usr/bin/env python3
"""
snapshot_all_scenarios.py

Discovers Scenario objects and renders a screenshot for each by:
- launching your Flask/Socket.IO app with the scenario's CSV and time window
- adding scenario lines (immediate + scheduled) via /api/lines
- starting the stream at the scenario's timeframe (default 5m)
- waiting for 'stream_end' and taking a screenshot of the chart container

Assumptions:
- Your app reads env vars CSV_FILE, START_ISO, END_ISO, BARS_PER_SECOND (patch shown earlier)
- Your app emits 'stream_end' on Socket.IO when playback finishes (tiny patch shown earlier)
- Frontend chart container selector is '#chart-root' (override with --chart-selector)
- Scenario class lives in tests/scenario_model.py, and scenario files live under tests/scenarios/

Discovery rules for each Python file that matches --glob (default: tests/scenarios/**/*.py):
- SCENARIO: a Scenario object or a zero-arg callable returning one
- SCENARIOS: an iterable of Scenario objects or zero-arg callables
- Any zero-arg top-level function whose name starts with 'scenario' returning a Scenario

Usage:
  python scripts/snapshot_all_scenarios.py --outdir snapshots
  python scripts/snapshot_all_scenarios.py --glob "tests/scenarios/**/*.py" --outdir snapshots --file-dtformat auto
"""

import argparse
import asyncio
import csv
import importlib.util
import os
import runpy
import signal
import subprocess
import sys
import threading
import time
import inspect
from dataclasses import is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

try:
    from dateutil import parser as dtparser
    from dateutil.tz import gettz
except Exception:
    dtparser = None
    gettz = None

from playwright.async_api import async_playwright

APP_URL = "http://127.0.0.1:5001"
DEFAULT_FILE_DTFORMAT = "%d/%m/%Y %H:%M:%S"  # "31/07/2024 15:12:00"

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # repo root (one up from scripts/)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------- tiny CSV helpers ----------

def parse_any_dt(s: str) -> datetime:
    if s is None:
        raise ValueError("datetime string is None")
    if dtparser is not None:
        return dtparser.parse(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"Install python-dateutil or pass a supported datetime format: {s!r}")

def load_csv_times(csv_path: Path, file_dtformat: Optional[str]) -> List[datetime]:
    dts: List[datetime] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        rdr = csv.DictReader(f, delimiter=";")
        for row in rdr:
            dt = (datetime.strptime(f"{row['Date']} {row['Time']}", file_dtformat)
                  if file_dtformat else parse_any_dt(f"{row['Date']} {row['Time']}"))
            dts.append(dt)
    return dts

def map_unix_to_index(dts: List[datetime]) -> Dict[int, int]:
    m: Dict[int, int] = {}
    for i, dt in enumerate(dts):
        ts = int(dt.timestamp())
        if ts not in m:
            m[ts] = i
    return m

# ---------- HTTP + app helpers ----------

def wait_for_http(url: str, timeout=30):
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
    r = requests.post(f"{APP_URL}/api/lines", json={"pair": pair, "price": float(price)}, timeout=10)
    if not r.ok:
        raise SystemExit(f"POST /api/lines failed: {r.status_code} {r.text}")

# ---------- scenario parsing ----------

def is_scenario_instance(x: Any) -> bool:
    # Loose duck-typing: must have csv_path and lines
    return hasattr(x, "csv_path") and hasattr(x, "lines")

def discover_scenarios(pyfile: Path) -> List[Any]:
    """
    Execute `pyfile` and return a list of Scenario *instances* discovered inside.
    Discovery rules (in this order):
      1) SCENARIO = Scenario(...)        # instance
         SCENARIO = scenario_factory()   # zero-arg function returning instance
      2) SCENARIOS = [Scenario(...), ...] or a zero-arg function returning an iterable
      3) Any zero-arg top-level function whose name starts with 'scenario' returning an instance

    Safety:
      - Only zero-arg *functions* are auto-called. Classes are never called.
      - If SCENARIO/SCENARIOS contains the class (not an instance), a TypeError is raised
        so the caller can show a clear "Did you mean SCENARIO = Scenario(...)" message.
    """
    # duck-typing: what *looks* like a Scenario instance?
    def looks_like_scenario(x: Any) -> bool:
        return hasattr(x, "csv_path") and hasattr(x, "lines")

    def normalize(obj: Any) -> Any:
        """Return a Scenario instance if possible; never call classes."""
        # Already an instance?
        if looks_like_scenario(obj):
            return obj

        # Call only zero-arg FUNCTIONS (not classes)
        if inspect.isfunction(obj):
            sig = inspect.signature(obj)
            req = [
                p for p in sig.parameters.values()
                if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            if not req:  # zero required positional params
                res = obj()
                if looks_like_scenario(res):
                    return res
                return res  # caller will validate and potentially error

        # Do not call classes; return as-is
        return obj

    ns = runpy.run_path(str(pyfile))
    found: List[Any] = []

    # 1) SCENARIO
    if "SCENARIO" in ns:
        s = normalize(ns["SCENARIO"])
        if inspect.isclass(s):
            raise TypeError(
                f"{pyfile} exports the Scenario class in SCENARIO. "
                f"Did you mean 'SCENARIO = Scenario(...)'?"
            )
        if looks_like_scenario(s):
            found.append(s)
        elif s is not None:
            raise TypeError(
                f"{pyfile} SCENARIO is not a Scenario instance (got {type(s).__name__})."
            )

    # 2) SCENARIOS
    if "SCENARIOS" in ns:
        seq = ns["SCENARIOS"]
        if inspect.isfunction(seq):
            # zero-arg factory returning iterable
            sig = inspect.signature(seq)
            req = [
                p for p in sig.parameters.values()
                if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            if not req:
                seq = seq()
        if isinstance(seq, Iterable) and not isinstance(seq, (str, bytes, dict)):
            for item in seq:
                s = normalize(item)
                if inspect.isclass(s):
                    raise TypeError(
                        f"{pyfile} SCENARIOS contains the Scenario class. "
                        f"Use Scenario(...) instances."
                    )
                if looks_like_scenario(s):
                    found.append(s)
                else:
                    raise TypeError(
                        f"{pyfile} SCENARIOS element is not a Scenario instance (got {type(s).__name__})."
                    )
        else:
            raise TypeError(f"{pyfile} SCENARIOS is not an iterable of scenarios.")

    # 3) Any zero-arg function named scenario*
    for name, val in ns.items():
        if name.lower().startswith("scenario") and inspect.isfunction(val):
            sig = inspect.signature(val)
            req = [
                p for p in sig.parameters.values()
                if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            if req:
                continue  # needs args; skip
            res = val()
            if looks_like_scenario(res):
                found.append(res)
            elif inspect.isclass(res):
                raise TypeError(
                    f"{pyfile} function {name} returned the Scenario class; return an instance instead."
                )
            else:
                raise TypeError(
                    f"{pyfile} function {name} did not return a Scenario instance (got {type(res).__name__})."
                )

    return found

# ---------- line scheduling (parse like Scenario’s flexible format) ----------

def parse_line_spec(line_row: List[Any]) -> Dict[str, Any]:
    lid, at_raw = None, None
    flat = list(line_row)
    # time token
    for i, x in enumerate(list(flat)):
        if isinstance(x, str) and (x.startswith("row:") or x[:4].isdigit()):
            at_raw = flat.pop(i); break
    # id token
    for i, x in enumerate(list(flat)):
        if isinstance(x, str) and x.upper().startswith("L"):
            lid = flat.pop(i); break
    if len(flat) != 2:
        raise ValueError(f"Bad line spec: {line_row!r}. Expect [dir, level] (+optional id/at)")
    a, b = flat
    if isinstance(a, str):
        direction, level = a, float(b)
    else:
        direction, level = b, float(a)
    direction = {"buy":"long","sell":"short"}.get(direction.lower(), direction.lower())
    return {"id": lid, "direction": direction, "level": level, "at_raw": at_raw}

def resolve_at_to_epoch(at_raw: Optional[str], all_dts: List[datetime]) -> Optional[int]:
    if at_raw is None: return None
    if at_raw.startswith("row:"):
        n = int(at_raw.split(":",1)[1])
        if n <= 0 or n > len(all_dts):
            raise ValueError(f"{at_raw} out of range 1..{len(all_dts)}")
        return int(all_dts[n-1].timestamp())
    dt = parse_any_dt(at_raw)
    if dt.tzinfo is None and gettz is not None:
        dt = dt.replace(tzinfo=gettz("America/New_York"))
    return int(dt.timestamp())

# ---------- Playwright screenshot ----------

async def shoot(output_png: Path, timeframe: str, from_epoch: int, selector: str):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()
        await page.goto(APP_URL + "/", wait_until="domcontentloaded")

        # Install our hooks inside the page and wait for the socket to connect
        await page.expose_function("__py_log", lambda *args: print("[page]", *args))

        # Create or reuse the socket, wait for 'connect', set completion conditions
        await page.evaluate("""
            (function () {
                window.__done = false;

                // Prefer existing socket created by the page (if any)
                let sock = window.socket || (window.io ? window.io() : null);
                if (!sock) throw new Error("Socket.IO client not found on the page");

                window.socket = sock;

                // If not connected yet, we'll wait for 'connect' below
                sock.on('stream_end', () => { window.__done = true; });
                sock.on('stream_status', (p) => {
                    if (p && p.playing === false) window.__done = true;
                });

                // Stash a helper promise we can await from Python
                window.__waitConnected = new Promise((resolve) => {
                    if (sock.connected) return resolve(true);
                    sock.once('connect', () => resolve(true));
                });
            })();
        """)

        # Wait for the socket to actually connect
        await page.evaluate("() => window.__waitConnected")

        # Start the stream (object-arg form for Playwright)
        await page.evaluate(
            """(args) => {
                window.socket.emit('start_stream', { timeframe: args.tf, fromTime: args.fromTs });
            }""",
            {"tf": timeframe, "fromTs": int(from_epoch)}
        )

        # Ensure the chart is visible
        locator = page.locator(selector)
        await locator.wait_for(state="visible", timeout=25000)

        # Wait until the backend signals EOF via either event
        await page.wait_for_function("() => window.__done === true", timeout=180000)

        # Small settle so the last bar/lines render
        await page.wait_for_timeout(400)

        # Try element screenshot; fall back to full-page
        try:
            await locator.screenshot(path=str(output_png))
        except Exception:
            await page.screenshot(path=str(output_png), full_page=True)

        await ctx.close()
        await browser.close()

# ---------- per-scenario run ----------

def sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in ("-","_") else "_" for c in name)[:120]

def run_one_scenario(
    scenario: Any,
    outdir: Path,
    chart_selector: str,
    file_dtformat: Optional[str],
    bars_per_second: int,
    app_path: Path,
    port: str,
):
    name = getattr(scenario, "name", sanitize(Path(getattr(scenario, "csv_path")).stem))
    tf   = getattr(scenario, "strategy_tf", None) or "5m"  # default to 5m
    csv_path: Path = Path(scenario.csv_path)
    lines_spec: List[List[Any]] = list(getattr(scenario, "lines", []) or [])

    # Load CSV and set full window if not provided
    fdt = None if (file_dtformat or "").lower() == "auto" else file_dtformat
    all_dts = load_csv_times(csv_path, file_dtformat=fdt)
    if not all_dts:
        print(f"[skip] {name}: CSV is empty: {csv_path}")
        return
    start_dt, end_dt = all_dts[0], all_dts[-1]
    start_epoch = int(start_dt.timestamp())

    # Launch app with env overrides
    env = os.environ.copy()
    env["CSV_FILE"] = str(csv_path.resolve())
    env["START_ISO"] = start_dt.isoformat()
    env["END_ISO"]   = end_dt.isoformat()
    env["BARS_PER_SECOND"] = str(bars_per_second)
    env["FLASK_RUN_PORT"] = port

    print(f"[run] {name}  tf={tf}  rows={len(all_dts)}")
    proc = subprocess.Popen([sys.executable, str(app_path)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    try:
        wait_for_http(APP_URL + "/api/pair", timeout=30)
        pair = requests.get(APP_URL + "/api/pair", timeout=5).json()["pair"]

        # Build line schedule
        idx_map = map_unix_to_index(all_dts)
        start_idx = idx_map.get(start_epoch, 0)
        emit_delay = 1.0 / float(bars_per_second)

        immediate: List[float] = []
        scheduled: List[Tuple[int, float]] = []
        for spec in lines_spec:
            p = parse_line_spec(spec)
            price = p["level"]
            at_ts = resolve_at_to_epoch(p["at_raw"], all_dts) if p["at_raw"] else None
            if at_ts is None:
                immediate.append(price)
            else:
                scheduled.append((at_ts, price))

        # Post immediate lines now
        for price in immediate:
            add_line(pair, price)

        # Scheduler for timed lines
        def scheduler():
            for at_ts, price in sorted(scheduled):
                # map at_ts → index
                sec = int(at_ts)
                if sec in idx_map:
                    at_idx = idx_map[sec]
                else:
                    at_idx = min(range(len(all_dts)),
                                 key=lambda i: abs(int(all_dts[i].timestamp()) - sec))
                if at_idx <= start_idx:
                    delay = 0.0
                else:
                    delay = (at_idx - start_idx) * emit_delay
                time.sleep(delay)
                try:
                    add_line(pair, price)
                except Exception as e:
                    print(f"[{name}] warn: add_line failed: {e}", file=sys.stderr)

        if scheduled:
            threading.Thread(target=scheduler, daemon=True).start()

        # Shoot
        out_png = outdir / f"{sanitize(name)}_{tf}.png"
        asyncio.run(shoot(out_png, tf, start_epoch, chart_selector))
        print(f"[ok]  {name} → {out_png}")

    finally:
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description="Discover and snapshot ALL scenarios.")
    ap.add_argument("--glob", default="tests/scenarios/**/*.py",
                    help="Glob pattern to find scenario files (default: tests/scenarios/**/*.py).")
    ap.add_argument("--outdir", required=True, help="Directory to write PNG snapshots.")
    ap.add_argument("--chart-selector", default="#chartContainer",
                    help="CSS selector for the chart container (default '#chart-root').")
    ap.add_argument("--file-dtformat", default=DEFAULT_FILE_DTFORMAT,
                    help="Datetime format for CSV Date+Time; use 'auto' to autodetect.")
    ap.add_argument("--bars-per-second", type=int, default=2000,
                    help="Playback speed (default 2000 bars/sec).")
    ap.add_argument("--app-path", default="app.py", help="Path to your app entry (default app.py).")
    ap.add_argument("--port", default="5001", help="App port (must match frontend).")
    args = ap.parse_args()

    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    app_path = Path(args.app_path)

    # Find scenario files
    files = sorted(Path().glob(args.glob))
    if not files:
        raise SystemExit(f"No scenario files found for glob: {args.glob}")

    # Discover scenarios across files
    scenarios: List[Any] = []
    for f in files:
        try:
            scenarios.extend(discover_scenarios(f))
        except Exception as e:
            print(f"[warn] skipping {f}: {e}", file=sys.stderr)

    if not scenarios:
        raise SystemExit("Found 0 scenarios.")

    print(f"Discovered {len(scenarios)} scenarios\n")

    # Run sequentially (avoid port collisions)
    for s in scenarios:
        try:
            run_one_scenario(
                scenario=s,
                outdir=outdir,
                chart_selector=args.chart_selector,
                file_dtformat=args.file_dtformat,
                bars_per_second=args.bars_per_second,
                app_path=app_path,
                port=args.port,
            )
        except Exception as e:
            name = getattr(s, "name", "<unnamed>")
            print(f"[fail] {name}: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()