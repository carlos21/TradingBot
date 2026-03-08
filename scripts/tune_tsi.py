#!/usr/bin/env python3
"""
Systematically test multiple VelocityTriggerConfig variants against the scenario suite
and rank them by Net P&L.

Usage:
    poetry run python scripts/tune_tsi.py

Each variant patches prod_config.py, runs ./run_scenarios.sh, captures output,
then restores the original. Results are ranked at the end.
"""

import subprocess
import re
import sys
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROD_CONFIG  = PROJECT_ROOT / "src" / "prod_config.py"
BACKUP       = PROJECT_ROOT / "src" / "prod_config.py.tune_bak"

ANSI_ESCAPE  = re.compile(r'\033\[[0-9;]*m')

# ---------------------------------------------------------------------------
# Variant definitions
# ---------------------------------------------------------------------------

@dataclass
class C:
    """A single TsiCrossCondition shorthand."""
    tf: str
    count: int

    def __str__(self):
        return f'TsiCrossCondition("{self.tf}", {self.count})'


@dataclass
class Variant:
    name: str
    fast:     List[C]
    moderate: List[C]
    slow:     List[C]


VARIANTS: List[Variant] = [
    # ---- baseline (current prod config) ------------------------------------
    Variant(
        name="A  baseline (current prod)",
        fast=    [C("5m", 2)],
        moderate=[C("1m", 2), C("3m", 1)],
        slow=    [C("1m", 2), C("3m", 1)],
    ),
    # ---- all single crosses on graduated TF --------------------------------
    Variant(
        name="B  graduated-single  (5m×1, 3m×1, 1m×1)",
        fast=    [C("5m", 1)],
        moderate=[C("3m", 1)],
        slow=    [C("1m", 1)],
    ),
    # ---- all double crosses on graduated TF --------------------------------
    Variant(
        name="C  graduated-double  (5m×2, 3m×2, 1m×2)",
        fast=    [C("5m", 2)],
        moderate=[C("3m", 2)],
        slow=    [C("1m", 2)],
    ),
    # ---- fast strict, rest lenient -----------------------------------------
    Variant(
        name="D  fast-strict       (5m×2, 3m×1, 1m×1)",
        fast=    [C("5m", 2)],
        moderate=[C("3m", 1)],
        slow=    [C("1m", 1)],
    ),
    # ---- push to higher TFs ------------------------------------------------
    Variant(
        name="E  higher-tf         (5m×2, 5m×1, 3m×1)",
        fast=    [C("5m", 2)],
        moderate=[C("5m", 1)],
        slow=    [C("3m", 1)],
    ),
    # ---- very strict everywhere --------------------------------------------
    Variant(
        name="F  very-strict       (5m×2, 5m×2, 3m×2)",
        fast=    [C("5m", 2)],
        moderate=[C("5m", 2)],
        slow=    [C("3m", 2)],
    ),
    # ---- fully lenient -----------------------------------------------------
    Variant(
        name="G  lenient-all       (1m×1, 1m×1, 1m×1)",
        fast=    [C("1m", 1)],
        moderate=[C("1m", 1)],
        slow=    [C("1m", 1)],
    ),
    # ---- fast-only strict, everything else 1m×1 ---------------------------
    Variant(
        name="H  fast-only-strict  (5m×2, 1m×1, 1m×1)",
        fast=    [C("5m", 2)],
        moderate=[C("1m", 1)],
        slow=    [C("1m", 1)],
    ),
    # ---- fast 5m×2 fallback, moderate 3m×1 OR 1m×1, slow 1m×1 ------------
    Variant(
        name="I  mixed-fallback    (5m×2, 3m×1|1m×1, 1m×1)",
        fast=    [C("5m", 2)],
        moderate=[C("3m", 1), C("1m", 1)],
        slow=    [C("1m", 1)],
    ),
    # ---- 3m as anchoring timeframe everywhere ------------------------------
    Variant(
        name="J  3m-anchored       (3m×2, 3m×1, 3m×1)",
        fast=    [C("3m", 2)],
        moderate=[C("3m", 1)],
        slow=    [C("3m", 1)],
    ),
]

# ---------------------------------------------------------------------------
# Patching
# ---------------------------------------------------------------------------

TRIGGER_PATTERN = re.compile(
    r'make_velocity_adaptive_tsi_trigger\(VelocityTriggerConfig\(.*?\)\),',
    re.DOTALL,
)


def _conds(lst: List[C]) -> str:
    return ", ".join(str(c) for c in lst)


def _build_trigger_block(v: Variant) -> str:
    return (
        f'make_velocity_adaptive_tsi_trigger(VelocityTriggerConfig(\n'
        f'                fast_threshold=3.0,\n'
        f'                slow_threshold=1.0,\n'
        f'                lookback=5,\n'
        f'                fast=    [{_conds(v.fast)}],\n'
        f'                moderate=[{_conds(v.moderate)}],\n'
        f'                slow=    [{_conds(v.slow)}],\n'
        f'            )),'
    )


