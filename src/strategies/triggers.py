# (path: src/strategies/triggers.py)

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List
from src.strategies.entry_context import EntryContext, EntryTrigger
from src.types import Direction

# --- CONFIGURATION ---
FAST_MOVE_LOOKBACK   = 5      # Check the last 5 bars (15m)
FAST_MOVE_THRESHOLD  = 3.0    # Fast if price moved > 120pts (assuming 40pt avg)
RESCUE_TSI_TIMEFRAME = "5m"   # Timeframe to check for "Rescue" cross
PROTECTION_INVALIDATION_DIST = 40.0 # Points against trade to invalidate line

# Velocity-based adaptive trigger thresholds
VELOCITY_FAST_THRESHOLD = 3.0  # |velocity| > this → fast move → need 2x 5m TSI cross
VELOCITY_SLOW_THRESHOLD = 1.0  # |velocity| < this → slow move → 1m TSI cross; between → 3m TSI cross


# ---------------------------------------------------------------------------
# Velocity-adaptive trigger configuration
# ---------------------------------------------------------------------------

@dataclass
class TsiCrossCondition:
    """
    A single confirmaton requirement: N TSI crosses on a given timeframe.

    count=1 → single cross (stateless, fires on any qualifying crossover bar)
    count=2 → double cross with a reset in between (stateful, tracked per line)
    """
    timeframe: str   # e.g. "1m", "3m", "5m"
    count: int = 1   # 1 or 2


@dataclass
class VelocityTriggerConfig:
    """
    Configures velocity_adaptive_tsi_trigger regime thresholds and
    the list of acceptable entry conditions per regime.

    Each regime holds a list of TsiCrossConditions that are tried in order;
    the first one that returns an EntryContext wins (OR logic).

    Example – slow regime accepts either a double-1m cross or a single-1m cross:
        slow=[TsiCrossCondition("1m", 2), TsiCrossCondition("1m", 1)]
    """
    fast_threshold: float = 5.0   # pts/min — above this is fast
    slow_threshold: float = 2.0   # pts/min — below this is slow; between = moderate
    lookback: int = 30            # number of 1m bars to measure over
    fast:     List[TsiCrossCondition] = field(default_factory=lambda: [TsiCrossCondition("5m", 2)])
    moderate: List[TsiCrossCondition] = field(default_factory=lambda: [TsiCrossCondition("3m", 1)])
    slow:     List[TsiCrossCondition] = field(default_factory=lambda: [TsiCrossCondition("1m", 1)])
    # After the 1st TSI cross in a double-cross setup, if price moves this many points
    # away from the line (in the trade direction), the setup is invalidated and the
    # line is removed. 0.0 = disabled.
    post_cross1_max_dist: float = 0.0

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

def _calculate_velocity_score(history_1m: List[Dict[str, Any]], lookback: int) -> float:
    """
    Calculates velocity as points-per-minute using 1m bars.

    Measures how fast price is moving by looking at the net displacement
    over the last *lookback* 1-minute bars, divided by the number of minutes.
    Positive = price moving up, negative = price moving down.
    """
    if len(history_1m) < lookback:
        return 0.0

    subset = history_1m[-lookback:]
    start_price = subset[0]['open']
    end_price = subset[-1]['close']
    displacement = end_price - start_price

    return displacement / lookback

