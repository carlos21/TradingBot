#!/usr/bin/env python3
"""
Scenario management layer for discovery, persistence, and snapshot cleanup.
Organised by Clean Architecture layers: Domain -> Application -> Infrastructure.
"""

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_YAML = PROJECT_ROOT / "tests" / "scenarios.yaml"
TEST_SCENARIO_YAML = PROJECT_ROOT / "tests" / "test_scenario.yaml"
SOURCE_CSV = PROJECT_ROOT / "csvs" / "NQ_live.csv"
SNAP_BASE_DIR = PROJECT_ROOT / "scenarios_out" / "MNQ"


# ---------------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveryResult:
    """Immutable result of a discovery run."""

    expect: dict
    tf: str
    had_trade: bool
    trade_type: str = "?"
    reentry: dict | None = None


class ScenarioRepository(Protocol):
    """Protocol for scenario persistence."""

    def load(self) -> dict: ...
    def insert(self, scenario: dict, insert_idx: int, scenarios: list) -> None: ...
    def replace(self, name: str, scenario: dict) -> bool: ...


class TestScenarioWriter(Protocol):
    """Protocol for writing test_scenario.yaml."""

    def write(self, scenario: dict, *, include_expect: bool = True) -> None: ...


class DiscoveryRunner(Protocol):
    """Protocol for running discovery via run_scenarios.py."""

    def run(self, results_json_path: str, rr_ratio: float = 5.0) -> bool: ...


class SnapshotCleaner(Protocol):
    """Protocol for cleaning stale snapshot files."""

    def clean_stale(self, date_label: str, stale_tf: str = "5m") -> None: ...


class Prompter(Protocol):
    """Protocol for user interaction."""

    def ask(self, msg: str, default: str = "") -> str: ...


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class DiscoveryResultParser:
    """Pure logic: parse discovery JSON into expect + tf."""

    @staticmethod
    def parse(raw: dict) -> DiscoveryResult:
        results = raw.get("results", []) if isinstance(raw, dict) else raw
        trade_pairs = results[0].get("trade_pairs", []) if results else []
        trades = [tp[0] for tp in trade_pairs if tp[0]] if trade_pairs else []

        if not trades:
            return DiscoveryResult(
                expect={"none": True}, tf="5m", had_trade=False
            )

        trade = trades[0]
        entry = trade.get("entry") or trade.get("entry_price")
        sl = trade.get("orig_sl") or trade.get("stop_loss")
        tp = trade.get("take_profit")
        trade_tf = trade.get("tf") or "1m"
        trade_type = trade.get("type", "?")

        expect: dict = {"entry": entry, "sl": sl, "tp": tp}
        reentry = None

        if len(trades) > 1:
            re_trade = trades[1]
            re_entry = re_trade.get("entry") or re_trade.get("entry_price")
            re_sl = re_trade.get("orig_sl") or re_trade.get("stop_loss")
            re_tp = re_trade.get("take_profit")
            reentry = {"entry": re_entry, "sl": re_sl, "tp": re_tp}
            expect["reentry"] = reentry

        return DiscoveryResult(
            expect=expect,
            tf=trade_tf,
            had_trade=True,
            trade_type=trade_type,
            reentry=reentry,
        )


class ScenarioDiffService:
    """Pure logic: compute differences between old and new expects."""

    @staticmethod
    def compute_diff(
        old_expect: dict, new_expect: dict, old_tf: str, new_tf: str
    ) -> list[str]:
        changes: list[str] = []

        if new_tf != old_tf:
            changes.append(f"  tf:    {old_tf} -> {new_tf}")

        if old_expect.get("none") and not new_expect.get("none"):
            changes.append("  expect: none:true -> trade found")
        elif not old_expect.get("none") and new_expect.get("none"):
            changes.append("  expect: had values -> none:true")
        elif not new_expect.get("none"):
            for key in ("entry", "sl", "tp"):
                ov = old_expect.get(key)
                nv = new_expect.get(key)
                if ov is None or nv is None or abs(float(ov) - float(nv)) > 0.01:
                    changes.append(f"  {key}: {ov} -> {nv}")

        old_re = old_expect.get("reentry")
        new_re = new_expect.get("reentry")
        if new_re and not old_re:
            changes.append(
                f"  reentry: (new) entry={new_re.get('entry')} "
                f"sl={new_re.get('sl')} tp={new_re.get('tp')}"
            )
        elif old_re and not new_re:
            changes.append("  reentry: removed")
        elif old_re and new_re:
            for key in ("entry", "sl", "tp"):
                ov = old_re.get(key)
                nv = new_re.get(key)
                if ov is None or nv is None or abs(float(ov) - float(nv)) > 0.01:
                    changes.append(f"  reentry {key}: {ov} -> {nv}")

        return changes


