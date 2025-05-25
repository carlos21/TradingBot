from flask import Flask, jsonify, request, abort, render_template
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from datetime import datetime, timezone

from src.bars_loader import BarsConfig, BarsLoader
from src.controllers.lines_controller import LinesController
from src.controllers.trades_controller import TradesController
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
    socketio         = socketio
)
tloader = BarsLoader(
    config=     BarsConfig,
    socketio=   socketio,
    strategy=   tstrategy,
    bar_callback=trade_manager.handle_new_1m_bar
)
lines_controller = LinesController(line_repository, tloader, tstrategy)
trades_controller = TradesController(tloader, trade_manager)

# ───────── Bootstrapping existing lines ─────────
for l in line_repository.list_lines():
    if l.pair != PAIR:
        continue

    # use the stored direction
    direction = l.direction
    # print(f"[Boot] restoring line {l.line_id} @ {l.price} as {direction}")
    tstrategy.add_strategy_line(l.line_id, l.price, direction)


# ───────── HTTP Endpoints ─────────
@app.route('/')
def index():
    return render_template('tester.html')


@app.route('/api/bars')
def get_bars():
    pair = request.args.get('pair', PAIR)
    tf   = request.args.get('tf', '5m')
    st   = request.args.get('start_time', type=int)
    data = tloader.prepare_agg_bars(pair, tf, start_time=st)
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
    # report whether the 5m‐based stream is currently running
    playing = tloader.streaming_1m.get(PAIR, False)
    emit('stream_status', {'playing': playing})

@socketio.on('start_stream')
def on_start_stream(payload):
    tf   = payload.get('timeframe', '1m')
    pair = payload.get('pair', PAIR)
    tloader.set_timeframe(tf)

    # align raw 1m pointer
    from_time = payload.get('fromTime', int(BarsConfig.INITIAL_END.timestamp()))
    idx = next((i for i,b in enumerate(tloader.all_1m_data[pair]) if b['time'] > from_time),
               len(tloader.all_1m_data[pair]))
    tloader.current_1m_index[pair] = idx

    # drop any partial aggregates before starting fresh
    tloader._5m_buffer[pair].clear()
    tloader.tf_buffer[pair].clear()

    # start single 1m loop
    tloader.streaming_1m[pair] = True
    socketio.start_background_task(tloader.stream_1m_bars, pair)
    emit('stream_status', {'playing': True})

@socketio.on('pause_stream')
def on_pause_stream():
    print("[Server] 🔴 pause_stream received — stopping stream_1m_bars")
    tloader.streaming_1m[PAIR] = False
    emit('stream_status', {'playing': False})

if __name__ == '__main__':
    socketio.run(app, debug=True)