def patch_prod_config(v: Variant):
    content = PROD_CONFIG.read_text()
    new_content, n = TRIGGER_PATTERN.subn(_build_trigger_block(v), content)
    if n == 0:
        raise RuntimeError(
            "Could not locate make_velocity_adaptive_tsi_trigger(...) block in prod_config.py"
        )
    PROD_CONFIG.write_text(new_content)


# ---------------------------------------------------------------------------
# Running & parsing
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    variant_name: str
    trades: int
    wins: int
    losses: int
    bes: int
    win_rate: float
    net_pnl: float
    error: str = ""


def run_scenarios() -> str:
    proc = subprocess.run(
        ["bash", "run_scenarios.sh", "--mode", "sim"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return proc.stdout + proc.stderr


def parse_output(raw: str, variant_name: str) -> RunResult:
    clean = ANSI_ESCAPE.sub("", raw)

    trades_m  = re.search(r'Trades\s*:\s*(\d+)\s*\((\d+)W\s*/\s*(\d+)L\s*/\s*(\d+)BE\)', clean)
    winrate_m = re.search(r'Win Rate:\s*([\d.]+)%', clean)
    pnl_m     = re.search(r'Net P&L\s*:\s*\$([+\-]?[\d,]+)', clean)

    if not trades_m:
        # Try to surface any error
        error_snippet = clean[-800:].strip()
        return RunResult(variant_name=variant_name, trades=0, wins=0, losses=0,
                         bes=0, win_rate=0.0, net_pnl=0.0, error=error_snippet)

    trades   = int(trades_m.group(1))
    wins     = int(trades_m.group(2))
    losses   = int(trades_m.group(3))
    bes      = int(trades_m.group(4))
    win_rate = float(winrate_m.group(1)) if winrate_m else 0.0
    net_pnl  = float(pnl_m.group(1).replace(",", "")) if pnl_m else 0.0

    return RunResult(
        variant_name=variant_name,
        trades=trades,
        wins=wins,
        losses=losses,
        bes=bes,
        win_rate=win_rate,
        net_pnl=net_pnl,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    shutil.copy2(PROD_CONFIG, BACKUP)
    print(f"✅ Backed up prod_config.py → {BACKUP.name}")
    print(f"🧪 Testing {len(VARIANTS)} configurations against scenarios.yaml\n")

    results: List[RunResult] = []

    try:
        for i, variant in enumerate(VARIANTS, 1):
            print(f"[{i:2}/{len(VARIANTS)}] {variant.name}")
            patch_prod_config(variant)
            try:
                raw = run_scenarios()
            except subprocess.TimeoutExpired:
                print("        ⏱  Timed out — skipping\n")
                results.append(RunResult(variant_name=variant.name, trades=0,
                                         wins=0, losses=0, bes=0, win_rate=0.0,
                                         net_pnl=0.0, error="TIMEOUT"))
                continue

            r = parse_output(raw, variant.name)
            results.append(r)

            if r.error:
                print(f"        ⚠️  Parse error — last output:\n{r.error}\n")
            else:
                print(f"        T:{r.trades}  W:{r.wins}  L:{r.losses}  BE:{r.bes}"
                      f"  WR:{r.win_rate:.1f}%  P&L:${r.net_pnl:+,.0f}\n")

    finally:
        shutil.copy2(BACKUP, PROD_CONFIG)
        BACKUP.unlink(missing_ok=True)
        print("✅ Restored prod_config.py\n")

    # ── Ranked summary ──────────────────────────────────────────────────────
    valid = [r for r in results if not r.error]
    if not valid:
        print("❌ No parseable results.")
        return

    ranked = sorted(valid, key=lambda r: (r.net_pnl, r.win_rate), reverse=True)

    W = 100
    print("=" * W)
    print("  CONFIGURATION RANKING  (sorted by Net P&L, SIM mode)")
    print("=" * W)
    hdr = f"  {'RANK':<4} {'VARIANT':<46} {'T':>3}  {'W':>3}  {'L':>3}  {'BE':>2}  {'WR':>6}  {'NET P&L':>12}"
    print(hdr)
    print("-" * W)

    for rank, r in enumerate(ranked, 1):
        medal = "🥇" if rank == 1 else ("🥈" if rank == 2 else ("🥉" if rank == 3 else f"  {rank}."))
        pnl_str = f"${r.net_pnl:>+11,.0f}"
        print(f"  {medal}  {r.variant_name:<46} {r.trades:>3}  {r.wins:>3}  "
              f"{r.losses:>3}  {r.bes:>2}  {r.win_rate:>5.1f}%  {pnl_str}")

    print("=" * W)

    best = ranked[0]
    print(f"\n🏆  Best: {best.variant_name}")
    print(f"    Trades:{best.trades}  W:{best.wins}  L:{best.losses}  BE:{best.bes}"
          f"  WR:{best.win_rate:.1f}%  P&L:${best.net_pnl:+,.0f}")


if __name__ == "__main__":
    main()
