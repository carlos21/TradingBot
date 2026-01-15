from typing import Any, Dict, Optional, List
from src.strategies.entry_context import EntryContext, EntryTrigger

def trigger_with_timeframes(trigger_func: EntryTrigger, timeframes: List[str]) -> EntryTrigger:
    """
    Returns a new trigger that only executes if the bar's timeframe is in the allowed list.
    """
    def wrapper(strategy, line_id, line, bar) -> Optional[EntryContext]:
        if bar.get('tf') not in timeframes:
            return None
        return trigger_func(strategy, line_id, line, bar)
    
    wrapper.__name__ = trigger_func.__name__
    return wrapper

def wick_near_line_trigger(
    strategy: "LiquidityStrategyV2",
    line_id: Any,
    line: Dict[str, Any],
    bar: Dict[str, Any],
) -> Optional[EntryContext]:
    
    dir_ = line.get("direction")
    if dir_ is None: return None

    lvl = line["level"]
    
    # 1. Check for Line Interaction (Touch)
    if not (bar["high"] >= lvl >= bar["low"]):
        return None

    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    rng = h - l
    if rng <= 0: return None

    body = abs(c - o)
    body_ratio = body / rng
    cfg = strategy.candle_config

    # 2. Check Body Size
    if body_ratio > cfg.small_body_max_ratio:
        strategy.log_decision(
            bar['time'], bar.get('tf'), line_id, "WICK_FAIL", 
            f"Body too big: {body_ratio:.2f} > Max {cfg.small_body_max_ratio}"
        )
        return None

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    upper_ratio = upper_wick / rng
    lower_ratio = lower_wick / rng

    # 3. Check Wick Size based on direction
    if dir_ == "long":
        if lower_ratio < cfg.wick_min_ratio:
            strategy.log_decision(
                bar['time'], bar.get('tf'), line_id, "WICK_FAIL", 
                f"Long wick too small: {lower_ratio:.2f} < Min {cfg.wick_min_ratio}"
            )
            return None
        true_extreme = min(line['extreme'], l)
        cross_depth = max(0.0, lvl - true_extreme)
    else:
        if upper_ratio < cfg.wick_min_ratio:
            strategy.log_decision(
                bar['time'], bar.get('tf'), line_id, "WICK_FAIL", 
                f"Short wick too small: {upper_ratio:.2f} < Min {cfg.wick_min_ratio}"
            )
            return None
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
    
    dir_ = line.get("direction")
    if dir_ is None: return None

    tf = bar.get('tf')
    if not tf: return None

    history = strategy.get_history(tf, 3)
    if len(history) < 3: return None

    c1, c2, c3 = history[0], history[1], history[2]
    lvl = line["level"]
    cfg = strategy.candle_config

    def get_ratios(b):
        rng = b['high'] - b['low']
        if rng <= 0: return 0, 0, 0
        body = abs(b['close'] - b['open'])
        upper = b['high'] - max(b['open'], b['close'])
        lower = min(b['open'], b['close']) - b['low']
        return body/rng, upper/rng, lower/rng

    c2_touches = (c2['high'] >= lvl >= c2['low'])
    
    if not c2_touches:
        return None

    if dir_ == "short":
        b2_ratio, b2_upper, b2_lower = get_ratios(c2)
        if b2_ratio > cfg.hammer_body_max_ratio: 
            strategy.log_decision(bar['time'], tf, line_id, "3C_FAIL", f"C2 Body {b2_ratio:.2f} > Max {cfg.hammer_body_max_ratio}")
            return None
        if b2_upper > cfg.hammer_nose_max_ratio: 
            strategy.log_decision(bar['time'], tf, line_id, "3C_FAIL", f"C2 Nose {b2_upper:.2f} > Max {cfg.hammer_nose_max_ratio}")
            return None

        if c3['close'] >= c3['open']: 
            strategy.log_decision(bar['time'], tf, line_id, "3C_FAIL", "C3 is not Bearish (Red)")
            return None

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

    elif dir_ == "long":
        b2_ratio, b2_upper, b2_lower = get_ratios(c2)
        if b2_ratio > cfg.hammer_body_max_ratio: 
            strategy.log_decision(bar['time'], tf, line_id, "3C_FAIL", f"C2 Body {b2_ratio:.2f} > Max {cfg.hammer_body_max_ratio}")
            return None
        if b2_lower > cfg.hammer_nose_max_ratio: 
            strategy.log_decision(bar['time'], tf, line_id, "3C_FAIL", f"C2 Nose {b2_lower:.2f} > Max {cfg.hammer_nose_max_ratio}")
            return None

        if c3['close'] <= c3['open']: 
            strategy.log_decision(bar['time'], tf, line_id, "3C_FAIL", "C3 is not Bullish (Green)")
            return None

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

