"""Core HTTP routes - index page and basic API."""

from flask import Flask, jsonify, render_template
from src.data_sources.combined_datasource import CombinedDataSource
from src.utils.app_logger import ILogger


def register_core_routes(
    app: Flask,
    pair: str,
    data_source: CombinedDataSource,
    logger: ILogger,
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
        return jsonify({'pair': pair})

    @app.route('/api/bars')
    def get_bars():
        from flask import request
        tf       = request.args.get('tf', '5m')
        start_ts = request.args.get('start_time', type=int)
        bars     = data_source.load_historical_bars(tf, start_ts)
        
        # Debug: Check for problematic bars that could cause "Value is null" in charts
        if bars and len(bars) > 0:
            sample = bars[0]
            logger.info(f"[API /bars] tf={tf} count={len(bars)} first_bar={sample}")
            # Check for None/NaN values
            bad_bars = [b for b in bars if b.get('open') is None or b.get('high') is None 
                       or b.get('low') is None or b.get('close') is None or b.get('time') is None]
            if bad_bars:
                logger.error(f"[API /bars] FOUND {len(bad_bars)} bars with null values! First: {bad_bars[0]}")
        
        return jsonify(bars)
