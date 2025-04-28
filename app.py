from flask import Flask, jsonify, request, abort, render_template
from flask_socketio import SocketIO, emit
from flask_cors import CORS

from src.bars_loader import BarsConfig, BarsLoader
from src.lines_repository import SQLLineRepository, DBNotFoundException
from src.strategies.liquidity_strategy import LiquidityStrategy
from src.database import database

# ───────── Setup ─────────
database.setup_database()
line_repository = SQLLineRepository()

app      = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# ───────── Strategy + Loader ─────────
PAIR     = 'EURUSD'
tstrategy = LiquidityStrategy(
    min_stop_loss=BarsConfig.STOP_LOSS_CONFIG[PAIR],
    max_bounce=   BarsConfig.MAX_BOUNCE_CONFIG[PAIR],
    socketio=     socketio
)
tloader = BarsLoader(
    config=     BarsConfig,
    socketio=   socketio,
    strategy=   tstrategy,
    last_rows=  100_000
)

# ───────── Bootstrapping existing lines ─────────
for l in line_repository.list_lines():
    if l.pair != PAIR:
        continue

    # use the stored direction
    direction = l.direction
    print(f"[Boot] restoring line {l.line_id} @ {l.price} as {direction}")
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
    lines = line_repository.list_lines()
    return jsonify([{
        'id':            l.line_id,
        'pair':          l.pair,
        'price':         l.price,
        'direction':     l.direction,
        'creation_date': l.creation_date.isoformat()
    } for l in lines])

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
    idx  = tloader.current_5m_index[pair]
    last_close = tloader.agg_5m[pair][idx - 1]['close'] if idx > 0 else None
    direction  = 'short' if last_close < price else 'long'

    line = line_repository.insert_line(pair=pair, price=price, direction=direction)
    if line.pair == 'EURUSD':
        tstrategy.add_strategy_line(line.line_id, line.price, line.direction)

    return jsonify({
        'id':            line.line_id,
        'pair':          line.pair,
        'price':         line.price,
        'direction':     line.direction,
        'creation_date': line.creation_date
    }), 201

@app.route('/api/lines/<string:line_id>', methods=['DELETE'])
def delete_line(line_id):
    try:
        line_repository.delete_line(line_id)
        tstrategy.remove_strategy_line(line_id)
    except DBNotFoundException:
        abort(404, f"Line id={line_id} not found")
    return '', 204

# ───────── Socket.IO Events ─────────
@socketio.on('connect')
def on_connect(auth):
    # report whether the 5m‐based stream is currently running
    playing = tloader.streaming_5m.get(PAIR, False)
    emit('stream_status', {'playing': playing})

@socketio.on('start_stream')
def on_start_stream(payload):
    print(f"[Server] 🟢 start_stream payload={payload}")
    tf = payload.get('timeframe', '5m')
    tloader.set_timeframe(tf)

    # align pointer as before...
    from_time = payload.get('fromTime', int(BarsConfig.INITIAL_END.timestamp()))
    idx = next((i for i,b in enumerate(tloader.agg_5m[PAIR]) if b['time'] > from_time),
               len(tloader.agg_5m[PAIR]))
    print(f"[Server] ⏩ seeking to idx={idx} for bar.time > {from_time}")
    tloader.current_5m_index[PAIR] = idx

    # ALWAYS start a new loop, even if the old one hasn't fully torn down
    tloader.streaming_5m[PAIR] = True
    print(f"[Server] ▶ starting stream_5m_bars for {PAIR}")
    socketio.start_background_task(tloader.stream_5m_bars, PAIR)

    emit('stream_status', {'playing': True})

@socketio.on('pause_stream')
def on_pause_stream():
    print("[Server] 🔴 pause_stream received — stopping stream_5m_bars")
    tloader.streaming_5m[PAIR] = False
    emit('stream_status', {'playing': False})

if __name__ == '__main__':
    socketio.run(app, debug=True)