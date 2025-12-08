from __future__ import annotations
from typing import Any, Dict, List, Optional
from collections import deque
from src.strategies.base_liquidity_strategy import BaseLiquidityStrategy, StrategyOptions
from src.strategies.entry_context import EntryContext, EntryTrigger


def wick_near_line_trigger(
    strategy: "LiquidityStrategyV2",
    line_id: Any,
    line: Dict[str, Any],
    bar: Dict[str, Any],
) -> Optional[EntryContext]:
    
    # 1. Get Fixed Direction
    dir_ = line.get("direction")
    if dir_ is None: return None

    lvl = line["level"]
    
    # 2. Check for Line Interaction (Touch)
    if not (bar["high"] >= lvl >= bar["low"]):
        return None

    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    rng = h - l
    if rng <= 0: return None

    body = abs(c - o)
    body_ratio = body / rng

    small_body_max_ratio = getattr(strategy, "small_body_max_ratio", 0.25)
    wick_min_ratio = getattr(strategy, "wick_min_ratio", 0.60)

    if body_ratio > small_body_max_ratio: return None

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    upper_ratio = upper_wick / rng
    lower_ratio = lower_wick / rng

    # 3. Use Fixed Direction Logic & Tracked Extreme
    if dir_ == "long":
        # Support bounce -> Needs big lower wick
        if lower_ratio < wick_min_ratio: return None
        true_extreme = min(line['extreme'], l)
        cross_depth = max(0.0, lvl - true_extreme)
    else:
        # Resistance reject -> Needs big upper wick
        if upper_ratio < wick_min_ratio: return None
        true_extreme = max(line['extreme'], h)
        cross_depth = max(0.0, true_extreme - lvl)

    return EntryContext(
        strategy=strategy,
        line_id=line_id,
        direction=dir_,
        level=lvl,
        bar=bar,
        close=c,
        low=l,
        high=h,
        extreme=true_extreme,
        cross_depth=cross_depth,
    )


def three_candle_reversal_trigger(
    strategy: "LiquidityStrategyV2",
    line_id: Any,
    line: Dict[str, Any],
    bar: Dict[str, Any],
) -> Optional[EntryContext]:
    
    # 1. Get Fixed Direction
    dir_ = line.get("direction")
    if dir_ is None: return None

    tf = bar.get('tf')
    if not tf: return None

    history = strategy.get_history(tf, 3)
    if len(history) < 3: return None

    c1, c2, c3 = history[0], history[1], history[2]
    lvl = line["level"]

    # Configs
    big_body_min_ratio = getattr(strategy, "big_body_min_ratio", 0.50)
    hammer_body_max_ratio = getattr(strategy, "hammer_body_max_ratio", 0.30)
    hammer_nose_max_ratio = getattr(strategy, "hammer_nose_max_ratio", 0.10) 

    def get_ratios(b):
        rng = b['high'] - b['low']
        if rng <= 0: return 0, 0, 0
        body = abs(b['close'] - b['open'])
        upper = b['high'] - max(b['open'], b['close'])
        lower = min(b['open'], b['close']) - b['low']
        return body/rng, upper/rng, lower/rng

    # --- SHORT LOGIC (Fixed) ---
    if dir_ == "short":
        # 1. Green Candle
        if c1['close'] <= c1['open']: return None
        b1_ratio, _, _ = get_ratios(c1)
        if b1_ratio < big_body_min_ratio: return None

        # 2. Hammer at Top
        b2_ratio, b2_upper, b2_lower = get_ratios(c2)
        if b2_ratio > hammer_body_max_ratio: return None
        if b2_upper > hammer_nose_max_ratio: return None

        # 3. Hammer MUST touch the line
        if not (c2['high'] >= lvl >= c2['low']): return None

        # 4. Red Candle
        if c3['close'] >= c3['open']: return None

        # Extreme
        pattern_high = max(c1['high'], c2['high'], c3['high'])
        true_extreme = max(line['extreme'], pattern_high)
        cross_depth = max(0.0, true_extreme - lvl)
        
        return EntryContext(
            strategy=strategy,
            line_id=line_id,
            direction="short",
            level=lvl,
            bar=c3,
            close=c3['close'],
            low=c3['low'],
            high=c3['high'],
            extreme=true_extreme,
            cross_depth=cross_depth 
        )

    # --- LONG LOGIC (Fixed) ---
    elif dir_ == "long":
        # 1. Red Candle
        if c1['close'] >= c1['open']: return None
        b1_ratio, _, _ = get_ratios(c1)
        if b1_ratio < big_body_min_ratio: return None

        # 2. Hammer at Bottom
        b2_ratio, b2_upper, b2_lower = get_ratios(c2)
        if b2_ratio > hammer_body_max_ratio: return None
        if b2_lower > hammer_nose_max_ratio: return None

        # 3. Hammer MUST touch the line
        if not (c2['high'] >= lvl >= c2['low']): return None

        # 4. Green Candle
        if c3['close'] <= c3['open']: return None

        # Extreme
        pattern_low = min(c1['low'], c2['low'], c3['low'])
        true_extreme = min(line['extreme'], pattern_low)
        cross_depth = max(0.0, lvl - true_extreme)

        return EntryContext(
            strategy=strategy,
            line_id=line_id,
            direction="long",
            level=lvl,
            bar=c3,
            close=c3['close'],
            low=c3['low'],
            high=c3['high'],
            extreme=true_extreme,
            cross_depth=cross_depth
        )

    return None


class LiquidityStrategyV2(BaseLiquidityStrategy):
    def __init__(
        self,
        min_stop_loss: float,
        max_bounce: float,
        socketio,
        line_repository,
        trade_repository,
        extra_sl_space: float,
        timeframes: List[str] = None,
        options: Optional[StrategyOptions] = None,
        htf_fetcher=None,
        *,
        small_body_max_ratio: float = 0.25,
        wick_min_ratio: float = 0.60,
        big_body_min_ratio: float = 0.50,
        hammer_body_max_ratio: float = 0.30,
        hammer_nose_max_ratio: float = 0.15, 
    ):
        self.timeframes = timeframes or ["5m"]
        
        super().__init__(
            min_stop_loss=min_stop_loss,
            max_bounce=max_bounce,
            socketio=socketio,
            line_repository=line_repository,
            trade_repository=trade_repository,
            extra_sl_space=extra_sl_space,
            strategy_tf=self.timeframes[0], 
            options=options,
            htf_fetcher=htf_fetcher,
        )

        self.small_body_max_ratio = small_body_max_ratio
        self.wick_min_ratio = wick_min_ratio
        self.big_body_min_ratio = big_body_min_ratio
        self.hammer_body_max_ratio = hammer_body_max_ratio
        self.hammer_nose_max_ratio = hammer_nose_max_ratio

        self.triggers = list(self.options.triggers or self.default_triggers())

        self._tf_aggregators = {}
        self._tf_histories = {}

        for tf in self.timeframes:
            self._tf_aggregators[tf] = {
                "seconds": self._parse_tf_seconds(tf),
                "buf": [],
                "start": None
            }
            self._tf_histories[tf] = deque(maxlen=10)

    def default_triggers(self) -> List[EntryTrigger]:
        return [wick_near_line_trigger, three_candle_reversal_trigger]

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

                active_triggers = self.triggers or self.default_triggers()

                for trig in active_triggers:
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