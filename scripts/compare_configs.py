#!/usr/bin/env python3
"""
Compare strategy results across different configurations (e.g., RR values).
Runs run_scenarios.py once per config and generates a comparison HTML report.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from html_comparison_report import generate_comparison_report


def main():
    ap = argparse.ArgumentParser(description="Compare strategy configs side by side")
    ap.add_argument("--rr", nargs="+", type=float, required=True,
                    help="List of RR values to compare (e.g., --rr 3.3 4.0 5.0)")
    ap.add_argument("--yaml", required=True)
    ap.add_argument("--source-csv", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--bars-per-second", type=int, default=800)
    ap.add_argument("--chart-selector", default="#chartContainer")
    ap.add_argument("--mode", choices=["sim", "real_futures", "real_cfd", "both"], default="real_futures")
    ap.add_argument("--risk", type=float, default=1000.0)
    ap.add_argument("--account", type=float, default=100_000.0)
    ap.add_argument("--no-breakeven", action="store_true", default=False)
    ap.add_argument("--no-snapshot", action="store_true", default=False,
                    help="Skip chart snapshots (always true for comparisons)")
    ap.add_argument("--cfd-spread", type=float, default=0.5)
    ap.add_argument("--cfd-commission", type=float, default=5.0)
    args = ap.parse_args()

    n = len(args.rr)
    all_data = []

    for i, rr in enumerate(args.rr):
        print(f"\n{'='*60}")
        print(f"  Running config {i+1}/{n}: RR {rr}")
        print(f"{'='*60}\n")

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
            "--mode", args.mode,
            "--risk", str(args.risk),
            "--account", str(args.account),
            "--rr", str(rr),
            "--quiet",
            "--no-snapshot",
            "--results-json", json_path,
            "--cfd-spread", str(args.cfd_spread),
            "--cfd-commission", str(args.cfd_commission),
        ]
        if args.no_breakeven:
            cmd.append("--no-breakeven")

        result = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parent.parent))
        if result.returncode != 0:
            print(f"\n  Config RR {rr} failed with exit code {result.returncode}")
            sys.exit(1)

        data = json.loads(Path(json_path).read_text())
        all_data.append(data)
        Path(json_path).unlink(missing_ok=True)

    # Generate comparison report
    out_path = Path(args.outdir) / "comparison.html"
    generate_comparison_report(all_data, str(out_path))
    print(f"\n{'='*60}")
    print(f"  Comparison report: {out_path.resolve()}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
