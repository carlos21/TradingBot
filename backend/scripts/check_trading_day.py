#!/usr/bin/env python3
"""Concise, organized trading-day report for the check-tradingbot-logs skill.

This script is a thin presentation adapter: it depends on narrow protocols,
uses repository code for persistence access, and delegates all formatting to a
ReportBuilder. Domain rules (e.g., slippage math) live in the domain layer and
are reused here, not reimplemented.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, Protocol
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Allow running from repo root or backend/scripts/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.src.domain.models import TradeData  # noqa: E402


# ---------------------------------------------------------------------------
# Domain / application DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TradeRow:
    """Flat DTO for a single closed trade row in the report."""

    time: datetime
    direction: str
    pair: str
    calc_entry: float | None
    real_entry: float
    slippage: float | None
    exit_price: float | None
    result_type: str | None
    result_r: float | None
    gross_pnl: float | None
    commission: float | None
    realized_pnl: float | None
    is_estimate: bool


@dataclass(frozen=True)
class DaySummary:
    """Aggregated numbers for the trading day."""

    total: int
    winners: int
    losers: int
    breakeven: int
    gross_pnl: float
    commission: float
    realized_pnl: float


@dataclass(frozen=True)
class LogSummary:
    """Problems and state changes pulled from application logs."""

    errors: list[str]
    warnings: list[str]
    connection_events: list[str]
    trade_events: list[str]


# ---------------------------------------------------------------------------
# Protocols (Dependency Inversion)
# ---------------------------------------------------------------------------


class TradeQueryService(Protocol):
    """Read-only query surface for trade data."""

    def get_trades_for_date(self, target_date: date) -> list[TradeData]:
        ...


class LogReader(Protocol):
    """Read-only log surface for a single date."""

    def log_paths(self, target_date: date) -> list[Path]:
        ...

    def read_errors(self, target_date: date) -> list[str]:
        ...

    def read_warnings(self, target_date: date) -> list[str]:
        ...

    def read_connection_events(self, target_date: date) -> list[str]:
        ...

    def read_trade_events(self, target_date: date) -> list[str]:
        ...


# ---------------------------------------------------------------------------
# Infrastructure adapters
# ---------------------------------------------------------------------------


class SQLTradeQueryService:
    """SQLite-backed trade query service.

    Uses raw SQL so the script can run without initializing the full ORM stack,
    while still relying on the domain TradeData DTO.
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path

    def get_trades_for_date(self, target_date: date) -> list[TradeData]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT
                    trade_id, pair, trade_type, entry_price, stop_loss, take_profit,
                    risk, risk_dollars, risk_pct, account_balance, contracts,
                    entry_time, exit_price, exit_time, result, result_type,
                    fees, pnl_usd, original_entry_price, gross_pnl, realized_pnl,
                    params, logs, source, account, signal_id, created_at
                FROM trades
                WHERE date(entry_time) = ?
                ORDER BY entry_time
                """,
                (target_date.isoformat(),),
            ).fetchall()
        finally:
            conn.close()

        return [_row_to_trade_data(row) for row in rows]


class PostgresTradeQueryService:
    """PostgreSQL-backed trade query service for the live instance.

    The live database only listens on the Windows host (see DATABASE_URL in
    .env), so this service is expected to run via Windows interop from WSL.
    Day boundaries are computed in the market timezone (Etc/GMT+5) so the
    filter is independent of the Postgres server timezone.
    """

    MARKET_TZ = ZoneInfo("Etc/GMT+5")

    def __init__(self, database_url: str):
        from sqlalchemy import create_engine

        self._engine = create_engine(database_url)

    def get_trades_for_date(self, target_date: date) -> list[TradeData]:
        from sqlalchemy import text

        start = datetime(target_date.year, target_date.month, target_date.day,
                         tzinfo=self.MARKET_TZ)
        end = start + timedelta(days=1)
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT
                        trade_id, pair, trade_type, entry_price, stop_loss, take_profit,
                        risk, risk_dollars, risk_pct, account_balance, contracts,
                        entry_time, exit_price, exit_time, result, result_type,
                        fees, pnl_usd, original_entry_price, gross_pnl, realized_pnl,
                        params, logs, source, account, signal_id, created_at
                    FROM trades
                    WHERE entry_time >= :start AND entry_time < :end
                    ORDER BY entry_time
                    """
                ),
                {"start": start, "end": end},
            ).mappings().all()

        return [_row_to_trade_data(row) for row in rows]


