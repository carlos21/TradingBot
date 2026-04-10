"""Line management HTTP routes."""

from flask import Flask, jsonify, request, abort
from src.controllers.lines_controller import LinesController
from src.utils.app_logger import ILogger


def register_lines_routes(
    app: Flask,
    lines_controller: LinesController,
    logger: ILogger,
):
    """Register line management routes.
    
    Args:
        app: Flask application instance
        lines_controller: Controller for line operations
    """
    
    @app.route('/api/lines', methods=['GET'])
    def list_lines():
        pair = request.args.get('pair')
        if not pair:
            abort(400, "Query param 'pair' is required, e.g. /api/lines?pair=NQ")
        return lines_controller.list_lines(pair)

    @app.route('/api/lines', methods=['POST'])
    def add_line():
        data = request.get_json() or {}
        
        if 'pair' not in data or 'price' not in data:
            abort(400, 'Must provide {"pair":..., "price":...}')
        
        try:
            price = float(data['price'])
        except ValueError:
            abort(400, "Field 'price' must be a number")
            
        # Extract optional creation_time from request
        creation_time = data.get('creation_time')
        if creation_time is not None:
            try:
                creation_time = float(creation_time)
            except ValueError:
                abort(400, "Field 'creation_time' must be a timestamp number")

        # INJECT dependencies into the controller method
        return lines_controller.add_line(
            pair=data['pair'], 
            price=price, 
            creation_timestamp=creation_time
        )

    @app.route('/api/lines/<string:line_id>', methods=['GET'])
    def get_line(line_id):
        """Get a single line by ID."""
        return lines_controller.get_line(line_id)

    @app.route('/api/lines/<string:line_id>', methods=['PUT'])
    def update_line(line_id):
        """Update a line's price."""
        data = request.get_json() or {}
        if 'price' not in data:
            abort(400, 'Must provide {"price":...}')
        try:
            price = float(data['price'])
        except ValueError:
            abort(400, "Field 'price' must be a number")
        return lines_controller.update_line(line_id, price)

    @app.route('/api/lines/<string:line_id>', methods=['DELETE'])
    def delete_line(line_id):
        return lines_controller.delete_line(line_id)
