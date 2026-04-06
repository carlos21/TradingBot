"""Trade management HTTP routes."""

from flask import Flask, jsonify, request, abort
from src.controllers.trades_controller import TradesController
from src.repositories.trades_repository import TradeRepository
from src.services.trade_logger import TradeLogger


def register_trades_routes(
    app: Flask,
    trades_controller: TradesController,
    trades_repo: TradeRepository,
    pair: str,
    trade_logger: TradeLogger,
):
    """Register trade management routes.
    
    Args:
        app: Flask application instance
        trades_controller: Controller for trade operations
        trades_repo: Repository for trade data access
        pair: Trading pair for filtering trades
        trade_logger: Logger for trade lifecycle events
    """
    
    @app.route('/api/trades', methods=['GET'])
    def list_trades():
        request_pair = request.args.get('pair')
        if not request_pair:
            abort(400, "Query param 'pair' is required")
        return trades_controller.list_trades(request_pair)

    @app.route('/api/trades', methods=['POST'])
    def open_trade():
        data = request.get_json() or {}
        req_pair       = data.get('pair')
        trade_type = data.get('type', '').lower()
        stop_loss  = data.get('stop_loss')
        if not isinstance(req_pair, str) or trade_type not in ('buy', 'sell'):
            abort(400, '"pair" must be a string and "type" must be "buy" or "sell"')
        try:
            stop_loss = float(stop_loss)
        except Exception:
            abort(400, '"stop_loss" must be a number')
        return trades_controller.open_trade(req_pair, stop_loss, trade_type)

    @app.route('/api/trades/<string:trade_id>/close', methods=['POST'])
    def close_trade(trade_id):
        return trades_controller.close_trade(trade_id)

    @app.route('/api/trades/<string:trade_id>/logs', methods=['GET'])
    def get_trade_logs(trade_id):
        fmt = request.args.get('format', 'json')
        if fmt == 'text':
            all_trades = trades_repo.list_trades(pair)
            trade = next((t for t in all_trades if t.trade_id == trade_id), None)
            if not trade:
                abort(404, 'Trade not found')
            return TradeLogger.format_logs(trade), 200, {'Content-Type': 'text/plain'}
        else:
            logs = trades_repo.get_trade_logs(trade_id)
            return jsonify(logs)
