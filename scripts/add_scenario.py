#!/usr/bin/env python3
"""
Interactive script to add a new test scenario to tests/scenarios.yaml
and tests/test_scenario.yaml by running the scenario and extracting real results.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SCENARIOS_YAML = PROJECT_ROOT / "tests" / "scenarios.yaml"
TEST_SCENARIO_YAML = PROJECT_ROOT / "tests" / "test_scenario.yaml"
SOURCE_CSV = PROJECT_ROOT / "csvs" / "NQ_live.csv"


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def parse_ts(s: str) -> str:
    """
    Parse a user-input datetime string (NY local time) and return the
    canonical YAML format: 'YYYY-MM-DD HH:MM:SSZ'.

    The trailing 'Z' is conventional — run_scenarios.py ignores it and
    always interprets these timestamps as America/New_York local time.
    """
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%SZ")
        except ValueError:
            pass
    raise ValueError(f"Cannot parse datetime: {s!r}  (expected YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS)")


def ts_date(ts: str) -> str:
    """Extract 'YYYY-MM-DD' from a YAML timestamp string."""
    return ts[:10]


def parse_ts_with_date(s: str, session_date: str) -> str:
    """
    Like parse_ts() but also accepts bare time strings (HH:MM or HH:MM:SS),
    prepending session_date automatically.  Full 'YYYY-MM-DD HH:MM' still works
    as an override (e.g. when a line is from the prior day).
    """
    s = s.strip()
    # Bare time: "HH:MM" or "HH:MM:SS"
    if len(s) <= 8 and ":" in s and "-" not in s:
        s = f"{session_date} {s}"
    return parse_ts(s)


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

def derive_name(start_ts: str, existing_names: list) -> str:
    """Derive a unique scenario name from the start date."""
    base = f"NQ - {ts_date(start_ts)}"
    if base not in existing_names:
        return base
    i = 2
    while True:
        candidate = f"{base} - {i}"
        if candidate not in existing_names:
            return candidate
        i += 1


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

def load_scenarios_yaml() -> dict:
    if SCENARIOS_YAML.exists():
        return yaml.safe_load(SCENARIOS_YAML.read_text()) or {"scenarios": []}
    return {"scenarios": []}


def find_insert_position(scenarios: list, new_start_ts: str) -> int:
    """Return the index at which the new scenario should be inserted."""
    new_key = new_start_ts.replace("Z", "")
    for i, sc in enumerate(scenarios):
        sc_start = sc.get("start", "").replace("Z", "")
        if sc_start > new_key:
            return i
    return len(scenarios)


def _format_lines_block(lines: list) -> str:
    return "".join(
        f'      - {{ price: {l["price"]:.2f}, at: "{l["at"]}" }}\n'
        for l in lines
    )


def _format_expect_block(expect: dict) -> str:
    if expect.get("none"):
        return "    expect:\n      none: true\n"
    block = (
        f'    expect:\n'
        f'      entry: {expect["entry"]:.2f}\n'
        f'      sl:    {expect["sl"]:.2f}\n'
        f'      tp:    {expect["tp"]:.2f}\n'
    )
    if "reentry" in expect:
        re_ = expect["reentry"]
        block += (
            f'      reentry:\n'
            f'        entry: {re_["entry"]:.2f}\n'
            f'        sl:    {re_["sl"]:.2f}\n'
            f'        tp:    {re_["tp"]:.2f}\n'
        )
    return block


def format_scenario_block(sc: dict) -> str:
    """Format a scenario dict as a YAML block matching the existing file style."""
    return (
        f'  - name: "{sc["name"]}"\n'
        f'    pair: "NQ"\n'
        f'    tf: "{sc["tf"]}"\n'
        f'    start: "{sc["start"]}"\n'
        f'    end:   "{sc["end"]}"\n'
        f'    lines:\n'
        f'{_format_lines_block(sc["lines"])}'
        f'{_format_expect_block(sc["expect"])}'
    )


def insert_scenario_in_yaml_file(sc: dict, insert_idx: int, scenarios: list):
    """Insert the new scenario block into scenarios.yaml at the right position."""
    content = SCENARIOS_YAML.read_text()
    new_block = format_scenario_block(sc)

    if insert_idx >= len(scenarios):
        # Append at end
        SCENARIOS_YAML.write_text(content.rstrip() + "\n\n" + new_block)
        return

    # Find the line '  - name: "..."' for the scenario at insert_idx
    target_name = scenarios[insert_idx]["name"]
    pattern = re.compile(r'^  - name: "' + re.escape(target_name) + r'"', re.MULTILINE)
    match = pattern.search(content)
    if match:
        pos = match.start()
        SCENARIOS_YAML.write_text(content[:pos] + new_block + "\n" + content[pos:])
    else:
        # Fallback: append
        SCENARIOS_YAML.write_text(content.rstrip() + "\n\n" + new_block)


def replace_scenario_block(name: str, new_sc: dict) -> bool:
    """Replace an existing scenario's block in scenarios.yaml in-place."""
    content = SCENARIOS_YAML.read_text()

    start_pat = re.compile(r'(?m)^  - name: "' + re.escape(name) + r'"')
    m = start_pat.search(content)
    if not m:
        return False

    block_start = m.start()

    # End = the '\n' that immediately precedes the next '  - name: "' line
    end_pat = re.compile(r'\n  - name: "')
    m2 = end_pat.search(content, m.end())
    block_end = m2.start() + 1 if m2 else len(content)

    SCENARIOS_YAML.write_text(content[:block_start] + format_scenario_block(new_sc) + content[block_end:])
    return True


