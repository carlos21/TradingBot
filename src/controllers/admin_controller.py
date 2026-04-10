"""Admin controller for dashboard API endpoints."""
from flask import jsonify, abort
from typing import Optional

from src.services.analytics_service import AnalyticsService
from src.repositories.lines_repository import LineRepository
from src.utils.app_logger import ILogger


class AdminController:
    """Controller for admin dashboard operations."""

    def __init__(
        self,
        analytics_service: AnalyticsService,
        line_repository: LineRepository,
        logger: ILogger,
    ):
        self._analytics = analytics_service
        self._lines_repo = line_repository
        self.logger = logger

    def get_dashboard_stats(self, pair: str):
        """Get overall dashboard statistics."""
        stats = self._analytics.calculate_statistics(pair)
        return jsonify(stats.to_dict()), 200

    def get_trade_history(self, pair: str, limit: int = 50, offset: int = 0):
        """Get paginated trade history."""
        result = self._analytics.get_paginated_trades(pair, limit, offset)
        return jsonify(result), 200

    def get_trade_details(self, trade_id: str):
        """Get detailed trade information with logs."""
        trade = self._analytics.get_trade_detail(trade_id)
        if not trade:
            abort(404, f"Trade {trade_id} not found")
        return jsonify(trade.to_dict()), 200

    def get_analytics(self, pair: str):
        """Get all analytics data for charts."""
        equity_curve = self._analytics.get_equity_curve(pair)
        trades_by_hour = self._analytics.get_trades_by_hour(pair)
        trades_by_day = self._analytics.get_trades_by_day(pair)
        result_dist = self._analytics.get_result_distribution(pair)
        monthly_pnl = self._analytics.get_monthly_pnl(pair)
        pnl_dist = self._analytics.get_pnl_distribution(pair)

        return jsonify({
            "equity_curve": equity_curve.to_dict(),
            "trades_by_hour": trades_by_hour.to_dict(),
            "trades_by_day": trades_by_day.to_dict(),
            "result_distribution": result_dist.to_dict(),
            "monthly_pnl": monthly_pnl.to_dict(),
            "pnl_distribution": pnl_dist.to_dict(),
        }), 200

    def get_lines(self, pair: str):
        """Get all lines for a pair."""
        lines = self._lines_repo.list_lines(pair)
        return jsonify([
            {
                "id": l.line_id,
                "pair": l.pair,
                "price": l.price,
                "creation_date": l.creation_date.isoformat(),
            }
            for l in lines
        ]), 200
