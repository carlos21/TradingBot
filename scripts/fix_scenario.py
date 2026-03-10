#!/usr/bin/env python3
"""
Interactive script to fix an existing scenario:
  - Re-runs it to discover the actual trade (entry/sl/tp/tf)
  - Shows what changed
  - Updates scenarios.yaml and test_scenario.yaml
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Make sibling scripts importable without a package __init__.py
sys.path.insert(0, str(Path(__file__).resolve().parent))

from add_scenario import (
    SCENARIOS_YAML,
    TEST_SCENARIO_YAML,
    load_scenarios_yaml,
    replace_scenario_block,
    write_test_scenario_yaml,
    _format_lines_block,
    run_discovery,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def prompt(msg: str, default: str = "") -> str:
    if default:
        val = input(f"{msg} [{default}]: ").strip()
        return val if val else default
    return input(f"{msg}: ").strip()


def _expect_summary(expect: dict) -> str:
    if not expect:
        return "(none set)"
    if expect.get("none"):
        return "none: true"
    return f"entry={expect.get('entry')}  sl={expect.get('sl')}  tp={expect.get('tp')}"


def write_discovery_yaml(sc: dict):
    """Write test_scenario.yaml for a discovery run (no expect, export_summary on)."""
    content = (
        f'scenarios:\n'
        f'  - name: "{sc["name"]}"\n'
        f'    pair: "NQ"\n'
        f'    tf: "5m"\n'
        f'    start: "{sc["start"]}"\n'
        f'    end:   "{sc["end"]}"\n'
        f'    lines:\n'
        f'{_format_lines_block(sc["lines"])}'
        f'    snapshot: false\n'
        f'    export_summary: true\n'
        f'    show_tsi: false\n'
    )
    TEST_SCENARIO_YAML.write_text(content)


def write_snapshot_yaml(sc: dict):
    """Write test_scenario.yaml for a snapshot-only re-run (with expect, no summary)."""
    content = (
        f'scenarios:\n'
        f'  - name: "{sc["name"]}"\n'
        f'    pair: "NQ"\n'
        f'    tf: "{sc["tf"]}"\n'
        f'    start: "{sc["start"]}"\n'
        f'    end:   "{sc["end"]}"\n'
        f'    lines:\n'
        f'{_format_lines_block(sc["lines"])}'
        f'    snapshot: true\n'
        f'    export_summary: false\n'
        f'    show_tsi: false\n'
    )
    TEST_SCENARIO_YAML.write_text(content)


def _run_and_discard() -> None:
    fd, path = tempfile.mkstemp(suffix=".json", prefix="fix_snap_")
    os.close(fd)
    run_discovery(path)
    Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    yaml_doc = load_scenarios_yaml()
    scenarios = yaml_doc.get("scenarios", [])

    if not scenarios:
        print("No scenarios found in scenarios.yaml.")
        return

    # ── Show list ────────────────────────────────────────────────────────────
    print("Existing scenarios:\n")
    for i, sc in enumerate(scenarios):
        tf_str = sc.get("tf", "?")
        exp = sc.get("expect", {})
        exp_str = "none:true" if exp.get("none") else f"entry={exp.get('entry', '?')}"
        print(f"  {i+1:3}.  {sc['name']:<38}  tf={tf_str:<4}  {exp_str}")

    print()
    sel = prompt("Enter number or part of name").strip()

    # ── Select scenario ──────────────────────────────────────────────────────
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
    print(f"  Current expect: {_expect_summary(old_expect)}")

    # ── Discovery run ────────────────────────────────────────────────────────
    write_discovery_yaml(sc)

    fd, results_json = tempfile.mkstemp(suffix=".json", prefix="fix_scenario_")
    os.close(fd)

    print("\nRunning scenario...")
    run_discovery(results_json)

    # ── Read results ─────────────────────────────────────────────────────────
    new_tf = old_tf
    new_expect = old_expect

    try:
        results = json.loads(Path(results_json).read_text())
        if results and results[0].get("trades"):
            trade = results[0]["trades"][0]
            entry    = trade.get("entry")
            sl       = trade.get("orig_sl") or trade.get("stop_loss")
            tp       = trade.get("take_profit")
            trade_tf = trade.get("tf")

            new_tf     = trade_tf or "1m"
            new_expect = {"entry": entry, "sl": sl, "tp": tp}

            print(f"\n  Found trade:")
            print(f"    tf:     {new_tf}")
            print(f"    entry:  {entry}")
            print(f"    sl:     {sl}")
            print(f"    tp:     {tp}")
        else:
            print("\n  No trade found.")
            ans = prompt("  Update expect to {none: true}?", default="n")
            if ans.lower() in ("y", "yes"):
                new_expect = {"none": True}
                new_tf = old_tf   # keep existing tf
            else:
                print("  No changes made.")
                return
    except Exception as e:
        print(f"\n  Could not read results: {e}")
        return
    finally:
        Path(results_json).unlink(missing_ok=True)

    # ── Compute diff ─────────────────────────────────────────────────────────
    changes = []

    if new_tf != old_tf:
        changes.append(f"  tf:    {old_tf} → {new_tf}")

    if old_expect.get("none") and not new_expect.get("none"):
        changes.append(f"  expect: none:true → trade found")
    elif not old_expect.get("none") and new_expect.get("none"):
        changes.append(f"  expect: had values → none:true")
    elif not new_expect.get("none"):
        for key in ("entry", "sl", "tp"):
            ov = old_expect.get(key)
            nv = new_expect.get(key)
            if ov is None or nv is None or abs(float(ov) - float(nv)) > 0.01:
                changes.append(f"  {key}: {ov} → {nv}")

    if not changes:
        print("\n✅ No changes needed — scenario is already correct.")
        write_test_scenario_yaml(sc, export_summary=True)
        return

    print("\n  Changes:")
    for c in changes:
        print(c)

    ans = prompt("\nApply changes?", default="y")
    if ans.lower() not in ("y", "yes", ""):
        print("Aborted.")
        return

    # ── Apply ────────────────────────────────────────────────────────────────
    updated_sc = {**sc, "tf": new_tf, "expect": new_expect}

    if not replace_scenario_block(sc["name"], updated_sc):
        print(f"  ❌ Could not find '{sc['name']}' in scenarios.yaml")
        return

    # Re-run with correct tf to get proper snapshot
    if new_tf != "5m":
        print(f"\n  Re-running with tf={new_tf} for snapshot...")
        write_snapshot_yaml(updated_sc)
        _run_and_discard()

    write_test_scenario_yaml(updated_sc, export_summary=True)

    print(f"\n✅ Done! '{sc['name']}' updated.")
    print(f"   scenarios.yaml     — updated")
    print(f"   test_scenario.yaml — updated")


if __name__ == "__main__":
    main()
