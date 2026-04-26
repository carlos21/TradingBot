#!/usr/bin/env python3
"""
Fast Integration Test Runner — no browser, no snapshots.

Uses python-socketio client directly instead of Playwright for maximum speed.
Exits with code 0 if all scenarios pass, 1 otherwise.
"""

import sys
import time
import threading
import argparse
from pathlib import Path
from multiprocessing import Process, Event

import yaml
import requests
import socketio as sio_lib
from dateutil import parser as dtparser
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_scenarios import (
    run_test_server,
    parse_line_spec,
    wait_http_ok,
    add_line_http,
    reset_app_state,
    check_expectations,
    PAIR_TZS,
)

APP_HOST = "127.0.0.1"

# ANSI colours
RST   = '\033[0m';  BOLD  = '\033[1m'
GREEN = '\033[92m'; RED   = '\033[91m'
GRAY  = '\033[90m'; CYAN  = '\033[96m'
YELLOW = '\033[93m'


def run_scenario(base_url: str, pair_name: str, pair_tz: ZoneInfo, sc: dict, get_epoch) -> tuple:
    name     = sc.get("name", "?")
    start_ts = get_epoch(sc["start"])
    end_ts   = get_epoch(sc["end"])
    tf       = sc.get("tf", "5m")

    # Warmup: pre-seed 2 hours so TSI is warm by scenario start
    warmup_start_ts = start_ts - 2 * 3600
    if not reset_app_state(base_url, start=warmup_start_ts, end=start_ts):
        return "ERROR", "Reset failed", "", []

    for line in [parse_line_spec(l) for l in sc.get("lines", [])]:
        c_ts = get_epoch(line["at_raw"]) if line["at_raw"] else None
        add_line_http(base_url, pair_name, line["level"], creation_time=c_ts)

    trades: list  = []
    closes: dict  = {}
    done          = threading.Event()

    client = sio_lib.Client(logger=False, engineio_logger=False)

    @client.on('trade_open')
    def on_trade_open(data):
        trades.append(data)

    @client.on('trade_close')
    def on_trade_close(data):
        closes[str(data.get('trade_id', ''))] = data

    @client.on('stream_end')
    def on_stream_end(data=None):
        done.set()

    client.connect(base_url)
    client.emit('start_stream', {'timeframe': tf, 'fromTime': start_ts, 'stopAt': end_ts})

    if not done.wait(timeout=120):
        client.disconnect()
        return "ERROR", "Timeout waiting for stream_end", "", []

    client.disconnect()

    trade_pairs = [(t, closes.get(str(t.get('trade_id', '')))) for t in trades]
    status, reason, values = check_expectations(sc.get("expect"), trades)
    return status, reason, values, trade_pairs


def main():
    ap = argparse.ArgumentParser(description="Fast integration tests — no browser, no snapshots")
    ap.add_argument("--yaml",            required=True)
    ap.add_argument("--source-csv",      required=True)
    ap.add_argument("--port",            type=int, default=5002)
    ap.add_argument("--bars-per-second", type=int, default=50000)
    args = ap.parse_args()

    yaml_path = Path(args.yaml)
    if not yaml_path.exists():
        print(f"❌ YAML not found: {yaml_path}")
        sys.exit(1)

    ydoc      = yaml.safe_load(yaml_path.read_text())
    scenarios = ydoc.get("scenarios", [])
    if not scenarios:
        print("⚠️  No scenarios found.")
        sys.exit(1)

    base_url     = f"http://{APP_HOST}:{args.port}"
    server_ready = Event()

    server_proc = Process(
        target=run_test_server,
        args=(str(Path(args.source_csv).resolve()), args.bars_per_second, args.port, server_ready, True),
    )
    server_proc.start()

    if not server_ready.wait(timeout=10):
        print("❌ Server failed to start.")
        server_proc.terminate()
        sys.exit(1)

    try:
        wait_http_ok(f"{base_url}/api/pair")
    except TimeoutError:
        print("❌ Server HTTP not reachable.")
        server_proc.terminate()
        sys.exit(1)

    pair_name = requests.get(f"{base_url}/api/pair").json()["pair"]
    pair_tz   = ZoneInfo(PAIR_TZS.get(pair_name, "UTC"))

    def get_epoch(dt_str: str) -> int:
        dt = dtparser.parse(dt_str)
        # Strip any existing timezone suffix (e.g. Z) and treat the wall-clock time
        # as belonging to the pair's local timezone.
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        dt = dt.replace(tzinfo=pair_tz)
        return int(dt.timestamp())

    print(f"\n{BOLD}{CYAN}🧪 Integration Tests — {len(scenarios)} scenarios  [{pair_name}]{RST}\n")
    col_w = max(len(sc.get("name", "")) for sc in scenarios) + 2

    passed = failed = errors = 0
    t_total = time.time()

    try:
        for i, sc in enumerate(scenarios):
            name = sc.get("name", f"scenario_{i}")
            t0   = time.time()
            status, reason, values, _ = run_scenario(base_url, pair_name, pair_tz, sc, get_epoch)
            elapsed = time.time() - t0

            if status == "PASS":
                passed += 1
                tag = f"{GREEN}{BOLD}PASS{RST}"
            elif status == "ERROR":
                errors += 1
                tag = f"{YELLOW}{BOLD}ERR {RST}"
            else:
                failed += 1
                tag = f"{RED}{BOLD}FAIL{RST}"

            details = f"{reason} {values}".strip()
            print(f"  [{tag}]  {name:<{col_w}}  {GRAY}{elapsed:5.1f}s{RST}  {details}")

    finally:
        server_proc.terminate()
        server_proc.join()

    total   = len(scenarios)
    elapsed = time.time() - t_total
    print(f"\n{'─' * 72}")
    if failed == 0 and errors == 0:
        print(f"  {GREEN}{BOLD}✅  ALL {total} TESTS PASSED{RST}  {GRAY}({elapsed:.1f}s total){RST}")
    else:
        bad = failed + errors
        print(f"  {RED}{BOLD}❌  {bad}/{total} FAILED{RST}  ({passed} passed, {elapsed:.1f}s total)")
    print(f"{'─' * 72}\n")

    sys.exit(0 if (failed == 0 and errors == 0) else 1)


if __name__ == "__main__":
    main()
