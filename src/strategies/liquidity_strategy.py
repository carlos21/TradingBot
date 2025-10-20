# src/strategy.py
from threading import RLock
from datetime import datetime, timezone
from src.dbexception import DBNotFoundException
from src.repositories.lines_repository import LineRepository
from src.repositories.trades_repository import TradeRepository
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Tuple, List, Dict, Any
from src.strategies.entry_context import *



class LineRemovalMode(str, Enum):
    ON_EVALUATE = "on_evaluate"   # remove line after we evaluated it (old behavior)
    ON_ENTER    = "on_enter"      # remove only if we actually opened a trade
    NEVER       = "never"         # never remove (we'll reset its state for future triggers)

@dataclass
class StrategyOptions:
    line_removal_mode: LineRemovalMode = LineRemovalMode.ON_EVALUATE
    entry_filters: Optional[List[EntryFilter]] = None
    triggers: Optional[List[EntryTrigger]] = None

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
        extra_sl_space: float,
        strategy_tf: str = '5m',
        options: Optional[StrategyOptions] = None,
        htf_fetcher: Optional[Callable[[str, int, str], Optional[dict]]] = None,
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

        self.options = options or StrategyOptions()
        self.entry_filters = list(self.options.entry_filters or [])
        self.triggers = list(self.options.triggers or [retest_cross_trigger])

        # optional dependency for multi-TF checks (15m/1h, etc.)
        self.htf_fetcher = htf_fetcher

    # Convenience if you want to tweak filters at runtime:
    def set_entry_filters(self, filters: List[EntryFilter]):
        with self.lock:
            self.entry_filters = list(filters)

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
        1) check exits on open trades
        2) for each strategy line, run triggers → may propose an EntryContext
        3) if proposed, run filters; if allowed, open trade and maybe remove the line
        """
        with self.lock:
            # 1) exits first
            self._check_open_trades(bar)

            # 2) evaluate lines via triggers
            for sid, line in list(self.strategy_lines.items()):
                opened = False
                proposed_ctx: Optional[EntryContext] = None

                # run triggers in order until one proposes
                for trig in self.triggers:
                    proposed_ctx = trig(self, sid, line, bar)
                    if proposed_ctx is not None:
                        break

                if proposed_ctx is None:
                    # nothing to evaluate / no retest yet
                    continue

                # 3) run filters
                allow, reason = self._filters_allow_entry(proposed_ctx)
                if allow:
                    extra = self.extra_sl_space
                    if proposed_ctx.direction == 'long':
                        entry    = proposed_ctx.close
                        raw_risk = max(entry - proposed_ctx.extreme, self.min_stop_loss)
                        eff_risk = raw_risk + extra
                        sl       = entry - eff_risk
                        tp       = entry + 4 * eff_risk
                        trade    = self._make_trade_dict(bar, 'long', entry, sl, tp, eff_risk)
                    else:
                        entry    = proposed_ctx.close
                        raw_risk = max(proposed_ctx.extreme - entry, self.min_stop_loss)
                        eff_risk = raw_risk + extra
                        sl       = entry + eff_risk
                        tp       = entry - 4 * eff_risk
                        trade    = self._make_trade_dict(bar, 'short', entry, sl, tp, eff_risk)

                    self._store_and_emit_open(trade)
                    opened = True

                # line removal policy (evaluate vs trade)
                self._maybe_remove_line(sid, opened)

    def _reset_line_state(self, line_state: Dict[str, Any]):
        """If we keep the line, reset it so it can trigger again in future."""
        line_state['has_crossed'] = False
        line_state['extreme'] = float('inf') if line_state['direction'] == 'long' else float('-inf')

    def _filters_allow_entry(self, ctx: EntryContext) -> Tuple[bool, str]:
        """
        Run all pluggable entry filters. If any returns (False, reason), block the entry.
        """
        for f in self.entry_filters:
            ok, reason = f(ctx)
            if not ok:
                return False, reason
        return True, "ok"

    def _maybe_remove_line(self, line_id: Any, opened: bool):
        """
        Remove the evaluated strategy line depending on removal mode.
        """
        mode = self.options.line_removal_mode
        if mode == LineRemovalMode.NEVER:
            return
        if mode == LineRemovalMode.ON_EVALUATE:
            self.remove_strategy_line(line_id)
        elif mode == LineRemovalMode.ON_ENTER and opened:
            self.remove_strategy_line(line_id)

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