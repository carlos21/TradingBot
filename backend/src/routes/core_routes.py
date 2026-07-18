"""Core HTTP routes - index page and basic API."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from flask import Flask, jsonify, send_from_directory

from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.utils.app_logger import ILogger

if TYPE_CHECKING:
    from src.services.settings_service import SettingsService


def register_core_routes(
    app: Flask,
    pair: str,
    data_source: CombinedDataSource,
    logger: ILogger,
    frontend_dir: str,
    platform_type: str,
    platform_label: str,
    settings_service: "SettingsService" | None = None,
):
    """Register core routes on the Flask app.

    Args:
        app: Flask application instance
        pair: Trading pair (e.g., "MNQ")
        data_source: Data source for historical bars
        logger: Logger instance
        frontend_dir: Absolute path to the frontend HTML files
        platform_type: Platform identifier (e.g., "ninjatrader")
        platform_label: Human-readable platform label
        settings_service: Optional settings service for instrument registry data
    """

    @app.route('/')
    def index():
        return send_from_directory(frontend_dir, 'index.html')

    @app.route('/api/config')
    def get_config():
        instruments = []
        if settings_service is not None:
            try:
                instruments = settings_service.get_instruments()
            except Exception as exc:
                # No-DB setups (e.g. the in-memory scenario server) have no
                # settings store; fall back to the configured pair.
                logger.error(f"[API /config] failed to load instruments: {exc}")
        return jsonify({
            'instruments': instruments,
            'pair': instruments[0]["symbol"] if instruments else pair,
            'platform_type': platform_type,
            'platform_label': platform_label,
            'is_ninjatrader': platform_type == 'ninjatrader',
            'is_metatrader': platform_type == 'metatrader',
        })

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