class ScenarioYamlFormatter:
    """Pure logic: format scenario dicts as YAML text."""

    @staticmethod
    def format_lines_block(lines: list) -> str:
        return "".join(
            f'      - {{ price: {l["price"]:.2f}, at: "{l["at"]}" }}\n'
            for l in lines
        )

    @staticmethod
    def format_expect_block(expect: dict) -> str:
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

    @classmethod
    def format_scenario_block(cls, sc: dict) -> str:
        return (
            f'  - name: "{sc["name"]}"\n'
            f'    pair: "MNQ"\n'
            f'    tf: "{sc["tf"]}"\n'
            f'    start: "{sc["start"]}"\n'
            f'    end:   "{sc["end"]}"\n'
            f'    lines:\n'
            f'{cls.format_lines_block(sc["lines"])}'
            f'{cls.format_expect_block(sc["expect"])}'
        )

    @classmethod
    def format_test_scenario(cls, sc: dict, *, include_expect: bool = True) -> str:
        expect_block = cls.format_expect_block(sc["expect"]) if include_expect else ""
        return (
            f'scenarios:\n'
            f'  - name: "{sc["name"]}"\n'
            f'    pair: "MNQ"\n'
            f'    tf: "{sc["tf"]}"\n'
            f'    start: "{sc["start"]}"\n'
            f'    end:   "{sc["end"]}"\n'
            f'    lines:\n'
            f'{cls.format_lines_block(sc["lines"])}'
            f'{expect_block}'
            f'    show_tsi: false\n'
        )


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

class FileScenarioRepository:
    """File-based persistence for scenarios.yaml."""

    def __init__(self, path: Path):
        self._path = path

    def load(self) -> dict:
        if self._path.exists():
            return yaml.safe_load(self._path.read_text()) or {"scenarios": []}
        return {"scenarios": []}

    def insert(self, scenario: dict, insert_idx: int, scenarios: list) -> None:
        content = self._path.read_text()
        new_block = ScenarioYamlFormatter.format_scenario_block(scenario)

        if insert_idx >= len(scenarios):
            self._path.write_text(content.rstrip() + "\n\n" + new_block)
            return

        target_name = scenarios[insert_idx]["name"]
        pattern = re.compile(
            r'^  - name: "' + re.escape(target_name) + r'"', re.MULTILINE
        )
        match = pattern.search(content)
        if match:
            pos = match.start()
            self._path.write_text(content[:pos] + new_block + "\n" + content[pos:])
        else:
            self._path.write_text(content.rstrip() + "\n\n" + new_block)

    def replace(self, name: str, new_sc: dict) -> bool:
        content = self._path.read_text()
        start_pat = re.compile(r'(?m)^  - name: "' + re.escape(name) + r'"')
        m = start_pat.search(content)
        if not m:
            return False

        block_start = m.start()
        end_pat = re.compile(r'\n  - name: "')
        m2 = end_pat.search(content, m.end())
        block_end = m2.start() + 1 if m2 else len(content)

        self._path.write_text(
            content[:block_start]
            + ScenarioYamlFormatter.format_scenario_block(new_sc)
            + content[block_end:]
        )
        return True


class FileTestScenarioWriter:
    """File-based writer for test_scenario.yaml."""

    def __init__(self, path: Path):
        self._path = path

    def write(self, scenario: dict, *, include_expect: bool = True) -> None:
        self._path.write_text(
            ScenarioYamlFormatter.format_test_scenario(
                scenario, include_expect=include_expect
            )
        )


