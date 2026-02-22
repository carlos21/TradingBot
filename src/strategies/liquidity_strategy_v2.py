# (path: src/strategies/liquidity_strategy_v2.py)

from __future__ import annotations
from typing import Any, Dict, List, Optional
from collections import deque
from src.services.trade_manager import TradeManager
from src.strategies.base_liquidity_strategy import BaseLiquidityStrategy, StrategyOptions
from src.strategies.entry_context import EntryContext, EntryTrigger
from src.strategies.strategy_config import CandleConfig
from src.strategies.triggers import _calculate_tsi_series, RESCUE_TSI_TIMEFRAME


class LiquidityStrategyV2(BaseLiquidityStrategy):
    def __init__(
        self,
        min_stop_loss: float,
        max_bounce: float,
        socketio,
        line_repository,
        trade_repository,
        trade_manager: TradeManager,
        extra_sl_space: float,
        fixed_stop_loss: Optional[float] = None,
        max_stop_loss: Optional[float] = None,
        timeframes: List[str] = None,
        options: Optional[StrategyOptions] = None,
        htf_fetcher=None,
        candle_config: Optional[CandleConfig] = None, 
    ):
        self.timeframes = timeframes or ["5m"]
        
        # Internal: Ensure we always aggregate required timeframes.
        # 15m: velocity scoring; RESCUE_TSI_TIMEFRAME (5m): rescue logic;
        # 1m / 3m: velocity-adaptive trigger (slow / moderate regimes).
        self._internal_timeframes = list(self.timeframes)
        for req in ["15m", RESCUE_TSI_TIMEFRAME, "1m", "3m"]:
            if req not in self._internal_timeframes:
                self._internal_timeframes.append(req)

        super().__init__(
            min_stop_loss=min_stop_loss,
            max_bounce=max_bounce,
            socketio=socketio,
            line_repository=line_repository,
            trade_repository=trade_repository,
            trade_manager=trade_manager,
            extra_sl_space=extra_sl_space,
            fixed_stop_loss=fixed_stop_loss,
            max_stop_loss=max_stop_loss,
            strategy_tf=self.timeframes[0], 
            options=options,
            htf_fetcher=htf_fetcher,
        )

        self.candle_config = candle_config
        self._tf_aggregators = {}
        self._tf_histories = {}
        
        self.decision_logs = []

        for tf in self._internal_timeframes:
            self._tf_aggregators[tf] = {
                "seconds": self._parse_tf_seconds(tf),
                "buf": [],
                "start": None
            }
            self._tf_histories[tf] = deque(maxlen=100)

    def reset(self):
        with self.lock:
            self.strategy_lines.clear()
            self.open_trades.clear()
            self.trade_manager.open_trades.clear()
            self.decision_logs.clear()
            for tf in self._internal_timeframes:
                self._tf_aggregators[tf] = {
                    "seconds": self._parse_tf_seconds(tf),
                    "buf": [],
                    "start": None
                }
                self._tf_histories[tf].clear()
            print("[StrategyV2] 🧹 Internal state fully reset.")

    def _reset_line_state(self, line_state: Dict[str, Any]):
        super()._reset_line_state(line_state)
        if "d5_stage" in line_state:
            line_state["d5_stage"] = 0
        if "tsi_stage" in line_state:
            line_state["tsi_stage"] = 0
            line_state["tsi_ref_price"] = 0.0
        if "vat_5m_stage" in line_state:
            line_state["vat_5m_stage"] = 0
            line_state["vat_5m_reset"] = False

    def _parse_tf_seconds(self, tf: str) -> int:
        unit = tf[-1].lower()
        val = int(tf[:-1])
        if unit == 'm': return val * 60
        if unit == 'h': return val * 3600
        if unit == 'd': return val * 86400
        return val

    def get_history(self, tf: str, count: int) -> List[Dict[str, Any]]:
        hist = self._tf_histories.get(tf, [])
        if len(hist) < count:
            return []
        return list(hist)[-count:]

    def log_decision(self, bar_time: int, tf: str, line_id: str, event: str, details: str):
        self.decision_logs.append({
            "time": bar_time,
            "tf": tf,
            "line_id": line_id,
            "event": event,
            "details": details
        })

    def on_raw_bar(self, bar: Dict[str, Any]):
        with self.lock:
            self._check_open_trades(bar)

            if self.options.breakeven:
                self._check_breakeven(bar)
            
            current_price = bar['close']
            bar_time = bar['time']
            lines_to_remove = set()

            for sid, line in self.strategy_lines.items():
                creation_ts = line.get('creation_ts', 0)
                if creation_ts > bar_time:
                    continue

                if line['direction'] is None:
                    line['direction'] = "short" if current_price < line['level'] else "long"
                    line['extreme'] = float("-inf") if line['direction'] == "short" else float("inf")
                    self.log_decision(bar_time, "1m", sid, "LATCH", f"Latched {line['direction']} @ {current_price}")
                
                elif line['direction'] == 'short':
                    line['extreme'] = max(line['extreme'], bar['high'])
                    if current_price > (line['level'] + self.max_bounce):
                        msg = f"Price {current_price} > {line['level'] + self.max_bounce} (Max Bounce)"
                        self.log_decision(bar_time, "1m", sid, "REMOVE", msg)
                        lines_to_remove.add(sid)
                
                elif line['direction'] == 'long':
                    line['extreme'] = min(line['extreme'], bar['low'])
                    if current_price < (line['level'] - self.max_bounce):
                        msg = f"Price {current_price} < {line['level'] - self.max_bounce} (Max Bounce)"
                        self.log_decision(bar_time, "1m", sid, "REMOVE", msg)
                        lines_to_remove.add(sid)

            short_lines = [l for l in self.strategy_lines.values() if l['direction'] == 'short']
            long_lines  = [l for l in self.strategy_lines.values() if l['direction'] == 'long']

            for sid, line in self.strategy_lines.items():
                if sid in lines_to_remove: continue
                if line['direction'] == 'short':
                    for other in short_lines:
                        if other is line: continue
                        if line['level'] < other['level'] <= bar['high']:
                            self.log_decision(bar_time, "1m", sid, "REMOVE", f"Hit higher resistance {other['level']}")
                            lines_to_remove.add(sid)
                            break
                elif line['direction'] == 'long':
                    for other in long_lines:
                        if other is line: continue
                        if line['level'] > other['level'] >= bar['low']:
                            self.log_decision(bar_time, "1m", sid, "REMOVE", f"Hit lower support {other['level']}")
                            lines_to_remove.add(sid)
                            break

            for sid in lines_to_remove:
                self.remove_strategy_line(sid)

        ts = bar["time"]
        # Iterate over ALL internal aggregators (including 3m/15m)
        for tf, state in self._tf_aggregators.items():
            window_secs = state["seconds"]
            window_start = (ts // window_secs) * window_secs

            if state["start"] is None:
                state["start"] = window_start

            if state["buf"] and state["buf"][-1]['time'] == ts:
                continue

            if window_start == state["start"]:
                state["buf"].append(bar)
            else:
                if state["buf"]:
                    agg_bar = self._aggregate_bars(state["buf"], state["start"], window_secs)
                    agg_bar['tf'] = tf 
                    self._tf_histories[tf].append(agg_bar)
                    self._on_strategy_bar(agg_bar)

                state["buf"] = [bar]
                state["start"] = window_start

    def _on_strategy_bar(self, bar: Dict[str, Any]):
        with self.lock:
            # Calculate and Emit TSI for Visualization ---
            tf = bar.get('tf')
            if tf:
                history = self.get_history(tf, 100)
                closes = [b['close'] for b in history]
                tsi_vals, sig_vals = _calculate_tsi_series(closes, 6, 13, 4)
                
                if tsi_vals and sig_vals:
                    # --- NEW: Detect Crossover ---
                    cross_type = None
                    if len(tsi_vals) >= 2:
                        curr_tsi = tsi_vals[-1]
                        curr_sig = sig_vals[-1]
                        prev_tsi = tsi_vals[-2]
                        prev_sig = sig_vals[-2]

                        # Bullish Cross: Blue crosses ABOVE Red
                        if prev_tsi <= prev_sig and curr_tsi > curr_sig:
                            cross_type = 'bullish'
                        # Bearish Cross: Blue crosses BELOW Red
                        elif prev_tsi >= prev_sig and curr_tsi < curr_sig:
                            cross_type = 'bearish'

                    self.socketio.emit('indicator_update', {
                        'tf': tf,
                        'time': bar['time'],
                        'tsi': tsi_vals[-1],
                        'signal': sig_vals[-1],
                        'cross_type': cross_type 
                    })

            for sid, line in list(self.strategy_lines.items()):
                if line.get('creation_ts', 0) > bar['time']:
                    continue

                opened = False
                proposed_ctx: Optional[EntryContext] = None
                trigger_name = "None"

                for trig in self.triggers:
                    proposed_ctx = trig(self, sid, line, bar)
                    if proposed_ctx is not None:
                        trigger_name = trig.__name__
                        break
                
                if proposed_ctx is None:
                    continue

                allow, reason = self._filters_allow_entry(proposed_ctx)
                
                if allow:
                    self.log_decision(bar['time'], bar.get('tf'), sid, "ENTRY", 
                        f"Trigger: {trigger_name} | Dir: {proposed_ctx.direction} | Price: {proposed_ctx.close}")
                    
                    trade = self._build_trade_from_context(proposed_ctx)
                    self._store_and_emit_open(trade)
                    opened = True
                else:
                    self.log_decision(bar['time'], bar.get('tf'), sid, "FILTER_BLOCK", 
                        f"Trigger: {trigger_name} | Reason: {reason}")
                
                self._maybe_remove_line(sid, opened)