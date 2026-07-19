#!/usr/bin/env python3
"""
Interactive script to add a new test scenario to a scenario group yaml
(src/strategies/liquidity_v2/scenarios/<group>.yaml, see --group) and
test_scenario.yaml by running the scenario and extracting real results.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from scenario_management import (
    PROJECT_ROOT,
    TEST_SCENARIO_YAML,
    ConsolePrompter,
    DiscoveryResultParser,
    FileScenarioRepository,
    FileSnapshotCleaner,
    FileTestScenarioWriter,
    SubprocessDiscoveryRunner,
    derive_name,
    find_insert_position,
    is_valid_date,
    parse_ts_with_date,
    resolve_group_yaml_or_exit,
    session_window_for_group,
    snap_dir_for_group,
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
    ap.add_argument(
        "--group",
        default="ny",
        help="Scenario group to add to (default: ny)",
    )
    args = ap.parse_args()

    yaml_path = resolve_group_yaml_or_exit(args.group)
    yaml_rel = yaml_path.relative_to(PROJECT_ROOT)

    print("=== Add New Trading Scenario ===\n")

    # Dependencies
    repo = FileScenarioRepository(yaml_path)
    writer = FileTestScenarioWriter(TEST_SCENARIO_YAML)
    runner = SubprocessDiscoveryRunner(
        PROJECT_ROOT, TEST_SCENARIO_YAML, extra_cmd_args=["--group", args.group]
    )
    cleaner = FileSnapshotCleaner(snap_dir_for_group(args.group))
    prompter = ConsolePrompter()

    yaml_doc = repo.load()
    scenarios = yaml_doc.get("scenarios", [])
    existing_names = [sc.get("name", "") for sc in scenarios]

    # Session date
    while True:
        session_date = prompter.ask("Session date (YYYY-MM-DD)").strip()
        if is_valid_date(session_date):
            break
        print("  Expected format: YYYY-MM-DD")

    # Session window (fixed per-group defaults)
    def_start, def_end = session_window_for_group(args.group)
    start_ts = f"{parse_ts_with_date(def_start, session_date)}Z"
    end_ts = f"{parse_ts_with_date(def_end, session_date)}Z"

    # Lines date: London lines are drawn the evening before the session, so
    # use the day before automatically and only ask for the time. Other
    # groups prompt, defaulting to the session date.
    if args.group == "london":
        from datetime import datetime, timedelta

        lines_date = (
            datetime.strptime(session_date, "%Y-%m-%d") - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        print(f"Lines date: {lines_date} (day before the session)")
    else:
        while True:
            lines_date = prompter.ask("Lines date (YYYY-MM-DD)", default=session_date)
            if is_valid_date(lines_date):
                break
            print("  Expected format: YYYY-MM-DD")

    # Collect lines
    lines: list[dict] = []
    print(
        "\nEnter support/resistance lines (press Enter with no price to stop):"
    )
    print(
        f"  'at' time: type HH:MM to use {lines_date}, "
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
                at_ts = parse_ts_with_date(at_str, lines_date)
                break
            except ValueError as e:
                print(f"  {e}")

        lines.append({"price": price, "at": at_ts})

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
    if not runner.run(results_json, rr_ratio=args.rr):
        print("\n❌ Scenario runner failed.")
        Path(results_json).unlink(missing_ok=True)
        sys.exit(1)

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
    except json.JSONDecodeError as e:
        print(f"\n❌ Could not parse results JSON ({e}).")
        Path(results_json).unlink(missing_ok=True)
        sys.exit(1)
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

    # Insert into the group's scenarios yaml
    insert_idx = find_insert_position(scenarios, start_ts)
    print(
        f"\nInserting at position {insert_idx + 1} "
        f"of {len(scenarios) + 1} in {yaml_rel}..."
    )
    repo.insert(sc, insert_idx, scenarios)

    # Write final test_scenario.yaml
    writer.write(sc)

    print(f"\n✅ Done! Scenario '{name}' added.")
    print(f"   {yaml_rel} — updated")
    print("   test_scenario.yaml — updated")
    print("\nRun './bin/run_test_scenario.sh' to validate.")


if __name__ == "__main__":
    main()
