#!/usr/bin/env python3
"""
HTML Mode Comparison Report — side-by-side CFD vs Real Futures.
"""

from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _h(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _fmt_usd(val):
    return f"${val:+,.0f}"


def _fmt_pct(val):
    return f"{val:+.2f}%"


def _pnl_class(val):
    if val > 0.005:
        return "positive"
    if val < -0.005:
        return "negative"
    return "neutral"


def _diff_class(val, higher_is_better=True):
    """Color a diff cell. For PnL, higher is better for futures (green = futures won)."""
    if abs(val) < 0.01:
        return "neutral"
    if (val > 0 and higher_is_better) or (val < 0 and not higher_is_better):
        return "positive"
    return "negative"


def _wl_str(bucket):
    parts = []
    if bucket.get("wins"):   parts.append(f"{bucket['wins']}W")
    if bucket.get("losses"): parts.append(f"{bucket['losses']}L")
    if bucket.get("be"):     parts.append(f"{bucket['be']}B")
    if bucket.get("sp"):     parts.append(f"{bucket['sp']}SP")
    return "/".join(parts) if parts else "-"


def _best_idx(values, rule="max"):
    """Return index of best value, or -1 if tie/empty."""
    if not values:
        return -1
    try:
        if rule in ("max",):
            best = max(values)
        else:
            best = min(values)
    except Exception:
        return -1
    # Only highlight if there's a clear winner (not a tie)
    if values.count(best) > 1:
        return -1
    return values.index(best)


def _calc_equity(buckets, account):
    """Return list of {date, balance} from sorted bucket dict."""
    points = [{"date": "Start", "balance": account}]
    running = account
    for key in sorted(buckets):
        running += buckets[key]["usd"]
        points.append({"date": key, "balance": running})
    return points


def _equity_svg(eq1, eq2, label1="Futures", label2="CFD", color1="#3b82f6", color2="#ef4444"):
    """Generate overlaid equity curve SVG."""
    all_balances = [p["balance"] for p in eq1 + eq2]
    if not all_balances or len(all_balances) < 2:
        return ""
    min_b = min(all_balances)
    max_b = max(all_balances)
    b_range = max_b - min_b if max_b != min_b else 1
    svg_w, svg_h = 900, 250
    padding = 50
    plot_w = svg_w - 2 * padding
    plot_h = svg_h - 2 * padding

    grid_lines = ""
    for i in range(5):
        gy = padding + (i / 4) * plot_h
        gval = max_b - (i / 4) * b_range
        grid_lines += f'<line x1="{padding}" y1="{gy:.1f}" x2="{svg_w - padding}" y2="{gy:.1f}" stroke="var(--border)" stroke-dasharray="4,4" />'
        grid_lines += f'<text x="{padding - 5}" y="{gy:.1f}" text-anchor="end" fill="var(--text-muted)" font-size="11" dominant-baseline="middle">${gval:,.0f}</text>'

    lines_svg = ""
    legend_items = ""
    for ci, (pts, lbl, col) in enumerate([(eq1, label1, color1), (eq2, label2, color2)]):
        if len(pts) < 2:
            continue
        n = len(pts)
        points = []
        for i, p in enumerate(pts):
            x = padding + (i / max(n - 1, 1)) * plot_w
            y = padding + plot_h - ((p["balance"] - min_b) / b_range) * plot_h
            points.append(f"{x:.1f},{y:.1f}")
        lines_svg += f'<polyline points="{" ".join(points)}" fill="none" stroke="{col}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" opacity="0.85" />\n'
        legend_items += f'<span style="color:{col}; margin-right:16px; font-weight:600;">&#9644; {_h(lbl)}</span>'

    return f"""
    <div class="equity-section">
        <h3>Equity Curves</h3>
        <div class="legend">{legend_items}</div>
        <svg viewBox="0 0 {svg_w} {svg_h}" class="equity-chart">
            {grid_lines}
            {lines_svg}
        </svg>
    </div>"""


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------

def _build_period_table(period_keys, fut_buckets, cfd_buckets, account, label):
    """Build HTML for daily/weekly/monthly comparison table."""
    rows = []
    fut_bal = account
    cfd_bal = account
    for key in period_keys:
        fb = fut_buckets.get(key, {"usd": 0.0, "commission": 0.0, "wins": 0, "losses": 0, "be": 0, "sp": 0})
        cb = cfd_buckets.get(key, {"usd": 0.0, "commission": 0.0, "wins": 0, "losses": 0, "be": 0, "sp": 0})
        fut_bal += fb["usd"]
        cfd_bal += cb["usd"]
        pnl_diff = fb["usd"] - cb["usd"]
        comm_diff = fb["commission"] - cb["commission"]

        rows.append(f"""
        <tr>
            <td class="row-label">{_h(key)}</td>
            <td>{_wl_str(fb)}</td>
            <td class="{_pnl_class(fb['usd'])}">{_fmt_usd(fb['usd'])}</td>
            <td>${fb['commission']:,.2f}</td>
            <td>${fut_bal:,.0f}</td>
            <td>{_wl_str(cb)}</td>
            <td class="{_pnl_class(cb['usd'])}">{_fmt_usd(cb['usd'])}</td>
            <td>${cb['commission']:,.2f}</td>
            <td>${cfd_bal:,.0f}</td>
            <td class="{_diff_class(pnl_diff, higher_is_better=True)}">{_fmt_usd(pnl_diff)}</td>
            <td class="{_diff_class(comm_diff, higher_is_better=False)}">{_fmt_usd(comm_diff)}</td>
        </tr>
        """)

    # Totals row
    total_fut_usd = sum(b["usd"] for b in fut_buckets.values())
    total_cfd_usd = sum(b["usd"] for b in cfd_buckets.values())
    total_fut_comm = sum(b.get("commission", 0.0) for b in fut_buckets.values())
    total_cfd_comm = sum(b.get("commission", 0.0) for b in cfd_buckets.values())
    total_pnl_diff = total_fut_usd - total_cfd_usd
    total_comm_diff = total_fut_comm - total_cfd_comm
    total_fut_w = sum(b.get("wins", 0) for b in fut_buckets.values())
    total_fut_l = sum(b.get("losses", 0) for b in fut_buckets.values())
    total_fut_be = sum(b.get("be", 0) for b in fut_buckets.values())
    total_fut_sp = sum(b.get("sp", 0) for b in fut_buckets.values())
    total_cfd_w = sum(b.get("wins", 0) for b in cfd_buckets.values())
    total_cfd_l = sum(b.get("losses", 0) for b in cfd_buckets.values())
    total_cfd_be = sum(b.get("be", 0) for b in cfd_buckets.values())
    total_cfd_sp = sum(b.get("sp", 0) for b in cfd_buckets.values())

    rows.append(f"""
    <tr class="total-row">
        <td class="row-label">TOTAL</td>
        <td>{_wl_str({'wins': total_fut_w, 'losses': total_fut_l, 'be': total_fut_be, 'sp': total_fut_sp})}</td>
        <td class="{_pnl_class(total_fut_usd)}">{_fmt_usd(total_fut_usd)}</td>
        <td>${total_fut_comm:,.2f}</td>
        <td>${account + total_fut_usd:,.0f}</td>
        <td>{_wl_str({'wins': total_cfd_w, 'losses': total_cfd_l, 'be': total_cfd_be, 'sp': total_cfd_sp})}</td>
        <td class="{_pnl_class(total_cfd_usd)}">{_fmt_usd(total_cfd_usd)}</td>
        <td>${total_cfd_comm:,.2f}</td>
        <td>${account + total_cfd_usd:,.0f}</td>
        <td class="{_diff_class(total_pnl_diff, higher_is_better=True)}">{_fmt_usd(total_pnl_diff)}</td>
        <td class="{_diff_class(total_comm_diff, higher_is_better=False)}">{_fmt_usd(total_comm_diff)}</td>
    </tr>
    """)

    return f"""
    <div class="section">
        <h3>{_h(label)} Breakdown</h3>
        <div class="table-wrap">
            <table class="stats-table">
                <thead>
                    <tr>
                        <th>Period</th>
                        <th colspan="4" class="config-group" style="border-left:1px solid var(--border);">Futures</th>
                        <th colspan="4" class="config-group" style="border-left:1px solid var(--border);">CFD</th>
                        <th colspan="2" class="config-group" style="border-left:1px solid var(--border);">Diff (F - C)</th>
                    </tr>
                    <tr>
                        <th></th>
                        <th>W/L</th><th>PnL</th><th>Comm</th><th>Balance</th>
                        <th>W/L</th><th>PnL</th><th>Comm</th><th>Balance</th>
                        <th>PnL</th><th>Comm</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows)}
                </tbody>
            </table>
        </div>
    </div>
    """


def _build_per_trade_table(results, per_trade_fut, per_trade_cfd, account, risk_usd, risk_pct):
    """Build collapsible per-trade detail table."""
    rows = []
    fut_bal = account
    cfd_bal = account
    for r in results:
        scenario_date = r.get("date", "")
        for trade, close in (r.get("trade_pairs") or []):
            if close is None:
                continue
            entry = (trade or {}).get("entry") or (trade or {}).get("entry_price", "")
            orig_sl = (trade or {}).get("orig_sl") or (trade or {}).get("stop_loss", "")
            actual_r = close.get("result", 0.0)

            f_usd, f_pct, f_r, f_comm, _ = per_trade_fut(trade, close, fut_bal)
            c_usd, c_pct, c_r, c_comm, _ = per_trade_cfd(trade, close, cfd_bal)
            if f_usd is not None:
                fut_bal += f_usd
            if c_usd is not None:
                cfd_bal += c_usd
            diff = (f_usd or 0.0) - (c_usd or 0.0)

            rows.append(f"""
            <tr>
                <td class="row-label">{_h(r['name'])}</td>
                <td>{_h(scenario_date)}</td>
                <td>{entry}</td>
                <td>{orig_sl}</td>
                <td>{actual_r:.2f}</td>
                <td class="{_pnl_class(f_usd or 0)}">{_fmt_usd(f_usd or 0)}</td>
                <td>${f_comm:,.2f}</td>
                <td class="{_pnl_class(c_usd or 0)}">{_fmt_usd(c_usd or 0)}</td>
                <td>${c_comm:,.2f}</td>
                <td class="{_diff_class(diff, higher_is_better=True)}">{_fmt_usd(diff)}</td>
            </tr>
            """)

    return f"""
    <div class="section">
        <h3 style="cursor:pointer;" onclick="document.getElementById('per-trade-body').classList.toggle('collapsed')">
            Per-Trade Detail (click to toggle)
        </h3>
        <div id="per-trade-body" class="collapsed">
            <div class="table-wrap">
                <table class="stats-table">
                    <thead>
                        <tr>
                            <th>Scenario</th>
                            <th>Date</th>
                            <th>Entry</th>
                            <th>SL</th>
                            <th>Result R</th>
                            <th>Fut PnL</th>
                            <th>Fut Comm</th>
                            <th>CFD PnL</th>
                            <th>CFD Comm</th>
                            <th>Diff</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(rows)}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    """


# ---------------------------------------------------------------------------
# Main report generator
# ---------------------------------------------------------------------------

def generate_mode_comparison_report(
    futures_data,
    cfd_data,
    raw_results,
    per_trade_fut_fn,
    per_trade_cfd_fn,
    account,
    risk_usd,
    risk_pct,
    cfd_spread,
    cfd_commission,
    output_path,
):
    """
    Generate side-by-side HTML report comparing futures vs CFD.

    futures_data / cfd_data: dicts from aggregate_breakdown():
        {"daily": {...}, "weekly": {...}, "monthly": {...}, "stats": {...}}
    raw_results: list of scenario result dicts (for per-trade table)
    per_trade_fut_fn / per_trade_cfd_fn: callables from mode_pnl
    """
    fut_stats = futures_data["stats"]
    cfd_stats = cfd_data["stats"]

    # --- Overall stats table ---
    stat_rows = [
        ("Trades", [str(fut_stats["total_trades"]), str(cfd_stats["total_trades"])], None),
        ("Wins", [str(fut_stats["wins"]), str(cfd_stats["wins"])], "max"),
        ("Losses", [str(fut_stats["losses"]), str(cfd_stats["losses"])], "min"),
        ("B/E", [str(fut_stats["be"]), str(cfd_stats["be"])], None),
        ("S/P", [str(fut_stats["sp"]), str(cfd_stats["sp"])], None),
        ("Win Rate", [f'{fut_stats["winrate"]:.1f}%', f'{cfd_stats["winrate"]:.1f}%'], "max_float"),
        ("Net PnL", [_fmt_usd(fut_stats["net_usd"]), _fmt_usd(cfd_stats["net_usd"])], "max_float"),
        ("Return", [_fmt_pct(fut_stats["net_pct"]), _fmt_pct(cfd_stats["net_pct"])], "max_float"),
        ("Total Commission", [f'${fut_stats["total_commission"]:,.2f}', f'${cfd_stats["total_commission"]:,.2f}'], "min_float"),
        ("Max Drawdown", [f'${fut_stats["max_dd_usd"]:,.0f}', f'${cfd_stats["max_dd_usd"]:,.0f}'], "min_float"),
        ("Max DD %", [f'{fut_stats["max_dd_pct"]:.2f}%', f'{cfd_stats["max_dd_pct"]:.2f}%'], "min_float"),
        ("Max Consec W", [str(fut_stats["max_cw"]), str(cfd_stats["max_cw"])], "max"),
        ("Max Consec L", [str(fut_stats["max_cl"]), str(cfd_stats["max_cl"])], "min"),
        ("Monthly Avg", [_fmt_usd(fut_stats["avg_monthly_usd"]), _fmt_usd(cfd_stats["avg_monthly_usd"])], "max_float"),
    ]

    def _best_for_row(label, rule):
        if rule is None:
            return -1
        nums = []
        for s in (fut_stats, cfd_stats):
            if rule in ("max", "max_float"):
                if label == "Wins": nums.append(s["wins"])
                elif label == "Win Rate": nums.append(s["winrate"])
                elif label == "Net PnL": nums.append(s["net_usd"])
                elif label == "Return": nums.append(s["net_pct"])
                elif label == "Max Consec W": nums.append(s["max_cw"])
                elif label == "Monthly Avg": nums.append(s["avg_monthly_usd"])
            elif rule in ("min", "min_float"):
                if label == "Losses": nums.append(s["losses"])
                elif label == "Total Commission": nums.append(s["total_commission"])
                elif label == "Max Drawdown": nums.append(s["max_dd_usd"])
                elif label == "Max DD %": nums.append(s["max_dd_pct"])
                elif label == "Max Consec L": nums.append(s["max_cl"])
        if not nums:
            return -1
        return _best_idx(nums, rule="max" if "max" in rule else "min")

    stats_header = "<th>Futures</th><th>CFD</th>"
    stats_body = ""
    for label, vals, rule in stat_rows:
        best = _best_for_row(label, rule)
        cells = ""
        for i, v in enumerate(vals):
            cls = ' class="best-val"' if i == best else ""
            cells += f"<td{cls}>{v}</td>"
        stats_body += f"<tr><td class='row-label'>{label}</td>{cells}</tr>\n"

    # --- Equity curves ---
    fut_eq = _calc_equity(futures_data["daily"], account)
    cfd_eq = _calc_equity(cfd_data["daily"], account)
    eq_svg = _equity_svg(fut_eq, cfd_eq, label1="Futures", label2="CFD")

    # --- Period tables ---
    all_days = sorted(set(futures_data["daily"]) | set(cfd_data["daily"]))
    all_weeks = sorted(set(futures_data["weekly"]) | set(cfd_data["weekly"]))
    all_months = sorted(set(futures_data["monthly"]) | set(cfd_data["monthly"]))

    daily_html = _build_period_table(all_days, futures_data["daily"], cfd_data["daily"], account, "Daily")
    weekly_html = _build_period_table(all_weeks, futures_data["weekly"], cfd_data["weekly"], account, "Weekly")
    monthly_html = _build_period_table(all_months, futures_data["monthly"], cfd_data["monthly"], account, "Monthly")

    # --- Per-trade table ---
    per_trade_html = _build_per_trade_table(
        raw_results, per_trade_fut_fn, per_trade_cfd_fn, account, risk_usd, risk_pct
    )

    risk_desc = f"{risk_pct}% of balance" if risk_pct is not None else f"${risk_usd:,.0f} fixed"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CFD vs Futures Comparison</title>
<style>
:root {{
    --bg: #0f172a;
    --bg-card: #1e293b;
    --bg-header: #0c1222;
    --border: #334155;
    --text: #e2e8f0;
    --text-muted: #94a3b8;
    --text-dim: #64748b;
    --green: #22c55e;
    --red: #ef4444;
    --yellow: #eab308;
    --blue: #3b82f6;
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
    min-height: 100vh;
}}

.container {{
    max-width: 1400px;
    margin: 0 auto;
    padding: 24px;
}}

header {{
    background: var(--bg-header);
    border-bottom: 1px solid var(--border);
    padding: 24px 0;
    margin-bottom: 24px;
}}

header .container {{
    display: flex;
    flex-direction: column;
    gap: 8px;
}}

h1 {{
    font-size: 1.5rem;
    font-weight: 700;
}}

h1 span {{
    color: var(--text-muted);
    font-weight: 400;
    font-size: 0.9rem;
    margin-left: 12px;
}}

h3 {{
    font-size: 0.85rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 8px;
}}

.section {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 24px;
}}

