# (path: src/strategies/liquidity_strategy_v2.py)

from __future__ import annotations
from typing import Any, Dict, List, Optional
from collections import deque
from src.services.trade_manager import TradeManager
from src.strategies.base_liquidity_strategy import BaseLiquidityStrategy, StrategyOptions
from src.financial_calc import FinancialCalc
from src.strategies.entry_context import EntryContext, EntryTrigger
from src.strategies.strategy_config import CandleConfig
from src.strategies.triggers import _calculate_tsi_series, RESCUE_TSI_TIMEFRAME
from src.utils.app_logger import ILogger


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
        point_value: float,
        account_balance: float,
        risk_per_trade: Optional[float] = None,
        risk_pct_per_trade: Optional[float] = None,
        fixed_stop_loss: Optional[float] = None,
        max_stop_loss: Optional[float] = None,
        timeframes: List[str] = None,
        options: Optional[StrategyOptions] = None,
        htf_fetcher=None,
        candle_config: Optional[CandleConfig] = None,
        sl_levels: Optional[List[float]] = None,
        sl_level_tolerance: float = 5.0,
        min_cross_depth: float = 0.0,
        rr_ratio: float = 5.0,
        use_fractional_lots: bool = False,
        fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
        broker_spread: float = 0.0,
        trade_logger=None,
        analytics=None,
        trigger_state_repo=None,
        logger: ILogger = None,
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
            sl_levels=sl_levels,
            sl_level_tolerance=sl_level_tolerance,
            min_cross_depth=min_cross_depth,
            rr_ratio=rr_ratio,
            point_value=point_value,
            account_balance=account_balance,
            risk_per_trade=risk_per_trade,
            risk_pct_per_trade=risk_pct_per_trade,
            use_fractional_lots=use_fractional_lots,
            fee_per_rt=fee_per_rt,
            broker_spread=broker_spread,
            trade_logger=trade_logger,
            analytics=analytics,
            trigger_state_repo=trigger_state_repo,
            logger=logger,
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

    def reset(self, preserve_trigger_state: bool = False):
        with self.lock:
            if not preserve_trigger_state:
                for line_id in list(self.strategy_lines.keys()):
                    self.trigger_state_repo.delete(str(line_id))
            self.strategy_lines.clear()
            self.open_trades.clear()
            self._reentry_opportunities.clear()
            self.trade_manager.open_trades.clear()
            self.decision_logs.clear()
            for tf in self._internal_timeframes:
                self._tf_aggregators[tf] = {
                    "seconds": self._parse_tf_seconds(tf),
                    "buf": [],
                    "start": None
                }
                self._tf_histories[tf].clear()
            self.logger.info("[StrategyV2] Internal state fully reset.")

    def _reset_trigger_state(self, line_state: Dict[str, Any]):
        """Reset trigger-specific state only, preserving direction and extreme."""
        if "d5_stage" in line_state:
            line_state["d5_stage"] = 0
        if "tsi_stage" in line_state:
            line_state["tsi_stage"] = 0
            line_state["tsi_ref_price"] = 0.0
            line_state.pop("tsi_reset_occurred", None)
        if "vat_5m_stage" in line_state:
            line_state["vat_5m_stage"] = 0
            line_state["vat_5m_reset"] = False
        line_state.pop("vat_regime", None)
        line_state.pop("vat_velocity", None)

    def _reset_line_state(self, line_state: Dict[str, Any]):
        super()._reset_line_state(line_state)
        self._reset_trigger_state(line_state)

    def _parse_tf_seconds(self, tf: str) -> int:
        from src.utils.bar_aggregator import BarAggregator
        return BarAggregator.parse_timeframe(tf)

    def get_history(self, tf: str, count: int) -> List[Dict[str, Any]]:
        hist = self._tf_histories.get(tf, [])
        return list(hist)[-count:] if hist else []

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

            if self.options.breakeven or self.options.reentry_breakeven:
                self._check_breakeven(bar)

            if (self.options.reentry_after_sl or self.options.reentry_only) and self._reentry_opportunities:
                self._check_reentry_opportunities(bar)
            
            current_price = bar['close']
            bar_time = bar['time']
            lines_to_remove = set()

            for sid, line in self.strategy_lines.items():
                creation_ts = line.get('creation_ts', 0)
                if creation_ts > bar_time:
                    continue

                if line['direction'] is None:
                    lvl = line['level']
                    if current_price < lvl:
                        # Potential short: close is below the line.
                        # Accumulate the highest high seen while close stays below the line.
                        if line.get('_pending_dir') != 'short':
                            line['_pending_dir'] = 'short'
                            line['_pending_extreme'] = bar['high']
                        else:
                            line['_pending_extreme'] = max(line['_pending_extreme'], bar['high'])
                        pending_ext = line['_pending_extreme']
                        # If high never crossed the line (price was already below when line drawn),
                        # latch immediately. Otherwise require min_cross_depth penetration.
                        if pending_ext <= lvl or pending_ext - lvl >= self.min_cross_depth:
                            line['direction'] = 'short'
                            line['extreme'] = line.pop('_pending_extreme')
                            line.pop('_pending_dir', None)
                            depth = pending_ext - lvl if pending_ext > lvl else 0.0
                            self.log_decision(bar_time, "1m", sid, "LATCH", f"Latched short @ {current_price} (depth={depth:.2f})")
                            self.analytics.capture_signal_event("LATCH", {"line_id": sid, "direction": "short", "level": lvl, "depth": depth})
                    elif current_price > lvl:
                        # Potential long: close is above the line.
                        # Accumulate the lowest low seen while close stays above the line.
                        if line.get('_pending_dir') != 'long':
                            line['_pending_dir'] = 'long'
                            line['_pending_extreme'] = bar['low']
                        else:
                            line['_pending_extreme'] = min(line['_pending_extreme'], bar['low'])
                        pending_ext = line['_pending_extreme']
                        # If low never crossed the line (bounce-off support or price already above),
                        # latch immediately. Otherwise require min_cross_depth penetration.
                        if pending_ext >= lvl or lvl - pending_ext >= self.min_cross_depth:
                            line['direction'] = 'long'
                            line['extreme'] = line.pop('_pending_extreme')
                            line.pop('_pending_dir', None)
                            depth = lvl - pending_ext if pending_ext < lvl else 0.0
                            self.log_decision(bar_time, "1m", sid, "LATCH", f"Latched long @ {current_price} (depth={depth:.2f})")
                            self.analytics.capture_signal_event("LATCH", {"line_id": sid, "direction": "long", "level": lvl, "depth": depth})
                
                elif line['direction'] == 'short':
                    line['extreme'] = max(line['extreme'], bar['high'])
                    if current_price > (line['level'] + self.max_bounce):
                        msg = f"Price {current_price} > {line['level'] + self.max_bounce} (Max Bounce)"
                        self.log_decision(bar_time, "1m", sid, "REMOVE", msg)
                        self.analytics.capture_signal_event("LINE_REMOVE", {"line_id": sid, "reason": "max_bounce", "level": line['level']})
                        lines_to_remove.add(sid)

                elif line['direction'] == 'long':
                    line['extreme'] = min(line['extreme'], bar['low'])
                    if current_price < (line['level'] - self.max_bounce):
                        msg = f"Price {current_price} < {line['level'] - self.max_bounce} (Max Bounce)"
                        self.log_decision(bar_time, "1m", sid, "REMOVE", msg)
                        self.analytics.capture_signal_event("LINE_REMOVE", {"line_id": sid, "reason": "max_bounce", "level": line['level']})
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

        self._persist_all_line_states()

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

                allow, reason, hold = self._filters_allow_entry(proposed_ctx)

                if allow:
                    self.log_decision(bar['time'], bar.get('tf'), sid, "ENTRY",
                        f"Trigger: {trigger_name} | Dir: {proposed_ctx.direction} | Price: {proposed_ctx.close}")

                    trade = self._build_trade_from_context(proposed_ctx)
                    trade['tf'] = bar.get('tf', '1m')
                    trade['velocity_regime'] = line.get('vat_regime', '')
                    if self.options.reentry_only:
                        trade["is_phantom"] = True
                    self._store_and_emit_open(trade)
                    self.analytics.capture_signal_event("ENTRY_SIGNAL", {
                        "line_id": sid, "trigger": trigger_name,
                        "direction": proposed_ctx.direction, "price": proposed_ctx.close,
                    })
                    if self.trade_logger:
                        self.trade_logger.log(trade['trade_id'], "SIGNAL",
                            f"{trigger_name} on {bar.get('tf', '1m')}, {proposed_ctx.direction} @ {proposed_ctx.close:.2f}")
                        self.trade_logger.log(trade['trade_id'], "OPEN",
                            f"Entry={trade['entry']:.2f} SL={trade['stop_loss']:.2f} TP={trade['take_profit']:.2f} Risk={trade['risk']:.2f}")
                    opened = True
                else:
                    self.log_decision(bar['time'], bar.get('tf'), sid, "FILTER_BLOCK",
                        f"Trigger: {trigger_name} | Reason: {reason}")
                    self.analytics.capture_signal_event("FILTER_BLOCK", {
                        "line_id": sid, "trigger": trigger_name, "reason": reason,
                    })
                    if hold:
                        # depth insufficient — reset trigger so it can re-fire, keep line alive
                        self._reset_trigger_state(line)
                        continue

                self._maybe_remove_line(sid, opened)


class LiveLiquidityStrategyV2(LiquidityStrategyV2):
    """Live mode variant — NinjaTrader handles all SL/TP/session-end closes."""

    def _check_open_trades(self, bar: Dict[str, Any]):
        pass  # NinjaTrader is source of truth for SL/TP

    def _check_session_end_close(self, bar: Dict[str, Any]):
        pass  # Wiring layer sends close commands to NinjaTrader