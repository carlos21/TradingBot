"""Trade management HTTP routes."""

from flask import Flask, abort, jsonify, request

from src.controllers.trades_controller import TradesController
from src.domain.repositories import TradeRepository
from src.services.trade_logger import TradeLogger
from src.utils.app_logger import ILogger


def register_trades_routes(
    app: Flask,
    trades_controller: TradesController,
    trades_repo: TradeRepository,
    pair: str,
    _trade_logger: TradeLogger,
    _logger: ILogger,
    coordinator=None,
):
    """Register trade management routes.

    Args:
        app: Flask application instance
        trades_controller: Default controller for trade operations
        trades_repo: Repository for trade data access
        pair: Trading pair for filtering trades
        trade_logger: Logger for trade lifecycle events
        coordinator: Optional StreamCoordinator for per-instrument controllers
    """

    def _trades_controller(req_pair: str | None) -> TradesController:
        if coordinator is not None and req_pair:
            return coordinator.require_session(req_pair).trades_controller
        return trades_controller

    @app.route('/api/trades', methods=['GET'])
    def list_trades():
        request_pair = request.args.get('pair')
        if not request_pair:
            abort(400, "Query param 'pair' is required")
        return _trades_controller(request_pair).list_trades(request_pair)

    @app.route('/api/trades', methods=['POST'])
    def open_trade():
        data = request.get_json() or {}
        req_pair       = data.get('pair')
        trade_type = data.get('type', '').lower()
        stop_loss  = data.get('stop_loss')
        if not isinstance(req_pair, str) or trade_type not in ('long', 'short'):
            abort(400, '"pair" must be a string and "type" must be "long" or "short"')
        try:
            stop_loss = float(stop_loss)
        except (TypeError, ValueError):
            abort(400, '"stop_loss" must be a number')
        return _trades_controller(req_pair).open_trade(req_pair, stop_loss, trade_type)

    @app.route('/api/trades/test', methods=['POST'])
    def open_test_trade():
        data = request.get_json() or {}
        req_pair = data.get('pair')
        direction = data.get('direction', '').lower()
        if not isinstance(req_pair, str) or direction not in ('long', 'short'):
            abort(400, '"pair" must be a string and "direction" must be "long" or "short"')
        return _trades_controller(req_pair).open_test_trade(req_pair, direction)

    @app.route('/api/trades/<string:trade_id>/close', methods=['POST'])
    def close_trade(trade_id):
        return _trades_controller(None).close_trade(trade_id)

    @app.route('/api/trades/<string:trade_id>/stop-loss', methods=['POST'])
    def modify_stop_loss(trade_id):
        data = request.get_json() or {}
        stop_loss = data.get('stop_loss')
        try:
            stop_loss = float(stop_loss)
        except (TypeError, ValueError):
            abort(400, '"stop_loss" must be a number')
        return _trades_controller(None).modify_stop_loss(trade_id, stop_loss)

    @app.route('/api/trades/close-all', methods=['POST'])
    def close_all_trades():
        data = request.get_json() or {}
        req_pair = data.get('pair')
        if not isinstance(req_pair, str):
            abort(400, '"pair" must be a string')
        return _trades_controller(req_pair).close_all_trades(req_pair)

    @app.route('/api/trades/<string:trade_id>/logs', methods=['GET'])
    def get_trade_logs(trade_id):
        fmt = request.args.get('format', 'json')
        if fmt == 'text':
            trade = trades_repo.get_trade(trade_id)
            if trade is None:
                abort(404, 'Trade not found')
            try:
                return TradeLogger.format_logs(trade), 200, {'Content-Type': 'text/plain'}
            except Exception as exc:
                _logger.error(f"[TradesRoutes] format_logs failed: {exc}")
                abort(500, "Failed to format trade logs")
        else:
            logs = trades_repo.get_trade_logs(trade_id)
            return jsonify(logs)
