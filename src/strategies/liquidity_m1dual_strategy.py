import contextlib
from datetime import datetime, timezone
from threading import RLock

from src.dbexception import DBNotFoundException
from src.repositories.lines_repository import LineRepository
from src.repositories.trades_repository import TradeRepository
from src.strategies.base_liquidity_strategy import BE_TRESHOLD_POINTS
from src.utils.app_logger import ILogger


class LiquidityDualM1Strategy:
    """
    Server-side version of the dual 1m candle strategy.
    Emits 'trade_open' and 'trade_close' via Socket.IO.
    Waits for two 1m candles: one initial cross then one entry cross.
    Stop loss set at the bounce extreme (low for longs, high for shorts).
    """
    def __init__(
        self,
        min_stop_loss: float,
        max_bounce: float,
        socketio,
        line_repository: LineRepository,
        trade_repository: TradeRepository,
        extra_sl_space: dict[str, float],
        logger: ILogger,
    ):
        self.min_stop_loss = min_stop_loss
        self.max_bounce = max_bounce
        self.socketio = socketio
        self.line_repository = line_repository
        self.trade_repository = trade_repository
        self.extra_sl_space = extra_sl_space
        self.logger = logger

        # Tracks pending lines: id -> { level, direction, crosses, extreme }
        self.strategy_lines = {}
        self.open_trades = []
        self.lock = RLock()

    def add_strategy_line(self, id: str, level: float, direction: str):
        """
        Register a new line to watch for dual 1m crosses.
        Initialize extreme for bounce tracking.
        """
        with self.lock:
            extreme_init = float('inf') if direction == 'long' else float('-inf')
            self.strategy_lines[id] = {
                'level': level,
                'direction': direction,
                'crosses': 0,
                'extreme': extreme_init
            }
        self.logger.info(f"[DualM1] Added line id={id}, level={level}, direction={direction}, extreme_init={extreme_init}")

    def remove_strategy_line(self, id: str):
        with self.lock:
            self.strategy_lines.pop(id, None)
        with contextlib.suppress(DBNotFoundException):
            self.line_repository.delete_line(id)
        self.socketio.emit('line_removed', {'id': id})

    def on_raw_bar(self, bar: dict):
        """
        Called on every 1m bar. Processes crosses and exit checks.
        """
        self._on_strategy_bar(bar)

    def _on_strategy_bar(self, bar: dict):
        with self.lock:
            self._check_open_trades(bar)
            # skip if any open
            if any(t['status'] == 'open' for t in self.open_trades):
                return

            for sid, s in list(self.strategy_lines.items()):
                lvl = s['level']
                dir_ = s['direction']
                o, h, low, c = bar['open'], bar['high'], bar['low'], bar['close']

                # LONG logic
                if dir_ == 'long':
                    # initial downward body cross
                    if s['crosses'] == 0 and o > lvl and c < lvl:
                        s['crosses'] = 1
                        s['extreme'] = low
                        self.logger.info(f"[DualM1][{sid}] first cross: low={low}, extreme set to={low}")
                    # after first cross, track bounce lows and check entry cross
                    elif s['crosses'] == 1:
                        # update bounce extreme
                        if low < s['extreme']:
                            old_extreme = s['extreme']
                            s['extreme'] = low
                            self.logger.info(f"[DualM1][{sid}] bounce update: low={low}, extreme updated from {old_extreme} to {low}")
                        # entry cross condition
                        if o < lvl and c > lvl:
                            s['crosses'] = 2
                            depth = lvl - s['extreme']
                            self.logger.info(f"[DualM1][{sid}] entry cross: lvl={lvl}, extreme={s['extreme']}, depth={depth}, max_bounce={self.max_bounce}")
                            if depth <= self.max_bounce:
                                entry = c
                                risk = max(entry - s['extreme'], self.min_stop_loss)
                                sl = s['extreme']
                                tp = entry + 4 * risk
                                self.logger.info(f"[DualM1][{sid}] Opening LONG → entry={entry}, sl={sl}, tp={tp}, risk={risk}")
                                trade = self._make_trade_dict(bar, 'long', entry, sl, tp, risk)
                                self._store_and_emit_open(trade)
                            else:
                                self.logger.info(f"[DualM1][{sid}] depth {depth} > max_bounce, skipping open")
                            self.remove_strategy_line(sid)
                            break

                # SHORT logic: mirror of LONG
                else:
                    # initial upward body cross
                    if s['crosses'] == 0 and o < lvl and c > lvl:
                        s['crosses'] = 1
                        s['extreme'] = h
                        self.logger.info(f"[DualM1][{sid}] first cross short: high={h}, extreme set to={h}")
                    # after first cross, track bounce highs and check entry cross
                    elif s['crosses'] == 1:
                        if h > s['extreme']:
                            old_extreme = s['extreme']
                            s['extreme'] = h
                            self.logger.info(f"[DualM1][{sid}] bounce update short: high={h}, extreme updated from {old_extreme} to {h}")
                        if o > lvl and c < lvl:
                            s['crosses'] = 2
                            depth = s['extreme'] - lvl
                            self.logger.info(f"[DualM1][{sid}] entry cross short: lvl={lvl}, extreme={s['extreme']}, depth={depth}, max_bounce={self.max_bounce}")
                            if depth <= self.max_bounce:
                                entry = c
                                risk = max(s['extreme'] - entry, self.min_stop_loss)
                                sl = s['extreme']
                                tp = entry - 4 * risk
                                self.logger.info(f"[DualM1][{sid}] Opening SHORT → entry={entry}, sl={sl}, tp={tp}, risk={risk}")
                                trade = self._make_trade_dict(bar, 'short', entry, sl, tp, risk)
                                self._store_and_emit_open(trade)
                            else:
                                self.logger.info(f"[DualM1][{sid}] depth {depth} > max_bounce, skipping open short")
                            self.remove_strategy_line(sid)
                            break

    def _check_open_trades(self, bar: dict):
        remaining = []
        for t in self.open_trades:
            if t['status'] != 'open':
                continue
            low, high = bar['low'], bar['high']

            if t['type'] == 'long':
                if low <= t['stop_loss']:
                    t.update(status='closed', result=-1, exit_time=bar['time'], exit_price=low)
                    self.socketio.emit('trade_close', t)
                elif high >= t['take_profit']:
                    t.update(status='closed', result=4, exit_time=bar['time'], exit_price=high)
                    self.socketio.emit('trade_close', t)
                else:
                    remaining.append(t)
            else:
                if high >= t['stop_loss']:
                    t.update(status='closed', result=-1, exit_time=bar['time'], exit_price=high)
                    self.socketio.emit('trade_close', t)
                elif low <= t['take_profit']:
                    t.update(status='closed', result=4, exit_time=bar['time'], exit_price=low)
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
            'pair': bar['pair'],
            'type': trade_type,
            'entry': entry,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'risk': risk,
            'status': 'open',
            'entry_time': bar['time']
        }

    def _store_and_emit_open(self, trade: dict):
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
        # Determine result_type if not already set
        result_type = trade.get('result_type')
        if not result_type:
            entry = trade.get('entry')
            sl = trade.get('stop_loss')
            tp = trade.get('take_profit')
            exit_px = trade.get('exit_price')
            # Check BE first (SL might be at entry for breakeven trades)
            if entry and abs(exit_px - entry) < BE_TRESHOLD_POINTS:
                result_type = "BE"
            elif sl and abs(exit_px - sl) < 0.5:
                result_type = "SL"
            elif tp and abs(exit_px - tp) < 0.5:
                result_type = "TP"
            else:
                result_type = "SP"
            trade['result_type'] = result_type
        self.socketio.emit('trade_close', trade)
        self.trade_repository.close_trade(
            trade_id=trade['trade_id'],
            exit_price=trade['exit_price'],
            exit_time=datetime.fromtimestamp(trade['exit_time'], tz=timezone.utc),
            result=trade['result'],
            result_type=result_type
        )
