"""Admin dashboard HTTP routes."""

from flask import Flask, abort, redirect, request, send_from_directory

from src.controllers.admin_controller import AdminController
from src.utils.app_logger import ILogger

ADMIN_PAGES = {
    'overview', 'trades', 'lines', 'analytics',
    'decisions', 'logs', 'settings', 'ninjatrader', 'metatrader',
}


def register_admin_routes(
    app: Flask,
    admin_controller: AdminController,
    _logger: ILogger,
    frontend_dir: str,
):
    """Register admin dashboard routes.

    Args:
        app: Flask application instance
        admin_controller: Controller for admin operations
        frontend_dir: Absolute path to the frontend HTML files
    """

    @app.route('/admin')
    def admin_dashboard():
        return redirect('/admin/overview')

    @app.route('/admin/<string:page>')
    def admin_page(page: str):
        if page not in ADMIN_PAGES:
            abort(404)
        return send_from_directory(frontend_dir, 'admin.html')

    @app.route('/api/admin/stats', methods=['GET'])
    def admin_stats():
        pair = request.args.get('pair')
        if not pair:
            abort(400, "Query param 'pair' is required")
        return admin_controller.get_dashboard_stats(pair)

    @app.route('/api/admin/trades', methods=['GET'])
    def admin_trades():
        pair = request.args.get('pair')
        if not pair:
            abort(400, "Query param 'pair' is required")
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        if limit is None or limit <= 0:
            abort(400, "Query param 'limit' must be a positive integer")
        if offset is None or offset < 0:
            abort(400, "Query param 'offset' must be a non-negative integer")
        account = request.args.get('account') or None
        return admin_controller.get_trade_history(pair, limit, offset, account)

    @app.route('/api/admin/trade-accounts', methods=['GET'])
    def admin_trade_accounts():
        pair = request.args.get('pair')
        if not pair:
            abort(400, "Query param 'pair' is required")
        return admin_controller.get_trade_accounts(pair)

    @app.route('/api/admin/trades/<string:trade_id>', methods=['GET'])
    def admin_trade_detail(trade_id):
        return admin_controller.get_trade_details(trade_id)

    @app.route('/api/admin/trades/<string:trade_id>', methods=['DELETE'])
    def admin_delete_trade(trade_id):
        try:
            return admin_controller.delete_trade(trade_id)
        except Exception as exc:
            _logger.error(f"[AdminRoutes] delete_trade failed: {exc}")
            abort(500, "Failed to delete trade")

    @app.route('/api/admin/trades/bulk-delete', methods=['POST'])
    def admin_bulk_delete_trades():
        body = request.get_json(silent=True) or {}
        trade_ids = body.get('trade_ids')
        if not isinstance(trade_ids, list) or not trade_ids:
            abort(400, "Request body must contain a non-empty 'trade_ids' list")
        if not all(isinstance(tid, str) and tid for tid in trade_ids):
            abort(400, "All trade_ids must be non-empty strings")
        try:
            return admin_controller.delete_trades(trade_ids)
        except Exception as exc:
            _logger.error(f"[AdminRoutes] bulk_delete_trades failed: {exc}")
            abort(500, "Failed to delete trades")

    @app.route('/api/admin/analytics', methods=['GET'])
    def admin_analytics():
        pair = request.args.get('pair')
        if not pair:
            abort(400, "Query param 'pair' is required")
        return admin_controller.get_analytics(pair)

    @app.route('/api/admin/decisions', methods=['GET'])
    def admin_decisions():
        pair = request.args.get('pair')
        if not pair:
            abort(400, "Query param 'pair' is required")
        event = request.args.get('event') or None
        line_id = request.args.get('line_id') or None
        limit = request.args.get('limit', 500, type=int)
        return admin_controller.get_decision_logs(pair, event, line_id, limit)

    @app.route('/api/admin/decisions/events', methods=['GET'])
    def admin_decision_events():
        return admin_controller.get_decision_events()

    @app.route('/api/admin/logs/recent', methods=['GET'])
    def admin_recent_logs():
        pair = request.args.get('pair')
        if not pair:
            abort(400, "Query param 'pair' is required")
        limit = request.args.get('limit', 200, type=int)
        offset = request.args.get('offset', 0, type=int)
        if limit is None or limit <= 0:
            abort(400, "Query param 'limit' must be a positive integer")
        if offset is None or offset < 0:
            abort(400, "Query param 'offset' must be a non-negative integer")
        return admin_controller.get_recent_logs(pair, limit, offset)
