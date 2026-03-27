#!/usr/bin/env python3
"""
HTML Comparison Report — side-by-side comparison of strategy configs.
"""

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from dateutil import parser as dtparser

from scripts.report_utils import compute_trade_pnl, calc_max_dd, fmt_usd, fmt_pct, pnl_class, h


# Colors for each config line in equity curve
CONFIG_COLORS = [
    "#3b82f6",  # blue
    "#22c55e",  # green
    "#ef4444",  # red
    "#eab308",  # yellow
    "#a855f7",  # purple
    "#f97316",  # orange
    "#06b6d4",  # cyan
    "#ec4899",  # pink
]


def _enrich_results(data, be_threshold=0.5):
    """Convert raw JSON data into enriched scenario list with PnL computed."""
    config = data["config"]
    account = config["account"]
    risk = config["risk"]
    mode = config["mode"]
    nq_pv = 2.0
    fee_per_rt = 1.50

    enriched = []
    for r in data["results"]:
        date = dtparser.parse(r["date"]).date()
        trades_data = []
        for trade, close in (r.get("trade_pairs") or []):
            td = compute_trade_pnl(trade, close, account, risk, mode, nq_pv, fee_per_rt, be_threshold)
            trades_data.append(td)
        net_usd = sum(t["usd"] for t in trades_data)
        net_pct = sum(t["pct"] for t in trades_data)
        enriched.append({
            "name": r["name"],
            "date": date,
            "status": r["status"],
            "trades": trades_data,
            "net_usd": net_usd,
            "net_pct": net_pct,
        })
    return enriched, config


def _compute_stats(enriched, account):
    """Compute overall stats from enriched results."""
    outcomes = []
    for e in enriched:
        for t in e["trades"]:
            if t["outcome"] != "open":
                outcomes.append(t["outcome"])

    total_t = len(outcomes)
    total_w = outcomes.count("win")
    total_l = outcomes.count("loss")
    total_be = outcomes.count("be")
    total_sp = outcomes.count("sp")
    winrate = (total_w / (total_w + total_l) * 100) if (total_w + total_l) > 0 else 0.0
    net_usd = sum(e["net_usd"] for e in enriched)
    net_pct = net_usd / account * 100 if account else 0.0

    # Max consecutive
    max_cw = max_cl = cw = cl = 0
    for o in outcomes:
        if o == "win":
            cw += 1; cl = 0
        elif o == "loss":
            cl += 1; cw = 0
        else:
            continue
        max_cw = max(max_cw, cw)
        max_cl = max(max_cl, cl)

    # Equity curve
    equity_points = [account]
    running = account
    sorted_enriched = sorted(enriched, key=lambda e: e["date"])
    for e in sorted_enriched:
        sc_usd = sum(t["usd"] for t in e["trades"] if t["outcome"] != "open")
        if sc_usd != 0:
            running += sc_usd
            equity_points.append(running)

    max_dd_usd, max_dd_pct = calc_max_dd(equity_points)

    # Monthly breakdown
    monthly = defaultdict(lambda: {"wins": 0, "losses": 0, "be": 0, "sp": 0, "usd": 0.0})
    for e in enriched:
        mk = e["date"].strftime("%Y-%m")
        for t in e["trades"]:
            if t["outcome"] == "open":
                continue
            monthly[mk]["usd"] += t["usd"]
            if t["outcome"] == "win":
                monthly[mk]["wins"] += 1
            elif t["outcome"] == "loss":
                monthly[mk]["losses"] += 1
            elif t["outcome"] == "be":
                monthly[mk]["be"] += 1
            elif t["outcome"] == "sp":
                monthly[mk]["sp"] += 1

    num_months = len(monthly) if monthly else 1
    avg_monthly_usd = net_usd / num_months

    # Equity curve with dates for SVG
    eq_dated = [{"date": "Start", "balance": account}]
    running2 = account
    for e in sorted_enriched:
        sc_usd = sum(t["usd"] for t in e["trades"] if t["outcome"] != "open")
        if sc_usd != 0:
            running2 += sc_usd
            eq_dated.append({"date": e["date"].strftime("%m/%d"), "balance": running2})

    return {
        "total_trades": total_t,
        "wins": total_w,
        "losses": total_l,
        "be": total_be,
        "sp": total_sp,
        "winrate": winrate,
        "net_usd": net_usd,
        "net_pct": net_pct,
        "max_cw": max_cw,
        "max_cl": max_cl,
        "max_dd_usd": max_dd_usd,
        "max_dd_pct": max_dd_pct,
        "avg_monthly_usd": avg_monthly_usd,
        "monthly": dict(monthly),
        "equity_points": eq_dated,
    }


