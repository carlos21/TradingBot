"""Core HTTP routes - index page and basic API."""

import time

from flask import Flask, jsonify, render_template

from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
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
        pair: Trading pair (e.g., "MNQ")
        data_source: Data source for historical bars
    """

    @app.route('/')
    def index():
        return render_template('chart.html', active_page='chart')

    @app.route('/api/pair')
    def get_pair():
        return jsonify({'pair': pair})

    @app.route('/api/bars')
    def get_bars():
        from flask import request
        tf       = request.args.get('tf', '5m')
        start_ts = request.args.get('start_time', type=int)

        try:
            bars = data_source.load_historical_bars(tf, start_ts)
        except Exception as exc:
            logger.error(f"[API /bars] failed to load bars: {exc}")
            return jsonify({"error": f"Failed to load bars: {exc}"}), 500

        # Trim bars whose open time is still in the future relative to this host.
        # NinjaTrader may include the currently forming bar with a timestamp
        # slightly ahead of the local clock; displaying it would cause live bars
        # to be dropped by the frontend until real time catches up.
        now = time.time()
        bars = [b for b in bars if b.get('time') and b['time'] <= now]

        # Debug: Check for problematic bars that could cause "Value is null" in charts
        if bars:
            sample = bars[0]
            logger.debug(f"[API /bars] tf={tf} count={len(bars)} first_bar={sample}")
            # Check for None/NaN values
            bad_bars = [b for b in bars if b.get('open') is None or b.get('high') is None
                       or b.get('low') is None or b.get('close') is None or b.get('time') is None]
            if bad_bars:
                logger.error(f"[API /bars] FOUND {len(bad_bars)} bars with null values! First: {bad_bars[0]}")

        return jsonify(bars)
