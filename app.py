from flask import Flask, jsonify, request, abort, render_template
from flask_socketio import SocketIO, emit
from flask_cors import CORS

from src.bars_loader import BarsLoader, LoaderConfig
from src.controllers.lines_controller import LinesController
from src.controllers.trades_controller import TradesController
from src.data_sources.csv_datasource import CSVDataSource
from src.data_sources.metatrader_datasource import MetaTraderConfig, MetaTraderDataSource
from src.repositories.lines_repository import SQLLineRepository
from src.strategies.liquidity_m1dual_strategy import LiquidityDualM1Strategy
from src.strategies.liquidity_strategy import LiquidityStrategy
from src.database import database
from src.services.trade_manager import TradeManager
from src.repositories.trades_repository import SQLTradeRepository
from src.strategies.strategy_config import StrategyConfig
from datetime import datetime

# ───────── Setup ─────────
database.setup_database()
line_repository = SQLLineRepository()
trade_repository = SQLTradeRepository()

app      = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# ───────── Strategy + Loader ─────────
PAIR     = 'NQ'
strat_cfg = StrategyConfig(
    stop_loss={ 'EURUSD': 0.0004, 'NQ': 10 },
    max_bounce={ 'EURUSD': 0.0020, 'NQ': 40 },
    extra_sl_space={ 'EURUSD': 0.0002, 'NQ': 2.0 }
)
# tstrategy = LiquidityStrategy(
#     min_stop_loss=strat_cfg.stop_loss[PAIR],
#     max_bounce=strat_cfg.max_bounce[PAIR],
#     extra_sl_space= strat_cfg.extra_sl_space[PAIR],
#     socketio=socketio,
#     line_repository = line_repository,
#     trade_repository = trade_repository
# )
tstrategy = LiquidityDualM1Strategy(
    min_stop_loss=strat_cfg.stop_loss[PAIR],
    max_bounce=strat_cfg.max_bounce[PAIR],
    extra_sl_space= strat_cfg.extra_sl_space[PAIR],
    socketio=socketio,
    line_repository = line_repository,
    trade_repository = trade_repository
)
trade_manager = TradeManager(
    trade_repository = trade_repository,
    socketio = socketio
)

mt_cfg = MetaTraderConfig(
    login=61371570,
    password='MiPuchuxD21@',
    server='Pepperstone-Demo',
    history_days=50,     # if you want more than the default
    host='127.0.0.1',
    port=9999
)
ds = MetaTraderDataSource(symbol='NAS100', cfg=mt_cfg)
# ds = CSVDataSource(pair= PAIR)

def combined_bar_callback(bar):
    trade_manager.handle_new_1m_bar(bar)
    tstrategy.on_raw_bar(bar)

loader_cfg = LoaderConfig(
    initial_start = datetime.fromisoformat("2024-01-14T00:00:00+00:00"),
    initial_end   = datetime.fromisoformat("2025-12-31T15:11:00+00:00"),
)

tloader = BarsLoader(
    loader_config=loader_cfg,
    data_source=ds,
    socketio=socketio,
    bar_callback=combined_bar_callback
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
    tloader.set_timeframe(tf)

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
    from_time = payload.get('fromTime', 0)
    tloader.start(from_time)
    emit('stream_status', {'playing': True})

@socketio.on('pause_stream')
def on_pause_stream():
    tloader.pause()
    emit('stream_status', {'playing': False})

@socketio.on('seek')
def on_seek(payload):
    from_time = payload.get('fromTime', 0)
    tloader.seek(from_time)

if __name__ == '__main__':
    socketio.run(app, debug=True)