.table-wrap {{
    overflow-x: auto;
}}

.stats-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
}}

.stats-table th {{
    padding: 10px 14px;
    text-align: center;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    border-bottom: 2px solid var(--border);
    background: rgba(0,0,0,0.15);
    white-space: nowrap;
}}

.stats-table th:first-child {{
    text-align: left;
}}

.stats-table td {{
    padding: 8px 14px;
    text-align: center;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}}

.stats-table .row-label {{
    text-align: left;
    color: var(--text-muted);
    font-weight: 500;
}}

.stats-table .best-val {{
    color: var(--green);
    font-weight: 700;
}}

.stats-table tr:hover td {{
    background: rgba(255,255,255,0.03);
}}

.stats-table .total-row td {{
    border-top: 2px solid var(--border);
    font-weight: 700;
}}

.equity-section {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 24px;
}}

.equity-chart {{
    width: 100%;
    height: auto;
    max-height: 250px;
}}

.legend {{
    margin-bottom: 12px;
    font-size: 0.85rem;
}}

.positive {{ color: var(--green); }}
.negative {{ color: var(--red); }}
.neutral  {{ color: var(--text-muted); }}

#per-trade-body.collapsed {{
    display: none;
}}

footer {{
    margin-top: 32px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
    color: var(--text-dim);
    font-size: 0.8rem;
    text-align: center;
}}
</style>
</head>
<body>

<header>
    <div class="container">
        <h1>CFD vs Futures Comparison <span>${account:,.0f} account, {risk_desc} risk/trade, CFD spread {_h(cfd_spread)}pt + ${_h(cfd_commission)}/lot</span></h1>
    </div>
</header>

<div class="container">

    <div class="section">
        <h3>Overall Stats</h3>
        <table class="stats-table">
            <thead>
                <tr>
                    <th>Metric</th>
                    {stats_header}
                </tr>
            </thead>
            <tbody>
                {stats_body}
            </tbody>
        </table>
    </div>

    {eq_svg}

    {daily_html}

    {weekly_html}

    {monthly_html}

    {per_trade_html}

    <footer>
        Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} &mdash; comparing CFD vs Real Futures
    </footer>
</div>

</body>
</html>"""

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return str(out_path)
