#!/usr/bin/env python3
"""
Batch-fix all scenarios: runs every scenario in one server session,
then updates tf + expect in scenarios.yaml so all tests pass.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from add_scenario import (
    SCENARIOS_YAML,
    load_scenarios_yaml,
    replace_scenario_block,
    _format_lines_block,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def write_discovery_yaml(scenarios: list, path: Path):
    """Write a stripped yaml (no expect, no snapshot) for the discovery run."""
    lines = ["scenarios:\n"]
    for sc in scenarios:
        lines.append(f'  - name: "{sc["name"]}"\n')
        lines.append(f'    pair: "NQ"\n')
        lines.append(f'    tf: "{sc.get("tf", "5m")}"\n')
        lines.append(f'    start: "{sc["start"]}"\n')
        lines.append(f'    end:   "{sc["end"]}"\n')
        lines.append(f'    lines:\n')
        lines.append(_format_lines_block(sc.get("lines", [])))
        lines.append(f'    snapshot: false\n')
        lines.append(f'\n')
    path.write_text("".join(lines))


def run_all(yaml_path: str, results_json: str, extra_args: list = None) -> bool:
    cmd = [
        "poetry", "run", "python", "scripts/run_scenarios.py",
        "--yaml", yaml_path,
        "--source-csv", "csvs/NQ_live.csv",
        "--outdir", "./scenarios_out",
        "--port", "5002",
        "--bars-per-second", "5000",
        "--mode", "sim",
        "--quiet",
        "--no-snapshot",
        "--no-breakeven",
        "--results-json", results_json,
    ]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode == 0


def _expect_changed(old: dict, new: dict) -> bool:
    if bool(old.get("none")) != bool(new.get("none")):
        return True
    if new.get("none"):
        return False
    for key in ("entry", "sl", "tp"):
        ov, nv = old.get(key), new.get(key)
        if ov is None or nv is None:
            return True
        if abs(float(ov) - float(nv)) > 0.01:
            return True
    return False


def main():
    # Extract arguments to pass to run_scenarios.py (skip script name and any fix_all-specific args)
    extra_args = [arg for arg in sys.argv[1:] if not arg.startswith("--fix")]

    yaml_doc = load_scenarios_yaml()
    scenarios = yaml_doc.get("scenarios", [])
    if not scenarios:
        print("No scenarios found.")
        return

    print(f"Found {len(scenarios)} scenarios. Running all in one pass...\n")

    fd1, discovery_yaml = tempfile.mkstemp(suffix=".yaml", prefix="fix_all_")
    os.close(fd1)
    fd2, results_json = tempfile.mkstemp(suffix=".json", prefix="fix_all_")
    os.close(fd2)

    try:
        write_discovery_yaml(scenarios, Path(discovery_yaml))
        ok = run_all(discovery_yaml, results_json, extra_args)
        if not ok:
            print("⚠️  Runner exited with non-zero status — results may be partial.")

        raw = json.loads(Path(results_json).read_text())
        results = raw.get("results", raw) if isinstance(raw, dict) else raw
    except Exception as e:
        print(f"❌ Failed to run or read results: {e}")
        return
    finally:
        Path(discovery_yaml).unlink(missing_ok=True)
        Path(results_json).unlink(missing_ok=True)

    results_by_name = {r["name"]: r for r in results}

    updated = 0
    unchanged = 0
    missing = 0

    for sc in scenarios:
        name = sc["name"]
        result = results_by_name.get(name)

        if result is None:
            print(f"  ⚠️  {name}: no result — skipped")
            missing += 1
            continue

        trade_pairs = result.get("trade_pairs", [])
        trades      = [tp[0] for tp in trade_pairs if tp[0]] if trade_pairs else []
        old_tf      = sc.get("tf", "5m")
        old_expect  = sc.get("expect", {})

        if trades:
            trade      = trades[0]
            new_tf     = trade.get("tf") or "1m"
            new_expect = {
                "entry": trade.get("entry"),
                "sl":    trade.get("orig_sl") or trade.get("stop_loss"),
                "tp":    trade.get("take_profit"),
            }
        else:
            new_tf     = old_tf
            new_expect = {"none": True}

        tf_changed     = new_tf != old_tf
        expect_changed = _expect_changed(old_expect, new_expect)

        if not tf_changed and not expect_changed:
            print(f"  ✅ {name}: up to date")
            unchanged += 1
            continue

        changes = []
        if tf_changed:
            changes.append(f"tf {old_tf}→{new_tf}")
        if expect_changed:
            if new_expect.get("none"):
                changes.append("expect→none")
            else:
                changes.append(f"entry={new_expect.get('entry')} sl={new_expect.get('sl')} tp={new_expect.get('tp')}")

        updated_sc = {**sc, "tf": new_tf, "expect": new_expect}
        if replace_scenario_block(name, updated_sc):
            print(f"  🔄 {name}: {', '.join(changes)}")
            updated += 1
        else:
            print(f"  ❌ {name}: could not locate block in YAML")
            missing += 1

    print(f"\nDone — {updated} updated, {unchanged} unchanged, {missing} skipped.")


if __name__ == "__main__":
    main()
