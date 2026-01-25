from typing import Any, Dict, Optional, List
from src.strategies.entry_context import EntryContext, EntryTrigger

# --- CONFIGURATION ---
FAST_MOVE_LOOKBACK  = 5    # Check the last 5 bars (15m)
FAST_MOVE_THRESHOLD = 3.0  # Fast if price moved > 120pts (assuming 40pt avg)

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

def _calculate_velocity_score(history: List[Dict[str, Any]], lookback: int) -> float:
    """
    Calculates a 'Velocity Score' to detect fast moves.
    Score = (Net Displacement) / (Average Candle Range)
    """
    if len(history) < lookback:
        return 0.0
    
    subset = history[-lookback:]
    total_range = sum(b['high'] - b['low'] for b in subset)
    avg_range = total_range / lookback if lookback > 0 else 1.0
    
    if avg_range == 0: return 0.0

    start_open = subset[0]['open']
    end_close  = subset[-1]['close']
    displacement = end_close - start_open
    
    return displacement / avg_range

def tsi_cross_trigger(
    strategy: "LiquidityStrategyV2",
    line_id: Any,
    line: Dict[str, Any],
    bar: Dict[str, Any],
) -> Optional[EntryContext]:
    """
    TSI Trigger with "Fast Move" Protection.
    
    Stages:
    0: Waiting for initial TSI Cross.
    1: Protection Mode (Fast Move Detected).
       Exit via:
       A) Sweep of Ref Price -> Go to Stage 2.
       B) 3m TSI Cross -> Trigger Entry Immediately.
    2: Swept -> Waiting for Reclaim (price to close back inside).
    """
    dir_ = line.get("direction")
    if dir_ is None: return None

    lvl = line["level"]
    
    if "tsi_stage" not in line:
        line["tsi_stage"] = 0       
        line["tsi_ref_price"] = 0.0 

    # 1. Interaction Check
    if dir_ == "long":
        if line['extreme'] > lvl: return None
    elif dir_ == "short":
        if line['extreme'] < lvl: return None

    tf = bar.get('tf')
    if not tf: return None

    # 2. Fetch History & Calculate TSI (Current Timeframe)
    history = strategy.get_history(tf, 100) 
    if len(history) < 30: return None

    closes = [b['close'] for b in history]
    tsi_line, sig_line = _calculate_tsi_series(closes, 6, 13, 4)

    if len(tsi_line) < 2 or len(sig_line) < 2: return None

    curr_tsi = tsi_line[-1]
    curr_sig = sig_line[-1]
    prev_tsi = tsi_line[-2]
    prev_sig = sig_line[-2]

    # --- LOGIC FLOW ---

    # STAGE 0: Waiting for the Cross
    if line["tsi_stage"] == 0:
        has_crossed = False
        
        if dir_ == "long":
            if (prev_tsi <= prev_sig) and (curr_tsi > curr_sig): has_crossed = True
        elif dir_ == "short":
            if (prev_tsi >= prev_sig) and (curr_tsi < curr_sig): has_crossed = True
        
        if has_crossed:
            # Fast Move Check
            hist_15m = strategy.get_history("15m", FAST_MOVE_LOOKBACK + 5)
            velocity_score = _calculate_velocity_score(hist_15m, FAST_MOVE_LOOKBACK)
            
            is_fast = False
            if dir_ == "long" and velocity_score < -FAST_MOVE_THRESHOLD: is_fast = True
            elif dir_ == "short" and velocity_score > FAST_MOVE_THRESHOLD: is_fast = True

            if is_fast:
                line["tsi_stage"] = 1
                line["tsi_ref_price"] = bar["low"] if dir_ == "long" else bar["high"]
                strategy.log_decision(bar['time'], tf, line_id, "TSI_FAST", 
                    f"Fast Move ({velocity_score:.2f}). Protection Mode ON. Wait for Sweep OR 3m TSI.")
                return None
            else:
                return _build_tsi_context(strategy, line_id, line, bar, lvl, dir_, curr_tsi, curr_sig)

    # STAGE 1: Protection Mode (Wait for Sweep OR 3m TSI Rescue)
    elif line["tsi_stage"] == 1:
        
        # --- A) CHECK 3m TSI RESCUE ---
        hist_3m = strategy.get_history("3m", 50)
        # Only proceed if we have enough 3m data
        if len(hist_3m) >= 30:
            closes_3m = [b['close'] for b in hist_3m]
            tsi_3m, sig_3m = _calculate_tsi_series(closes_3m, 6, 13, 4)
            
            if len(tsi_3m) >= 2:
                t3_curr, t3_prev = tsi_3m[-1], tsi_3m[-2]
                s3_curr, s3_prev = sig_3m[-1], sig_3m[-2]
                
                rescue = False
                if dir_ == "long":
                    # 3m Blue crosses ABOVE Orange
                    if t3_prev <= s3_prev and t3_curr > s3_curr: rescue = True
                elif dir_ == "short":
                    # 3m Blue crosses BELOW Orange
                    if t3_prev >= s3_prev and t3_curr < s3_curr: rescue = True
                
                if rescue:
                    strategy.log_decision(bar['time'], tf, line_id, "TSI_3M_RESCUE", 
                        f"3m TSI Cross detected in Protection Mode. Triggering Entry.")
                    
                    line["tsi_stage"] = 0
                    line["tsi_ref_price"] = 0.0
                    return _build_tsi_context(strategy, line_id, line, bar, lvl, dir_, curr_tsi, curr_sig)

        # --- B) CHECK SWEEP (Existing Logic) ---
        ref = line["tsi_ref_price"]
        
        if dir_ == "long":
            if bar["low"] < ref:
                line["tsi_stage"] = 2
                strategy.log_decision(bar['time'], tf, line_id, "TSI_SWEEP", 
                    f"Swept previous low {ref}. Now waiting for reclaim.")
        
        elif dir_ == "short":
            if bar["high"] > ref:
                line["tsi_stage"] = 2
                strategy.log_decision(bar['time'], tf, line_id, "TSI_SWEEP", 
                    f"Swept previous high {ref}. Now waiting for reclaim.")

    # STAGE 2: Waiting for Reclaim
    elif line["tsi_stage"] == 2:
        ref = line["tsi_ref_price"]
        should_enter = False
        
        if dir_ == "long":
            if bar["close"] > ref: should_enter = True
        elif dir_ == "short":
            if bar["close"] < ref: should_enter = True
        
        if should_enter:
            strategy.log_decision(bar['time'], tf, line_id, "TSI_RECLAIM", 
                f"Price reclaimed {ref}. Triggering Entry.")
            
            line["tsi_stage"] = 0
            line["tsi_ref_price"] = 0.0
            return _build_tsi_context(strategy, line_id, line, bar, lvl, dir_, curr_tsi, curr_sig)

    return None