def _row_to_trade_data(row: Mapping) -> TradeData:
    return TradeData(
        trade_id=row["trade_id"],
        pair=row["pair"],
        trade_type=row["trade_type"],
        entry_price=row["entry_price"],
        stop_loss=row["stop_loss"],
        take_profit=row["take_profit"],
        risk=row["risk"],
        risk_dollars=row["risk_dollars"],
        risk_pct=row["risk_pct"],
        account_balance=row["account_balance"],
        contracts=row["contracts"],
        entry_time=_parse_dt(row["entry_time"]),
        exit_price=row["exit_price"],
        exit_time=_parse_dt(row["exit_time"]),
        result=row["result"],
        result_type=row["result_type"],
        fees=row["fees"],
        pnl_usd=row["pnl_usd"],
        original_entry_price=row["original_entry_price"],
        gross_pnl=row["gross_pnl"],
        realized_pnl=row["realized_pnl"],
        params=_parse_json(row["params"]),
        logs=_parse_json(row["logs"]) or [],
        source=row["source"],
        account=row["account"],
        signal_id=row["signal_id"],
        created_at=_parse_dt(row["created_at"]),
    )


def _parse_dt(value):
    if value is None:
        return None
    if isinstance(value, str):
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return value


def _parse_json(value):
    import json

    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


class FileLogReader:
    """Reads Python application logs from the standard log directories."""

    def __init__(self, log_dir: Path):
        self._log_dir = log_dir

    def log_paths(self, target_date: date) -> list[Path]:
        candidates = [
            self._log_dir / f"app_{target_date.isoformat()}.log",
            self._log_dir / "ninja" / f"app_{target_date.isoformat()}.log",
            self._log_dir / "meta" / f"app_{target_date.isoformat()}.log",
        ]
        return [p for p in candidates if p.exists()]

    def read_errors(self, target_date: date) -> list[str]:
        return self._grep(
            target_date, ("ERROR", "CRITICAL", "FATAL", "EXCEPTION"), limit=30
        )

    def read_warnings(self, target_date: date) -> list[str]:
        return self._grep(
            target_date,
            ("WARNING", "timeout", "disconnect", "rejected", "cancelled", "failed"),
            limit=30,
        )

    def read_connection_events(self, target_date: date) -> list[str]:
        return self._grep(
            target_date,
            (
                "platform_connected",
                "platform_disconnected",
                "gateway_started",
                "gateway_stopped",
                "ZMQDataSource",
            ),
            limit=20,
        )

    def read_trade_events(self, target_date: date) -> list[str]:
        return self._grep(
            target_date,
            ("ENTRY FILL", "Exit fill", "TradeClose", "broker_pnl", "POSITION CLOSED"),
            limit=30,
        )

    def _grep(
        self, target_date: date, terms: Iterable[str], limit: int
    ) -> list[str]:
        found: list[str] = []
        for path in self.log_paths(target_date):
            try:
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if any(term.lower() in line.lower() for term in terms):
                            found.append(line.rstrip())
            except OSError:
                continue
        return found[-limit:]


# ---------------------------------------------------------------------------
# Presentation / report builder
# ---------------------------------------------------------------------------


