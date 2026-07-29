"""Debug and test helper HTTP routes."""

from flask import Flask, jsonify, request

from src.analytics import AnalyticsReporter
from src.bars_loader import BarsLoader
from src.domain.repositories import LineRepository, TradeRepository
from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.infrastructure.data_sources.csv_datasource import CSVDataSource
from src.notifier import Notifier
from src.services.trade_manager import TradeManager
from src.strategies.liquidity_v2.strategy import LiquidityStrategyV2
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
    coordinator=None,
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
        coordinator: Optional StreamCoordinator for per-instrument components
    """

    def _components():
        """Resolve (strategy, loader, trade_manager) for the configured pair."""
        if coordinator is not None:
            session = coordinator.require_session(pair)
            return session.strategy, session.bars_loader, session.trade_manager
        return strategy, loader, trade_manager

    @app.route('/api/debug/logs', methods=['GET'])
    def get_debug_logs():
        """Return the decision logs from the strategy."""
        strat, _, _ = _components()
        return jsonify(strat.decision_logs)

    @app.route('/__reset_all', methods=['POST'])
    def reset_all():
        """Clears all state, resets DataSource range, AND warms up strategy."""
        strat, loader, trade_mgr = _components()
        try:
            # 1. Clear in-memory strategy state (Deep Reset)
            strat.reset()

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
            trade_mgr.open_trades.clear()
            trade_mgr._monitored_trades.clear()

            # 6. Reset DataSource history
            data = request.get_json() or {}
            start_ts = data.get('start_time')
            end_ts   = data.get('end_time')

            if isinstance(data_source, CSVDataSource):
                try:
                    data_source.reset(start_time=start_ts, end_time=end_ts)
                except TypeError:
                    data_source.reset()

            # 7. SEED LINES (if provided) so they are present during warmup
            seed_lines = data.get('seed_lines', [])
            for sl in seed_lines:
                strat.add_strategy_line(
                    sl['id'],
                    float(sl['price']),
                    creation_timestamp=float(sl.get('creation_ts', 0.0))
                )
                lines_repo.insert_line(pair, float(sl['price']))

            # 8. WARM UP STRATEGY (with lines present)
            played = data_source.load_historical_bars('1m', pair=pair)
            if played:
                logger.info(f"[Reset] Warming up strategy with {len(played)} bars ({len(seed_lines)} lines)...")
                for bar in played:
                    strat.on_raw_bar(bar)
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
