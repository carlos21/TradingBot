from flask import Flask, jsonify, request, abort, render_template
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from src.bars_loader import BarsConfig, BarsLoader
from src.lines_repository import SQLLineRepository, DBNotFoundException
from src.database import database

database.setup_database()

line_repository = SQLLineRepository()

# ───────── Application Setup ─────────
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")
bars_loader = BarsLoader(BarsConfig, socketio, last_rows=100_000)


@app.route('/')
def index():
    return render_template('tester.html')


@app.route('/api/bars', methods=['GET'])
def get_bars():
    pair      = request.args.get('pair', 'EURUSD')
    tf        = request.args.get('tf', '5m')
    st        = request.args.get('start_time', type=int)
    data      = bars_loader.prepare_agg_bars(pair, tf, start_time=st)
    return jsonify(data)


@app.route('/api/lines', methods=['GET'])
def list_lines():
    lines = line_repository.list_lines()
    result = []
    for l in lines:
        result.append({
            'id': l.line_id,
            'pair': l.pair,
            'price': l.price,
            'creation_date': l.creation_date.isoformat()
        })
    return jsonify(result)


@app.route('/api/lines', methods=['POST'])
def add_line():
    data = request.get_json()
    if not data or 'pair' not in data or 'price' not in data:
        abort(400, 'Must provide {"pair": <str>, "price": <number>}')
    try:
        price = float(data['price'])
    except (TypeError, ValueError):
        abort(400, "Field 'price' must be a number")

    line = line_repository.insert_line(pair=data['pair'], price=price)
    return jsonify({
        'id': line.line_id,
        'pair': line.pair,
        'price': line.price,
        'creation_date': line.creation_date.isoformat()
    }), 201


@app.route('/api/lines/<string:line_id>', methods=['DELETE'])
def delete_line(line_id):
    try:
        line_repository.delete_line(line_id)
    except DBNotFoundException:
        abort(404, f"Line id={line_id} not found")
    return '', 204


@socketio.on('connect')
def on_connect():
    emit('stream_status', {'playing': bars_loader.streaming})


@socketio.on('start_stream')
def on_start_stream(payload=None):
    if not bars_loader.streaming:
        bars_loader.streaming = True
        socketio.start_background_task(bars_loader.stream_bars)
    emit('stream_status', {'playing': bars_loader.streaming})


@socketio.on('pause_stream')
def on_pause_stream():
    bars_loader.streaming = False
    emit('stream_status', {'playing': bars_loader.streaming})


if __name__ == '__main__':
    socketio.run(app, debug=True)
