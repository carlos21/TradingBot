"""Admin dashboard HTTP routes."""

from flask import Flask, abort, redirect, render_template, request

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
):
    """Register admin dashboard routes.

    Args:
        app: Flask application instance
        admin_controller: Controller for admin operations
    """

    @app.route('/admin')
    def admin_dashboard():
        return redirect('/admin/overview')

    @app.route('/admin/<string:page>')
    def admin_page(page: str):
        if page not in ADMIN_PAGES:
            abort(404)
        return render_template('admin.html', active_tab=page, active_page=page)

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