def generate_comparison_report(all_data, output_path, be_threshold=0.5):
    """
    Generate a comparison HTML report from multiple config results.

    all_data: list of dicts, each from a --results-json file (with "config" and "results" keys).
    """
    configs = []
    all_stats = []
    all_enriched = []

    for data in all_data:
        enriched, config = _enrich_results(data, be_threshold)
        account = config["account"]
        stats = _compute_stats(enriched, account)
        configs.append(config)
        all_stats.append(stats)
        all_enriched.append(enriched)

    account = configs[0]["account"]  # same across configs

    # Config labels
    labels = []
    for c in configs:
        parts = [f"RR {c['rr']}"]
        if c.get("no_breakeven"):
            parts.append("no-BE")
        labels.append(", ".join(parts))

    n_configs = len(configs)

    # ── Overall stats table ──
    stat_rows = [
        ("Trades", [str(s["total_trades"]) for s in all_stats], None),
        ("Wins", [str(s["wins"]) for s in all_stats], "max"),
        ("Losses", [str(s["losses"]) for s in all_stats], "min"),
        ("B/E", [str(s["be"]) for s in all_stats], None),
        ("Win Rate", [f'{s["winrate"]:.1f}%' for s in all_stats], "max_float"),
        ("Net PnL", [fmt_usd(s["net_usd"]) for s in all_stats], "max_float"),
        ("Return", [fmt_pct(s["net_pct"]) for s in all_stats], "max_float"),
        ("Max Drawdown", [f'${s["max_dd_usd"]:,.0f}' for s in all_stats], "min_float"),
        ("Max DD %", [f'{s["max_dd_pct"]:.2f}%' for s in all_stats], "min_float"),
        ("Max Consec W", [str(s["max_cw"]) for s in all_stats], "max"),
        ("Max Consec L", [str(s["max_cl"]) for s in all_stats], "min"),
        ("Monthly Avg", [fmt_usd(s["avg_monthly_usd"]) for s in all_stats], "max_float"),
    ]

    # Determine best index for each row
    def _best_idx(row_label, values_raw, rule):
        if rule is None:
            return -1
        nums = []
        for s in all_stats:
            if rule in ("max", "max_float"):
                if row_label == "Wins":
                    nums.append(s["wins"])
                elif row_label == "Win Rate":
                    nums.append(s["winrate"])
                elif row_label == "Net PnL":
                    nums.append(s["net_usd"])
                elif row_label == "Return":
                    nums.append(s["net_pct"])
                elif row_label == "Max Consec W":
                    nums.append(s["max_cw"])
                elif row_label == "Monthly Avg":
                    nums.append(s["avg_monthly_usd"])
            elif rule in ("min", "min_float"):
                if row_label == "Losses":
                    nums.append(s["losses"])
                elif row_label == "Max Drawdown":
                    nums.append(s["max_dd_usd"])
                elif row_label == "Max DD %":
                    nums.append(s["max_dd_pct"])
                elif row_label == "Max Consec L":
                    nums.append(s["max_cl"])
        if not nums:
            return -1
        if rule in ("max", "max_float"):
            return nums.index(max(nums))
        return nums.index(min(nums))

    stats_header = "".join(f'<th>{h(l)}</th>' for l in labels)
    stats_body = ""
    for label, vals, rule in stat_rows:
        best = _best_idx(label, vals, rule)
        cells = ""
        for i, v in enumerate(vals):
            cls = ' class="best-val"' if i == best else ""
            cells += f"<td{cls}>{v}</td>"
        stats_body += f"<tr><td class='row-label'>{label}</td>{cells}</tr>\n"

    # ── Equity curve SVG (overlaid) ──
    eq_svg = ""
    all_balances = []
    for s in all_stats:
        bals = [p["balance"] for p in s["equity_points"]]
        all_balances.extend(bals)

    if all_balances and len(all_balances) > 1:
        min_b = min(all_balances)
        max_b = max(all_balances)
        b_range = max_b - min_b if max_b != min_b else 1
        svg_w, svg_h = 900, 250
        padding = 50
        plot_w = svg_w - 2 * padding
        plot_h = svg_h - 2 * padding

        # Grid lines
        grid_lines = ""
        for i in range(5):
            gy = padding + (i / 4) * plot_h
            gval = max_b - (i / 4) * b_range
            grid_lines += f'<line x1="{padding}" y1="{gy:.1f}" x2="{svg_w - padding}" y2="{gy:.1f}" stroke="var(--border)" stroke-dasharray="4,4" />'
            grid_lines += f'<text x="{padding - 5}" y="{gy:.1f}" text-anchor="end" fill="var(--text-muted)" font-size="11" dominant-baseline="middle">${gval:,.0f}</text>'

        lines_svg = ""
        legend_items = ""
        for ci, s in enumerate(all_stats):
            pts = s["equity_points"]
            if len(pts) < 2:
                continue
            n = len(pts)
            color = CONFIG_COLORS[ci % len(CONFIG_COLORS)]
            points = []
            for i, p in enumerate(pts):
                x = padding + (i / max(n - 1, 1)) * plot_w
                y = padding + plot_h - ((p["balance"] - min_b) / b_range) * plot_h
                points.append(f"{x:.1f},{y:.1f}")
            lines_svg += f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" opacity="0.85" />\n'
            legend_items += f'<span style="color:{color}; margin-right:16px; font-weight:600;">&#9644; {h(labels[ci])}</span>'

        eq_svg = f"""
        <div class="equity-section">
            <h3>Equity Curves</h3>
            <div class="legend">{legend_items}</div>
            <svg viewBox="0 0 {svg_w} {svg_h}" class="equity-chart">
                {grid_lines}
                {lines_svg}
            </svg>
        </div>"""

    # ── Monthly breakdown table ──
    all_months = set()
    for s in all_stats:
        all_months.update(s["monthly"].keys())
    month_keys = sorted(all_months)

    monthly_header = ""
    for l in labels:
        monthly_header += f'<th colspan="3" class="config-group">{h(l)}</th>'

    monthly_sub = ""
    for _ in labels:
        monthly_sub += '<th>W/L/B</th><th>Net PnL</th><th>Balance</th>'

    monthly_body = ""
    balances = [account] * n_configs
    for mk in month_keys:
        dt = datetime.strptime(mk, "%Y-%m")
        month_label = dt.strftime("%b %Y")
        cells = ""
        for ci, s in enumerate(all_stats):
            m = s["monthly"].get(mk, {"wins": 0, "losses": 0, "be": 0, "sp": 0, "usd": 0.0})
            wlb = f'{m["wins"]}W/{m["losses"]}L/{m["be"]}B'
            balances[ci] += m["usd"]
            cells += f'<td>{wlb}</td><td class="{pnl_class(m["usd"])}">{fmt_usd(m["usd"])}</td><td>${balances[ci]:,.0f}</td>'
        monthly_body += f"<tr><td class='row-label'>{month_label}</td>{cells}</tr>\n"

    # ── Build HTML ──
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Strategy Comparison Report</title>
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

