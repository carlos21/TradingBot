#!/usr/bin/env python3
"""
HTML Report Generator — monthly horizontal-scroll view of scenario results.
"""

import calendar
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from dateutil import parser as dtparser

from report_utils import compute_trade_pnl, calc_max_dd, fmt_usd, fmt_pct, pnl_class, h as _h


def generate_html_report(summary_results, account, risk, mode, output_path,
                         nq_pv=2.0, fee_per_rt=1.50, be_threshold=0.5, risk_pct=None):
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
    enriched = []  # list of {name, date, status, trades: [{outcome, usd, pct, r}], net_usd, net_pct}
    running_balance = account
    for r in summary_results:
        date = dtparser.parse(r["date"]).date()
        trades_data = []
        for trade, close in (r.get("trade_pairs") or []):
            td = compute_trade_pnl(trade, close, account, risk, mode, nq_pv, fee_per_rt, be_threshold,
                                   risk_pct=risk_pct, balance=running_balance)
            trades_data.append(td)
            if td["outcome"] != "open":
                running_balance += td["usd"]
        net_usd = sum(t["usd"] for t in trades_data)
        net_pct = sum(t["pct"] for t in trades_data)
        enriched.append({
            "name": r["name"],
            "date": date,
            "status": r["status"],
            "reason": r.get("reason", ""),
            "values": r.get("values", ""),
            "trades": trades_data,
            "net_usd": net_usd,
            "net_pct": net_pct,
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
        reentry_win = reentry_loss = 0
        m_usd = 0.0
        m_pct = 0.0
        for sc in monthly[mk]:
            for t in sc["trades"]:
                if t["outcome"] == "open":
                    continue
                m_usd += t["usd"]
                m_pct += t["pct"]
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
                        reentry_win += 1
        balance += m_usd
        month_agg[mk] = {
            "wins": wins, "losses": losses, "be": bes, "sp": sps,
            "usd": m_usd, "pct": m_pct, "balance": balance,
            "reentry_win": reentry_win, "reentry_loss": reentry_loss,
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
    net_pct = sum(a["pct"] for a in month_agg.values())

    # Calculate monthly average profit
    num_months = len(month_agg) if month_agg else 1
    avg_monthly_usd = net_usd / num_months if num_months > 0 else 0.0
    avg_monthly_pct = avg_monthly_usd / account * 100 if account > 0 else 0.0

    total_rw = sum(a["reentry_win"] for a in month_agg.values())
    total_rl = sum(a["reentry_loss"] for a in month_agg.values())

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
    max_dd_usd, max_dd_pct = calc_max_dd(equity_balances)

    # ── 6. Build HTML ─────────────────────────────────────────────────────
    def outcome_badge(outcome):
        cls_map = {"win": "badge-win", "loss": "badge-loss", "be": "badge-be", "sp": "badge-sp", "open": "badge-open"}
        label_map = {"win": "W", "loss": "L", "be": "B/E", "sp": "SP", "open": "OPEN"}
        return f'<span class="badge {cls_map.get(outcome, "")}">{label_map.get(outcome, "?")}</span>'

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
                badges = '<span class="text-muted">--</span>'

            status_cls = "status-pass" if sc["status"] == "PASS" else "status-fail"
            status_label = sc["status"]

            sc_usd = sc["net_usd"]
            sc_pct = sc["net_pct"]

            rows_html.append(f"""
                <tr>
                    <td class="date-col">{_h(date_str)}</td>
                    <td class="name-col">{_h(sc['name'])}</td>
                    <td class="status-col"><span class="{status_cls}">{status_label}</span></td>
                    <td class="result-col">{badges}</td>
                    <td class="pnl-col {pnl_class(sc_pct)}">{fmt_pct(sc_pct)}</td>
                    <td class="pnl-col {pnl_class(sc_usd)}">{fmt_usd(sc_usd)}</td>
                </tr>""")

        wl_parts = []
        if agg["wins"]:
            wl_parts.append(f'<span class="positive">{agg["wins"]}W</span>')
        if agg["losses"]:
            wl_parts.append(f'<span class="negative">{agg["losses"]}L</span>')
        if agg["be"]:
            wl_parts.append(f'<span class="text-yellow">{agg["be"]}B/E</span>')
        if agg["sp"]:
            wl_parts.append(f'<span class="text-blue">{agg["sp"]}SP</span>')
        wl_str = " / ".join(wl_parts) if wl_parts else '<span class="text-muted">--</span>'

        re_pill = ""
        re_w, re_l = agg["reentry_win"], agg["reentry_loss"]
        if re_w + re_l > 0:
            re_pill = f'<div class="stat-pill">RE: <span class="positive">{re_w}W</span> / <span class="negative">{re_l}L</span></div>'

        card = f"""
        <div class="month-card" data-month="{mk}" id="month-{idx}">
            <div class="month-header">
                <h2>{month_label}</h2>
                <div class="month-stats">
                    <div class="stat-pill">{wl_str}</div>
                    <div class="stat-pill {pnl_class(agg['usd'])}">Net: {fmt_usd(agg['usd'])}</div>
                    <div class="stat-pill">Balance: ${agg['balance']:,.0f}</div>
                    {re_pill}
                </div>
            </div>
            <table class="trades-table">
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Scenario</th>
                        <th>Status</th>
                        <th>Result</th>
                        <th>%</th>
                        <th>$ PnL</th>
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
                    cells.append('<td class="cal-cell cal-empty"></td>')
                    continue

                day_scenarios = day_map.get(day, [])
                if day_scenarios:
                    inner_parts = [f'<div class="cal-day-num">{day}</div>']
                    for sc in day_scenarios:
                        badges = " ".join(outcome_badge(t["outcome"]) for t in sc["trades"])
                        if not badges:
                            badges = '<span class="text-muted">--</span>'
                        pnl_cls = pnl_class(sc["net_usd"])
                        inner_parts.append(
                            f'<div class="cal-trade">'
                            f'{badges}'
                            f'<div class="cal-pnl {pnl_cls}">{fmt_usd(sc["net_usd"])}</div>'
                            f'</div>'
                        )
                    cells.append(f'<td class="cal-cell cal-has-trade">{"".join(inner_parts)}</td>')
                else:
                    cells.append(f'<td class="cal-cell"><div class="cal-day-num">{day}</div></td>')
            weeks_html.append(f'<tr>{"".join(cells)}</tr>')

        wl_parts = []
        if agg["wins"]:
            wl_parts.append(f'<span class="positive">{agg["wins"]}W</span>')
        if agg["losses"]:
            wl_parts.append(f'<span class="negative">{agg["losses"]}L</span>')
        if agg["be"]:
            wl_parts.append(f'<span class="text-yellow">{agg["be"]}B/E</span>')
        if agg["sp"]:
            wl_parts.append(f'<span class="text-blue">{agg["sp"]}SP</span>')
        wl_str = " / ".join(wl_parts) if wl_parts else '<span class="text-muted">--</span>'

        cal_re_pill = ""
        cal_re_w, cal_re_l = agg["reentry_win"], agg["reentry_loss"]
        if cal_re_w + cal_re_l > 0:
            cal_re_pill = f'<div class="stat-pill">RE: <span class="positive">{cal_re_w}W</span> / <span class="negative">{cal_re_l}L</span></div>'

        visible = "block" if idx == 0 else "none"
        cal_card = f"""
        <div class="calendar-card" data-month="{mk}" data-cal-idx="{idx}" style="display:{visible}">
            <div class="month-header">
                <h2>{month_label}</h2>
                <div class="month-stats">
                    <div class="stat-pill">{wl_str}</div>
                    <div class="stat-pill {pnl_class(agg['usd'])}">Net: {fmt_usd(agg['usd'])}</div>
                    <div class="stat-pill">Balance: ${agg['balance']:,.0f}</div>
                    {cal_re_pill}
                </div>
            </div>
            <table class="cal-grid">
                <thead>
                    <tr>
                        <th>Sun</th><th>Mon</th><th>Tue</th><th>Wed</th><th>Thu</th><th>Fri</th><th>Sat</th>
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
        active = " active" if idx == 0 else ""
        month_pills_html.append(
            f'<button class="month-pill{active}" data-index="{idx}" onclick="scrollToMonth({idx})">{label}</button>'
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
            grid_lines += f'<line x1="{padding}" y1="{gy:.1f}" x2="{svg_w - padding}" y2="{gy:.1f}" stroke="var(--border)" stroke-dasharray="4,4" />'
            grid_lines += f'<text x="{padding - 5}" y="{gy:.1f}" text-anchor="end" fill="var(--text-muted)" font-size="11" dominant-baseline="middle">${gval:,.0f}</text>'

        # Color the line based on final balance vs start
        line_color = "var(--green)" if running >= account else "var(--red)"

        eq_svg = f"""
        <svg viewBox="0 0 {svg_w} {svg_h}" class="equity-chart">
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
    --blue-dim: #1e40af;
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
    max-width: 1200px;
    margin: 0 auto;
    padding: 24px;
}}

/* ── Header ── */
header {{
    background: var(--bg-header);
    border-bottom: 1px solid var(--border);
    padding: 24px 0;
    margin-bottom: 24px;
}}

header .container {{
    display: flex;
    flex-direction: column;
    gap: 16px;
}}

h1 {{
    font-size: 1.5rem;
    font-weight: 700;
    color: var(--text);
}}

h1 span {{
    color: var(--text-muted);
    font-weight: 400;
    font-size: 0.9rem;
    margin-left: 12px;
}}

.overall-stats {{
    display: flex;
    gap: 24px;
    flex-wrap: wrap;
}}

.stat-box {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 20px;
    min-width: 140px;
}}

.stat-box .label {{
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    margin-bottom: 2px;
}}

.stat-box .value {{
    font-size: 1.25rem;
    font-weight: 700;
}}

/* ── Equity curve ── */
.equity-section {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 20px;
    margin-bottom: 24px;
}}

.equity-section h3 {{
    font-size: 0.85rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 8px;
}}

.equity-chart {{
    width: 100%;
    height: auto;
    max-height: 200px;
}}

/* ── Month navigation ── */
.month-nav {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 20px;
    flex-wrap: wrap;
}}

.month-pill {{
    padding: 6px 14px;
    border-radius: 20px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-muted);
    font-size: 0.85rem;
    cursor: pointer;
    transition: all 0.2s;
    font-family: inherit;
}}

.month-pill:hover {{
    border-color: var(--blue);
    color: var(--text);
}}

.month-pill.active {{
    background: var(--blue);
    border-color: var(--blue);
    color: #fff;
    font-weight: 600;
}}

.nav-arrow {{
    width: 36px;
    height: 36px;
    border-radius: 50%;
    border: 1px solid var(--border);
    background: var(--bg-card);
    color: var(--text);
    font-size: 1.1rem;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s;
    font-family: inherit;
    flex-shrink: 0;
}}

.nav-arrow:hover {{
    border-color: var(--blue);
    background: var(--blue-dim);
}}

.nav-arrow:disabled {{
    opacity: 0.3;
    cursor: default;
}}

/* ── Month scroller ── */
.month-scroller {{
    display: flex;
    overflow-x: auto;
    scroll-snap-type: x mandatory;
    gap: 20px;
    padding-bottom: 12px;
    scroll-behavior: smooth;
    -webkit-overflow-scrolling: touch;
}}

.month-scroller::-webkit-scrollbar {{
    height: 6px;
}}

.month-scroller::-webkit-scrollbar-track {{
    background: var(--bg);
    border-radius: 3px;
}}

.month-scroller::-webkit-scrollbar-thumb {{
    background: var(--border);
    border-radius: 3px;
}}

.month-card {{
    scroll-snap-align: start;
    min-width: 100%;
    flex-shrink: 0;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
}}

.month-header {{
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
}}

.month-header h2 {{
    font-size: 1.2rem;
    font-weight: 700;
}}

.month-stats {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
}}

.stat-pill {{
    font-size: 0.85rem;
    padding: 4px 12px;
    border-radius: 6px;
    background: rgba(255,255,255,0.05);
    white-space: nowrap;
}}

/* ── Trades table ── */
.trades-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
}}

.trades-table th {{
    text-align: left;
    padding: 10px 16px;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    background: rgba(0,0,0,0.15);
}}

.trades-table td {{
    padding: 10px 16px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
}}

.trades-table tr:last-child td {{
    border-bottom: none;
}}

.trades-table tr:hover td {{
    background: rgba(255,255,255,0.03);
}}

.date-col {{ width: 60px; color: var(--text-muted); white-space: nowrap; }}
.name-col {{ font-weight: 500; }}
.status-col {{ width: 60px; text-align: center; }}
.result-col {{ width: 80px; }}
.pnl-col {{ width: 90px; text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }}

/* ── Badges & colors ── */
.badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.03em;
}}

.badge-win  {{ background: rgba(34,197,94,0.15); color: var(--green); }}
.badge-loss {{ background: rgba(239,68,68,0.15); color: var(--red); }}
.badge-be   {{ background: rgba(234,179,8,0.15); color: var(--yellow); }}
.badge-sp   {{ background: rgba(59,130,246,0.15); color: var(--blue); }}
.badge-open {{ background: rgba(148,163,184,0.15); color: var(--text-muted); }}

.positive {{ color: var(--green); }}
.negative {{ color: var(--red); }}
.neutral  {{ color: var(--text-muted); }}
.text-yellow {{ color: var(--yellow); }}
.text-blue   {{ color: var(--blue); }}
.text-muted  {{ color: var(--text-muted); }}

.status-pass {{ color: var(--green); font-weight: 600; }}
.status-fail {{ color: var(--red); font-weight: 600; }}

/* ── View toggle ── */
.view-toggle {{
    display: flex;
    gap: 4px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 3px;
    margin-right: 12px;
}}

.view-btn {{
    padding: 6px 14px;
    border-radius: 6px;
    border: none;
    background: transparent;
    color: var(--text-muted);
    font-size: 0.85rem;
    cursor: pointer;
    font-family: inherit;
    transition: all 0.2s;
}}

.view-btn:hover {{
    color: var(--text);
}}

.view-btn.active {{
    background: var(--blue);
    color: #fff;
    font-weight: 600;
}}

/* ── Calendar view ── */
.calendar-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
}}

.cal-grid {{
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
}}

.cal-grid th {{
    padding: 10px 4px;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    text-align: center;
    border-bottom: 1px solid var(--border);
    background: rgba(0,0,0,0.15);
}}

.cal-cell {{
    vertical-align: top;
    padding: 8px;
    height: 100px;
    border: 1px solid var(--border);
    overflow: hidden;
}}

.cal-empty {{
    background: rgba(0,0,0,0.1);
    border-color: rgba(255,255,255,0.04);
}}

.cal-has-trade {{
    background: rgba(59,130,246,0.08);
}}

.cal-day-num {{
    font-size: 0.8rem;
    color: var(--text-dim);
    margin-bottom: 4px;
    font-weight: 500;
}}

.cal-has-trade .cal-day-num {{
    color: var(--text);
    font-weight: 700;
}}

.cal-trade {{
    margin-top: 4px;
}}

.cal-trade .badge {{
    font-size: 0.7rem;
    padding: 2px 6px;
}}

.cal-pnl {{
    font-size: 0.8rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    margin-top: 3px;
}}

/* ── Footer ── */
footer {{
    margin-top: 32px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
    color: var(--text-dim);
    font-size: 0.8rem;
    text-align: center;
}}

@media (max-width: 768px) {{
    .overall-stats {{ gap: 12px; }}
    .stat-box {{ min-width: 100px; padding: 8px 12px; }}
    .stat-box .value {{ font-size: 1rem; }}
    .month-header {{ flex-direction: column; align-items: flex-start; }}
}}
</style>
</head>
<body>

<header>
    <div class="container">
        <h1>Trading Strategy Report <span>{_h(mode_label)} &mdash; ${account:,.0f} account, ${risk:,.0f} risk/trade</span></h1>
        <div class="overall-stats">
            <div class="stat-box">
                <div class="label">Trades</div>
                <div class="value">{total_t}</div>
            </div>
            <div class="stat-box">
                <div class="label">Record</div>
                <div class="value"><span class="positive">{total_w}W</span> / <span class="negative">{total_l}L</span> / <span class="text-yellow">{total_be}B</span>{f'/ <span class="text-blue">{total_sp}SP</span>' if total_sp else ''}</div>
            </div>
            <div class="stat-box">
                <div class="label">Win Rate</div>
                <div class="value {pnl_class(winrate - 50)}">{winrate:.1f}%</div>
            </div>
            <div class="stat-box">
                <div class="label">Net P&amp;L</div>
                <div class="value {pnl_class(net_usd)}">{fmt_usd(net_usd)}</div>
            </div>
            <div class="stat-box">
                <div class="label">Return</div>
                <div class="value {pnl_class(net_pct)}">{fmt_pct(net_pct)}</div>
            </div>
            <div class="stat-box">
                <div class="label">Max Consec W / L</div>
                <div class="value"><span class="positive">{max_cw}</span> / <span class="negative">{max_cl}</span></div>
            </div>
            <div class="stat-box">
                <div class="label">Max Drawdown</div>
                <div class="value negative">${max_dd_usd:,.0f}</div>
            </div>
            <div class="stat-box">
                <div class="label">Max Drawdown %</div>
                <div class="value negative">{max_dd_pct:.2f}%</div>
            </div>
            <div class="stat-box">
                <div class="label">Monthly Avg</div>
                <div class="value {pnl_class(avg_monthly_usd)}">{fmt_usd(avg_monthly_usd)}</div>
            </div>
            {"" if total_rw + total_rl == 0 else f'''<div class="stat-box">
                <div class="label">Re-entries</div>
                <div class="value"><span class="positive">{total_rw}W</span> / <span class="negative">{total_rl}L</span></div>
            </div>'''}
        </div>
    </div>
</header>

<div class="container">
    {"" if not eq_svg else f'''
    <div class="equity-section">
        <h3>Equity Curve</h3>
        {eq_svg}
    </div>
    '''}

    <div class="month-nav">
        <div class="view-toggle">
            <button class="view-btn active" onclick="switchView('list')">List</button>
            <button class="view-btn" onclick="switchView('calendar')">Calendar</button>
        </div>
        <button class="nav-arrow" id="prevBtn" onclick="navigate(-1)">&larr;</button>
        {" ".join(month_pills_html)}
        <button class="nav-arrow" id="nextBtn" onclick="navigate(1)">&rarr;</button>
    </div>

    <div class="month-scroller" id="scroller">
        {"".join(month_cards_html)}
    </div>

    <div id="calendarContainer" style="display:none">
        {"".join(calendar_cards_html)}
    </div>

    <footer>
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
    pills.forEach((p, i) => p.classList.toggle('active', i === idx));

    if (currentView === 'list') {{
        listCards[idx].scrollIntoView({{ behavior: 'smooth', inline: 'start', block: 'nearest' }});
    }} else {{
        calCards.forEach((c, i) => c.style.display = i === idx ? 'block' : 'none');
    }}
}}

function navigate(dir) {{
    showMonth(currentIndex + dir);
}}

function switchView(view) {{
    currentView = view;
    viewBtns.forEach(b => b.classList.toggle('active', b.textContent.toLowerCase() === view));
    if (view === 'list') {{
        listScroller.style.display = 'flex';
        calContainer.style.display = 'none';
    }} else {{
        listScroller.style.display = 'none';
        calContainer.style.display = 'block';
        calCards.forEach((c, i) => c.style.display = i === currentIndex ? 'block' : 'none');
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
            pills.forEach((p, i) => p.classList.toggle('active', i === idx));
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
