"""Admin dashboard HTTP routes."""

from flask import Flask, abort, render_template, request

from src.controllers.admin_controller import AdminController
from src.utils.app_logger import ILogger


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
        return render_template('admin.html')

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
        return admin_controller.get_trade_history(pair, limit, offset)

    @app.route('/api/admin/trades/<string:trade_id>', methods=['GET'])
    def admin_trade_detail(trade_id):
        return admin_controller.get_trade_details(trade_id)

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