def _build_tsi_context(strategy, line_id, line, bar, lvl, dir_, tsi_val, sig_val):
    if dir_ == "long":
        true_extreme = min(line['extreme'], bar['low'])
        cross_depth = max(0.0, lvl - true_extreme)
        strategy.log_decision(bar['time'], bar.get('tf'), line_id, "TSI_CROSS", f"Long Trigger: TSI({tsi_val:.2f}) > Sig({sig_val:.2f})")
        return EntryContext(strategy, line_id, "long", lvl, bar, bar['close'], bar['low'], bar['high'], true_extreme, cross_depth)
    else:
        true_extreme = max(line['extreme'], bar['high'])
        cross_depth = max(0.0, true_extreme - lvl)
        strategy.log_decision(bar['time'], bar.get('tf'), line_id, "TSI_CROSS", f"Short Trigger: TSI({tsi_val:.2f}) < Sig({sig_val:.2f})")
        return EntryContext(strategy, line_id, "short", lvl, bar, bar['close'], bar['low'], bar['high'], true_extreme, cross_depth)

# --- HELPER FUNCTIONS FOR TSI ---

def _calculate_ema(values: List[float], length: int) -> List[float]:
    if not values: return []
    alpha = 2 / (length + 1)
    ema_values = [values[0]]
    for price in values[1:]:
        prev_ema = ema_values[-1]
        new_ema = (price * alpha) + (prev_ema * (1 - alpha))
        ema_values.append(new_ema)
    return ema_values

def _calculate_tsi_series(closes: List[float], long_len: int, short_len: int, sig_len: int):
    if len(closes) < long_len + short_len + sig_len: return [], []
    pc = [0.0] * len(closes)
    abs_pc = [0.0] * len(closes)
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        pc[i] = diff
        abs_pc[i] = abs(diff)
    ema_pc_2  = _calculate_ema(_calculate_ema(pc, long_len), short_len)
    ema_apc_2 = _calculate_ema(_calculate_ema(abs_pc, long_len), short_len)
    tsi_values = []
    for val, abs_val in zip(ema_pc_2, ema_apc_2):
        tsi_values.append(100.0 * (val / abs_val) if abs_val != 0 else 0.0)
    signal_values = _calculate_ema(tsi_values, sig_len)
    return tsi_values, signal_values

# ... (wick_near_line_trigger, three_candle_reversal_trigger, double_5m_cross_trigger remain unchanged) ...
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