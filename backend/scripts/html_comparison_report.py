#!/usr/bin/env python3
"""
HTML Comparison Report — side-by-side comparison of strategy configs.
"""

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from dateutil import parser as dtparser

from scripts.report_utils import compute_trade_pnl, calc_max_dd, fmt_usd, fmt_pct, pnl_class, h


TAILWIND_CONFIG = """tailwind.config = {
  theme: {
    extend: {
      colors: {
        surface: {
          950: '#0b1220', 900: '#0f172a', 850: '#151f35',
          800: '#1e293b', 700: '#334155', 600: '#475569',
        },
        accent: {
          DEFAULT: '#06b6d4', 50: '#ecfeff', 100: '#cffafe',
          200: '#a5f3fc', 300: '#67e8f9', 400: '#22d3ee',
          500: '#06b6d4', 600: '#0891b2',
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },
    },
  },
};"""


# Colors for each config line in equity curve (theme-aligned)
CONFIG_COLORS = [
    "#22d3ee",  # accent-400
    "#34d399",  # emerald-400
    "#f43f5e",  # rose-500
    "#fbbf24",  # amber-400
    "#8b5cf6",  # violet-500
    "#f97316",  # orange-500
    "#06b6d4",  # accent-500
    "#ec4899",  # pink-500
]


def _pnl_class_tw(val):
    """Map numeric value to a Tailwind theme color class."""
    if val > 0.005:
        return "text-emerald-400"
    if val < -0.005:
        return "text-rose-500"
    return "text-slate-400"


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

    max_dd_usd, max_dd_pct, max_dd_start_usd, max_dd_start_pct = calc_max_dd(equity_points)

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
        "max_dd_start_usd": max_dd_start_usd,
        "max_dd_start_pct": max_dd_start_pct,
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
        ("Max DD (from peak)", [f'${s["max_dd_usd"]:,.0f}' for s in all_stats], "min_float"),
        ("Max DD % (from peak)", [f'{s["max_dd_pct"]:.2f}%' for s in all_stats], "min_float"),
        ("Max DD (from start)", [f'${s["max_dd_start_usd"]:,.0f}' for s in all_stats], "min_float"),
        ("Max DD % (from start)", [f'{s["max_dd_start_pct"]:.2f}%' for s in all_stats], "min_float"),
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
                elif row_label in ("Max DD (from peak)", "Max DD (from start)"):
                    nums.append(s["max_dd_usd"] if "peak" in row_label else s["max_dd_start_usd"])
                elif row_label in ("Max DD % (from peak)", "Max DD % (from start)"):
                    nums.append(s["max_dd_pct"] if "peak" in row_label else s["max_dd_start_pct"])
                elif row_label == "Max Consec L":
                    nums.append(s["max_cl"])
        if not nums:
            return -1
        if rule in ("max", "max_float"):
            return nums.index(max(nums))
        return nums.index(min(nums))

    stats_header = "".join(f'<th class="px-3.5 py-2.5 text-center text-xs uppercase tracking-wider text-slate-400 border-b-2 border-surface-700 bg-black/15 whitespace-nowrap">{h(l)}</th>' for l in labels)
    stats_body = ""
    for label, vals, rule in stat_rows:
        best = _best_idx(label, vals, rule)
        cells = ""
        for i, v in enumerate(vals):
            cls = 'text-emerald-400 font-bold' if i == best else ''
            cells += f'<td class="px-3.5 py-2 text-center border-b border-white/5 tabular-nums {cls}">{v}</td>'
        stats_body += f"<tr class='hover:bg-white/[0.03]'><td class='px-3.5 py-2 text-left border-b border-white/5 tabular-nums text-slate-400 font-medium'>{label}</td>{cells}</tr>\n"

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
            grid_lines += f'<line x1="{padding}" y1="{gy:.1f}" x2="{svg_w - padding}" y2="{gy:.1f}" stroke="#334155" stroke-dasharray="4,4" />'
            grid_lines += f'<text x="{padding - 5}" y="{gy:.1f}" text-anchor="end" fill="#94a3b8" font-size="11" dominant-baseline="middle">${gval:,.0f}</text>'

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
        <div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-4 mb-6">
            <h3 class="text-sm text-slate-400 uppercase tracking-wider mb-2">Equity Curves</h3>
            <div class="mb-3 text-sm">{legend_items}</div>
            <svg viewBox="0 0 {svg_w} {svg_h}" class="w-full h-auto max-h-[250px]">
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
        monthly_header += f'<th colspan="3" class="px-3 py-2 text-center text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15 border-l border-surface-700">{h(l)}</th>'

    monthly_sub = ""
    for _ in labels:
        monthly_sub += '<th class="px-3 py-2 text-center text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15">W/L/B</th><th class="px-3 py-2 text-center text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15">Net PnL</th><th class="px-3 py-2 text-center text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15">Balance</th>'

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
            cells += f'<td class="px-3 py-2 text-center border-b border-white/5 tabular-nums">{wlb}</td><td class="px-3 py-2 text-center border-b border-white/5 tabular-nums {_pnl_class_tw(m["usd"])}">{fmt_usd(m["usd"])}</td><td class="px-3 py-2 text-center border-b border-white/5 tabular-nums">${balances[ci]:,.0f}</td>'
        monthly_body += f"<tr class='hover:bg-white/[0.03]'><td class='px-3 py-2 text-left border-b border-white/5 tabular-nums text-slate-400 font-medium'>{month_label}</td>{cells}</tr>\n"

    # ── Build HTML ──
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Strategy Comparison Report</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>
{TAILWIND_CONFIG}
</script>
</head>
<body class="bg-surface-950 text-slate-300 font-sans leading-relaxed min-h-screen">

<header class="bg-surface-900 border-b border-surface-700 py-6 mb-6">
    <div class="max-w-[1400px] mx-auto px-6">
        <h1 class="text-2xl font-bold text-slate-100">Strategy Comparison <span class="text-slate-400 font-normal text-sm ml-3">{n_configs} configs &mdash; ${account:,.0f} account, ${configs[0]['risk']:,.0f} risk/trade</span></h1>
    </div>
</header>

<div class="max-w-[1400px] mx-auto px-6">

    <div class="bg-surface-800 border border-surface-700 rounded-lg p-5 mb-6">
        <h3 class="text-sm text-slate-400 uppercase tracking-wider mb-2">Overall Stats</h3>
        <div class="overflow-x-auto">
            <table class="w-full border-collapse text-sm">
                <thead>
                    <tr>
                        <th class="px-3.5 py-2.5 text-left text-xs uppercase tracking-wider text-slate-400 border-b-2 border-surface-700 bg-black/15 whitespace-nowrap">Metric</th>
                        {stats_header}
                    </tr>
                </thead>
                <tbody>
                    {stats_body}
                </tbody>
            </table>
        </div>
    </div>

    {eq_svg}

    <div class="bg-surface-800 border border-surface-700 rounded-lg p-5 mb-6">
        <h3 class="text-sm text-slate-400 uppercase tracking-wider mb-2">Monthly Breakdown</h3>
        <div class="overflow-x-auto">
            <table class="w-full border-collapse text-sm">
                <thead>
                    <tr>
                        <th rowspan="2" class="px-3 py-2 text-left text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15">Month</th>
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
    </div>

    <footer class="mt-8 pt-4 border-t border-surface-700 text-slate-500 text-sm text-center">
        Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} &mdash; comparing {n_configs} configurations
    </footer>
</div>

</body>
</html>"""

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return str(out_path)
