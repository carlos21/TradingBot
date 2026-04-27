#!/usr/bin/env python3
"""
Interactive script to add a new test scenario to tests/scenarios.yaml
and tests/test_scenario.yaml by running the scenario and extracting real results.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path

from scenario_management import (
    PROJECT_ROOT,
    SCENARIOS_YAML,
    TEST_SCENARIO_YAML,
    SNAP_BASE_DIR,
    ConsolePrompter,
    DiscoveryResultParser,
    FileScenarioRepository,
    FileSnapshotCleaner,
    FileTestScenarioWriter,
    SubprocessDiscoveryRunner,
    derive_name,
    find_insert_position,
    parse_ts_with_date,
    ts_date,
)


def main():
    ap = argparse.ArgumentParser(description="Add a new test scenario")
    ap.add_argument(
        "--rr",
        type=float,
        default=4.0,
        help="Risk:Reward ratio for TP calculation (default: 4.0)",
    )
    args = ap.parse_args()

    print("=== Add New Trading Scenario ===\n")

    # Dependencies
    repo = FileScenarioRepository(SCENARIOS_YAML)
    writer = FileTestScenarioWriter(TEST_SCENARIO_YAML)
    runner = SubprocessDiscoveryRunner(PROJECT_ROOT, TEST_SCENARIO_YAML)
    cleaner = FileSnapshotCleaner(SNAP_BASE_DIR)
    prompter = ConsolePrompter()

    yaml_doc = repo.load()
    scenarios = yaml_doc.get("scenarios", [])
    existing_names = [sc.get("name", "") for sc in scenarios]

    # Session date
    while True:
        session_date = prompter.ask("Session date (YYYY-MM-DD)").strip()
        if (
            len(session_date) == 10
            and session_date[4] == "-"
            and session_date[7] == "-"
        ):
            break
        print("  Expected format: YYYY-MM-DD")

    # Collect lines
    lines: list[dict] = []
    print(
        "\nEnter support/resistance lines (press Enter with no price to stop):"
    )
    print(
        f"  'at' time: type HH:MM to use {session_date}, "
        f"or full YYYY-MM-DD HH:MM to override."
    )
    while True:
        price_str = prompter.ask("  Line price (or empty to stop)").strip()
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
            at_str = prompter.ask(f"  'at' time for {price}")
            try:
                at_ts = parse_ts_with_date(at_str, session_date)
                break
            except ValueError as e:
                print(f"  {e}")

        lines.append({"price": price, "at": at_ts})

    # Start / end (fixed defaults)
    start_ts = f"{session_date} 06:00:00Z"
    end_ts = f"{session_date} 16:00:00Z"

    # Derive name
    name = derive_name(start_ts, existing_names)
    print(f"\nScenario name: {name}")

    # Discovery run
    discovery_sc = {
        "name": name,
        "tf": "5m",
        "start": start_ts,
        "end": end_ts,
        "lines": lines,
        "expect": {},
    }
    writer.write(discovery_sc, include_expect=False)

    results_fd, results_json = tempfile.mkstemp(
        suffix=".json", prefix="add_scenario_"
    )
    os.close(results_fd)

    print(f"\nRunning test to discover trade (RR: {args.rr})...")
    runner.run(results_json, rr_ratio=args.rr)

    # Read results
    try:
        raw = json.loads(Path(results_json).read_text())
        result = DiscoveryResultParser.parse(raw)
        expect = result.expect
        tf = result.tf

        if result.had_trade:
            print("\nTrade found:")
            print(f"  Type:   {result.trade_type}")
            print(f"  Entry:  {expect['entry']}")
            print(f"  SL:     {expect['sl']}")
            print(f"  TP:     {expect['tp']}")
            print(f"  TF:     {tf}")
        else:
            print("\nNo trade was found — adding with expect: none.")
    except Exception as e:
        print(f"\nCould not read results ({e}) — adding with expect: none.")
        expect = {"none": True}
        tf = "5m"
    finally:
        Path(results_json).unlink(missing_ok=True)

    # Build final scenario
    sc = {
        "name": name,
        "tf": tf,
        "start": start_ts,
        "end": end_ts,
        "lines": lines,
        "expect": expect,
    }

    # Re-run with correct tf if it differs from discovery tf ("5m")
    if tf != "5m":
        date_label = ts_date(start_ts)
        cleaner.clean_stale(date_label, stale_tf="5m")

        print(f"\nRe-running with tf={tf} to generate correct snapshot...")
        writer.write(sc)
        results_fd2, results_json2 = tempfile.mkstemp(
            suffix=".json", prefix="add_scenario_snap_"
        )
        os.close(results_fd2)
        runner.run(results_json2, rr_ratio=args.rr)

        # Parse the snapshot re-run so expect matches the actual snapshotted trades
        try:
            raw2 = json.loads(Path(results_json2).read_text())
            result2 = DiscoveryResultParser.parse(raw2)
            if result2.had_trade:
                sc["expect"] = result2.expect
                sc["tf"] = result2.tf
        except Exception as e:
            print(f"  ⚠️  Could not parse snapshot run results ({e}), using discovery expect.")
        finally:
            Path(results_json2).unlink(missing_ok=True)

    # Insert into scenarios.yaml
    insert_idx = find_insert_position(scenarios, start_ts)
    print(
        f"\nInserting at position {insert_idx + 1} "
        f"of {len(scenarios) + 1} in scenarios.yaml..."
    )
    repo.insert(sc, insert_idx, scenarios)

    # Write final test_scenario.yaml
    writer.write(sc)

    print(f"\n✅ Done! Scenario '{name}' added.")
    print("   scenarios.yaml     — updated")
    print("   test_scenario.yaml — updated")
    print("\nRun './bin/run_test_scenario.sh' to validate.")


if __name__ == "__main__":
    main()