def double_5m_cross_trigger(
    strategy: "LiquidityStrategyV2",
    line_id: Any,
    line: Dict[str, Any],
    bar: Dict[str, Any],
) -> Optional[EntryContext]:
    """
    Logic:
    1. Dip below the line (Stage 0 -> 1)
    2. 5m bar closing above the line (Stage 1 -> 2)
    3. Dip below the line (Stage 2 -> 3)
    4. Second 5m bar closing above the line (Stage 3 -> Trigger)
       * MUST have Open < Level (Body Cross)
    """
    
    tf = bar.get('tf', 'unknown')
    dir_ = line.get("direction")
    if dir_ is None: return None

    lvl = line["level"]
    close = bar["close"]
    open_ = bar["open"]
    low = bar["low"]
    high = bar["high"]
    
    # State: d5_stage
    # 0: Waiting for 1st Dip
    # 1: Waiting for 1st Close Back
    # 2: Waiting for 2nd Dip
    # 3: Waiting for 2nd Close Back (Trigger)

    if "d5_stage" not in line:
        line["d5_stage"] = 0

    stage = line["d5_stage"]

    if dir_ == "long":
        # --- LONG LOGIC ---
        
        # 1. Initial Dip
        if stage == 0:
            if low < lvl:
                line["d5_stage"] = 1
                strategy.log_decision(bar['time'], tf, line_id, "D5_STAGE_1", f"1. Initial Dip < {lvl}")
                stage = 1 # Allow fallthrough
        
        # 2. First Close Above
        if stage == 1:
            if close > lvl:
                line["d5_stage"] = 2
                strategy.log_decision(bar['time'], tf, line_id, "D5_STAGE_2", f"2. 1st Close > {lvl}")
                # STOP HERE. Wait for new dip in subsequent bars.
                return None 

        # 3. Second Dip
        if stage == 2:
            if low < lvl:
                line["d5_stage"] = 3
                strategy.log_decision(bar['time'], tf, line_id, "D5_STAGE_3", f"3. 2nd Dip < {lvl}")
                stage = 3 # Allow fallthrough

        # 4. Second Close Above (Trigger)
        if stage == 3:
            # Must Close Above AND Open Below (Body Cross)
            if close > lvl and open_ < lvl:
                # TRIGGER
                true_extreme = min(line['extreme'], low)
                cross_depth = max(0.0, lvl - true_extreme)
                
                line["d5_stage"] = 0 # Reset
                
                return EntryContext(
                    strategy=strategy,
                    line_id=line_id,
                    direction="long",
                    level=lvl,
                    bar=bar,
                    close=close,
                    low=low,
                    high=high,
                    extreme=true_extreme,
                    cross_depth=cross_depth
                )

    elif dir_ == "short":
        # --- SHORT LOGIC ---

        # 1. Initial Pop
        if stage == 0:
            if high > lvl:
                line["d5_stage"] = 1
                strategy.log_decision(bar['time'], tf, line_id, "D5_STAGE_1", f"1. Initial Pop > {lvl}")
                stage = 1

        # 2. First Close Below
        if stage == 1:
            if close < lvl:
                line["d5_stage"] = 2
                strategy.log_decision(bar['time'], tf, line_id, "D5_STAGE_2", f"2. 1st Close < {lvl}")
                # STOP HERE.
                return None

        # 3. Second Pop
        if stage == 2:
            if high > lvl:
                line["d5_stage"] = 3
                strategy.log_decision(bar['time'], tf, line_id, "D5_STAGE_3", f"3. 2nd Pop > {lvl}")
                stage = 3

        # 4. Second Close Below (Trigger)
        if stage == 3:
            # Must Close Below AND Open Above (Body Cross)
            if close < lvl and open_ > lvl:
                # TRIGGER
                true_extreme = max(line['extreme'], high)
                cross_depth = max(0.0, true_extreme - lvl)
                
                line["d5_stage"] = 0

                return EntryContext(
                    strategy=strategy,
                    line_id=line_id,
                    direction="short",
                    level=lvl,
                    bar=bar,
                    close=close,
                    low=low,
                    high=high,
                    extreme=true_extreme,
                    cross_depth=cross_depth
                )

    return None

