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
    """
    Original single-candle wick trigger.
    """
    lvl = line["level"]
    dir_ = line["direction"]

    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    rng = h - l
    if rng <= 0:
        return None

    body = abs(c - o)
    body_ratio = body / rng

    # Use strategy config
    small_body_max_ratio = getattr(strategy, "small_body_max_ratio", 0.25)
    wick_min_ratio = getattr(strategy, "wick_min_ratio", 0.60)

    if body_ratio > small_body_max_ratio:
        return None

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    upper_ratio = upper_wick / rng
    lower_ratio = lower_wick / rng

    if dir_ == "long":
        # Long needs big lower wick
        if lower_ratio < wick_min_ratio:
            return None
        extreme = l
    else:
        # Short needs big upper wick
        if upper_ratio < wick_min_ratio:
            return None
        extreme = h

    min_dist_to_line = min(abs(lvl - x) for x in (o, h, l, c))

    return EntryContext(
        strategy=strategy,
        line_id=line_id,
        direction=dir_,
        level=lvl,
        bar=bar,
        close=c,
        low=l,
        high=h,
        extreme=extreme,
        cross_depth=min_dist_to_line,
    )


def three_candle_reversal_trigger(
    strategy: "LiquidityStrategyV2",
    line_id: Any,
    line: Dict[str, Any],
    bar: Dict[str, Any],
) -> Optional[EntryContext]:
    """
    3-Candle Pattern Trigger.
    
    SHORT ENTRY (Bearish Reversal):
      1. Green Candle, Big Body.
      2. Hammer (Body at Top), Small Upper Wick. MUST CROSS LINE.
      3. Red Candle (Current Bar).
      
    LONG ENTRY (Bullish Reversal):
      1. Red Candle, Big Body.
      2. Hammer (Body at Bottom), Small Lower Wick. MUST CROSS LINE.
      3. Green Candle (Current Bar).
    """
    tf = bar.get('tf')
    if not tf:
        return None

    # Get last 3 bars: [Index -3, Index -2, Current]
    history = strategy.get_history(tf, 3)
    if len(history) < 3:
        return None

    c1, c2, c3 = history[0], history[1], history[2] # c3 is 'bar'
    
    lvl = line["level"]
    dir_ = line["direction"]

    # Configs
    big_body_min_ratio = getattr(strategy, "big_body_min_ratio", 0.50)
    hammer_body_max_ratio = getattr(strategy, "hammer_body_max_ratio", 0.30)
    hammer_nose_max_ratio = getattr(strategy, "hammer_nose_max_ratio", 0.10) 

    # Helper to calc ratios
    def get_ratios(b):
        rng = b['high'] - b['low']
        if rng <= 0: return 0, 0, 0
        body = abs(b['close'] - b['open'])
        upper = b['high'] - max(b['open'], b['close'])
        lower = min(b['open'], b['close']) - b['low']
        return body/rng, upper/rng, lower/rng

    # --- SHORT LOGIC ---
    if dir_ == "short":
        # 1. First Candle: Green + Big Body
        if c1['close'] <= c1['open']: return None
        b1_ratio, _, _ = get_ratios(c1)
        if b1_ratio < big_body_min_ratio: return None

        # 2. Second Candle: Hammer (Body at Top)
        b2_ratio, b2_upper, b2_lower = get_ratios(c2)
        if b2_ratio > hammer_body_max_ratio: return None
        if b2_upper > hammer_nose_max_ratio: return None

        # 3. Line Interaction: Hammer MUST go through the line
        #    (High >= Level >= Low)
        if not (c2['high'] >= lvl >= c2['low']):
            return None

        # 4. Third Candle: Red
        if c3['close'] >= c3['open']: return None

        # Extreme for SL is the High of the pattern
        extreme = max(c1['high'], c2['high'], c3['high'])
        
        # Cross depth is 0 because we enforced crossing, but we can pass the overlap amount or 0
        return EntryContext(
            strategy=strategy,
            line_id=line_id,
            direction="short",
            level=lvl,
            bar=c3,
            close=c3['close'],
            low=c3['low'],
            high=c3['high'],
            extreme=extreme,
            cross_depth=0.0 
        )

    # --- LONG LOGIC ---
    elif dir_ == "long":
        # 1. First Candle: Red + Big Body
        if c1['close'] >= c1['open']: return None
        b1_ratio, _, _ = get_ratios(c1)
        if b1_ratio < big_body_min_ratio: return None

        # 2. Second Candle: Hammer (Body at Bottom)
        b2_ratio, b2_upper, b2_lower = get_ratios(c2)
        if b2_ratio > hammer_body_max_ratio: return None
        if b2_lower > hammer_nose_max_ratio: return None

        # 3. Line Interaction: Hammer MUST go through the line
        if not (c2['high'] >= lvl >= c2['low']):
            return None

        # 4. Third Candle: Green
        if c3['close'] <= c3['open']: return None

        # Extreme for SL is the Low of the pattern
        extreme = min(c1['low'], c2['low'], c3['low'])

        return EntryContext(
            strategy=strategy,
            line_id=line_id,
            direction="long",
            level=lvl,
            bar=c3,
            close=c3['close'],
            low=c3['low'],
            high=c3['high'],
            extreme=extreme,
            cross_depth=0.0
        )

    return None