class ReportBuilder:
    """Builds a concise Markdown trading-day report from domain DTOs."""

    TZ = ZoneInfo("Etc/GMT+5")

    def build(
        self,
        target_date: date,
        trades: list[TradeData],
        log_paths: list[Path],
        log_summary: LogSummary,
    ) -> str:
        lines: list[str] = []

        lines.append(f"# Trading Day Report: {target_date.isoformat()}")
        lines.append("")
        lines.append(f"**Log files checked:** {', '.join(str(p) for p in log_paths) or 'none found'}")
        lines.append("")

        if not trades:
            lines.append("**Status:** No trades recorded.")
            lines.append("")
            self._append_errors(lines, log_summary)
            self._append_connection_events(lines, log_summary)
            return "\n".join(lines)

        rows = [self._to_trade_row(t) for t in trades]
        summary = self._summarize(rows)

        lines.append("## Day Summary")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|---|---|")
        lines.append(f"| Total trades | {summary.total} |")
        lines.append(f"| Winners | {summary.winners} |")
        lines.append(f"| Losers | {summary.losers} |")
        lines.append(f"| Breakeven | {summary.breakeven} |")
        lines.append(f"| Gross PnL | ${summary.gross_pnl:,.2f} |")
        lines.append(f"| Commission | ${summary.commission:,.2f} |")
        lines.append(f"| Realized PnL | ${summary.realized_pnl:,.2f} |")
        lines.append("")

        lines.append("## Trades")
        lines.append("")
        lines.append(
            "| Time | Dir | Calc Entry | Real Entry | Slippage | Exit | Type | Gross PnL | Commission | Realized PnL |"
        )
        lines.append(
            "|---|---|---|---|---|---|---|---|---|---|"
        )
        for row in rows:
            calc_entry = self._fmt_price(row.calc_entry) if row.calc_entry is not None else "—"
            slippage = f"{row.slippage:+.2f}" if row.slippage is not None else "—"
            gross = self._fmt_money(row.gross_pnl, row.is_estimate)
            comm = self._fmt_money(row.commission, row.is_estimate)
            realized = self._fmt_money(row.realized_pnl, row.is_estimate)
            exit_price = self._fmt_price(row.exit_price) if row.exit_price is not None else "—"
            time = row.time.astimezone(self.TZ).strftime("%H:%M")
            lines.append(
                f"| {time} | {row.direction} {row.pair} | {calc_entry} | {row.real_entry:.2f} | {slippage} | "
                f"{exit_price} | {row.result_type or '—'} | {gross} | {comm} | {realized} |"
            )
        lines.append("")

        self._append_errors(lines, log_summary)
        self._append_connection_events(lines, log_summary)
        self._append_narrative(lines, target_date, summary, log_summary)

        return "\n".join(lines)

    @staticmethod
    def _to_trade_row(trade: TradeData) -> TradeRow:
        has_gross = trade.gross_pnl is not None
        has_realized = trade.realized_pnl is not None
        has_commission = trade.fees is not None

        if has_gross and has_realized and has_commission:
            gross_pnl = trade.gross_pnl
            commission = trade.fees
            realized_pnl = trade.realized_pnl
            is_estimate = False
        elif trade.pnl_usd is not None and trade.fees is not None:
            realized_pnl = trade.pnl_usd
            commission = trade.fees
            gross_pnl = trade.pnl_usd + trade.fees
            is_estimate = True
        elif trade.pnl_usd is not None:
            realized_pnl = trade.pnl_usd
            commission = None
            gross_pnl = trade.pnl_usd
            is_estimate = True
        else:
            gross_pnl = commission = realized_pnl = None
            is_estimate = True

        return TradeRow(
            time=trade.entry_time,
            direction=trade.trade_type.upper(),
            pair=trade.pair,
            calc_entry=trade.original_entry_price,
            real_entry=trade.entry_price,
            slippage=trade.slippage,
            exit_price=trade.exit_price,
            result_type=trade.result_type,
            result_r=trade.result,
            gross_pnl=gross_pnl,
            commission=commission,
            realized_pnl=realized_pnl,
            is_estimate=is_estimate,
        )

    @staticmethod
    def _summarize(rows: list[TradeRow]) -> DaySummary:
        total = len(rows)
        winners = losers = breakeven = 0
        gross = comm = realized = 0.0

        for row in rows:
            if row.result_type == "BE":
                breakeven += 1
            elif row.realized_pnl is not None and row.realized_pnl > 0:
                winners += 1
            elif row.realized_pnl is not None and row.realized_pnl < 0:
                losers += 1

            if row.gross_pnl is not None:
                gross += row.gross_pnl
            if row.commission is not None:
                comm += row.commission
            if row.realized_pnl is not None:
                realized += row.realized_pnl

        return DaySummary(
            total=total,
            winners=winners,
            losers=losers,
            breakeven=breakeven,
            gross_pnl=gross,
            commission=comm,
            realized_pnl=realized,
        )

    def _append_errors(self, lines: list[str], log_summary: LogSummary) -> None:
        lines.append("## Errors / Anomalies")
        lines.append("")
        if log_summary.errors:
            for line in log_summary.errors[-10:]:
                lines.append(f"- `{line}`")
        else:
            lines.append("No errors, critical messages, or rejected orders found.")
        lines.append("")

        if log_summary.warnings:
            lines.append("**Warnings / Timeouts / Disconnects:**")
            for line in log_summary.warnings[-10:]:
                lines.append(f"- `{line}`")
            lines.append("")

    def _append_connection_events(
        self, lines: list[str], log_summary: LogSummary
    ) -> None:
        lines.append("## Connection / Platform State")
        lines.append("")
        if log_summary.connection_events:
            for line in log_summary.connection_events[-10:]:
                lines.append(f"- `{line}`")
        else:
            lines.append("No platform connection events found.")
        lines.append("")

    def _append_narrative(
        self,
        lines: list[str],
        target_date: date,
        summary: DaySummary,
        log_summary: LogSummary,
    ) -> None:
        lines.append("## Session Narrative")
        lines.append("")

        has_connection_events = bool(log_summary.connection_events)
        connected = any("connected" in e.lower() for e in log_summary.connection_events)
        disconnected = any(
            "disconnected" in e.lower() for e in log_summary.connection_events
        )

        parts = [f"On {target_date.isoformat()},"]
        if connected:
            parts.append("the platform connected.")
        elif disconnected:
            parts.append("the platform disconnected at least once.")
        elif has_connection_events:
            parts.append("platform state changes were logged.")
        else:
            parts.append("no connection events were logged.")

        if summary.total == 0:
            parts.append("No trades were taken.")
        else:
            parts.append(
                f"{summary.total} trade(s) were taken: {summary.winners} winner(s), "
                f"{summary.losers} loser(s), {summary.breakeven} breakeven."
            )
            if summary.realized_pnl > 0:
                parts.append(f"It was a winning day (+${summary.realized_pnl:,.2f} realized).")
            elif summary.realized_pnl < 0:
                parts.append(f"It was a losing day (${summary.realized_pnl:,.2f} realized).")
            else:
                parts.append("The day finished flat.")

        if log_summary.errors:
            parts.append("Errors or anomalies were detected; see above.")

        lines.append(" ".join(parts))
        lines.append("")

    @staticmethod
    def _fmt_price(value: float | None) -> str:
        if value is None:
            return "—"
        return f"{value:.2f}"

    @staticmethod
    def _fmt_money(value: float | None, is_estimate: bool) -> str:
        if value is None:
            return "—"
        prefix = "~" if is_estimate else ""
        return f"{prefix}${value:,.2f}"


