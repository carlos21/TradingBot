#!/usr/bin/env python3
"""
Interactive script to fix an existing scenario:
  - Re-runs it to discover the actual trade (entry/sl/tp/tf)
  - Shows what changed
  - Updates src/strategies/liquidity_v2/scenarios/<group>.yaml (--group, default ny)
    and test_scenario.yaml
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_management import (
    PROJECT_ROOT,
    TEST_SCENARIO_YAML,
    ConsolePrompter,
    DiscoveryResultParser,
    FileScenarioRepository,
    FileSnapshotCleaner,
    FileTestScenarioWriter,
    ScenarioDiffService,
    SubprocessDiscoveryRunner,
    expect_summary,
    resolve_group_yaml_or_exit,
    snap_dir_for_group,
    ts_date,
)


def main():
    ap = argparse.ArgumentParser(description="Fix an existing scenario")
    ap.add_argument(
        "--group",
        default="ny",
        help="Scenario group to fix scenarios in (default: ny)",
    )
    args = ap.parse_args()

    yaml_path = resolve_group_yaml_or_exit(args.group)
    yaml_rel = yaml_path.relative_to(PROJECT_ROOT)

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

    if not scenarios:
        print(f"No scenarios found in {yaml_rel}.")
        return

    # Show list
    print("Existing scenarios:\n")
    for i, sc in enumerate(scenarios):
        tf_str = sc.get("tf", "?")
        exp = sc.get("expect", {})
        exp_str = "none:true" if exp.get("none") else f"entry={exp.get('entry', '?')}"
        print(f"  {i+1:3}.  {sc['name']:<38}  tf={tf_str:<4}  {exp_str}")

    print()
    sel = prompter.ask("Enter number or part of name").strip()

    # Select scenario
    sc = None
    if sel.isdigit():
        idx = int(sel) - 1
        if 0 <= idx < len(scenarios):
            sc = scenarios[idx]
        else:
            print(f"  Out of range (1–{len(scenarios)}).")
            return
    else:
        matches = [s for s in scenarios if sel.lower() in s["name"].lower()]
        if len(matches) == 1:
            sc = matches[0]
        elif len(matches) > 1:
            print("Multiple matches:")
            for m in matches:
                print(f"  {m['name']}")
            return
        else:
            print(f"No match for: {sel!r}")
            return

    print(f"\nFixing: {sc['name']}")
    old_tf = sc.get("tf", "5m")
    old_expect = sc.get("expect", {})
    print(f"  Current tf:     {old_tf}")
    print(f"  Current expect: {expect_summary(old_expect)}")

    # Discovery run
    discovery_sc = {**sc, "tf": "5m", "expect": {}}
    writer.write(discovery_sc, include_expect=False)

    fd, results_json = tempfile.mkstemp(suffix=".json", prefix="fix_scenario_")
    os.close(fd)

    print("\nRunning scenario...")
    if not runner.run(results_json):
        print("\n  ❌ Scenario runner failed.")
        Path(results_json).unlink(missing_ok=True)
        sys.exit(1)

    # Read results
    new_tf = old_tf
    new_expect = old_expect

    try:
        raw = json.loads(Path(results_json).read_text())
        result = DiscoveryResultParser.parse(raw)

        if result.had_trade:
            new_tf = result.tf
            new_expect = result.expect

            print("\n  Found trade #1:")
            print(f"    tf:     {new_tf}")
            print(f"    entry:  {new_expect['entry']}")
            print(f"    sl:     {new_expect['sl']}")
            print(f"    tp:     {new_expect['tp']}")

            if result.reentry:
                print("\n  Found re-entry trade #2:")
                print(f"    entry:  {result.reentry['entry']}")
                print(f"    sl:     {result.reentry['sl']}")
                print(f"    tp:     {result.reentry['tp']}")
        else:
            print("\n  No trade found.")
            ans = prompter.ask("  Update expect to {none: true}?", default="n")
            if ans.lower() in ("y", "yes"):
                new_expect = {"none": True}
                new_tf = old_tf
            else:
                print("  No changes made.")
                return
    except json.JSONDecodeError as e:
        print(f"\n  ❌ Could not parse results JSON: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n  ❌ Could not read results: {e}")
        sys.exit(1)
    finally:
        Path(results_json).unlink(missing_ok=True)

    # Compute diff
    changes = ScenarioDiffService.compute_diff(old_expect, new_expect, old_tf, new_tf)

    if not changes:
        print("\n✅ No changes needed — scenario is already correct.")
        writer.write(sc)
        return

    print("\n  Changes:")
    for c in changes:
        print(c)

    ans = prompter.ask("\nApply changes?", default="y")
    if ans.lower() not in ("y", "yes", ""):
        print("Aborted.")
        return

    # Apply
    updated_sc = {**sc, "tf": new_tf, "expect": new_expect}

    if not repo.replace(sc["name"], updated_sc):
        print(f"  ❌ Could not find '{sc['name']}' in {yaml_rel}")
        return

    # Re-run with correct tf to get proper snapshot
    if new_tf != "5m":
        date_label = ts_date(sc["start"])
        cleaner.clean_stale(date_label, stale_tf="5m")

        print(f"\n  Re-running with tf={new_tf} for snapshot...")
        writer.write(updated_sc, include_expect=False)

        snap_fd, snap_json = tempfile.mkstemp(suffix=".json", prefix="fix_snap_")
        os.close(snap_fd)
        runner.run(snap_json)

        # Parse the snapshot re-run so expect matches the actual snapshotted trades
        try:
            raw_snap = json.loads(Path(snap_json).read_text())
            snap_result = DiscoveryResultParser.parse(raw_snap)
            if snap_result.had_trade:
                updated_sc["expect"] = snap_result.expect
                updated_sc["tf"] = snap_result.tf
        except Exception as e:
            print(f"  ⚠️  Could not parse snapshot run results ({e}), using discovery expect.")
        finally:
            Path(snap_json).unlink(missing_ok=True)

    writer.write(updated_sc)

    print(f"\n✅ Done! '{sc['name']}' updated.")
    print(f"   {yaml_rel} — updated")
    print("   test_scenario.yaml — updated")


if __name__ == "__main__":
    main()
