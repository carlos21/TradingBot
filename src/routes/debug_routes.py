"""Debug and test helper HTTP routes."""

from flask import Flask, jsonify, request

from src.analytics import AnalyticsReporter
from src.bars_loader import BarsLoader
from src.data_sources.combined_datasource import CombinedDataSource
from src.data_sources.csv_datasource import CSVDataSource
from src.notifier import Notifier
from src.repositories.lines_repository import LineRepository
from src.repositories.trades_repository import TradeRepository
from src.services.trade_manager import TradeManager
from src.strategies.liquidity_strategy_v2 import LiquidityStrategyV2
from src.utils.app_logger import ILogger


def register_debug_routes(
    app: Flask,
    strategy: LiquidityStrategyV2,
    loader: BarsLoader,
    trade_manager: TradeManager,
    lines_repo: LineRepository,
    trades_repo: TradeRepository,
    data_source: CombinedDataSource,
    pair: str,
    notifier: Notifier,
    analytics: AnalyticsReporter,
    logger: ILogger,
):
    """Register debug and test helper routes.

    Args:
        app: Flask application instance
        strategy: Trading strategy instance
        loader: Bars loader for stream control
        trade_manager: Manager for trade lifecycle
        lines_repo: Repository for lines
        trades_repo: Repository for trades
        data_source: Data source for bars
        pair: Trading pair
        notifier: Notification service
        analytics: Analytics service
    """

    @app.route('/api/debug/logs', methods=['GET'])
    def get_debug_logs():
        """Return the decision logs from the strategy."""
        return jsonify(strategy.decision_logs)

    @app.route('/__reset_all', methods=['POST'])
    def reset_all():
        """Clears all state, resets DataSource range, AND warms up strategy."""
        try:
            # 1. Clear in-memory strategy state (Deep Reset)
            strategy.reset()

            # 2. Reset Loader State (so _last_played_ts goes back to 0)
            loader.reset()

            # 3. Clear DB lines
            try:
                all_lines = lines_repo.list_lines(pair)
                for line in all_lines:
                    lines_repo.delete_line(line.line_id)
            except Exception as e:
                return jsonify({"error": str(e)}), 500

            # 4. Clear Trades (Fix for leaking trades between scenarios)
            # Only clear in-memory fakes; SQL trades persist across scenarios
            trades_repo.clear_in_memory()

            # 5. Clear trade_manager open trades to prevent leaks between scenarios
            trade_manager.open_trades.clear()
            trade_manager._monitored_trades.clear()

            # 6. Reset DataSource history
            data = request.get_json() or {}
            start_ts = data.get('start_time')
            end_ts   = data.get('end_time')

            if isinstance(data_source, CSVDataSource):
                try:
                    data_source.reset(start_time=start_ts, end_time=end_ts)
                except TypeError:
                    data_source.reset()

            # 7. WARM UP STRATEGY (Without lines)
            played = data_source.load_historical_bars('1m')
            if played:
                logger.info(f"[Reset] Warming up strategy with {len(played)} bars (No lines)...")
                for bar in played:
                    strategy.on_raw_bar(bar)
                logger.info("[Reset] Warmup complete.")

            return jsonify({"status": "OK"})
        except Exception as e:
            logger.error(f"[Reset] Critical error: {e}")
            analytics.capture_exception(e, {"op": "reset_all"})
            notifier.send(f"[Reset] Critical error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/__shutdown', methods=['POST'])
    def shutdown():
        func = request.environ.get('werkzeug.server.shutdown')
        if func:
            func()
        return "OK"