# ---------------------------------------------------------------------------
# Composition root / CLI
# ---------------------------------------------------------------------------


def _today_local() -> date:
    return datetime.now(tz=ReportBuilder.TZ).date()


def _build_default_services() -> tuple[TradeQueryService, FileLogReader]:
    log_dir = PROJECT_ROOT / "logs"
    log_reader = FileLogReader(log_dir)

    # The live instance persists to PostgreSQL (DATABASE_URL in .env, e.g.
    # localhost:5433 on Windows). Only fall back to SQLite when no Postgres
    # URL is configured.
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url.startswith("postgresql"):
        return PostgresTradeQueryService(database_url), log_reader

    # The live NinjaTrader instance uses ninja.db; fall back to database.db.
    for candidate in (PROJECT_ROOT / "ninja.db", PROJECT_ROOT / "database.db"):
        if candidate.exists():
            db_path = candidate
            break
    else:
        db_path = PROJECT_ROOT / "database.db"
    return SQLTradeQueryService(db_path), log_reader


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a concise trading-day report."
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=_today_local(),
        help="Date to review (YYYY-MM-DD). Defaults to today in the market timezone.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to the SQLite database. Defaults to database.db in the repo root.",
    )
    args = parser.parse_args()

    trade_service, log_reader = _build_default_services()
    if args.db is not None:
        trade_service = SQLTradeQueryService(args.db)

    try:
        trades = trade_service.get_trades_for_date(args.date)
    except Exception as exc:
        print(
            f"ERROR: could not query trades: {exc}\n"
            "Hint: the live Postgres DATABASE_URL only resolves on the Windows "
            "host; run this script via Windows interop (see the "
            "check-tradingbot-logs skill), or pass --db for a local SQLite file.",
            file=sys.stderr,
        )
        return 1
    log_paths = log_reader.log_paths(args.date)
    log_summary = LogSummary(
        errors=log_reader.read_errors(args.date),
        warnings=log_reader.read_warnings(args.date),
        connection_events=log_reader.read_connection_events(args.date),
        trade_events=log_reader.read_trade_events(args.date),
    )

    report = ReportBuilder().build(args.date, trades, log_paths, log_summary)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