def _process_tsi_rescue(strategy, line_id, line, bar, lvl, dir_, curr_tsi, curr_sig, tf):
    """
    Handles the 'Rescue' logic:
    1. Wait for TSI to Reset (go against trade direction).
    2. Wait for TSI to Cross back (in trade direction).
    
    Updates line["tsi_reset_occurred"] state.
    Returns EntryContext if rescue triggers, else None.
    """
    rescue_tf = RESCUE_TSI_TIMEFRAME
    hist = strategy.get_history(rescue_tf, 50)
    
    if len(hist) < 30:
        return None

    closes = [b['close'] for b in hist]
    tsi_vals, sig_vals = _calculate_tsi_series(closes, 6, 13, 4)
    
    if len(tsi_vals) < 2:
        return None

    # Check the latest closed bar
    t_curr, t_prev = tsi_vals[-1], tsi_vals[-2]
    s_curr, s_prev = sig_vals[-1], sig_vals[-2]
    
    # 1. CHECK FOR RESET
    # If we haven't reset yet, check if the lines are currently in the "bad" direction
    if not line.get("tsi_reset_occurred", False):
        if dir_ == Direction.LONG:
            # Reset condition: Blue is BELOW Orange (Bearish state)
            if t_curr < s_curr:
                line["tsi_reset_occurred"] = True
                strategy.log_decision(bar['time'], tf, line_id, "TSI_RESET", 
                    f"TSI Reset detected (Blue < Orange). Ready for Rescue Cross.")
        elif dir_ == Direction.SHORT:
            # Reset condition: Blue is ABOVE Orange (Bullish state)
            if t_curr > s_curr:
                line["tsi_reset_occurred"] = True
                strategy.log_decision(bar['time'], tf, line_id, "TSI_RESET", 
                    f"TSI Reset detected (Blue > Orange). Ready for Rescue Cross.")

    # 2. CHECK FOR TRIGGER (Only if Reset has occurred)
    if line.get("tsi_reset_occurred", False):
        rescue = False
        if dir_ == Direction.LONG:
            # Rescue Long: Blue crosses ABOVE Orange
            if t_prev <= s_prev and t_curr > s_curr: 
                rescue = True
        elif dir_ == Direction.SHORT:
            # Rescue Short: Blue crosses BELOW Orange
            if t_prev >= s_prev and t_curr < s_curr: 
                rescue = True
        
        if rescue:
            strategy.log_decision(bar['time'], tf, line_id, f"TSI_{rescue_tf}_RESCUE", 
                f"{rescue_tf} TSI Rescue Cross detected. Triggering Entry.")
            
            # Reset internal state
            line["tsi_stage"] = 0
            line["tsi_ref_price"] = 0.0
            line["tsi_reset_occurred"] = False
            
            return _build_tsi_context(strategy, line_id, line, bar, lvl, dir_, curr_tsi, curr_sig)
    
    return None

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
       - Wait for SWEEP of the fast candle's extreme.
    2: Post-Sweep Mode.
       - Wait for TSI RESCUE (Reset + Cross).
    -1: Invalidated/Dead.
    """
    dir_str = line.get("direction")
    if dir_str is None: return None
    dir_ = Direction.from_string(dir_str)

    lvl = line["level"]
    
    if "tsi_stage" not in line:
        line["tsi_stage"] = 0       
        line["tsi_ref_price"] = 0.0 
        line["tsi_reset_occurred"] = False

    # If line is dead, ignore
    if line["tsi_stage"] == -1:
        return None

    # 1. Interaction Check
    if dir_ == Direction.LONG:
        if line['extreme'] > lvl: return None
    elif dir_ == Direction.SHORT:
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
        
        if dir_ == Direction.LONG:
            if (prev_tsi <= prev_sig) and (curr_tsi > curr_sig): has_crossed = True
        elif dir_ == Direction.SHORT:
            if (prev_tsi >= prev_sig) and (curr_tsi < curr_sig): has_crossed = True
        
        if has_crossed:
            # Fast Move Check (1m bars)
            hist_1m = strategy.get_history("1m", FAST_MOVE_LOOKBACK + 5)
            velocity_score = _calculate_velocity_score(hist_1m, FAST_MOVE_LOOKBACK)
            
            is_fast = False
            if dir_ == Direction.LONG and velocity_score < -FAST_MOVE_THRESHOLD: is_fast = True
            elif dir_ == Direction.SHORT and velocity_score > FAST_MOVE_THRESHOLD: is_fast = True

            if is_fast:
                line["tsi_stage"] = 1
                line["tsi_ref_price"] = bar["low"] if dir_ == Direction.LONG else bar["high"]
                line["tsi_reset_occurred"] = False 
                
                strategy.log_decision(bar['time'], tf, line_id, "TSI_FAST", 
                    f"Fast Move ({velocity_score:.2f}). Protection Mode ON. Wait for Sweep of {line['tsi_ref_price']}.")
                return None
            else:
                return _build_tsi_context(strategy, line_id, line, bar, lvl, dir_, curr_tsi, curr_sig)

    # STAGE 1: Protection Mode (Wait for Sweep)
    elif line["tsi_stage"] == 1:
        
        # A) CHECK INVALIDATION
        invalidated = False
        if dir_ == Direction.LONG:
            if bar['close'] < (lvl - PROTECTION_INVALIDATION_DIST): invalidated = True
        elif dir_ == Direction.SHORT:
            if bar['close'] > (lvl + PROTECTION_INVALIDATION_DIST): invalidated = True
        
        if invalidated:
            strategy.log_decision(bar['time'], tf, line_id, "TSI_INVALID", 
                f"Price moved > {PROTECTION_INVALIDATION_DIST}pts against trade. Line Invalidated.")
            line["tsi_stage"] = -1
            return None

        # B) CHECK SWEEP
        ref = line["tsi_ref_price"]
        swept = False
        
        if dir_ == Direction.LONG:
            if bar["low"] < ref: swept = True
        elif dir_ == Direction.SHORT:
            if bar["high"] > ref: swept = True
            
        if swept:
            line["tsi_stage"] = 2
            line["tsi_reset_occurred"] = False # Ensure reset logic starts fresh
            strategy.log_decision(bar['time'], tf, line_id, "TSI_SWEEP", 
                f"Swept previous extreme {ref}. Now waiting for {RESCUE_TSI_TIMEFRAME} TSI Rescue.")

    # STAGE 2: Post-Sweep (Wait for TSI Rescue)
    elif line["tsi_stage"] == 2:
        
        # A) CHECK INVALIDATION (Still applies)
        invalidated = False
        if dir_ == Direction.LONG:
            if bar['close'] < (lvl - PROTECTION_INVALIDATION_DIST): invalidated = True
        elif dir_ == Direction.SHORT:
            if bar['close'] > (lvl + PROTECTION_INVALIDATION_DIST): invalidated = True
        
        if invalidated:
            strategy.log_decision(bar['time'], tf, line_id, "TSI_INVALID", 
                f"Price moved > {PROTECTION_INVALIDATION_DIST}pts against trade. Line Invalidated.")
            line["tsi_stage"] = -1
            return None

        # B) CHECK RESCUE
        rescue_ctx = _process_tsi_rescue(strategy, line_id, line, bar, lvl, dir_, curr_tsi, curr_sig, tf)
        if rescue_ctx: return rescue_ctx

    return None

def _handle_single_tsi_cross(strategy, line_id, line, bar, lvl, dir_, tf):
    """Check for a single TSI cross on the given timeframe."""
    history = strategy.get_history(tf, 50)
    if len(history) < 30:
        return None

    closes = [b['close'] for b in history]
    tsi_line, sig_line = _calculate_tsi_series(closes, 6, 13, 4)
    if len(tsi_line) < 2:
        return None

    curr_tsi, prev_tsi = tsi_line[-1], tsi_line[-2]
    curr_sig, prev_sig = sig_line[-1], sig_line[-2]

    if dir_ == Direction.LONG and prev_tsi <= prev_sig and curr_tsi > curr_sig:
        return _build_tsi_context(strategy, line_id, line, bar, lvl, dir_, curr_tsi, curr_sig)
    if dir_ == Direction.SHORT and prev_tsi >= prev_sig and curr_tsi < curr_sig:
        return _build_tsi_context(strategy, line_id, line, bar, lvl, dir_, curr_tsi, curr_sig)

    # No cross – record values so we can see how far TSI is from crossing
    gap = curr_tsi - curr_sig
    strategy.log_decision(bar['time'], tf, line_id, "TSI_CHECK",
        f"TSI {prev_tsi:+.2f}→{curr_tsi:+.2f} | Sig {prev_sig:+.2f}→{curr_sig:+.2f} | gap={gap:+.2f} (no cross)")
    return None


def _handle_double_tsi_cross(strategy, line_id, line, bar, lvl, dir_, tf, state_prefix, max_dist: float = 0.0):
    """
    Require two TSI crosses on *tf* with a reset in between.
    State is stored in line under keys derived from *state_prefix* so that
    different (timeframe, count) combinations don't collide.

    max_dist: if > 0, invalidate and remove the line when price moves more than
    this many points away from the line (in trade direction) after the 1st cross.
    """
    history = strategy.get_history(tf, 50)
    if len(history) < 30:
        return None

    closes = [b['close'] for b in history]
    tsi_line, sig_line = _calculate_tsi_series(closes, 6, 13, 4)
    if len(tsi_line) < 2:
        return None

    curr_tsi, prev_tsi = tsi_line[-1], tsi_line[-2]
    curr_sig, prev_sig = sig_line[-1], sig_line[-2]

    stage_key = f"{state_prefix}_stage"
    reset_key = f"{state_prefix}_reset"
    stage = line.get(stage_key, 0)

    if stage == 0:
        crossed = (
            (dir_ == Direction.LONG  and prev_tsi <= prev_sig and curr_tsi > curr_sig) or
            (dir_ == Direction.SHORT and prev_tsi >= prev_sig and curr_tsi < curr_sig)
        )
        if crossed:
            line[stage_key] = 1
            line[reset_key] = False
            strategy.log_decision(bar['time'], tf, line_id, "VAT_CROSS_1",
                f"1st {tf} TSI cross ({dir_}). Waiting for reset then 2nd cross.")
        return None

    elif stage == 1:
        # Check if price has moved too far from the line after the 1st cross
        if max_dist > 0:
            too_far = (
                (dir_ == Direction.LONG  and bar['close'] > lvl + max_dist) or
                (dir_ == Direction.SHORT and bar['close'] < lvl - max_dist)
            )
            if too_far:
                dist = abs(bar['close'] - lvl)
                strategy.log_decision(bar['time'], tf, line_id, "VAT_CROSS1_TOO_FAR",
                    f"Price {bar['close']} moved {dist:.1f}pts from line {lvl} after 1st cross (max={max_dist}). Invalidated.")
                line[stage_key] = 0
                line[reset_key] = False
                strategy.remove_strategy_line(line_id)
                return None

        # Step A: check for reset (TSI going against trade direction)
        if not line.get(reset_key, False):
            reset = (
                (dir_ == Direction.LONG  and curr_tsi < curr_sig) or
                (dir_ == Direction.SHORT and curr_tsi > curr_sig)
            )
            if reset:
                line[reset_key] = True
                strategy.log_decision(bar['time'], tf, line_id, "VAT_RESET",
                    f"{tf} TSI reset. Ready for 2nd cross.")

        # Step B: once reset, look for 2nd cross
        if line.get(reset_key, False):
            crossed2 = (
                (dir_ == Direction.LONG  and prev_tsi <= prev_sig and curr_tsi > curr_sig) or
                (dir_ == Direction.SHORT and prev_tsi >= prev_sig and curr_tsi < curr_sig)
            )
            if crossed2:
                line[stage_key] = 0
                line[reset_key] = False
                strategy.log_decision(bar['time'], tf, line_id, "VAT_CROSS_2",
                    f"2nd {tf} TSI cross ({dir_}). Triggering entry.")
                return _build_tsi_context(strategy, line_id, line, bar, lvl, dir_, curr_tsi, curr_sig)

    return None


def _check_tsi_condition(strategy, line_id, line, bar, lvl, dir_, cond: TsiCrossCondition, max_dist: float = 0.0):
    """
    Evaluate one TsiCrossCondition against the current bar.
    Returns an EntryContext if the condition fires, otherwise None.
    Only runs when bar['tf'] matches cond.timeframe.
    """
    if bar.get('tf') != cond.timeframe:
        return None
    if cond.count == 1:
        return _handle_single_tsi_cross(strategy, line_id, line, bar, lvl, dir_, cond.timeframe)
    else:
        state_prefix = f"vat_{cond.timeframe}_{cond.count}x"
        return _handle_double_tsi_cross(strategy, line_id, line, bar, lvl, dir_, cond.timeframe, state_prefix, max_dist)


def make_velocity_adaptive_tsi_trigger(config: VelocityTriggerConfig = None):
    """
    Factory that returns a velocity-adaptive TSI trigger function.

    The returned trigger classifies the current velocity into slow / moderate / fast
    and tries each TsiCrossCondition in the matching regime list (OR logic: first
    condition that fires wins).

    Usage in prod_config.py:
        triggers=[
            make_velocity_adaptive_tsi_trigger(VelocityTriggerConfig(
                fast_threshold=3.0,
                slow_threshold=1.0,
                lookback=5,
                fast=    [TsiCrossCondition("5m", 2)],
                moderate=[TsiCrossCondition("3m", 1)],
                slow=    [TsiCrossCondition("1m", 1)],
                # e.g. to also allow a single-1m as fallback on slow regime:
                # slow=[TsiCrossCondition("1m", 2), TsiCrossCondition("1m", 1)],
            ))
        ]
    """
    if config is None:
        config = VelocityTriggerConfig()

    def trigger(
        strategy: "LiquidityStrategyV2",
        line_id: Any,
        line: Dict[str, Any],
        bar: Dict[str, Any],
    ) -> Optional[EntryContext]:
        dir_str = line.get("direction")
        if dir_str is None:
            return None
        dir_ = Direction.from_string(dir_str) if isinstance(dir_str, str) else dir_str

        lvl = line["level"]
        if not bar.get('tf'):
            return None

        # Interaction check: price must have touched the line
        if dir_ == Direction.LONG  and line['extreme'] > lvl:
            return None
        if dir_ == Direction.SHORT and line['extreme'] < lvl:
            return None

        tf = bar['tf']

        # Lock regime on first touch; reuse on all subsequent bars
        if 'vat_regime' not in line:
            hist_1m = strategy.get_history("1m", config.lookback + 10)
            velocity_score = _calculate_velocity_score(hist_1m, config.lookback)
            abs_vel = abs(velocity_score)

            is_fast     = abs_vel > config.fast_threshold
            is_moderate = not is_fast and abs_vel > config.slow_threshold

            regime_label = "FAST" if is_fast else ("MODERATE" if is_moderate else "SLOW")
            line['vat_regime']   = regime_label
            line['vat_velocity'] = velocity_score

            regime_conditions = config.fast if is_fast else (config.moderate if is_moderate else config.slow)
            conds_str = " OR ".join(f"{c.timeframe}×{c.count}" for c in regime_conditions)
            strategy.log_decision(bar['time'], tf, line_id, "VAT_REGIME",
                f"vel={abs_vel:.2f} pts/min → {regime_label} | need [{conds_str}] (locked at touch)")
        else:
            regime_label = line['vat_regime']
            is_fast      = regime_label == "FAST"
            is_moderate  = regime_label == "MODERATE"

        if is_fast:
            regime_conditions = config.fast
        elif is_moderate:
            regime_conditions = config.moderate
        else:
            regime_conditions = config.slow

        # Try each condition in order; return the first that fires
        for cond in regime_conditions:
            result = _check_tsi_condition(strategy, line_id, line, bar, lvl, dir_, cond, config.post_cross1_max_dist)
            if result is not None:
                return result
        return None

    trigger.__name__ = "velocity_adaptive_tsi_trigger"
    return trigger


# Backward-compatible default instance (uses VelocityTriggerConfig defaults)
velocity_adaptive_tsi_trigger = make_velocity_adaptive_tsi_trigger()


def _build_tsi_context(strategy, line_id, line, bar, lvl, dir_, tsi_val, sig_val):
    if dir_ == Direction.LONG:
        true_extreme = min(line['extreme'], bar['low'])
        cross_depth = max(0.0, lvl - true_extreme)
        strategy.log_decision(bar['time'], bar.get('tf'), line_id, "TSI_CROSS", f"Long Trigger: TSI({tsi_val:.2f}) > Sig({sig_val:.2f})")
        return EntryContext(strategy, line_id, Direction.LONG, lvl, bar, bar['close'], bar['low'], bar['high'], true_extreme, cross_depth)
    else:
        true_extreme = max(line['extreme'], bar['high'])
        cross_depth = max(0.0, true_extreme - lvl)
        strategy.log_decision(bar['time'], bar.get('tf'), line_id, "TSI_CROSS", f"Short Trigger: TSI({tsi_val:.2f}) < Sig({sig_val:.2f})")
        return EntryContext(strategy, line_id, Direction.SHORT, lvl, bar, bar['close'], bar['low'], bar['high'], true_extreme, cross_depth)

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

def wick_near_line_trigger(
    strategy: "LiquidityStrategyV2",
    line_id: Any,
    line: Dict[str, Any],
    bar: Dict[str, Any],
) -> Optional[EntryContext]:
    
    dir_str = line.get("direction")
    if dir_str is None: return None
    dir_ = Direction.from_string(dir_str) if isinstance(dir_str, str) else dir_str

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
    if dir_ == Direction.LONG:
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
    
    dir_str = line.get("direction")
    if dir_str is None: return None
    dir_ = Direction.from_string(dir_str)

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

    if dir_ == Direction.SHORT:
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
            direction=Direction.SHORT,
            level=lvl,
            bar=c3,
            close=c3['close'],
            low=c3['low'],
            high=c3['high'],
            extreme=true_extreme,
            cross_depth=cross_depth 
        )

    elif dir_ == Direction.LONG:
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
            direction=Direction.LONG,
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
    dir_str = line.get("direction")
    if dir_str is None: return None
    dir_ = Direction.from_string(dir_str)

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

    if dir_ == Direction.LONG:
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
                    direction=Direction.LONG,
                    level=lvl,
                    bar=bar,
                    close=close,
                    low=low,
                    high=high,
                    extreme=true_extreme,
                    cross_depth=cross_depth
                )

    elif dir_ == Direction.SHORT:
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
                    direction=Direction.SHORT,
                    level=lvl,
                    bar=bar,
                    close=close,
                    low=low,
                    high=high,
                    extreme=true_extreme,
                    cross_depth=cross_depth
                )

    return None