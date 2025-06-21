# src/strategy.py
from threading import RLock
from datetime import datetime, timezone

from src.dbexception import DBNotFoundException
from src.repositories.lines_repository import LineRepository
from src.repositories.trades_repository import TradeRepository

class LiquidityStrategy:
    """
    Server-side version of your JS strategy.
    Emits 'trade_open' and 'trade_close' via Socket.IO.
    """
    def __init__(
        self, 
        min_stop_loss, 
        max_bounce, 
        socketio, 
        line_repository: LineRepository,
        trade_repository: TradeRepository,
        extra_sl_space: dict[str, float],
        strategy_tf: str = '5m'
    ):
        self.min_stop_loss = min_stop_loss
        self.max_bounce    = max_bounce
        self.socketio      = socketio
        self.line_repository = line_repository
        self.trade_repository = trade_repository
        self.extra_sl_space = extra_sl_space

        self.strategy_lines = {}   # id -> { level, direction, has_crossed, extreme }
        self.open_trades    = []   # list of open trade dicts
        self.total_pnl      = 0
        self.lock           = RLock()

        num, unit = int(strategy_tf[:-1]), strategy_tf[-1]
        self.strategy_window = num * (60 if unit=='m' else 3600)
        self._buf = []
        self._group_start = None

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
        with self.lock:
            self.strategy_lines.pop(id, None)
        try:
            self.line_repository.delete_line(id)
        except DBNotFoundException:
            pass
        self.socketio.emit('line_removed', {'id': id})

    def _aggregate_bars(self, bars: list[dict], window_start: int, window_secs: int) -> dict:
        high    = max(b['high']   for b in bars)
        low     = min(b['low']    for b in bars)
        open_   = bars[0]['open']
        close_  = bars[-1]['close']
        volume  = sum(b['volume'] for b in bars)
        pair    = bars[0]['pair']
        return {
            'time':       window_start + window_secs,
            'open':       open_,
            'high':       high,
            'low':        low,
            'close':      close_,
            'volume':     volume,
            'pair':       pair
        }
    
    def on_raw_bar(self, bar):
        # 1) aggregate into your chosen TF
        ts = bar['time']
        win = (ts // self.strategy_window) * self.strategy_window
        if self._group_start is None:
            self._group_start = win
        if win == self._group_start:
            self._buf.append(bar)
        else:
            agg = self._aggregate_bars(self._buf, self._group_start, self.strategy_window)
            self._buf, self._group_start = [bar], win
            # 2) now run your entry/exit logic on that agg bar
            self._on_strategy_bar(agg)

    def _on_strategy_bar(self, bar: dict):
        """
        Called on every 5m bar. Checks existing open trades for exit,
        then scans strategy_lines for new entries. Uses helper to store/emit.
        """
        with self.lock:
            self._check_open_trades(bar)

            # don't open if there's already an open trade
            if any(t['status'] == 'open' for t in self.open_trades):
                return

            for sid, s in list(self.strategy_lines.items()):
                lvl       = s['level']
                dir_      = s['direction']
                close     = bar['close']
                low, high = bar['low'], bar['high']
                extra     = self.extra_sl_space.get(bar['pair'], 0)

                # detect entry
                if dir_ == 'long':
                    if close < lvl:
                        s['has_crossed'] = True
                        s['extreme']     = min(s['extreme'], low)
                    if s['has_crossed'] and close >= lvl:
                        depth = lvl - s['extreme']
                        if depth <= self.max_bounce:
                            entry   = close
                            risk    = max(entry - s['extreme'], self.min_stop_loss)
                            sl      = entry - risk - extra
                            tp      = entry + 4 * risk
                            trade   = self._make_trade_dict(bar, 'long', entry, sl, tp, risk)
                            self._store_and_emit_open(trade)
                        self.remove_strategy_line(sid)
                        break
                else:
                    if close > lvl:
                        s['has_crossed'] = True
                        s['extreme']     = max(s['extreme'], high)
                    if s['has_crossed'] and close <= lvl:
                        depth = s['extreme'] - lvl
                        if depth <= self.max_bounce:
                            entry   = close
                            risk    = max(s['extreme'] - entry, self.min_stop_loss)
                            sl      = entry + risk + extra
                            tp      = entry - 4 * risk
                            trade   = self._make_trade_dict(bar, 'short', entry, sl, tp, risk)
                            self._store_and_emit_open(trade)
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

    def _make_trade_dict(
        self,
        bar: dict,
        trade_type: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        risk: float
    ) -> dict:
        return {
            'pair':       bar['pair'],
            'type':       trade_type,
            'entry':      entry,
            'stop_loss':  stop_loss,
            'take_profit':take_profit,
            'risk':       risk,
            'status':     'open',
            'entry_time': bar['time']
        }

    def _store_and_emit_open(self, trade: dict):
        """
        Persist a newly opened trade and emit the open event.
        """
        td = self.trade_repository.insert_trade(
            pair=trade['pair'],
            trade_type=trade['type'],
            entry_price=trade['entry'],
            stop_loss=trade['stop_loss'],
            take_profit=trade['take_profit'],
            risk=trade['risk'],
            entry_time=datetime.fromtimestamp(trade['entry_time'], tz=timezone.utc),
            params={}
        )
        trade['trade_id'] = td.trade_id
        self.open_trades.append(trade)
        self.socketio.emit('trade_open', trade)

    def _store_and_emit_close(self, trade: dict):
        """
        Persist a closed trade and emit the close event.
        """
        # emit first so clients see status update
        self.socketio.emit('trade_close', trade)
        self.trade_repository.close_trade(
            trade_id=trade['trade_id'],
            exit_price=trade['exit_price'],
            exit_time=datetime.fromtimestamp(trade['exit_time'], tz=timezone.utc),
            result=trade['result']
        )