def write_test_scenario_yaml(sc: dict):
    """Write tests/test_scenario.yaml for the given scenario."""
    content = (
        f'scenarios:\n'
        f'  - name: "{sc["name"]}"\n'
        f'    pair: "NQ"\n'
        f'    tf: "{sc["tf"]}"\n'
        f'    start: "{sc["start"]}"\n'
        f'    end:   "{sc["end"]}"\n'
        f'    lines:\n'
        f'{_format_lines_block(sc["lines"])}'
        f'{_format_expect_block(sc["expect"])}'
        f'    show_tsi: false\n'
    )
    TEST_SCENARIO_YAML.write_text(content)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_discovery(results_json_path: str, rr_ratio: float = 5.0) -> bool:
    """Run run_scenarios.py against test_scenario.yaml and write results JSON."""
    cmd = [
        "poetry", "run", "python", "scripts/run_scenarios.py",
        "--yaml", "tests/test_scenario.yaml",
        "--source-csv", "csvs/NQ_live.csv",
        "--outdir", "./scenarios_out",
        "--port", "5002",
        "--bars-per-second", "800",
        "--chart-selector", "#chartContainer",
        "--quiet",
        "--decision-log",
        "--results-json", results_json_path,
        "--rr", str(rr_ratio),
        "--no-breakeven",
    ]
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def prompt(msg: str, default: str = "") -> str:
    if default:
        val = input(f"{msg} [{default}]: ").strip()
        return val if val else default
    return input(f"{msg}: ").strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Add a new test scenario")
    ap.add_argument("--rr", type=float, default=4.0,
                    help="Risk:Reward ratio for TP calculation (default: 4.0)")
    args = ap.parse_args()

    print("=== Add New Trading Scenario ===\n")

    yaml_doc = load_scenarios_yaml()
    scenarios = yaml_doc.get("scenarios", [])
    existing_names = [sc.get("name", "") for sc in scenarios]

    # ── Session date ─────────────────────────────────────────────────────────
    while True:
        session_date = prompt("Session date (YYYY-MM-DD)").strip()
        if len(session_date) == 10 and session_date[4] == "-" and session_date[7] == "-":
            break
        print("  Expected format: YYYY-MM-DD")

    # ── Collect lines ────────────────────────────────────────────────────────
    lines = []
    print(f"\nEnter support/resistance lines (press Enter with no price to stop):")
    print(f"  'at' time: type HH:MM to use {session_date}, or full YYYY-MM-DD HH:MM to override.")
    while True:
        price_str = prompt("  Line price (or empty to stop)").strip()
        if not price_str:
            if not lines:
                print("  (You need at least one line.)")
                continue
            break
        try:
            price = float(price_str)
        except ValueError:
            print(f"  Invalid price: {price_str!r}")
            continue

        while True:
            at_str = prompt(f"  'at' time for {price}")
            try:
                at_ts = parse_ts_with_date(at_str, session_date)
                break
            except ValueError as e:
                print(f"  {e}")

        lines.append({"price": price, "at": at_ts})

    # ── Start / end (fixed defaults) ───────────────────────────────────────
    start_ts = f"{session_date} 06:00:00Z"
    end_ts = f"{session_date} 16:00:00Z"

    # ── Derive name & tf ─────────────────────────────────────────────────────
    name = derive_name(start_ts, existing_names)
    print(f"\nScenario name: {name}")

    tf = "5m"  # default; overridden by trade result after discovery

    # ── Write discovery test_scenario.yaml (no expect) ───────────────────────
    discover_sc = {
        "name": name,
        "tf": tf,
        "start": start_ts,
        "end": end_ts,
        "lines": lines,
        "expect": {},   # no expect → run_scenarios treats as "pass if trade found"
    }

    # Write without expect block for discovery run
    discovery_content = (
        f'scenarios:\n'
        f'  - name: "{name}"\n'
        f'    pair: "NQ"\n'
        f'    tf: "{tf}"\n'
        f'    start: "{start_ts}"\n'
        f'    end:   "{end_ts}"\n'
        f'    lines:\n'
        f'{_format_lines_block(lines)}'
        f'    show_tsi: false\n'
    )
    TEST_SCENARIO_YAML.write_text(discovery_content)

    # ── Run test ─────────────────────────────────────────────────────────────
    results_fd, results_json = tempfile.mkstemp(suffix=".json", prefix="add_scenario_")
    os.close(results_fd)

    print(f"\nRunning test to discover trade (RR: {args.rr})...")
    run_discovery(results_json, rr_ratio=args.rr)

    # ── Read results ─────────────────────────────────────────────────────────
    expect = {}
    try:
        raw = json.loads(Path(results_json).read_text())
        scenario_results = raw.get("results", []) if isinstance(raw, dict) else raw
        trade_pairs = scenario_results[0].get("trade_pairs", []) if scenario_results else []
        if trade_pairs:
            trade = trade_pairs[0][0]  # first (trade, close) pair → trade dict
            entry = trade.get("entry") or trade.get("entry_price")
            sl = trade.get("orig_sl") or trade.get("stop_loss")
            tp = trade.get("take_profit")
            trade_tf = trade.get("tf")
            trade_type = trade.get("type", "?")

            print(f"\nTrade found:")
            print(f"  Type:   {trade_type}")
            print(f"  Entry:  {entry}")
            print(f"  SL:     {sl}")
            print(f"  TP:     {tp}")
            print(f"  TF:     {trade_tf or 'unknown'}")

            expect = {"entry": entry, "sl": sl, "tp": tp}

            # Use the triggering timeframe (fall back to "1m" if not in payload)
            tf = trade_tf or "1m"
        else:
            print("\nNo trade was found — adding with expect: none.")
            expect = {"none": True}
    except Exception as e:
        print(f"\nCould not read results ({e}) — adding with expect: none.")
        expect = {"none": True}
    finally:
        Path(results_json).unlink(missing_ok=True)

    # ── Build final scenario ─────────────────────────────────────────────────
    sc = {
        "name": name,
        "tf": tf,
        "start": start_ts,
        "end": end_ts,
        "lines": lines,
        "expect": expect,
    }

    # ── Re-run with correct tf if it differs from discovery tf ("5m") ────────
    if tf != "5m":
        # Remove the stale 5m snapshot from the discovery run
        date_label = ts_date(start_ts)
        stale_snapshot = PROJECT_ROOT / "scenarios_out" / "NQ" / f"{date_label}_5m.png"
        stale_snapshot.unlink(missing_ok=True)

        print(f"\nRe-running with tf={tf} to generate correct snapshot...")
        write_test_scenario_yaml(sc)
        results_fd2, results_json2 = tempfile.mkstemp(suffix=".json", prefix="add_scenario_snap_")
        os.close(results_fd2)
        run_discovery(results_json2, rr_ratio=args.rr)
        Path(results_json2).unlink(missing_ok=True)

    # ── Insert into scenarios.yaml ───────────────────────────────────────────
    insert_idx = find_insert_position(scenarios, start_ts)
    print(f"\nInserting at position {insert_idx + 1} of {len(scenarios) + 1} in scenarios.yaml...")
    insert_scenario_in_yaml_file(sc, insert_idx, scenarios)

    # ── Write final test_scenario.yaml ───────────────────────────────────────
    write_test_scenario_yaml(sc)

    print(f"\n✅ Done! Scenario '{name}' added.")
    print(f"   scenarios.yaml     — updated")
    print(f"   test_scenario.yaml — updated")
    print(f"\nRun './bin/run_test_scenario.sh' to validate.")


if __name__ == "__main__":
    main()
