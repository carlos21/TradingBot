"""Core HTTP routes - index page and basic API."""

from flask import Flask, jsonify, render_template
from src.data_sources.combined_datasource import CombinedDataSource


def register_core_routes(
    app: Flask,
    pair: str,
    data_source: CombinedDataSource,
):
    """Register core routes on the Flask app.
    
    Args:
        app: Flask application instance
        pair: Trading pair (e.g., "NQ")
        data_source: Data source for historical bars
    """
    
    @app.route('/')
    def index():
        return render_template('chart.html')

    @app.route('/api/pair')
    def get_pair():
        from src.data_sources.ninjatrader_datasource import NinjaTraderDataSource
        result = {'pair': pair}
        if isinstance(data_source, NinjaTraderDataSource) and data_source._cfg.account:
            result['account'] = data_source._cfg.account
        return jsonify(result)

    @app.route('/api/bars')
    def get_bars():
        from flask import request
        tf       = request.args.get('tf', '5m')
        start_ts = request.args.get('start_time', type=int)
        bars     = data_source.load_historical_bars(tf, start_ts)
        return jsonify(bars)
