from flask import Flask, jsonify, request, abort, render_template
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from datetime import datetime, timezone

from src.bars_loader import BarsConfig, BarsLoader
from src.controllers.lines_controller import LinesController
from src.controllers.trades_controller import TradesController
from src.data_sources.csv_datasource import CSVDataSource
from src.data_sources.metatrader_datasource import MetaTraderDataSource
from src.repositories.lines_repository import SQLLineRepository
from src.strategies.liquidity_strategy import LiquidityStrategy
from src.database import database
from src.services.trade_manager import TradeManager
from src.repositories.trades_repository import SQLTradeRepository

# ───────── Setup ─────────
database.setup_database()
line_repository = SQLLineRepository()
trade_repository = SQLTradeRepository()

app      = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# ───────── Strategy + Loader ─────────
PAIR     = 'NQ'
extra_space = {
    'EURUSD': 0.0002,   # 2 pips
    'NQ':      2.0      # 2 points
}
tstrategy = LiquidityStrategy(
    min_stop_loss=BarsConfig.STOP_LOSS_CONFIG[PAIR],
    max_bounce=   BarsConfig.MAX_BOUNCE_CONFIG[PAIR],
    socketio=     socketio,
    line_repository = line_repository,
    trade_repository = trade_repository,
    extra_sl_space=extra_space
)
trade_manager = TradeManager(
    trade_repository = trade_repository,
    socketio = socketio
)

if app.config.get('USE_MT5', False):
    ds = MetaTraderDataSource(
        pair   = PAIR,
        creds  = {'login':123, 'password':'…'},
        ws_url = "wss://mt5.ticks"
    )
else:
    ds = CSVDataSource(
        pair      = PAIR,
        filename  = BarsConfig.CSV_FILES[PAIR],
        time_fmt  = BarsConfig.TIME_FORMATS[PAIR],
        tz        = BarsConfig.PAIR_TIMEZONES[PAIR],
        speed     = app.config.get('REPLAY_SPEED', 1.0)
    )

tloader = BarsLoader(
    config=BarsConfig,
    data_source=ds,
    socketio=socketio,
    strategy=tstrategy,
    bar_callback=trade_manager.handle_new_1m_bar
)
lines_controller = LinesController(line_repository, tloader, tstrategy)
trades_controller = TradesController(tloader, trade_manager)

# ───────── Bootstrapping existing lines ─────────
for l in line_repository.list_lines():
    if l.pair != PAIR:
        continue
    tstrategy.add_strategy_line(l.line_id, l.price, l.direction)


# ───────── HTTP Endpoints ─────────
@app.route('/')
def index():
    return render_template('tester.html')


@app.route('/api/bars')
def get_bars():
    tf   = request.args.get('tf', '5m')
    st   = request.args.get('start_time', type=int)
    data = tloader.prepare_agg_bars(tf, start_time=st)
    return jsonify(data)


@app.route('/api/lines', methods=['GET'])
def list_lines():
    return lines_controller.list_lines()


@app.route('/api/lines', methods=['POST'])
def add_line():
    data = request.get_json() or {}
    if 'pair' not in data or 'price' not in data:
        abort(400, 'Must provide {"pair":..., "price":...}')
    try:
        price = float(data['price'])
    except ValueError:
        abort(400, "Field 'price' must be a number")

    # compute direction based on most recent 5m close
    pair = data['pair']

    return lines_controller.add_line(pair, price)


@app.route('/api/lines/<string:line_id>', methods=['DELETE'])
def delete_line(line_id):
    return lines_controller.delete_line(line_id)


@app.route('/api/trades', methods=['POST'])
def open_trade():
    data = request.get_json() or {}
    # validate inputs
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



# ───────── Socket.IO Events ─────────
@socketio.on('connect')
def on_connect(auth):
    # single boolean instead of dict
    emit('stream_status', {'playing': tloader.streaming_1m})

@socketio.on('start_stream')
def on_start_stream(payload):
    tf = payload.get('timeframe', '1m')
    tloader.set_timeframe(tf)

    # align raw-1m pointer
    from_time = payload.get('fromTime', int(BarsConfig.INITIAL_END.timestamp()))
    idx = next(
        (i for i, b in enumerate(tloader.raw_1m) if b['time'] > from_time),
        len(tloader.raw_1m)
    )
    tloader.current_1m_index = idx

    # clear any partial aggregates
    tloader._5m_buffer.clear()
    tloader.tf_buffer.clear()

    # kick off the loop
    tloader.streaming_1m = True
    socketio.start_background_task(tloader.stream_1m_bars)
    emit('stream_status', {'playing': True})

@socketio.on('pause_stream')
def on_pause_stream():
    tloader.streaming_1m = False
    emit('stream_status', {'playing': False})

if __name__ == '__main__':
    socketio.run(app, debug=True)