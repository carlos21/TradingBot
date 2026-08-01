#!/usr/bin/env python3
"""
Run a scenario group with and without entry slippage and produce a per-trade
comparison report.

Example:
    ./bin/compare_slippage.sh --group ny --account 10000 --risk-pct 2.0 --rr 5.0 --slippage 5
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_FEE_PER_RT = 1.50
DEFAULT_POINT_VALUE = 2.0


def run_scenarios(args: argparse.Namespace, slippage: float, results_json: Path) -> None:
    """Invoke run_scenarios.sh with the requested slippage value."""
    cmd = [
        str(PROJECT_ROOT / "bin" / "run_scenarios.sh"),
        "--group", args.group,
        "--account", str(args.account),
        "--risk-pct", str(args.risk_pct),
        "--rr", str(args.rr),
        "--mode", args.mode,
        "--slippage", str(slippage),
        "--results-json", str(results_json),
        "--no-snapshot",
    ]
    if args.quiet:
        cmd.append("--quiet")
    if args.cfd_spread is not None:
        cmd.extend(["--cfd-spread", str(args.cfd_spread)])
    if args.cfd_commission is not None:
        cmd.extend(["--cfd-commission", str(args.cfd_commission)])
    if args.commission is not None:
        cmd.extend(["--commission", str(args.commission)])
    if args.source_csv is not None:
        cmd.extend(["--source-csv", args.source_csv])

    print(f"\n▶ Running scenarios with --slippage {slippage} ...", flush=True)
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def trade_pnl(trade: Optional[Dict[str, Any]], close: Optional[Dict[str, Any]], fee_per_rt: float) -> float:
    """Compute PnL from trade result, risk and contracts."""
    if trade is None or close is None:
        return 0.0
    result = close.get("result", 0.0) or 0.0
    contracts = trade.get("contracts", 0) or 0
    risk = trade.get("risk", 0) or 0
    return contracts * result * risk * DEFAULT_POINT_VALUE - contracts * fee_per_rt


def build_report(
    baseline: Dict[str, Any],
    slipped: Dict[str, Any],
    group: str,
    slippage: float,
    fee_per_rt: float,
) -> str:
    """Build a Markdown comparison table."""
    rows: List[Dict[str, Any]] = []
    baseline_total = 0.0
    slipped_total = 0.0

    for b_sc, s_sc in zip(baseline.get("results", []), slipped.get("results", [])):
        b_pairs = b_sc.get("trade_pairs") or []
        s_pairs = s_sc.get("trade_pairs") or []
        for idx, ((b_trade, b_close), (s_trade, s_close)) in enumerate(zip(b_pairs, s_pairs), start=1):
            if b_trade is None or s_trade is None:
                continue
            b_pnl = trade_pnl(b_trade, b_close, fee_per_rt)
            s_pnl = trade_pnl(s_trade, s_close, fee_per_rt)
            baseline_total += b_pnl
            slipped_total += s_pnl

            b_entry = b_trade.get("entry")
            s_entry = s_trade.get("entry")
            if b_entry == s_entry:
                continue

            rows.append({
                "scenario": b_sc.get("name", ""),
                "trade": idx,
                "type": b_trade.get("type", ""),
                "entry_base": b_entry,
                "entry_slip": s_entry,
                "sl": b_trade.get("stop_loss") or b_trade.get("orig_sl"),
                "tp": b_trade.get("take_profit"),
                "r_base": round(b_close.get("result", 0.0), 2) if b_close else None,
                "r_slip": round(s_close.get("result", 0.0), 2) if s_close else None,
                "pnl_base": round(b_pnl, 2),
                "pnl_slip": round(s_pnl, 2),
                "pnl_delta": round(s_pnl - b_pnl, 2),
            })

    lines = [
        f"# Slippage comparison: baseline vs --slippage {slippage}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Group | {group} |",
        f"| Account | ${baseline.get('config', {}).get('account', 0):,.0f} |",
        f"| Risk % | {baseline.get('config', {}).get('risk_pct', '')}% |",
        f"| RR | {baseline.get('config', {}).get('rr', '')} |",
        f"| Slippage | {slippage} points |",
        f"| Trades with different entry | {len(rows)} |",
        f"| Baseline total PnL | ${baseline_total:,.2f} |",
        f"| Slippage total PnL | ${slipped_total:,.2f} |",
        f"| Net PnL delta | ${slipped_total - baseline_total:+,.2f} |",
        "",
        "## Per-trade differences",
        "",
        "| Scenario | # | Type | Entry (no slip) | Entry (+slip) | SL | TP | R (no slip) | R (+slip) | PnL (no slip) | PnL (+slip) | PnL Δ |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for r in rows:
        lines.append(
            f"| {r['scenario']} | {r['trade']} | {r['type']} | {r['entry_base']} | {r['entry_slip']} | "
            f"{r['sl']} | {r['tp']} | {r['r_base']} | {r['r_slip']} | "
            f"{r['pnl_base']} | {r['pnl_slip']} | {r['pnl_delta']:+} |"
        )

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run a scenario group with/without slippage and compare per-trade results."
    )
    ap.add_argument("--group", required=True, help="Scenario group (e.g. ny, london)")
    ap.add_argument("--account", type=float, default=100_000.0, help="Simulated account size")
    ap.add_argument("--risk-pct", type=float, required=True, help="Risk per trade as % of balance")
    ap.add_argument("--rr", type=float, default=4.0, help="Risk:Reward ratio")
    ap.add_argument("--slippage", type=float, required=True, help="Slippage in points")
    ap.add_argument("--mode", choices=["sim", "real_futures", "real_cfd", "both"], default="real_futures")
    ap.add_argument("--commission", type=float, default=None, help="Round-trip commission per contract/lot")
    ap.add_argument("--cfd-spread", type=float, default=None)
    ap.add_argument("--cfd-commission", type=float, default=None)
    ap.add_argument("--source-csv", default=None)
    ap.add_argument("--quiet", action="store_true", default=True, help="Suppress run_scenarios progress output")
    ap.add_argument("--no-quiet", dest="quiet", action="store_false", help="Show run_scenarios progress output")
    ap.add_argument("--out-report", default=None, help="Output markdown report path")
    args = ap.parse_args()

    fee_per_rt = args.commission if args.commission is not None else DEFAULT_FEE_PER_RT

    with tempfile.TemporaryDirectory(prefix="compare_slippage_") as tmpdir:
        baseline_json = Path(tmpdir) / "baseline.json"
        slipped_json = Path(tmpdir) / "slipped.json"

        run_scenarios(args, 0.0, baseline_json)
        run_scenarios(args, args.slippage, slipped_json)

        baseline = json.loads(baseline_json.read_text())
        slipped = json.loads(slipped_json.read_text())

    report = build_report(baseline, slipped, args.group, args.slippage, fee_per_rt)

    if args.out_report:
        report_path = Path(args.out_report)
    else:
        outdir = Path("scenarios_out")
        outdir.mkdir(parents=True, exist_ok=True)
        safe_group = "".join(c if c.isalnum() or c in "-_" else "_" for c in args.group)
        report_path = outdir / f"slippage_comparison_{safe_group}.md"

    report_path.write_text(report)
    print(f"\n✅ Report written: {report_path.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
