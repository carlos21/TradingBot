#!/usr/bin/env python3
"""
Compare CFD vs Real Futures side-by-side.
Runs scenarios once and generates a mode comparison HTML report.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.mode_pnl import (
    per_trade_futures,
    per_trade_cfd,
    aggregate_breakdown,
    PAIR_TZS,
    _detect_pair,
)
from scripts.html_mode_comparison_report import generate_mode_comparison_report


def main():
    ap = argparse.ArgumentParser(description="Compare CFD vs Real Futures side-by-side")
    ap.add_argument("--yaml", required=True, help="Path to scenarios YAML file")
    ap.add_argument("--source-csv", required=True, help="Path to source CSV")
    ap.add_argument("--outdir", required=True, help="Output directory")
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--bars-per-second", type=int, default=800)
    ap.add_argument("--chart-selector", default="#chartContainer")
    ap.add_argument("--rr", type=float, default=4.0)
    ap.add_argument("--risk", type=float, default=1000.0)
    ap.add_argument("--risk-pct", type=float, default=None)
    ap.add_argument("--account", type=float, default=100_000.0)
    ap.add_argument("--no-breakeven", action="store_true", default=False)
    ap.add_argument("--no-reentry-breakeven", action="store_true", default=False)
    ap.add_argument("--cfd-spread", type=float, default=1.5)
    ap.add_argument("--cfd-commission", type=float, default=5.0)
    ap.add_argument("--quiet", action="store_true", default=False)
    args = ap.parse_args()

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        json_path = f.name

    cmd = [
        sys.executable, "scripts/run_scenarios.py",
        "--yaml", args.yaml,
        "--source-csv", args.source_csv,
        "--outdir", args.outdir,
        "--port", str(args.port),
        "--bars-per-second", str(args.bars_per_second),
        "--chart-selector", args.chart_selector,
        "--mode", "real_futures",
        "--risk", str(args.risk),
        "--account", str(args.account),
        "--rr", str(args.rr),
        "--quiet",
        "--no-snapshot",
        "--results-json", json_path,
    ]
    if args.risk_pct is not None:
        cmd.extend(["--risk-pct", str(args.risk_pct)])
    if args.no_breakeven:
        cmd.append("--no-breakeven")
    if args.no_reentry_breakeven:
        cmd.append("--no-reentry-breakeven")

    print(f"\n{'='*60}")
    print("  Running scenarios (real_futures mode)")
    print(f"{'='*60}\n")

    result = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parent.parent))
    if result.returncode != 0:
        print(f"\n  Scenario run failed with exit code {result.returncode}")
        sys.exit(1)

    data = json.loads(Path(json_path).read_text())
    Path(json_path).unlink(missing_ok=True)

    config = data["config"]
    results = data["results"]
    account = config["account"]
    risk = config["risk"]
    risk_pct = config.get("risk_pct")
    cfd_spread = args.cfd_spread
    cfd_commission = args.cfd_commission

    # Detect pair for timezone
    pair = "MNQ"
    if results:
        pair = _detect_pair(results[0].get("name", ""))
    pair_tz = PAIR_TZS.get(pair, "Etc/GMT+5")

    print(f"\n{'='*60}")
    print("  Computing futures breakdown...")
    print(f"{'='*60}")
    fut_daily, fut_weekly, fut_monthly, fut_stats = aggregate_breakdown(
        results,
        lambda t, c, b: per_trade_futures(t, c, b, risk, risk_pct),
        account, risk, risk_pct, pair_tz_name=pair_tz
    )

    print(f"{'='*60}")
    print("  Computing CFD breakdown...")
    print(f"{'='*60}")
    cfd_daily, cfd_weekly, cfd_monthly, cfd_stats = aggregate_breakdown(
        results,
        lambda t, c, b: per_trade_cfd(t, c, b, risk, risk_pct, nq_pv=2.0, cfd_spread=cfd_spread, cfd_commission=cfd_commission),
        account, risk, risk_pct, pair_tz_name=pair_tz
    )

    futures_data = {"daily": fut_daily, "weekly": fut_weekly, "monthly": fut_monthly, "stats": fut_stats}
    cfd_data = {"daily": cfd_daily, "weekly": cfd_weekly, "monthly": cfd_monthly, "stats": cfd_stats}

    out_path = Path(args.outdir) / "mode_comparison.html"
    generate_mode_comparison_report(
        futures_data=futures_data,
        cfd_data=cfd_data,
        raw_results=results,
        per_trade_fut_fn=lambda t, c, b: per_trade_futures(t, c, b, risk, risk_pct),
        per_trade_cfd_fn=lambda t, c, b: per_trade_cfd(t, c, b, risk, risk_pct, nq_pv=2.0, cfd_spread=cfd_spread, cfd_commission=cfd_commission),
        account=account,
        risk_usd=risk,
        risk_pct=risk_pct,
        cfd_spread=cfd_spread,
        cfd_commission=cfd_commission,
        output_path=str(out_path),
    )

    print(f"\n{'='*60}")
    print(f"  Mode comparison report: {out_path.resolve()}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
