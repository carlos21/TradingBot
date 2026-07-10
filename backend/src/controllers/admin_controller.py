"""Admin controller for dashboard API endpoints."""

import re
from datetime import datetime
from pathlib import Path

from flask import abort, jsonify

from src.dbexception import DBNotFoundException
from src.domain.repositories import LineRepository
from src.infrastructure.repositories.decision_log_repository import (
    DecisionLogRepository,
)
from src.services.analytics_service import AnalyticsService
from src.utils.app_logger import ILogger

# Regex for the timestamp prefix of a log line.
_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) ")

# Regex to locate the log-level bracket in the remainder of the line.
_LOG_LEVEL_RE = re.compile(r"\[(DEBUG|INFO|WARN|ERROR)\] ")

# Heuristic source tag at the start of a message, e.g. "[LiveMode] ...".
_LOG_SOURCE_RE = re.compile(r"^\[([^\]]+)\]\s*")


class AdminController:
    """Controller for admin dashboard operations."""

    # Known decision event types for the filter dropdown
    DECISION_EVENTS = [
        "LATCH", "LATCH_PENDING", "REMOVE",
        "TRIGGER_SKIP", "FILTER_BLOCK", "ENTRY",
        "TSI_CROSS", "TSI_FAST", "TSI_SWEEP",
        "VAT_REGIME", "VAT_CROSS_1", "VAT_CROSS_2",
        "TSI_RESET", "REENTRY_WATCH", "REENTRY_CANCEL",
    ]

    def __init__(
        self,
        analytics_service: AnalyticsService,
        line_repository: LineRepository,
        logger: ILogger,
        decision_log_repository: DecisionLogRepository | None = None,
        log_dir: str = "logs",
    ):
        self._analytics = analytics_service
        self._lines_repo = line_repository
        self.logger = logger
        self._decision_logs = decision_log_repository
        self._log_dir = log_dir

    def get_dashboard_stats(self, pair: str):
        """Get overall dashboard statistics."""
        stats = self._analytics.calculate_statistics(pair)
        return jsonify(stats.to_dict()), 200

    def get_trade_history(
        self,
        pair: str,
        limit: int = 50,
        offset: int = 0,
        account: str | None = None,
    ):
        """Get paginated trade history with optional account filter."""
        result = self._analytics.get_paginated_trades(pair, limit, offset, account)
        return jsonify(result), 200

    def get_trade_accounts(self, pair: str):
        """Get distinct account names present in trade history."""
        accounts = self._analytics.get_trade_accounts(pair)
        return jsonify({"accounts": accounts}), 200

    def get_trade_details(self, trade_id: str):
        """Get detailed trade information with logs."""
        trade = self._analytics.get_trade_detail(trade_id)
        if not trade:
            abort(404, f"Trade {trade_id} not found")
        return jsonify(trade.to_dict()), 200

    def delete_trade(self, trade_id: str):
        """Delete a trade and any related child trades."""
        try:
            self._analytics.delete_trade(trade_id)
        except Exception as exc:  # noqa: BLE001
            # Repository already raises domain DB exceptions; surface as 404/500
            if isinstance(exc, DBNotFoundException):
                abort(404, str(exc))
            self.logger.error(f"Failed to delete trade {trade_id}: {exc}")
            abort(500, "Failed to delete trade")
        return jsonify({"deleted": True, "trade_id": trade_id}), 200

    def delete_trades(self, trade_ids: list[str]):
        """Delete multiple trades and any related child trades."""
        try:
            self._analytics.delete_trades(trade_ids)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DBNotFoundException):
                abort(404, str(exc))
            self.logger.error(f"Failed to delete trades {trade_ids}: {exc}")
            abort(500, "Failed to delete trades")
        return jsonify({"deleted": True, "trade_ids": trade_ids}), 200

    def get_analytics(self, pair: str):
        """Get all analytics data for charts."""
        equity_curve = self._analytics.get_equity_curve(pair)
        trades_by_hour = self._analytics.get_trades_by_hour(pair)
        trades_by_day = self._analytics.get_trades_by_day(pair)
        result_dist = self._analytics.get_result_distribution(pair)
        monthly_pnl = self._analytics.get_monthly_pnl(pair)
        pnl_dist = self._analytics.get_pnl_distribution(pair)
        account_stats = self._analytics.get_account_analytics(pair)

        return jsonify({
            "equity_curve": equity_curve.to_dict(),
            "trades_by_hour": trades_by_hour.to_dict(),
            "trades_by_day": trades_by_day.to_dict(),
            "result_distribution": result_dist.to_dict(),
            "monthly_pnl": monthly_pnl.to_dict(),
            "pnl_distribution": pnl_dist.to_dict(),
            "account_stats": account_stats,
        }), 200

    def get_lines(self, pair: str):
        """Get all lines for a pair."""
        lines = self._lines_repo.list_lines(pair)
        return jsonify([
            {
                "id": line.line_id,
                "pair": line.pair,
                "price": line.price,
                "creation_date": line.creation_date.isoformat(),
            }
            for line in lines
        ]), 200

    def get_decision_logs(
        self,
        pair: str,
        event: str | None = None,
        line_id: str | None = None,
        limit: int = 500,
    ):
        """Get recent decision logs with optional filters."""
        if self._decision_logs is None:
            return jsonify({"logs": [], "events": []}), 200
        logs = self._decision_logs.get_recent(
            pair=pair, event=event, line_id=line_id, limit=limit
        )
        return jsonify({"logs": logs, "events": self.DECISION_EVENTS}), 200

    def get_decision_events(self) -> list[str]:
        """Get list of known decision event types."""
        return jsonify({"events": self.DECISION_EVENTS}), 200

    def get_recent_logs(
        self,
        pair: str,  # noqa: ARG002
        limit: int = 200,
        offset: int = 0,
    ):
        """Get recent application log entries from today's log file.

        Args:
            pair: Trading pair (kept for API consistency; logs are global).
            limit: Maximum number of log entries to return.
            offset: Number of most-recent entries to skip (pagination).

        Returns:
            JSON with ``logs`` (oldest-first within the page), ``sources``
            (distinct source values discovered) and ``has_more``.
        """
        log_file = Path(self._log_dir) / f"app_{datetime.now().strftime('%Y-%m-%d')}.log"
        if not log_file.exists():
            return jsonify({"logs": [], "sources": [], "has_more": False}), 200

        try:
            with open(log_file, encoding="utf-8") as f:
                lines = [line.rstrip("\n") for line in f if line.strip()]
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"[AdminController] failed to read log file: {exc}")
            return jsonify({"logs": [], "sources": [], "has_more": False}), 200

        # Newest entries are at the end of the file; reverse for pagination.
        lines.reverse()
        total = len(lines)
        end = offset + limit
        page_lines = lines[offset:end]

        logs = []
        sources = set()
        for line in page_lines:
            entry = self._parse_log_line(line)
            if entry is None:
                continue
            sources.add(entry["source"])
            logs.append(entry)

        # Return each page newest-first; older pages are appended below.
        return jsonify({
            "logs": logs,
            "sources": sorted(sources),
            "has_more": total > end,
        }), 200

    @staticmethod
    def _parse_log_line(line: str) -> dict | None:
        """Parse a single log file line into a log entry dict."""
        ts_match = _LOG_TS_RE.match(line)
        if not ts_match:
            return None

        ts_str = ts_match.group(1)
        rest = line[ts_match.end():]

        level_match = _LOG_LEVEL_RE.search(rest)
        if not level_match:
            return None

        level = level_match.group(1)
        prefix_part = rest[:level_match.start()].strip()
        message = rest[level_match.end():]

        instance_prefix = None
        if prefix_part.startswith("[") and prefix_part.endswith("]"):
            instance_prefix = prefix_part[1:-1]

        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f").timestamp()
        except ValueError:
            return None

        source = AdminController._extract_source(instance_prefix, message)
        return {
            "time": ts,
            "level": level,
            "source": source,
            "message": message,
        }

    @staticmethod
    def _extract_source(instance_prefix: str | None, message: str) -> str:
        """Infer a source tag from the message or instance prefix."""
        msg_match = _LOG_SOURCE_RE.match(message)
        if msg_match:
            return msg_match.group(1)
        if instance_prefix:
            return instance_prefix
        return "server"