class SubprocessDiscoveryRunner:
    """Runs discovery via subprocess invocation of run_scenarios.py."""

    def __init__(
        self,
        project_root: Path,
        test_scenario_yaml_path: Path,
        extra_cmd_args: list[str] | None = None,
    ):
        self._project_root = project_root
        self._test_scenario_yaml = str(
            test_scenario_yaml_path.relative_to(project_root)
        )
        self._extra_args = extra_cmd_args or []

    def run(self, results_json_path: str, rr_ratio: float = 5.0) -> bool:
        cmd = [
            "poetry",
            "run",
            "python",
            "scripts/run_scenarios.py",
            "--yaml",
            self._test_scenario_yaml,
            "--source-csv",
            "csvs/NQ_live.csv",
            "--outdir",
            "./scenarios_out",
            "--port",
            "5002",
            "--bars-per-second",
            "800",
            "--chart-selector",
            "#chartContainer",
            "--quiet",
            "--decision-log",
            "--results-json",
            results_json_path,
            "--rr",
            str(rr_ratio),
            "--no-breakeven",
        ]
        cmd.extend(self._extra_args)
        result = subprocess.run(cmd, cwd=str(self._project_root))
        return result.returncode == 0


class FileSnapshotCleaner:
    """Removes stale snapshot PNGs from the output directory."""

    def __init__(self, snap_base_dir: Path):
        self._snap_base_dir = snap_base_dir

    def clean_stale(self, date_label: str, stale_tf: str = "5m") -> None:
        snap_dir = self._snap_base_dir / date_label
        for stale in snap_dir.glob(f"{date_label}_{stale_tf}*.png"):
            stale.unlink(missing_ok=True)


class ConsolePrompter:
    """CLI prompter for interactive user input."""

    def ask(self, msg: str, default: str = "") -> str:
        if default:
            val = input(f"{msg} [{default}]: ").strip()
            return val if val else default
        return input(f"{msg}: ").strip()


# ---------------------------------------------------------------------------
# Pure helpers (no I/O)
# ---------------------------------------------------------------------------


def ts_date(ts: str) -> str:
    """Extract 'YYYY-MM-DD' from a YAML timestamp string."""
    return ts[:10]


def parse_ts(s: str) -> str:
    """
    Parse a user-input datetime string (pair local time) and return the
    canonical YAML format: 'YYYY-MM-DD HH:MM:SS'.
    """
    from datetime import datetime

    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    raise ValueError(
        f"Cannot parse datetime: {s!r}  "
        f"(expected YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS)"
    )


def parse_ts_with_date(s: str, session_date: str) -> str:
    """
    Like parse_ts() but also accepts bare time strings (HH:MM or HH:MM:SS),
    prepending session_date automatically.
    """
    s = s.strip()
    if len(s) <= 8 and ":" in s and "-" not in s:
        s = f"{session_date} {s}"
    return parse_ts(s)


def derive_name(start_ts: str, existing_names: list) -> str:
    """Derive a unique scenario name from the start date."""
    base = f"MNQ - {ts_date(start_ts)}"
    if base not in existing_names:
        return base
    i = 2
    while True:
        candidate = f"{base} - {i}"
        if candidate not in existing_names:
            return candidate
        i += 1


def find_insert_position(scenarios: list, new_start_ts: str) -> int:
    """Return the index at which the new scenario should be inserted."""
    new_key = new_start_ts.replace("Z", "")
    for i, sc in enumerate(scenarios):
        sc_start = sc.get("start", "").replace("Z", "")
        if sc_start > new_key:
            return i
    return len(scenarios)


def expect_summary(expect: dict) -> str:
    """Human-readable summary of an expect block."""
    if not expect:
        return "(none set)"
    if expect.get("none"):
        return "none: true"
    s = f"entry={expect.get('entry')}  sl={expect.get('sl')}  tp={expect.get('tp')}"
    if "reentry" in expect:
        re_ = expect["reentry"]
        s += (
            f"  | reentry: entry={re_.get('entry')}  "
            f"sl={re_.get('sl')}  tp={re_.get('tp')}"
        )
    return s
