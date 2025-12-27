from typing import Any, Dict, Optional
from src.strategies.entry_context import EntryContext

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
    1. First 5m candle breaks back through the line (Stage 1).
    2. Price comes back down/reverses through the line (Stage 2).
    3. Another 5m candle breaks back through the line (Trigger).
    """
    
    # Only run on 5m bars
    if bar.get('tf') != '5m':
        return None

    dir_ = line.get("direction")
    if dir_ is None: return None

    lvl = line["level"]
    close = bar["close"]
    
    # Initialize state if not present
    # d5_stage: 0=Waiting, 1=FirstBreak, 2=Retest/Dip
    if "d5_stage" not in line:
        line["d5_stage"] = 0

    stage = line["d5_stage"]

    if dir_ == "long":
        # We are looking for breaks ABOVE the line
        
        if stage == 0:
            # Waiting for FIRST break UP
            if close > lvl:
                line["d5_stage"] = 1
                strategy.log_decision(bar['time'], "5m", line_id, "D5_STAGE_1", f"First 5m Close > {lvl}")
        
        elif stage == 1:
            # Waiting for price to come BACK DOWN (Retest)
            # We require a 5m CLOSE back below to confirm the 'dip' clearly
            if close < lvl:
                line["d5_stage"] = 2
                strategy.log_decision(bar['time'], "5m", line_id, "D5_STAGE_2", f"Dip: 5m Close < {lvl}")
            elif close > lvl:
                # It stayed above. Depending on strictness, we might keep waiting or reset.
                # For now, we stay in Stage 1 (it's still 'broken out', hasn't dipped yet).
                pass

        elif stage == 2:
            # Waiting for SECOND break UP (Trigger)
            if close > lvl:
                # TRIGGER
                true_extreme = min(line['extreme'], bar['low'])
                cross_depth = max(0.0, lvl - true_extreme)
                
                # Reset stage in case filter blocks it, so it doesn't trigger immediately next bar
                # (Unless strategy removes the line, which it usually does on entry)
                line["d5_stage"] = 0 
                
                return EntryContext(
                    strategy=strategy,
                    line_id=line_id,
                    direction="long",
                    level=lvl,
                    bar=bar,
                    close=close,
                    low=bar['low'],
                    high=bar['high'],
                    extreme=true_extreme,
                    cross_depth=cross_depth
                )
            elif close < lvl:
                # Still down, waiting...
                pass

    elif dir_ == "short":
        # We are looking for breaks BELOW the line

        if stage == 0:
            # Waiting for FIRST break DOWN
            if close < lvl:
                line["d5_stage"] = 1
                strategy.log_decision(bar['time'], "5m", line_id, "D5_STAGE_1", f"First 5m Close < {lvl}")

        elif stage == 1:
            # Waiting for price to come BACK UP
            if close > lvl:
                line["d5_stage"] = 2
                strategy.log_decision(bar['time'], "5m", line_id, "D5_STAGE_2", f"Retest: 5m Close > {lvl}")
        
        elif stage == 2:
            # Waiting for SECOND break DOWN (Trigger)
            if close < lvl:
                # TRIGGER
                true_extreme = max(line['extreme'], bar['high'])
                cross_depth = max(0.0, true_extreme - lvl)
                
                line["d5_stage"] = 0

                return EntryContext(
                    strategy=strategy,
                    line_id=line_id,
                    direction="short",
                    level=lvl,
                    bar=bar,
                    close=close,
                    low=bar['low'],
                    high=bar['high'],
                    extreme=true_extreme,
                    cross_depth=cross_depth
                )

    return None