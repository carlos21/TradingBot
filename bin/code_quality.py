#!/usr/bin/env python3
"""
Liquid Code Quality Checker

Runs static analysis and tests, then prints a summary.

Usage:
    python bin/code_quality.py          # Run all checks
    python bin/code_quality.py --fast   # Skip pytest (lint only)
    python bin/code_quality.py --fix    # Auto-fix what ruff can fix
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Paths relative to project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
TESTS_DIR = PROJECT_ROOT / "tests"

# Ruff rule groups
RUFF_CRITICAL = "F,E,W,B,C4,PIE"             # Bugs, errors, security
RUFF_STYLE = "I,UP,PYI,RSE,ARG,SIM"          # Import sort, pyupgrade, style, simplifications
RUFF_IGNORE = "E501,W505,E402,E741,B017,SIM117"  # Line length, docstring, test noise

# Pyflakes files that use string forward references with __future__ annotations
PYFLAKES_FALSE_POSITIVES = {
    "entry_context.py": {"LiquidityStrategy"},
    "triggers.py": {"LiquidityStrategyV2"},
}

# Additional pyflakes patterns to suppress (known safe patterns)
PYFLAKES_SUPPRESS_PATTERNS = [
    ("__init__.py", "imported but unused"),          # re-exports in __init__ files
    ("test_trade_manager.py", "redefinition of unused"),  # pytest class grouping pattern
]

# Colors
BOLD = "\033[1m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RST = "\033[0m"


def run(cmd: list[str], title: str, cwd: Path = PROJECT_ROOT) -> tuple[int, str]:
    """Run a command and return (returncode, stdout+stderr)."""
    print(f"\n{BOLD}{CYAN}▶ {title}…{RST}\n")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    output = (result.stdout or "") + (result.stderr or "")
    if output.strip():
        print(output.rstrip())
    return result.returncode, output


def filter_pyflakes(output: str) -> str:
    """Remove known false positives from pyflakes output."""
    lines = []
    for line in output.splitlines():
        stripped = line.strip()
        for fname, names in PYFLAKES_FALSE_POSITIVES.items():
            if fname in stripped and any(f"undefined name '{n}'" in stripped for n in names):
                break
        else:
            for fname, pattern in PYFLAKES_SUPPRESS_PATTERNS:
                if fname in stripped and pattern in stripped:
                    break
            else:
                lines.append(line)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Liquid code quality checker")
    parser.add_argument("--fast", action="store_true", help="Skip pytest (lint only)")
    parser.add_argument("--fix", action="store_true", help="Auto-fix ruff issues")
    args = parser.parse_args()

    import os
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "dummy")
    os.environ.setdefault("TELEGRAM_CHAT_ID", "dummy")

    critical_failures = 0
    style_issues = 0

    # ------------------------------------------------------------------
    # 1. Ruff — Critical rules only (gate)
    # ------------------------------------------------------------------
    ruff_cmd = [
        sys.executable, "-m", "ruff", "check",
        "--select", RUFF_CRITICAL,
        "--ignore", RUFF_IGNORE,
        "--output-format", "concise",
        str(SRC_DIR), str(TESTS_DIR),
    ]
    if args.fix:
        ruff_cmd.extend(["--fix", "--unsafe-fixes"])
    rc, out = run(ruff_cmd, f"Ruff linter (critical: {RUFF_CRITICAL})")
    if rc != 0:
        critical_failures += 1

    # ------------------------------------------------------------------
    # 2. Ruff — Style rules (info only, never gates)
    # ------------------------------------------------------------------
    if not args.fix:
        style_cmd = [
            sys.executable, "-m", "ruff", "check",
            "--select", RUFF_STYLE,
            "--ignore", RUFF_IGNORE,
            "--output-format", "concise",
            str(SRC_DIR), str(TESTS_DIR),
        ]
        rc2, out2 = run(style_cmd, f"Ruff linter (style: {RUFF_STYLE})")
        if rc2 != 0:
            style_issues += 1

    # ------------------------------------------------------------------
    # 3. Pyflakes
    # ------------------------------------------------------------------
    rc, out = run(
        [sys.executable, "-m", "pyflakes", str(SRC_DIR), str(TESTS_DIR)],
        "Pyflakes (undefined names / unused vars)",
    )
    filtered = filter_pyflakes(out)
    if filtered.strip():
        print(filtered)
        critical_failures += 1
        rc = 1
    else:
        rc = 0

    # ------------------------------------------------------------------
    # 4. Bandit
    # ------------------------------------------------------------------
    rc, out = run(
        [
            sys.executable, "-m", "bandit",
            "-r", str(SRC_DIR), str(TESTS_DIR),
            "-f", "txt",
            "-ll",
            "-s", "B104",
        ],
        "Bandit (security scan)",
    )
    # Bandit returns 1 when it finds issues at low severity; treat Medium+ as critical
    if rc != 0:
        if "Severity: Medium" in out or "Severity: High" in out:
            critical_failures += 1
        else:
            style_issues += 1

    # ------------------------------------------------------------------
    # 5. Pytest
    # ------------------------------------------------------------------
    if not args.fast:
        rc, out = run(
            [sys.executable, "-m", "pytest", "-q", str(TESTS_DIR)],
            "Pytest",
        )
        if rc != 0:
            critical_failures += 1

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"\n{'=' * 60}")
    print(f"{BOLD}Summary{RST}")
    print(f"{'=' * 60}")

    if critical_failures == 0:
        print(f"  {GREEN}✓ Critical checks passed{RST}")
    else:
        print(f"  {RED}✗ {critical_failures} critical check(s) failed{RST}")

    if style_issues > 0:
        print(f"  {YELLOW}⚠ {style_issues} style/low-severity issue(s) found{RST}")
        print(f"    Run with --fix to auto-fix ruff issues")

    print()
    if critical_failures == 0:
        print(f"{BOLD}{GREEN}✓ Quality gate passed.{RST}")
        return 0
    else:
        print(f"{BOLD}{RED}✗ Quality gate failed.{RST}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
