#!/usr/bin/env python3
"""
Run YAML scenarios (no line direction in YAML) with pure dependency injection.

Per scenario:
- Slice --source-csv to <outdir>/<scenario-name>/data.csv
- Start a fresh in-process Flask/Socket.IO app via create_app(...) (NO env vars)
- Post lines via HTTP (no direction; server infers)
- Play the whole CSV and take <outdir>/<scenario-name>/snapshot_<tf>.png at END
- Guaranteed clean state: no lines/trades carry over between scenarios
"""

import argparse
import asyncio
import csv
import sys
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

import yaml
import requests
from dateutil import parser as dtparser
from playwright.async_api import async_playwright

# --- repo root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# --- app imports (pure DI)
from flask import request as flask_request  # for shutdown/reset routes
from app_factory import create_app, Repositories, StrategyNumbers
from tests.fakes import FakeLineRepository, FakeTradeRepository
from src.data_sources.csv_datasource import CSVDataSource
from src.strategies.liquidity_strategy import StrategyOptions, LineRemovalMode
from src.strategies.entry_context import (
    open_trades_limit_filter, max_bounce_filter
)
# IMPORTS UPDATED TO MATCH APP.PY (V2 + Triggers)
from src.strategies.liquidity_strategy_v2 import LiquidityStrategyV2
from src.strategies.triggers import wick_near_line_trigger, three_candle_reversal_trigger
from src.bars_loader import BarsLoader
from src.services.trade_manager import TradeManager

APP_HOST = "127.0.0.1"

# ---------------- helpers ----------------

def sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in ("-","_"," ") else "_" for c in name).strip().replace(" ", "_")

_DT_FORMATS = [
    "%d/%m/%Y %H:%M:%S",  # 31/07/2024 15:12:00
    "%Y.%m.%d %H:%M",     # 2024.07.31 15:12
    "%Y-%m-%d %H:%M:%S",  # 2024-07-31 15:12:00
    "%m/%d/%Y %H:%M:%S",  # 07/31/2024 15:12:00
]

def _parse_dt_flexible(date_str: str, time_str: str) -> datetime:
    s = f"{date_str} {time_str}".strip()
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return dtparser.parse(s)

def _parse_any_dt_naive(s: str) -> datetime:
    dt = dtparser.parse(s)
    return dt.replace(tzinfo=None)

def slice_csv(source_csv: Path, out_csv: Path, start: str, end: str):
    """
    Auto-detect ',' vs ';' and keep rows with Date+Time in [start,end].
    Logs the CSV's actual date range so you can see gaps (e.g., weekends).
    """
    start_dt = _parse_any_dt_naive(start)
    end_dt   = _parse_any_dt_naive(end)
    if end_dt < start_dt:
        raise ValueError("end < start")

    out_csv.parent.mkdir(parents=True, exist_ok=True)

    with open(source_csv, newline="", encoding="utf-8") as fin:
        sample = fin.read(4096); fin.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        except csv.Error:
            class _D: ...
            dialect = _D(); dialect.delimiter = "," if ("," in sample and ";" not in sample) else ";"

        rdr = csv.DictReader(fin, dialect=dialect)
        fieldnames = rdr.fieldnames or ["Date","Time","Open","High","Low","Close","Volume"]

        rows_out, first_dt, last_dt = [], None, None
        for row in rdr:
            date_val = (row.get("Date") or row.get("date") or "").strip()
            time_val = (row.get("Time") or row.get("time") or "").strip()
            if not date_val or not time_val:
                continue
            try:
                dt = _parse_dt_flexible(date_val, time_val).replace(tzinfo=None)
            except Exception:
                continue
            if first_dt is None:
                first_dt = dt
            last_dt = dt
            if start_dt <= dt <= end_dt:
                rows_out.append(row)

    with open(out_csv, "w", newline="", encoding="utf-8") as fout:
        w = csv.DictWriter(fout, fieldnames=fieldnames, delimiter=dialect.delimiter)
        w.writeheader()
        for r in rows_out:
            w.writerow(r)

    print(f"[slice] {source_csv.name} → {out_csv}  kept_rows={len(rows_out)} "
          f"window=[{start_dt} .. {end_dt}]  csv_range=[{first_dt} .. {last_dt}] "
          f"delim='{dialect.delimiter}'")
    if last_dt and end_dt > last_dt:
        print(f"[slice] ⚠️ end > last CSV row ({last_dt}) — that's fine; you get all available rows.")

