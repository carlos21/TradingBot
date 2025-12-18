from __future__ import annotations
from typing import Any, Dict, List, Optional
from collections import deque
from src.services.trade_manager import TradeManager
from src.strategies.base_liquidity_strategy import BaseLiquidityStrategy, StrategyOptions
from src.strategies.entry_context import EntryContext, EntryTrigger
from src.strategies.strategy_config import CandleConfig


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
        timeframes: List[str] = None,
        options: Optional[StrategyOptions] = None,
        htf_fetcher=None,
        candle_config: Optional[CandleConfig] = None, 
    ):
        self.timeframes = timeframes or ["5m"]
        
        super().__init__(
            min_stop_loss=min_stop_loss,
            max_bounce=max_bounce,
            socketio=socketio,
            line_repository=line_repository,
            trade_repository=trade_repository,
            trade_manager=trade_manager,
            extra_sl_space=extra_sl_space,
            strategy_tf=self.timeframes[0], 
            options=options,
            htf_fetcher=htf_fetcher,
        )

        self.candle_config = candle_config
        self._tf_aggregators = {}
        self._tf_histories = {}

        for tf in self.timeframes:
            self._tf_aggregators[tf] = {
                "seconds": self._parse_tf_seconds(tf),
                "buf": [],
                "start": None
            }
            self._tf_histories[tf] = deque(maxlen=10)

    def reset(self):
        """
        Clears all internal state, including lines, trades, and aggregation buffers.
        Crucial for running back-to-back scenarios without state pollution.
        """
        with self.lock:
            # 1. Clear Lines and Trades
            self.strategy_lines.clear()
            self.open_trades.clear()
            self.trade_manager.open_trades.clear()
            
            # 2. Reset Aggregators
            for tf in self.timeframes:
                self._tf_aggregators[tf] = {
                    "seconds": self._parse_tf_seconds(tf),
                    "buf": [],
                    "start": None
                }
                # Clear the history deque
                self._tf_histories[tf].clear()
            
            print("[StrategyV2] 🧹 Internal state fully reset.")

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

    def on_raw_bar(self, bar: Dict[str, Any]):
        with self.lock:
            self._check_open_trades(bar)

            if self.options.breakeven:
                self._check_breakeven(bar)
            
            current_price = bar['close']
            lines_to_remove = set()

            # 1. Latch Directions & Track Extremes
            for sid, line in self.strategy_lines.items():
                if line['direction'] is None:
                    line['direction'] = "short" if current_price < line['level'] else "long"
                    line['extreme'] = float("-inf") if line['direction'] == "short" else float("inf")
                    print(f"[StrategyV2] 🔒 Latched line {sid} ({line['level']}) as {line['direction'].upper()} (Price: {current_price})")
                
                elif line['direction'] == 'short':
                    line['extreme'] = max(line['extreme'], bar['high'])
                    # Max Bounce Breach
                    if current_price > (line['level'] + self.max_bounce):
                        print(f"[StrategyV2] 🗑️ Removing Line {sid} ({line['level']}) - Max bounce breach")
                        lines_to_remove.add(sid)
                
                elif line['direction'] == 'long':
                    line['extreme'] = min(line['extreme'], bar['low'])
                    # Max Bounce Breach
                    if current_price < (line['level'] - self.max_bounce):
                        print(f"[StrategyV2] 🗑️ Removing Line {sid} ({line['level']}) - Max bounce breach")
                        lines_to_remove.add(sid)

            # 2. Next Line Breach Check (The Fix)
            # If price hits Line B, Line A is invalidated.
            
            # Group active lines by direction
            short_lines = [l for l in self.strategy_lines.values() if l['direction'] == 'short']
            long_lines  = [l for l in self.strategy_lines.values() if l['direction'] == 'long']

            for sid, line in self.strategy_lines.items():
                if sid in lines_to_remove: continue
                
                if line['direction'] == 'short':
                    # If there is another Short line ABOVE this one, and price hit it
                    # Condition: Level A < Level B <= Bar High
                    for other in short_lines:
                        if other is line: continue
                        if line['level'] < other['level'] <= bar['high']:
                            print(f"[StrategyV2] 🗑️ Removing Line {sid} ({line['level']}) - Price hit higher resistance {other['level']}")
                            lines_to_remove.add(sid)
                            break
                
                elif line['direction'] == 'long':
                    # If there is another Long line BELOW this one, and price hit it
                    # Condition: Level A > Level B >= Bar Low
                    for other in long_lines:
                        if other is line: continue
                        if line['level'] > other['level'] >= bar['low']:
                            print(f"[StrategyV2] 🗑️ Removing Line {sid} ({line['level']}) - Price hit lower support {other['level']}")
                            lines_to_remove.add(sid)
                            break

            # Perform removals
            for sid in lines_to_remove:
                self.remove_strategy_line(sid)

        ts = bar["time"]
        
        for tf, state in self._tf_aggregators.items():
            window_secs = state["seconds"]
            window_start = (ts // window_secs) * window_secs

            if state["start"] is None:
                state["start"] = window_start

            # DUPLICATE PROTECTION:
            # If we receive the exact same bar time as the last one in buffer, ignore it.
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
            for sid, line in list(self.strategy_lines.items()):
                opened = False
                proposed_ctx: Optional[EntryContext] = None

                for trig in self.triggers:
                    proposed_ctx = trig(self, sid, line, bar)
                    if proposed_ctx is not None:
                        break

                if proposed_ctx is None:
                    continue

                allow, reason = self._filters_allow_entry(proposed_ctx)
                if allow:
                    print(f"[StrategyV2] Triggered on {bar.get('tf')} timeframe!")
                    trade = self._build_trade_from_context(proposed_ctx)
                    self._store_and_emit_open(trade)
                    opened = True
                
                self._maybe_remove_line(sid, opened)