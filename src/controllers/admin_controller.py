"""Admin controller for dashboard API endpoints."""

from flask import abort, jsonify

from src.domain.repositories import LineRepository
from src.infrastructure.repositories.decision_log_repository import (
    DecisionLogRepository,
)
from src.services.analytics_service import AnalyticsService
from src.utils.app_logger import ILogger


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
    ):
        self._analytics = analytics_service
        self._lines_repo = line_repository
        self.logger = logger
        self._decision_logs = decision_log_repository

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
            from src.dbexception import DBNotFoundException
            if isinstance(exc, DBNotFoundException):
                abort(404, str(exc))
            self.logger.error(f"Failed to delete trade {trade_id}: {exc}")
            abort(500, "Failed to delete trade")
        return jsonify({"deleted": True, "trade_id": trade_id}), 200

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