def wait_http_ok(base_url: str, timeout: int = 30):
    t0 = time.time()
    url = base_url.rstrip("/") + "/api/pair"
    while time.time() - t0 < timeout:
        try:
            r = requests.get(url, timeout=2)
            if r.ok:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for {url}")

async def shoot_png(base_url: str, output_png: Path, timeframe: str,
                    selector: str = "#chartContainer",
                    snapshot_at: str = "end"):
    """
    Take the screenshot at the end of playback, but ensure trade lines
    (entry/SL/TP) are ALWAYS visible by adding persistent strategy lines
    as soon as trades open.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()
        await page.goto(base_url + "/", wait_until="domcontentloaded")

        # Push trade marker lines when trades open; wait for end.
        await page.evaluate(
            """async (args) => {
                const baseUrl = new URL(location.href).origin;
                // fetch pair
                const pair = (await (await fetch(baseUrl + "/api/pair")).json()).pair;

                window.__done = false;
                window.__added = new Set();

                const postLine = async (price) => {
                    const key = price.toFixed(8);
                    if (window.__added.has(key)) return;
                    window.__added.add(key);
                    try {
                        await fetch(baseUrl + "/api/lines", {
                            method: "POST",
                            headers: {"Content-Type":"application/json"},
                            body: JSON.stringify({ pair, price })
                        });
                    } catch (_) {}
                };

                const sock = (window.io && window.io()) || window.socket;
                if (!sock) throw new Error("Socket.IO client not found");
                window.socket = sock;

                sock.on('trade_open', (t) => {
                    const entry = t.entry ?? t.entry_price ?? t.price ?? t.level ?? t.entryPrice;
                    const sl    = t.stop_loss ?? t.sl ?? t.stopLoss;
                    const tp    = t.take_profit ?? t.tp ?? t.takeProfit;
                    if (typeof entry === "number") postLine(entry);
                    if (typeof sl    === "number") postLine(sl);
                    if (typeof tp    === "number") postLine(tp);
                });

                sock.on('stream_end', () => { window.__done = true; });

                if (!sock.connected) await new Promise(res => sock.once('connect', res));
                window.socket.emit('start_stream', { timeframe: args.tf, fromTime: 0 });
            }""",
            {"tf": timeframe}
        )

        locator = page.locator(selector)
        await locator.wait_for(state="visible", timeout=25000)
        await page.wait_for_function("() => window.__done === true", timeout=180000)
        await page.wait_for_timeout(500)  # let last paints render

        try:
            await locator.screenshot(path=str(output_png))
        except Exception:
            await page.screenshot(path=str(output_png), full_page=True)

        await ctx.close()
        await browser.close()

def add_line_http(base_url: str, pair: str, price: float):
    r = requests.post(base_url.rstrip("/") + "/api/lines",
                      json={"pair": pair, "price": float(price)},
                      timeout=10)
    if not r.ok:
        raise RuntimeError(f"POST /api/lines failed: {r.status_code} {r.text}")

def start_server_in_thread(pair: str,
                           csv_out: Path,
                           tf_for_strategy: str,
                           bars_per_second: int,
                           port: int) -> Tuple[threading.Thread, str, Any]:
    """
    Fresh app per scenario. Also exposes:
      - /__shutdown   → terminate server
      - /__reset_all  → clear lines/trades in memory (safety)
    """
    repos = Repositories(lines=FakeLineRepository(), trades=FakeTradeRepository())
    ds = CSVDataSource(pair=pair, filename=str(csv_out), bars_per_second=float(bars_per_second))
    numbers = StrategyNumbers(min_stop_loss=10.0, max_bounce=40.0, extra_sl_space=0.0)

    # UPDATED: Use same triggers as app.py (V2)
    options = StrategyOptions(
        line_removal_mode=LineRemovalMode.NEVER,
        triggers=[wick_near_line_trigger, three_candle_reversal_trigger],
        entry_filters=[open_trades_limit_filter(1), max_bounce_filter(numbers.max_bounce)],
    )

    wiring = create_app(
        pair=pair,
        data_source=ds,
        repos=repos,
        numbers=numbers,
        options=options,
        timeframes=[tf_for_strategy], # V2 requires list of timeframes
        bootstrap_existing_lines=False,   # <-- start clean
    )

    # Align pacing
    wiring.loader.bars_per_second = float(bars_per_second)
    wiring.loader._emit_delay     = 1.0 / float(bars_per_second)

    # --- maintenance routes (internal)
    def _shutdown():
        func = flask_request.environ.get('werkzeug.server.shutdown')
        if func:
            func()
        return "OK"

    def _reset_all():
        # wipe in-memory state (safety even though we create fresh each run)
        try:
            wiring.strategy.strategy_lines.clear()
            wiring.strategy.open_trades.clear()
            if hasattr(wiring.trade_manager, "open_trades"):
                wiring.trade_manager.open_trades.clear()
            if hasattr(repos.lines, "_store"):
                repos.lines._store.clear()
                repos.lines._seq = 0
            if hasattr(repos.trades, "inserted"):
                repos.trades.inserted.clear()
                repos.trades.closed.clear()
                repos.trades._seq = 0
        except Exception:
            pass
        return "OK"

    wiring.app.add_url_rule("/__shutdown",  "__shutdown",  _shutdown,  methods=["POST","GET"])
    wiring.app.add_url_rule("/__reset_all", "__reset_all", _reset_all, methods=["POST","GET"])

    base_url = f"http://{APP_HOST}:{port}"

    def _run():
        wiring.socketio.run(
            wiring.app,
            host=APP_HOST,
            port=port,
            debug=False,
            use_reloader=False,
            allow_unsafe_werkzeug=True,
        )

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t, base_url, wiring

def stop_server(base_url: str, thread: threading.Thread, join_timeout: float = 5.0):
    # ask app to stop
    try:
        requests.post(base_url.rstrip("/") + "/__shutdown", timeout=2)
    except Exception:
        pass
    # wait for the thread to exit to avoid state bleed
    if thread and thread.is_alive():
        thread.join(timeout=join_timeout)

# ---- quick validator (no HTTP) ----

def _validate_inproc(csv_path: Path, pair: str, tf: str,
                     lines: List[Dict[str, Any]],
                     expect: Optional[Dict[str, Any]]) -> Tuple[bool, str, Optional[Dict[str,Any]]]:
    class DummySock:
        def __init__(self): self.events=[]
        def emit(self, e, p): self.events.append((e,p))
        def start_background_task(self, target, *a, **k): return target(*a, **k)

    sock   = DummySock()
    lines_repo  = FakeLineRepository()
    trades_repo = FakeTradeRepository()
    
    # UPDATED: Use V2 triggers
    options = StrategyOptions(
        line_removal_mode=LineRemovalMode.NEVER,
        triggers=[wick_near_line_trigger, three_candle_reversal_trigger],
        entry_filters=[open_trades_limit_filter(1), max_bounce_filter(40.0)]
    )

    # 1. Init TradeManager first
    tm = TradeManager(trade_repository=trades_repo, socketio=sock)

    # 2. Pass TM to strategy factory
    strat = __make_strategy(sock, lines_repo, trades_repo, tm, options, tf)
    
    ds = CSVDataSource(pair=pair, filename=str(csv_path), bars_per_second=10000.0)
    if not getattr(ds, "_bars", None):
        raise RuntimeError(
            f"Sliced CSV has 0 rows: {csv_path}\n"
            f"- Verify your start/end exist in the source CSV (weekends/holidays are empty for NQ)."
        )

    def to_epoch(at_raw: Optional[str]) -> int:
        if not at_raw:
            return int(ds._bars[0]['time'])
        if str(at_raw).startswith("row:"):
            n = int(str(at_raw).split(":",1)[1])
            return int(ds._bars[max(1,n)-1]['time'])
        return int(_parse_any_dt_naive(str(at_raw)).timestamp())

    sched = [{"price": float(li["price"]),
              "at": to_epoch(li.get("at")),
              "added": False}
             for li in lines]

    def cb(bar):
        for item in sched:
            if not item["added"] and bar.get('time', 0) >= item["at"]:
                last_close = ds._played_bars[-1]['close'] if ds._played_bars else bar['close']
                direction  = 'short' if last_close < item["price"] else 'long'
                strat.add_strategy_line(f"L{len(strat.strategy_lines)+1}", item["price"])
                item["added"] = True
        tm.handle_new_1m_bar(bar)
        strat.on_raw_bar(bar)

    loader = BarsLoader(ds, sock, cb, bars_per_second=10000.0)
    loader.start(0)

    opened = trades_repo.inserted[0] if trades_repo.inserted else None
    if not expect:
        return True, "OK", opened
    if not opened:
        return False, "No trade_open", None

    tol = float(expect.get("tolerance", 1e-6))
    def ok(a,b): return abs(float(a)-float(b)) <= tol
    checks = []
    if "entry" in expect: checks.append(("entry_price", opened["entry_price"], expect["entry"]))
    if "sl"    in expect: checks.append(("stop_loss",   opened["stop_loss"],   expect["sl"]))
    if "tp"    in expect: checks.append(("take_profit", opened["take_profit"], expect["tp"]))
    for field, got, want in checks:
        if not ok(got, want):
            return False, f"{field} mismatch: got {got} want {want} tol={tol}", opened
    return True, "OK", opened

# UPDATED: Accept TradeManager and use V2
def __make_strategy(sock, lines_repo, trades_repo, trade_manager, options, tf):
    return LiquidityStrategyV2(
        min_stop_loss=10.0, 
        max_bounce=40.0, 
        extra_sl_space=0.0,
        socketio=sock, 
        line_repository=lines_repo, 
        trade_repository=trades_repo,
        trade_manager=trade_manager, # Passed here
        options=options, 
        timeframes=[tf] # V2 expects list
    )

# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser(
        description="Slice a large 1m CSV into per-scenario windows, validate, and snapshot. "
                    "Outputs are placed in <outdir>/<scenario-name>/ (data.csv + snapshot_<tf>.png)."
    )
    ap.add_argument("--yaml", required=True, help="YAML file with scenarios.")
    ap.add_argument("--source-csv", required=True, help="Path to the large 1m CSV to slice from.")
    ap.add_argument("--outdir", required=True,
                    help="Base directory where per-scenario folders will be created.")
    ap.add_argument("--port", type=int, default=5001,
                    help="Base port; each scenario uses base+index for isolation.")
    ap.add_argument("--bars-per-second", type=int, default=2000,
                    help="Playback speed for the app (default: 2000 bars/sec).")
    ap.add_argument("--chart-selector", default="#chartContainer",
                    help="CSS selector for the chart container (default: #chartContainer).")
    args = ap.parse_args()

    ydoc = yaml.safe_load(Path(args.yaml).read_text())
    scenarios = ydoc.get("scenarios") or []
    if not scenarios:
        raise SystemExit("No scenarios defined.")

    source_csv = Path(args.source_csv)
    outroot = Path(args.outdir); outroot.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(scenarios)} scenarios\n")

    for idx, sc in enumerate(scenarios):
        name  = sc.get("name") or f"scenario_{idx+1}"
        pair  = sc.get("pair", "NQ")
        tf    = sc.get("tf", "5m")
        start = sc["start"]
        end   = sc["end"]
        lines = sc.get("lines", [])
        expect = sc.get("expect")
        snapshot = sc.get("snapshot", True)

        # per-scenario folder + data.csv
        sdir    = outroot / sanitize(name)
        csv_out = sdir / "data.csv"
        sdir.mkdir(parents=True, exist_ok=True)
        slice_csv(source_csv, csv_out, start, end)

        # quick validation
        try:
            ok, reason, opened = _validate_inproc(csv_out, pair, tf, lines, expect)
            status = "OK" if ok else f"FAIL: {reason}"
            print(f"[check] {name}: {status}")
            if opened:
                print(f"        entry={opened['entry_price']} sl={opened['stop_loss']} "
                      f"tp={opened['take_profit']} type={opened.get('trade_type','?')}")
        except RuntimeError as e:
            print(f"[check] {name}: FAIL: {e}")
            opened = None

        if not snapshot:
            continue

        # fresh server (UNIQUE PORT per scenario)
        port = int(args.port) + idx
        thread, base_url, wiring = start_server_in_thread(
            pair=pair,
            csv_out=csv_out,
            tf_for_strategy=tf,
            bars_per_second=args.bars_per_second,
            port=port,
        )
        try:
            wait_http_ok(base_url, timeout=30)

            # hard reset (safety) even though it's a fresh app
            try:
                requests.post(base_url.rstrip("/") + "/__reset_all", timeout=3)
            except Exception:
                pass

            # Immediate lines now (no direction; server infers from last_close).
            for li in (li for li in lines if not li.get("at")):
                add_line_http(base_url, pair, li["price"])

            # Timed lines scheduled relative to playback indices
            timed = [li for li in lines if li.get("at")]
            if timed:
                # Read sliced CSV times to map at → index
                all_dts = []
                with open(csv_out, newline="", encoding="utf-8") as f:
                    sample = f.read(4096); f.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=",;")
                    except csv.Error:
                        class _D: ...
                        dialect = _D(); dialect.delimiter = "," if ("," in sample and ";" not in sample) else ";"
                    rdr = csv.DictReader(f, dialect=dialect)
                    for row in rdr:
                        all_dts.append(_parse_dt_flexible(
                            (row.get("Date") or row.get("date") or "").strip(),
                            (row.get("Time") or row.get("time") or "").strip()
                        ))
                if all_dts:
                    idx_map = {int(dt.timestamp()): i for i, dt in enumerate(all_dts)}
                    start_idx  = 0
                    emit_delay = 1.0 / float(args.bars_per_second)

                    def _resolve_at_to_index(at_str: str) -> int:
                        if at_str.startswith("row:"):
                            n = int(at_str.split(":",1)[1])
                            return max(0, min(n-1, len(all_dts)-1))
                        sec = int(_parse_any_dt_naive(at_str).timestamp())
                        return idx_map.get(sec, min(range(len(all_dts)),
                                                    key=lambda i: abs(int(all_dts[i].timestamp()) - sec)))

                    def post_timed():
                        items = []
                        for li in timed:
                            try:
                                items.append((_resolve_at_to_index(str(li["at"])), li))
                            except Exception:
                                continue
                        items.sort(key=lambda x: x[0])
                        last_idx = start_idx
                        for at_idx, li in items:
                            delay = max(0.0, (at_idx - last_idx) * emit_delay)
                            time.sleep(delay)
                            last_idx = at_idx
                            try:
                                add_line_http(base_url, pair, li["price"])
                            except Exception as e:
                                print(f"[sched] add_line failed: {e}", file=sys.stderr)

                    threading.Thread(target=post_timed, daemon=True).start()

            # Snapshot AFTER the full CSV playback; trade-level marker lines are added on opens
            png = sdir / f"snapshot_{tf}.png"
            asyncio.run(shoot_png(base_url, png, tf, selector=args.chart_selector, snapshot_at="end"))
            print(f"[snap] {name} → {png}")
        finally:
            stop_server(base_url, thread)

if __name__ == "__main__":
    main()