class LiquidityStrategyV2(BaseLiquidityStrategy):
    """
    Variant that checks MULTIPLE timeframes for patterns.
    Now supports history tracking for multi-candle patterns.
    """

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
        # Configs for Wick Trigger
        small_body_max_ratio: float = 0.25,
        wick_min_ratio: float = 0.60,
        # Configs for 3-Candle Trigger
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

        # Ratios
        self.small_body_max_ratio = small_body_max_ratio
        self.wick_min_ratio = wick_min_ratio
        self.big_body_min_ratio = big_body_min_ratio
        self.hammer_body_max_ratio = hammer_body_max_ratio
        self.hammer_nose_max_ratio = hammer_nose_max_ratio

        # Triggers: Add the new one to the list
        self.triggers = list(self.options.triggers or self.default_triggers())

        # Aggregation State
        self._tf_aggregators = {}
        # History Storage: { '5m': deque([...]), '15m': deque([...]) }
        self._tf_histories = {}

        for tf in self.timeframes:
            self._tf_aggregators[tf] = {
                "seconds": self._parse_tf_seconds(tf),
                "buf": [],
                "start": None
            }
            # Keep last 10 bars for pattern recognition
            self._tf_histories[tf] = deque(maxlen=10)

    def default_triggers(self) -> List[EntryTrigger]:
        # Enable BOTH triggers by default
        return [wick_near_line_trigger, three_candle_reversal_trigger]

    def _parse_tf_seconds(self, tf: str) -> int:
        unit = tf[-1].lower()
        val = int(tf[:-1])
        if unit == 'm': return val * 60
        if unit == 'h': return val * 3600
        if unit == 'd': return val * 86400
        return val

    def get_history(self, tf: str, count: int) -> List[Dict[str, Any]]:
        """Returns the last 'count' bars for the given timeframe."""
        hist = self._tf_histories.get(tf, [])
        if len(hist) < count:
            return []
        return list(hist)[-count:]

    def on_raw_bar(self, bar: Dict[str, Any]):
        with self.lock:
            self._check_open_trades(bar)

        ts = bar["time"]
        
        for tf, state in self._tf_aggregators.items():
            window_secs = state["seconds"]
            window_start = (ts // window_secs) * window_secs

            if state["start"] is None:
                state["start"] = window_start

            if window_start == state["start"]:
                state["buf"].append(bar)
            else:
                # Window closed
                if state["buf"]:
                    agg_bar = self._aggregate_bars(state["buf"], state["start"], window_secs)
                    agg_bar['tf'] = tf 
                    
                    # 1. Store in history
                    self._tf_histories[tf].append(agg_bar)

                    # 2. Run strategy
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