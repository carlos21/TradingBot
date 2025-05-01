# src/strategy.py
from threading import RLock

from src.dbexception import DBNotFoundException
from src.lines_repository import LineRepository

class LiquidityStrategy:
    """
    Server-side version of your JS strategy.
    Emits 'trade_open' and 'trade_close' via Socket.IO.
    """
    def __init__(self, min_stop_loss, max_bounce, socketio, line_repository: LineRepository, extra_sl_space: dict[str, float]):
        self.min_stop_loss = min_stop_loss
        self.max_bounce    = max_bounce
        self.socketio      = socketio
        self.line_repository = line_repository
        self.extra_sl_space = extra_sl_space

        self.strategy_lines = {}   # id -> { level, direction, has_crossed, extreme }
        self.open_trades    = []   # list of open trade dicts
        self.total_pnl      = 0
        self.lock           = RLock()

    def add_strategy_line(self, id, level, direction):
        print(f"[Strategy] ➕ add_strategy_line id={id} level={level} direction={direction}")
        with self.lock:
            self.strategy_lines[id] = {
                'level':       level,
                'direction':   direction,
                'has_crossed': False,
                'extreme':     float('inf') if direction == 'long' else float('-inf')
            }

    def remove_strategy_line(self, id):
        # self.strategy_lines.pop(id, None)
        with self.lock:
            self.strategy_lines.pop(id, None)
        try:
            self.line_repository.delete_line(id)
        except DBNotFoundException:
            pass
        self.socketio.emit('line_removed', {'id': id})

    def on_new_bar(self, bar):
        """
        Called on every 5m bar. Checks existing open trades for exit,
        then scans strategy_lines for new entries.
        """
        with self.lock:
            self._check_open_trades(bar)

            # don't open if there's already an open trade
            if any(t['status'] == 'open' for t in self.open_trades):
                return

            for sid, s in list(self.strategy_lines.items()):
                lvl, dir_ = s['level'], s['direction']
                close, low, high = bar['close'], bar['low'], bar['high']

                extra = self.extra_sl_space.get(bar['pair'], 0)

                if dir_ == 'long':
                    if close < lvl:
                        s['has_crossed'] = True
                        s['extreme'] = min(s['extreme'], low)
                    if s['has_crossed'] and close >= lvl:
                        depth = lvl - s['extreme']
                        if depth <= self.max_bounce:
                            entry = close
                            risk  = max(entry - s['extreme'], self.min_stop_loss)
                            sl    = entry - risk - extra
                            tp    = entry + 4 * risk
                            trade = {
                                'pair':        bar['pair'],
                                'type':        'long',
                                'entry':       entry,
                                'stop_loss':   sl,
                                'take_profit': tp,
                                'risk':        risk,
                                'status':      'open',
                                'entry_time':  bar['time']
                            }
                            self.open_trades.append(trade)
                            self.socketio.emit('trade_open', trade)
                        self.remove_strategy_line(sid)
                        break
                else:
                    if close > lvl:
                        s['has_crossed'] = True
                        s['extreme'] = max(s['extreme'], high)
                    if s['has_crossed'] and close <= lvl:
                        depth = s['extreme'] - lvl
                        if depth <= self.max_bounce:
                            entry = close
                            risk  = max(s['extreme'] - entry, self.min_stop_loss)
                            sl    = entry + risk + extra
                            tp    = entry - 4 * risk
                            trade = {
                                'pair':        bar['pair'],
                                'type':        'short',
                                'entry':       entry,
                                'stop_loss':   sl,
                                'take_profit': tp,
                                'risk':        risk,
                                'status':      'open',
                                'entry_time':  bar['time']
                            }
                            self.open_trades.append(trade)
                            self.socketio.emit('trade_open', trade)
                        self.remove_strategy_line(sid)
                        break

    def _check_open_trades(self, bar):
        remaining = []
        for t in self.open_trades:
            if t['status'] != 'open':
                continue
            low, high = bar['low'], bar['high']

            if t['type'] == 'long':
                if low <= t['stop_loss']:
                    t.update(status='closed', result=-1,
                             exit_time=bar['time'], exit_price=low)
                    # ← this emits the “exit trade” event
                    self.socketio.emit('trade_close', t)

                elif high >= t['take_profit']:
                    t.update(status='closed', result=4,
                             exit_time=bar['time'], exit_price=high)
                    self.socketio.emit('trade_close', t)
                else:
                    remaining.append(t)

            else:  # short
                if high >= t['stop_loss']:
                    t.update(status='closed', result=-1,
                             exit_time=bar['time'], exit_price=high)
                    self.socketio.emit('trade_close', t)

                elif low <= t['take_profit']:
                    t.update(status='closed', result=4,
                             exit_time=bar['time'], exit_price=low)
                    self.socketio.emit('trade_close', t)
                else:
                    remaining.append(t)

        self.open_trades = remaining