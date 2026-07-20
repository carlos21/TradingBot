#!/usr/bin/env python3
"""
HTML Report Generator — monthly horizontal-scroll view of scenario results.
"""

import calendar
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from dateutil import parser as dtparser

from scripts.report_utils import compute_trade_pnl, calc_max_dd, fmt_usd, fmt_pct, pnl_class, h as _h, flatten_trade_records


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


def _pnl_class_tw(val):
    """Map numeric value to a Tailwind theme color class."""
    if val > 0.005:
        return "text-emerald-400"
    if val < -0.005:
        return "text-rose-500"
    return "text-slate-400"


def generate_html_report(summary_results, account, risk, mode, output_path,
                         nq_pv=2.0, fee_per_rt=1.50, be_threshold=0.5, risk_pct=None,
                         cfd_spread=None, cfd_commission=None):
    """
    Generate a self-contained HTML report with monthly horizontal-scroll pages.

    Parameters match the CLI flags from run_scenarios.py.
    """
    if mode == "sim":
        mode_label = "SIM"
    elif mode == "real_cfd":
        mode_label = "REAL (CFD)"
    else:  # real_futures
        mode_label = "REAL (MNQ Futures)"

    # ── 1. Compute per-scenario / per-trade data ──────────────────────────
    # Per-trade PnL is computed in chronological order across all scenarios so
    # % risk compounding uses the correct running balance even when merged
    # groups (--group all) interleave in time.
    td_map = {}           # (sc_idx, pair_idx) -> trade pnl dict
    sc_start_balances = {}  # sc_idx -> balance before its first trade (chrono order)
    running_balance = account
    for rec in flatten_trade_records(summary_results):
        key = (rec["sc_idx"], rec["pair_idx"])
        if rec["sc_idx"] not in sc_start_balances:
            sc_start_balances[rec["sc_idx"]] = running_balance
        td = compute_trade_pnl(rec["trade"], rec["close"], account, risk, mode, nq_pv, fee_per_rt, be_threshold,
                               risk_pct=risk_pct, balance=running_balance,
                               cfd_spread=cfd_spread, cfd_commission=cfd_commission)
        td_map[key] = td
        if td["outcome"] != "open":
            running_balance += td["usd"]

    enriched = []  # list of {name, date, status, trades: [{outcome, usd, pct, r, contracts}], net_usd, net_pct}
    for sc_idx, r in enumerate(summary_results):
        date = dtparser.parse(r["date"]).date()
        sc_start_balance = sc_start_balances.get(sc_idx, account)
        trades_data = [td_map[(sc_idx, i)] for i in range(len(r.get("trade_pairs") or []))]
        net_usd = sum(t["usd"] for t in trades_data)
        net_pct = net_usd / sc_start_balance * 100 if sc_start_balance else 0.0
        net_commission = sum(t.get("commission", 0.0) for t in trades_data)
        enriched.append({
            "name": r["name"],
            "date": date,
            "status": r["status"],
            "reason": r.get("reason", ""),
            "values": r.get("values", ""),
            "trades": trades_data,
            "net_usd": net_usd,
            "net_pct": net_pct,
            "net_commission": net_commission,
        })

    # ── 2. Group by month ─────────────────────────────────────────────────
    monthly = defaultdict(list)
    for e in enriched:
        m_key = e["date"].strftime("%Y-%m")
        monthly[m_key].append(e)
    month_keys = sorted(monthly.keys())

    # ── 3. Monthly aggregation + running balance ──────────────────────────
    month_agg = {}  # key -> {wins, losses, be, sp, usd, pct, balance, reentry_win, reentry_loss}
    balance = account
    for mk in month_keys:
        wins = losses = bes = sps = 0
        reentry_win = reentry_loss = reentry_be = 0
        m_usd = 0.0
        m_commission = 0.0
        m_start_balance = balance
        for sc in monthly[mk]:
            for t in sc["trades"]:
                if t["outcome"] == "open":
                    continue
                m_usd += t["usd"]
                m_commission += t.get("commission", 0.0)
                is_re = t.get("is_reentry", False)
                if t["outcome"] == "win":
                    wins += 1
                    if is_re:
                        reentry_win += 1
                elif t["outcome"] == "loss":
                    losses += 1
                    if is_re:
                        reentry_loss += 1
                elif t["outcome"] == "sp":
                    sps += 1
                else:
                    bes += 1
                    if is_re:
                        reentry_be += 1
        balance += m_usd
        m_pct = m_usd / m_start_balance * 100 if m_start_balance else 0.0
        month_agg[mk] = {
            "wins": wins, "losses": losses, "be": bes, "sp": sps,
            "usd": m_usd, "pct": m_pct, "balance": balance, "commission": m_commission,
            "reentry_win": reentry_win, "reentry_loss": reentry_loss, "reentry_be": reentry_be,
        }

    # ── 4. Overall stats ──────────────────────────────────────────────────
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
    net_usd = sum(a["usd"] for a in month_agg.values())
    net_pct = net_usd / account * 100 if account else 0.0

    # Calculate monthly average profit
    num_months = len(month_agg) if month_agg else 1
    avg_monthly_usd = net_usd / num_months if num_months > 0 else 0.0
    avg_monthly_pct = avg_monthly_usd / account * 100 if account > 0 else 0.0

    total_commission = sum(a["commission"] for a in month_agg.values())

    total_rw = sum(a["reentry_win"] for a in month_agg.values())
    total_rl = sum(a["reentry_loss"] for a in month_agg.values())
    total_rb = sum(a["reentry_be"] for a in month_agg.values())

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

    # ── 5. Equity curve data points (cumulative by date) ──────────────────
    equity_points = [{"date": "Start", "balance": account}]
    running = account
    for mk in month_keys:
        for sc in sorted(monthly[mk], key=lambda s: s["date"]):
            sc_usd = sum(t["usd"] for t in sc["trades"] if t["outcome"] != "open")
            if sc_usd != 0:
                running += sc_usd
                equity_points.append({
                    "date": sc["date"].strftime("%m/%d"),
                    "balance": running,
                })

    # Calculate max drawdown from equity curve
    equity_balances = [p["balance"] for p in equity_points]
    max_dd_usd, max_dd_pct, max_dd_start_usd, max_dd_start_pct = calc_max_dd(equity_balances)

    # ── 6. Build HTML ─────────────────────────────────────────────────────
    def outcome_badge(outcome):
        cls_map = {
            "win": "bg-emerald-400/15 text-emerald-400",
            "loss": "bg-rose-500/15 text-rose-500",
            "be": "bg-amber-400/15 text-amber-400",
            "sp": "bg-accent-400/15 text-accent-400",
            "open": "bg-slate-400/15 text-slate-400",
        }
        label_map = {"win": "W", "loss": "L", "be": "B/E", "sp": "SP", "open": "OPEN"}
        return f'<span class="inline-block px-2 py-0.5 rounded text-xs font-bold tracking-wide {cls_map.get(outcome, "")}">{label_map.get(outcome, "?")}</span>'

    # Build month cards HTML
    month_cards_html = []
    for idx, mk in enumerate(month_keys):
        dt = datetime.strptime(mk, "%Y-%m")
        month_label = dt.strftime("%B %Y")
        agg = month_agg[mk]
        scenarios = monthly[mk]

        # Scenario rows
        rows_html = []
        for sc in sorted(scenarios, key=lambda s: s["date"]):
            date_str = sc["date"].strftime("%m/%d")
            badges = " ".join(outcome_badge(t["outcome"]) for t in sc["trades"])
            if not sc["trades"]:
                badges = '<span class="text-slate-400">--</span>'

            contracts_parts = []
            for t in sc["trades"]:
                c = t.get("contracts")
                if c is not None:
                    contracts_parts.append(f"{c:g}")
            if contracts_parts:
                contracts_str = " / ".join(contracts_parts)
            else:
                contracts_str = '<span class="text-slate-400">--</span>'

            status_cls = "text-emerald-400 font-semibold" if sc["status"] == "PASS" else "text-rose-500 font-semibold"
            status_label = sc["status"]

            sc_usd = sc["net_usd"]
            sc_pct = sc["net_pct"]
            sc_comm = sc.get("net_commission", 0.0)
            comm_str = f"${sc_comm:,.2f}" if sc_comm > 0 else "--"

            rows_html.append(f"""
                <tr class="hover:bg-white/[0.03]">
                    <td class="px-4 py-2.5 border-b border-white/5 w-[60px] text-slate-400 whitespace-nowrap">{_h(date_str)}</td>
                    <td class="px-4 py-2.5 border-b border-white/5 font-medium text-slate-100">{_h(sc['name'])}</td>
                    <td class="px-4 py-2.5 border-b border-white/5 w-[60px] text-center"><span class="{status_cls}">{status_label}</span></td>
                    <td class="px-4 py-2.5 border-b border-white/5 w-20">{badges}</td>
                    <td class="px-4 py-2.5 border-b border-white/5 w-[80px] text-center text-slate-200 tabular-nums text-xs">{contracts_str}</td>
                    <td class="px-4 py-2.5 border-b border-white/5 w-[90px] text-right tabular-nums font-semibold {_pnl_class_tw(sc_pct)}">{fmt_pct(sc_pct)}</td>
                    <td class="px-4 py-2.5 border-b border-white/5 w-[90px] text-right tabular-nums font-semibold {_pnl_class_tw(sc_usd)}">{fmt_usd(sc_usd)}</td>
                    <td class="px-4 py-2.5 border-b border-white/5 w-[90px] text-right tabular-nums font-semibold">{comm_str}</td>
                </tr>""")

        wl_parts = []
        if agg["wins"]:
            wl_parts.append(f'<span class="text-emerald-400">{agg["wins"]}W</span>')
        if agg["losses"]:
            wl_parts.append(f'<span class="text-rose-500">{agg["losses"]}L</span>')
        if agg["be"]:
            wl_parts.append(f'<span class="text-amber-400">{agg["be"]}B/E</span>')
        if agg["sp"]:
            wl_parts.append(f'<span class="text-accent-400">{agg["sp"]}SP</span>')
        wl_str = " / ".join(wl_parts) if wl_parts else '<span class="text-slate-400">--</span>'

        re_pill = ""
        re_w, re_l, re_b = agg["reentry_win"], agg["reentry_loss"], agg["reentry_be"]
        if re_w + re_l + re_b > 0:
            re_parts = []
            if re_w: re_parts.append(f'<span class="text-emerald-400">{re_w}W</span>')
            if re_l: re_parts.append(f'<span class="text-rose-500">{re_l}L</span>')
            if re_b: re_parts.append(f'<span class="text-amber-400">{re_b}B</span>')
            re_pill = f'<div class="text-sm px-3 py-1 rounded-md bg-white/5 whitespace-nowrap">RE: {" / ".join(re_parts)}</div>'

        card = f"""
        <div class="month-card snap-start min-w-full flex-shrink-0 bg-surface-800 border border-surface-700 rounded-xl overflow-hidden" data-month="{mk}" id="month-{idx}">
            <div class="month-header px-5 py-4 border-b border-surface-700 flex justify-between items-center flex-wrap gap-3">
                <h2 class="text-xl font-bold text-slate-100">{month_label}</h2>
                <div class="month-stats flex flex-wrap gap-3">
                    <div class="stat-pill text-sm px-3 py-1 rounded-md bg-white/5 whitespace-nowrap">{wl_str}</div>
                    <div class="stat-pill text-sm px-3 py-1 rounded-md bg-white/5 whitespace-nowrap {_pnl_class_tw(agg['usd'])}">Net: {fmt_usd(agg['usd'])}</div>
                    <div class="stat-pill text-sm px-3 py-1 rounded-md bg-white/5 whitespace-nowrap">Balance: ${agg['balance']:,.0f}</div>
                    {re_pill}
                </div>
            </div>
            <table class="trades-table w-full border-collapse text-sm">
                <thead>
                    <tr>
                        <th class="text-left px-4 py-2.5 text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15">Date</th>
                        <th class="text-left px-4 py-2.5 text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15">Scenario</th>
                        <th class="text-left px-4 py-2.5 text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15">Status</th>
                        <th class="text-left px-4 py-2.5 text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15">Result</th>
                        <th class="text-center px-4 py-2.5 text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15 w-[80px]">Contracts</th>
                        <th class="text-right px-4 py-2.5 text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15">%</th>
                        <th class="text-right px-4 py-2.5 text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15">$ PnL</th>
                        <th class="text-right px-4 py-2.5 text-xs uppercase tracking-wider text-slate-400 border-b border-surface-700 bg-black/15">Commission</th>
                    </tr>
                </thead>
                <tbody>
                    {"".join(rows_html)}
                </tbody>
            </table>
        </div>"""
        month_cards_html.append(card)

    # Build calendar view HTML
    calendar_cards_html = []
    for idx, mk in enumerate(month_keys):
        dt = datetime.strptime(mk, "%Y-%m")
        month_label = dt.strftime("%B %Y")
        agg = month_agg[mk]
        scenarios = monthly[mk]

        # Map date -> list of scenarios for that day
        day_map = defaultdict(list)
        for sc in scenarios:
            day_map[sc["date"].day].append(sc)

        # Build calendar grid
        cal = calendar.Calendar(firstweekday=6)  # Sunday first
        weeks = cal.monthdayscalendar(dt.year, dt.month)

        weeks_html = []
        for week in weeks:
            cells = []
            for day in week:
                if day == 0:
                    cells.append('<td class="align-top p-2 h-[100px] border border-surface-700 overflow-hidden bg-black/10 border-white/5"></td>')
                    continue

                day_scenarios = day_map.get(day, [])
                if day_scenarios:
                    inner_parts = [f'<div class="text-xs text-slate-500 mb-1 font-medium">{day}</div>']
                    for sc in day_scenarios:
                        badges = " ".join(outcome_badge(t["outcome"]) for t in sc["trades"])
                        if not badges:
                            badges = '<span class="text-slate-400">--</span>'
                        pnl_cls = _pnl_class_tw(sc["net_usd"])
                        inner_parts.append(
                            f'<div class="mt-1">'
                            f'{badges}'
                            f'<div class="text-xs font-bold tabular-nums mt-1 {pnl_cls}">{fmt_usd(sc["net_usd"])}</div>'
                            f'</div>'
                        )
                    cells.append(f'<td class="align-top p-2 h-[100px] border border-surface-700 overflow-hidden bg-accent-500/10">{"".join(inner_parts)}</td>')
                else:
                    cells.append(f'<td class="align-top p-2 h-[100px] border border-surface-700 overflow-hidden"><div class="text-xs text-slate-500 mb-1 font-medium">{day}</div></td>')
            weeks_html.append(f'<tr>{"".join(cells)}</tr>')

        wl_parts = []
        if agg["wins"]:
            wl_parts.append(f'<span class="text-emerald-400">{agg["wins"]}W</span>')
        if agg["losses"]:
            wl_parts.append(f'<span class="text-rose-500">{agg["losses"]}L</span>')
        if agg["be"]:
            wl_parts.append(f'<span class="text-amber-400">{agg["be"]}B/E</span>')
        if agg["sp"]:
            wl_parts.append(f'<span class="text-accent-400">{agg["sp"]}SP</span>')
        wl_str = " / ".join(wl_parts) if wl_parts else '<span class="text-slate-400">--</span>'

        cal_re_pill = ""
        cal_re_w, cal_re_l, cal_re_b = agg["reentry_win"], agg["reentry_loss"], agg["reentry_be"]
        if cal_re_w + cal_re_l + cal_re_b > 0:
            cal_re_parts = []
            if cal_re_w: cal_re_parts.append(f'<span class="text-emerald-400">{cal_re_w}W</span>')
            if cal_re_l: cal_re_parts.append(f'<span class="text-rose-500">{cal_re_l}L</span>')
            if cal_re_b: cal_re_parts.append(f'<span class="text-amber-400">{cal_re_b}B</span>')
            cal_re_pill = f'<div class="text-sm px-3 py-1 rounded-md bg-white/5 whitespace-nowrap">RE: {" / ".join(cal_re_parts)}</div>'

        visible = "block" if idx == 0 else "hidden"
        cal_card = f"""
        <div class="calendar-card {visible} bg-surface-800 border border-surface-700 rounded-xl overflow-hidden" data-month="{mk}" data-cal-idx="{idx}">
            <div class="month-header px-5 py-4 border-b border-surface-700 flex justify-between items-center flex-wrap gap-3">
                <h2 class="text-xl font-bold text-slate-100">{month_label}</h2>
                <div class="month-stats flex flex-wrap gap-3">
                    <div class="stat-pill text-sm px-3 py-1 rounded-md bg-white/5 whitespace-nowrap">{wl_str}</div>
                    <div class="stat-pill text-sm px-3 py-1 rounded-md bg-white/5 whitespace-nowrap {_pnl_class_tw(agg['usd'])}">Net: {fmt_usd(agg['usd'])}</div>
                    <div class="stat-pill text-sm px-3 py-1 rounded-md bg-white/5 whitespace-nowrap">Balance: ${agg['balance']:,.0f}</div>
                    {cal_re_pill}
                </div>
            </div>
            <table class="cal-grid w-full border-collapse table-fixed">
                <thead>
                    <tr>
                        <th class="px-1 py-2.5 text-xs uppercase tracking-wider text-slate-400 text-center border-b border-surface-700 bg-black/15">Sun</th>
                        <th class="px-1 py-2.5 text-xs uppercase tracking-wider text-slate-400 text-center border-b border-surface-700 bg-black/15">Mon</th>
                        <th class="px-1 py-2.5 text-xs uppercase tracking-wider text-slate-400 text-center border-b border-surface-700 bg-black/15">Tue</th>
                        <th class="px-1 py-2.5 text-xs uppercase tracking-wider text-slate-400 text-center border-b border-surface-700 bg-black/15">Wed</th>
                        <th class="px-1 py-2.5 text-xs uppercase tracking-wider text-slate-400 text-center border-b border-surface-700 bg-black/15">Thu</th>
                        <th class="px-1 py-2.5 text-xs uppercase tracking-wider text-slate-400 text-center border-b border-surface-700 bg-black/15">Fri</th>
                        <th class="px-1 py-2.5 text-xs uppercase tracking-wider text-slate-400 text-center border-b border-surface-700 bg-black/15">Sat</th>
                    </tr>
                </thead>
                <tbody>
                    {"".join(weeks_html)}
                </tbody>
            </table>
        </div>"""
        calendar_cards_html.append(cal_card)

    # Month pills for navigation
    month_pills_html = []
    for idx, mk in enumerate(month_keys):
        dt = datetime.strptime(mk, "%Y-%m")
        label = dt.strftime("%b %Y")
        active_cls = "bg-accent-600 border-accent-600 text-white font-semibold" if idx == 0 else "bg-transparent border-surface-700 text-slate-400 hover:border-accent hover:text-slate-100"
        month_pills_html.append(
            f'<button class="month-pill px-3.5 py-1.5 rounded-full border text-sm cursor-pointer transition-all {active_cls}" data-index="{idx}" onclick="scrollToMonth({idx})">{label}</button>'
        )

    # Equity curve SVG
    eq_svg = ""
    if len(equity_points) > 1:
        balances = [p["balance"] for p in equity_points]
        min_b = min(balances)
        max_b = max(balances)
        b_range = max_b - min_b if max_b != min_b else 1
        n = len(equity_points)
        svg_w, svg_h = 800, 200
        padding = 40
        plot_w = svg_w - 2 * padding
        plot_h = svg_h - 2 * padding

        points = []
        for i, p in enumerate(equity_points):
            x = padding + (i / max(n - 1, 1)) * plot_w
            y = padding + plot_h - ((p["balance"] - min_b) / b_range) * plot_h
            points.append(f"{x:.1f},{y:.1f}")

        # Area fill
        area_points = points + [f"{padding + plot_w:.1f},{padding + plot_h:.1f}", f"{padding:.1f},{padding + plot_h:.1f}"]

        # Grid lines (5 horizontal)
        grid_lines = ""
        for i in range(5):
            gy = padding + (i / 4) * plot_h
            gval = max_b - (i / 4) * b_range
            grid_lines += f'<line x1="{padding}" y1="{gy:.1f}" x2="{svg_w - padding}" y2="{gy:.1f}" stroke="#334155" stroke-dasharray="4,4" />'
            grid_lines += f'<text x="{padding - 5}" y="{gy:.1f}" text-anchor="end" fill="#94a3b8" font-size="11" dominant-baseline="middle">${gval:,.0f}</text>'

        # Color the line based on final balance vs start
        line_color = "#34d399" if running >= account else "#f43f5e"

        eq_svg = f"""
        <svg viewBox="0 0 {svg_w} {svg_h}" class="w-full h-auto max-h-[200px]">
            {grid_lines}
            <polygon points="{' '.join(area_points)}" fill="{line_color}" opacity="0.1" />
            <polyline points="{' '.join(points)}" fill="none" stroke="{line_color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
        </svg>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Strategy Report</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>
{TAILWIND_CONFIG}
</script>
<style>
.month-scroller::-webkit-scrollbar {{ height: 6px; }}
.month-scroller::-webkit-scrollbar-track {{ background: #0f172a; border-radius: 3px; }}
.month-scroller::-webkit-scrollbar-thumb {{ background: #334155; border-radius: 3px; }}
</style>
</head>
<body class="bg-surface-950 text-slate-300 font-sans leading-relaxed min-h-screen">

<header class="bg-surface-900 border-b border-surface-700 py-6 mb-6">
    <div class="max-w-7xl mx-auto px-6">
        <h1 class="text-2xl font-bold text-slate-100">Trading Strategy Report <span class="text-slate-400 font-normal text-sm ml-3">{_h(mode_label)} &mdash; ${account:,.0f} account, {f"{risk_pct}% of balance" if risk_pct else f"${risk:,.0f} fixed"} risk/trade</span></h1>
        <div class="flex flex-wrap gap-6 mt-4">
            <div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-3 min-w-[140px]">
                <div class="text-xs uppercase tracking-wider text-slate-400 mb-0.5">Trades</div>
                <div class="text-xl font-bold text-slate-100">{total_t}</div>
            </div>
            <div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-3 min-w-[140px]">
                <div class="text-xs uppercase tracking-wider text-slate-400 mb-0.5">Record</div>
                <div class="text-xl font-bold text-slate-100"><span class="text-emerald-400">{total_w}W</span> / <span class="text-rose-500">{total_l}L</span> / <span class="text-amber-400">{total_be}B</span>{f'/ <span class="text-accent-400">{total_sp}SP</span>' if total_sp else ''}</div>
            </div>
            <div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-3 min-w-[140px]">
                <div class="text-xs uppercase tracking-wider text-slate-400 mb-0.5">Win Rate</div>
                <div class="text-xl font-bold {_pnl_class_tw(winrate - 50)}">{winrate:.1f}%</div>
            </div>
            <div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-3 min-w-[140px]">
                <div class="text-xs uppercase tracking-wider text-slate-400 mb-0.5">Net P&amp;L</div>
                <div class="text-xl font-bold {_pnl_class_tw(net_usd)}">{fmt_usd(net_usd)}</div>
            </div>
            <div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-3 min-w-[140px]">
                <div class="text-xs uppercase tracking-wider text-slate-400 mb-0.5">Return</div>
                <div class="text-xl font-bold {_pnl_class_tw(net_pct)}">{fmt_pct(net_pct)}</div>
            </div>
            <div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-3 min-w-[140px]">
                <div class="text-xs uppercase tracking-wider text-slate-400 mb-0.5">Max Consec W / L</div>
                <div class="text-xl font-bold text-slate-100"><span class="text-emerald-400">{max_cw}</span> / <span class="text-rose-500">{max_cl}</span></div>
            </div>
            <div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-3 min-w-[140px]">
                <div class="text-xs uppercase tracking-wider text-slate-400 mb-0.5">Max Drawdown (from peak)</div>
                <div class="text-xl font-bold text-rose-500">${max_dd_usd:,.0f} ({max_dd_pct:.2f}%)</div>
            </div>
            <div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-3 min-w-[140px]">
                <div class="text-xs uppercase tracking-wider text-slate-400 mb-0.5">Max Drawdown (from start)</div>
                <div class="text-xl font-bold text-rose-500">${max_dd_start_usd:,.0f} ({max_dd_start_pct:.2f}%)</div>
            </div>
            <div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-3 min-w-[140px]">
                <div class="text-xs uppercase tracking-wider text-slate-400 mb-0.5">Monthly Avg</div>
                <div class="text-xl font-bold {_pnl_class_tw(avg_monthly_usd)}">{fmt_usd(avg_monthly_usd)}</div>
            </div>
            <div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-3 min-w-[140px]">
                <div class="text-xs uppercase tracking-wider text-slate-400 mb-0.5">Total Commission</div>
                <div class="text-xl font-bold text-rose-500">${total_commission:,.2f}</div>
            </div>
            {"" if total_rw + total_rl + total_rb == 0 else f'''<div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-3 min-w-[140px]">
                <div class="text-xs uppercase tracking-wider text-slate-400 mb-0.5">Re-entries</div>
                <div class="text-xl font-bold text-slate-100"><span class="text-emerald-400">{total_rw}W</span> / <span class="text-rose-500">{total_rl}L</span>{f' / <span class="text-amber-400">{total_rb}B</span>' if total_rb else ''}</div>
            </div>'''}
        </div>
    </div>
</header>

<div class="max-w-7xl mx-auto px-6">
    {"" if not eq_svg else f'''
    <div class="bg-surface-800 border border-surface-700 rounded-lg px-5 py-4 mb-6">
        <h3 class="text-sm text-slate-400 uppercase tracking-wider mb-2">Equity Curve</h3>
        {eq_svg}
    </div>
    '''}

    <div class="month-nav flex flex-wrap items-center gap-2 mb-5">
        <div class="view-toggle flex gap-1 bg-surface-800 border border-surface-700 rounded-lg p-1 mr-3">
            <button class="view-btn active px-3.5 py-1.5 rounded-md border-none bg-accent-600 text-white text-sm font-semibold cursor-pointer transition-all" onclick="switchView('list')">List</button>
            <button class="view-btn px-3.5 py-1.5 rounded-md border-none bg-transparent text-slate-400 text-sm cursor-pointer transition-all hover:text-slate-100" onclick="switchView('calendar')">Calendar</button>
        </div>
        <button class="nav-arrow w-9 h-9 rounded-full border border-surface-700 bg-surface-800 text-slate-100 text-lg cursor-pointer flex items-center justify-center transition-all flex-shrink-0 hover:border-accent" id="prevBtn" onclick="navigate(-1)">&larr;</button>
        {" ".join(month_pills_html)}
        <button class="nav-arrow w-9 h-9 rounded-full border border-surface-700 bg-surface-800 text-slate-100 text-lg cursor-pointer flex items-center justify-center transition-all flex-shrink-0 hover:border-accent" id="nextBtn" onclick="navigate(1)">&rarr;</button>
    </div>

    <div class="month-scroller flex overflow-x-auto snap-x snap-mandatory gap-5 pb-3 scroll-smooth" id="scroller">
        {"".join(month_cards_html)}
    </div>

    <div id="calendarContainer" class="hidden">
        {"".join(calendar_cards_html)}
    </div>

    <footer class="mt-8 pt-4 border-t border-surface-700 text-slate-500 text-sm text-center">
        Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} &mdash; {len(enriched)} scenarios across {len(month_keys)} month(s)
    </footer>
</div>

<script>
const listScroller = document.getElementById('scroller');
const calContainer = document.getElementById('calendarContainer');
const pills = document.querySelectorAll('.month-pill');
const listCards = listScroller.querySelectorAll('.month-card');
const calCards = calContainer.querySelectorAll('.calendar-card');
const viewBtns = document.querySelectorAll('.view-btn');
let currentIndex = 0;
let currentView = 'list';

function showMonth(idx) {{
    if (idx < 0 || idx >= listCards.length) return;
    currentIndex = idx;
    pills.forEach((p, i) => {{
        const isActive = i === idx;
        p.classList.toggle('bg-accent-600', isActive);
        p.classList.toggle('border-accent-600', isActive);
        p.classList.toggle('text-white', isActive);
        p.classList.toggle('font-semibold', isActive);
        p.classList.toggle('bg-transparent', !isActive);
        p.classList.toggle('border-surface-700', !isActive);
        p.classList.toggle('text-slate-400', !isActive);
        p.classList.toggle('hover:border-accent', !isActive);
        p.classList.toggle('hover:text-slate-100', !isActive);
    }});

    if (currentView === 'list') {{
        listCards[idx].scrollIntoView({{ behavior: 'smooth', inline: 'start', block: 'nearest' }});
    }} else {{
        calCards.forEach((c, i) => c.classList.toggle('hidden', i !== idx));
    }}
}}

function navigate(dir) {{
    showMonth(currentIndex + dir);
}}

function switchView(view) {{
    currentView = view;
    viewBtns.forEach(b => {{
        const isActive = b.textContent.toLowerCase() === view;
        b.classList.toggle('active', isActive);
        b.classList.toggle('bg-accent-600', isActive);
        b.classList.toggle('text-white', isActive);
        b.classList.toggle('font-semibold', isActive);
        b.classList.toggle('bg-transparent', !isActive);
        b.classList.toggle('text-slate-400', !isActive);
        b.classList.toggle('hover:text-slate-100', !isActive);
    }});
    if (view === 'list') {{
        listScroller.classList.remove('hidden');
        listScroller.classList.add('flex');
        calContainer.classList.add('hidden');
    }} else {{
        listScroller.classList.remove('flex');
        listScroller.classList.add('hidden');
        calContainer.classList.remove('hidden');
        calCards.forEach((c, i) => c.classList.toggle('hidden', i !== currentIndex));
    }}
}}

function scrollToMonth(idx) {{ showMonth(idx); }}

// Update active pill on scroll (list view only)
let scrollTimeout;
listScroller.addEventListener('scroll', () => {{
    clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(() => {{
        const scrollLeft = listScroller.scrollLeft;
        const cardWidth = listCards[0]?.offsetWidth || 1;
        const idx = Math.round(scrollLeft / (cardWidth + 20));
        if (idx !== currentIndex && idx >= 0 && idx < listCards.length) {{
            currentIndex = idx;
            pills.forEach((p, i) => {{
                const isActive = i === idx;
                p.classList.toggle('bg-accent-600', isActive);
                p.classList.toggle('border-accent-600', isActive);
                p.classList.toggle('text-white', isActive);
                p.classList.toggle('font-semibold', isActive);
                p.classList.toggle('bg-transparent', !isActive);
                p.classList.toggle('border-surface-700', !isActive);
                p.classList.toggle('text-slate-400', !isActive);
                p.classList.toggle('hover:border-accent', !isActive);
                p.classList.toggle('hover:text-slate-100', !isActive);
            }});
        }}
    }}, 50);
}});

// Keyboard navigation
document.addEventListener('keydown', (e) => {{
    if (e.key === 'ArrowLeft') navigate(-1);
    if (e.key === 'ArrowRight') navigate(1);
}});
</script>

</body>
</html>"""

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return str(out_path)