/* Stats table */
.stats-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
}}

.stats-table th {{
    padding: 10px 16px;
    text-align: center;
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    border-bottom: 2px solid var(--border);
    background: rgba(0,0,0,0.15);
}}

.stats-table th:first-child {{
    text-align: left;
}}

.stats-table td {{
    padding: 10px 16px;
    text-align: center;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    font-variant-numeric: tabular-nums;
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

/* Equity */
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

/* Monthly table */
.monthly-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
}}

.monthly-table th {{
    padding: 8px 12px;
    text-align: center;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    background: rgba(0,0,0,0.15);
}}

.monthly-table th:first-child {{
    text-align: left;
}}

.monthly-table .config-group {{
    border-bottom: 2px solid var(--border);
    border-left: 1px solid var(--border);
}}

.monthly-table td {{
    padding: 8px 12px;
    text-align: center;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    font-variant-numeric: tabular-nums;
}}

.monthly-table .row-label {{
    text-align: left;
    color: var(--text-muted);
    font-weight: 500;
}}

.monthly-table tr:hover td {{
    background: rgba(255,255,255,0.03);
}}

.positive {{ color: var(--green); }}
.negative {{ color: var(--red); }}
.neutral  {{ color: var(--text-muted); }}

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
        <h1>Strategy Comparison <span>{n_configs} configs &mdash; ${account:,.0f} account, ${configs[0]['risk']:,.0f} risk/trade</span></h1>
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

    <div class="section">
        <h3>Monthly Breakdown</h3>
        <table class="monthly-table">
            <thead>
                <tr>
                    <th rowspan="2">Month</th>
                    {monthly_header}
                </tr>
                <tr>
                    {monthly_sub}
                </tr>
            </thead>
            <tbody>
                {monthly_body}
            </tbody>
        </table>
    </div>

    <footer>
        Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} &mdash; comparing {n_configs} configurations
    </footer>
</div>

</body>
</html>"""

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return str(out_path)