def tsi_cross_trigger(
    strategy: "LiquidityStrategyV2",
    line_id: Any,
    line: Dict[str, Any],
    bar: Dict[str, Any],
) -> Optional[EntryContext]:
    """
    TSI Trigger:
    - Long: Price dipped below line -> Wait for TSI (Blue) to cross ABOVE Signal (Red).
    - Short: Price popped above line -> Wait for TSI (Blue) to cross BELOW Signal (Red).
    
    Config: Long=6, Short=13, Signal=4
    """
    dir_ = line.get("direction")
    if dir_ is None: return None

    tf = bar.get('tf')
    if not tf: return None

    # 1. Fetch History
    # We need enough bars for EMA convergence. 
    # Strategy buffer is now 100, which is sufficient.
    history = strategy.get_history(tf, 100) 
    if len(history) < 30: 
        return None

    # 2. Prepare Data
    closes = [b['close'] for b in history]
    
    # User Config: tsi_long=6, tsi_short=13, tsi_signal=4
    # Note: Standard TSI usually has Long > Short (e.g. 25, 13), 
    # but we strictly follow the requested variables.
    TSI_LONG = 6
    TSI_SHORT = 13
    TSI_SIGNAL = 4

    tsi_line, sig_line = _calculate_tsi_series(closes, TSI_LONG, TSI_SHORT, TSI_SIGNAL)

    if len(tsi_line) < 2 or len(sig_line) < 2:
        return None

    # Current values (index -1) and Previous values (index -2)
    curr_tsi = tsi_line[-1]
    curr_sig = sig_line[-1]
    prev_tsi = tsi_line[-2]
    prev_sig = sig_line[-2]

    lvl = line["level"]
    
    # 3. Check Logic
    if dir_ == "long":
        # We are looking for a Bullish Cross (Blue crosses ABOVE Red)
        # AND the price must have dipped below the line (handled by strategy latching)
        
        # Check Cross: Previous TSI < Signal AND Current TSI > Signal
        bullish_cross = (prev_tsi <= prev_sig) and (curr_tsi > curr_sig)
        
        if bullish_cross:
            # Calculate Risk/Extreme
            # For long, extreme is the lowest low seen since the line was latched
            true_extreme = min(line['extreme'], bar['low'])
            cross_depth = max(0.0, lvl - true_extreme)
            
            strategy.log_decision(bar['time'], tf, line_id, "TSI_CROSS", 
                f"Long Trigger: TSI({curr_tsi:.2f}) > Sig({curr_sig:.2f})")

            return EntryContext(
                strategy=strategy,
                line_id=line_id,
                direction="long",
                level=lvl,
                bar=bar,
                close=bar['close'],
                low=bar['low'],
                high=bar['high'],
                extreme=true_extreme,
                cross_depth=cross_depth
            )

    elif dir_ == "short":
        # We are looking for a Bearish Cross (Blue crosses BELOW Red)
        
        # Check Cross: Previous TSI > Signal AND Current TSI < Signal
        bearish_cross = (prev_tsi >= prev_sig) and (curr_tsi < curr_sig)

        if bearish_cross:
            true_extreme = max(line['extreme'], bar['high'])
            cross_depth = max(0.0, true_extreme - lvl)

            strategy.log_decision(bar['time'], tf, line_id, "TSI_CROSS", 
                f"Short Trigger: TSI({curr_tsi:.2f}) < Sig({curr_sig:.2f})")

            return EntryContext(
                strategy=strategy,
                line_id=line_id,
                direction="short",
                level=lvl,
                bar=bar,
                close=bar['close'],
                low=bar['low'],
                high=bar['high'],
                extreme=true_extreme,
                cross_depth=cross_depth
            )

    return None


# --- HELPER FUNCTIONS FOR TSI ---

def _calculate_ema(values: List[float], length: int) -> List[float]:
    """Calculates EMA for a list of floats."""
    if not values:
        return []
    
    alpha = 2 / (length + 1)
    ema_values = [values[0]] # Seed with first value (simple SMA equivalent for 1 item)
    
    for price in values[1:]:
        prev_ema = ema_values[-1]
        new_ema = (price * alpha) + (prev_ema * (1 - alpha))
        ema_values.append(new_ema)
        
    return ema_values

def _calculate_tsi_series(closes: List[float], long_len: int, short_len: int, sig_len: int):
    """
    Returns tuple of lists: (tsi_values, signal_values)
    TSI = 100 * (DoubleSmoothedPC / DoubleSmoothedAbsPC)
    """
    if len(closes) < long_len + short_len + sig_len:
        return [], []

    # 1. Momentum (Price Change)
    pc = [0.0] * len(closes)
    abs_pc = [0.0] * len(closes)
    
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        pc[i] = diff
        abs_pc[i] = abs(diff)

    # 2. Double Smoothing
    # First Smoothing (Long Length)
    ema_pc_1 = _calculate_ema(pc, long_len)
    ema_apc_1 = _calculate_ema(abs_pc, long_len)

    # Second Smoothing (Short Length)
    ema_pc_2 = _calculate_ema(ema_pc_1, short_len)
    ema_apc_2 = _calculate_ema(ema_apc_1, short_len)

    # 3. Calculate TSI
    tsi_values = []
    for val, abs_val in zip(ema_pc_2, ema_apc_2):
        if abs_val == 0:
            tsi_values.append(0.0)
        else:
            tsi_values.append(100.0 * (val / abs_val))

    # 4. Calculate Signal Line
    signal_values = _calculate_ema(tsi_values, sig_len)

    return tsi_values, signal_values
