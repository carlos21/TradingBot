from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
import csv
import time
import threading
from datetime import datetime

import yfinance as yf
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

def fetch_yahoo_data(ticker, interval, ema_period=20, rsi_period=14):
    end_date = datetime.now()
    if interval in ['1m', '5m']:
        start_date = end_date - timedelta(days=7)
    elif interval in ['15m', '60m']:
        start_date = end_date - timedelta(days=60)
    elif interval == '1d':
        start_date = end_date - timedelta(days=365*5)
    elif interval == '1wk':
        start_date = end_date - timedelta(weeks=365*5)
    elif interval == '1mo':
        start_date = end_date - timedelta(days=365*5)

    data = yf.download(ticker, start=start_date, end=end_date, interval=interval)
    data.columns = data.columns.get_level_values(0)

    data['EMA'] = ta.ema(data['Close'], length=ema_period)
    data['RSI'] = ta.rsi(data['Close'], length=rsi_period)
    
    candlestick_data = [
        {
            'time': int(row.Index.timestamp()),
            'open': row.Open,
            'high': row.High,
            'low': row.Low,
            'close': row.Close
        }
        for row in data.itertuples()
    ]

    ema_data = [
        {
            'time': int(row.Index.timestamp()),
            'value': row.EMA
        }
        for row in data.itertuples() if not pd.isna(row.EMA)
    ]

    rsi_data = [
        {
            'time': int(row.Index.timestamp()),
            'value': row.RSI if not pd.isna(row.RSI) else 0  # Convert NaN to zero
        }
        for row in data.itertuples()
    ]

    return candlestick_data, ema_data, rsi_data

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data/<ticker>/<interval>/<int:ema_period>/<int:rsi_period>')
def get_data(ticker, interval, ema_period, rsi_period):
    candlestick_data, ema_data, rsi_data = fetch_yahoo_data(ticker, interval, ema_period, rsi_period)
    return jsonify({'candlestick': candlestick_data, 'ema': ema_data, 'rsi': rsi_data})

@app.route('/api/symbols')
def get_symbols():
    with open('symbols.txt') as f:
        symbols = [line.strip() for line in f]
    return jsonify(symbols)

@socketio.on('connect')
def on_connect():
    print("Client connected")

def load_csv_data(filename):
    data = []
    with open(filename, 'r') as csvfile:
        # CSV file uses semicolon delimiters
        reader = csv.DictReader(csvfile, delimiter=';')
        for row in reader:
            # Combine Date and Time into a datetime object
            dt_str = row['Date'] + ' ' + row['Time']
            dt = datetime.strptime(dt_str, '%d/%m/%Y %H:%M:%S')
            # Create a bar object (lightweight‑charts expects a UNIX timestamp in seconds)
            bar = {
                'time': int(dt.timestamp()),
                'open': float(row['Open']),
                'high': float(row['High']),
                'low': float(row['Low']),
                'close': float(row['Close']),
                'volume': int(row['Volume'])
            }
            data.append(bar)
    # Sort by time (ascending)
    data.sort(key=lambda x: x['time'])
    return data

def background_thread():
    """Simulate real-time feed by emitting one bar per second."""
    data = load_csv_data('nq-1m.csv', last_rows=100000)
    for bar in data:
        socketio.emit('new_bar', bar)
        time.sleep(1)  # wait one second before sending the next bar

if __name__ == '__main__':
    thread = threading.Thread(target=background_thread)
    thread.daemon = True
    thread.start()
    socketio.run(app, debug=True)