# src/app_factory.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List

from flask import Flask, jsonify, request, abort, render_template
from flask_cors import CORS
from flask_socketio import SocketIO, emit

from src.bars_loader import BarsLoader
from src.controllers.lines_controller import LinesController
from src.controllers.trades_controller import TradesController
from src.data_sources.combined_datasource import CombinedDataSource
from src.services.trade_manager import TradeManager
from src.strategies.liquidity_strategy import StrategyOptions
from src.strategies.entry_context import (
    open_trades_limit_filter, max_bounce_filter
)
from src.repositories.lines_repository import LineRepository
from src.repositories.trades_repository import TradeRepository
from src.strategies.liquidity_strategy_v2 import LiquidityStrategyV2


@dataclass
class Repositories:
    lines: LineRepository
    trades: TradeRepository

@dataclass
class StrategyNumbers:
    min_stop_loss: float
    max_bounce: float
    extra_sl_space: float

@dataclass
class AppWiring:
    app: Flask
    socketio: SocketIO
    loader: BarsLoader
    strategy: LiquidityStrategyV2
    trade_manager: TradeManager
    lines_controller: LinesController
    trades_controller: TradesController
    data_source: CombinedDataSource
    pair: str


def create_app(
    *,
    pair: str,
    data_source: CombinedDataSource,
    repos: Repositories,
    numbers: StrategyNumbers,
    options: Optional[StrategyOptions] = None,
    timeframes: Optional[List[str]] = None,  # New argument for V2
    strategy_tf: Optional[str] = None,       # Kept for backward compatibility
    bootstrap_existing_lines: bool = True,
) -> AppWiring:
    """
    Build the whole application with injected dependencies.
    No env vars; no global singletons.
    """
    app = Flask(__name__)
    CORS(app)
    socketio = SocketIO(app, cors_allowed_origins="*")

    # Resolve timeframes: prefer explicit list, fall back to legacy string, default to ["5m"]
    if timeframes is None:
        if strategy_tf:
            effective_timeframes = [strategy_tf]
        else:
            effective_timeframes = ["5m"]
    else:
        effective_timeframes = timeframes

    # Strategy & managers
    effective_options = options or StrategyOptions(
        entry_filters=[
            open_trades_limit_filter(1),
            max_bounce_filter(numbers.max_bounce),
        ]
    )
    
    # Initialize V2 Strategy with the list of timeframes
    tstrategy = LiquidityStrategyV2(
        min_stop_loss   = numbers.min_stop_loss,
        max_bounce      = numbers.max_bounce,
        extra_sl_space  = numbers.extra_sl_space,
        socketio        = socketio,
        line_repository = repos.lines,
        trade_repository= repos.trades,
        options         = effective_options,
        timeframes      = effective_timeframes, 
    )
    
    trade_manager = TradeManager(trade_repository=repos.trades, socketio=socketio)

    # Combined callback
    def combined_bar_callback(bar):
        trade_manager.handle_new_1m_bar(bar)
        tstrategy.on_raw_bar(bar)

    loader = BarsLoader(
        data_source=data_source,
        socketio=socketio,
        bar_callback=combined_bar_callback
    )

    lines_controller  = LinesController(repos.lines, loader, tstrategy)
    trades_controller = TradesController(loader, trade_manager)

    # Optionally load any preexisting lines from repo into the in-memory strategy
    if bootstrap_existing_lines:
        for l in repos.lines.list_lines(pair):
            tstrategy.add_strategy_line(l.line_id, l.price)

    # ---------------- HTTP endpoints (capturing the injected deps) ----------------

    @app.route('/')
    def index():
        return render_template('tester.html')

    @app.route('/api/pair')
    def get_pair():
        return jsonify({'pair': pair})

    @app.route('/api/bars')
    def get_bars():
        tf       = request.args.get('tf', '5m')
        start_ts = request.args.get('start_time', type=int)
        bars     = data_source.load_historical_bars(tf, start_ts)
        return jsonify(bars)

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
        return lines_controller.add_line(data['pair'], price)

    @app.route('/api/lines/<string:line_id>', methods=['DELETE'])
    def delete_line(line_id):
        return lines_controller.delete_line(line_id)

    @app.route('/api/trades', methods=['POST'])
    def open_trade():
        data = request.get_json() or {}
        pair       = data.get('pair')
        trade_type = data.get('type', '').lower()
        stop_loss  = data.get('stop_loss')
        if not isinstance(pair, str) or trade_type not in ('buy', 'sell'):
            abort(400, '"pair" must be a string and "type" must be "buy" or "sell"')
        try:
            stop_loss = float(stop_loss)
        except Exception:
            abort(400, '"stop_loss" must be a number')
        return trades_controller.open_trade(pair, stop_loss, trade_type)

    @app.route('/api/trades/<string:trade_id>/close', methods=['POST'])
    def close_trade(trade_id):
        return trades_controller.close_trade(trade_id)

    # Socket.IO events
    @socketio.on('connect')
    def on_connect(auth):
        emit('stream_status', {'playing': loader.streaming})

    @socketio.on('start_stream')
    def on_start_stream(payload):
        tf = payload.get('timeframe', '1m')
        from_time = payload.get('fromTime', 0)
        stop_at   = payload.get('stopAt')

        # Set base time first so set_timeframe uses the right window
        loader.seek(from_time)
        loader.set_timeframe(tf)
        loader.start(from_time, stop_at)
        emit('stream_status', {'playing': True})

    @socketio.on('pause_stream')
    def on_pause_stream():
        loader.pause()
        emit('stream_status', {'playing': False})

    @socketio.on('seek')
    def on_seek(payload):
        loader.seek(payload.get('fromTime', 0))

    @socketio.on('jump_day')
    def on_jump_day(payload):
        # payload: {direction: 1|-1, fast: true|false}
        direction = int(payload.get('direction', 1))
        fast      = bool(payload.get('fast', True))
        ts = loader.jump_day(direction=direction, fast=fast)
        emit('jump_result', {'to': ts})

    return AppWiring(
        app=app,
        socketio=socketio,
        loader=loader,
        strategy=tstrategy,
        trade_manager=trade_manager,
        lines_controller=lines_controller,
        trades_controller=trades_controller,
        data_source=data_source,
        pair=pair,
    )