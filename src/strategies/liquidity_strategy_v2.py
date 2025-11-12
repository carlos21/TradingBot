# src/strategies/liquidity_strategy_v2.py
from typing import Optional, Dict, Any
from src.strategies.liquidity_strategy import LiquidityStrategy, StrategyOptions
from src.strategies.entry_context import EntryContext


def wick_near_line_trigger(
    strategy: "LiquidityStrategyV2",
    line_id: Any,
    line: Dict[str, Any],
    bar: Dict[str, Any],
) -> Optional[EntryContext]:
    """
    Single-candle wick trigger near the strategy line.

    Opens a proposal when we see a candle with:
      - VERY SMALL BODY (body/range ≤ small_body_max_ratio)
      - BIG WICK on the side consistent with the line direction:
          * LONG line  -> big LOWER wick (hammer/pin)
          * SHORT line -> big UPPER wick (shooting star)
      - The candle prints (O/H/L/C) are not too far from the line. We encode the
        min distance to the line into EntryContext.cross_depth. Your existing
        max_bounce_filter will require cross_depth ≤ max_bounce to allow entry.

    Entry price = bar close (same convention as v1).
    Risk is computed against the wick extreme (low for long, high for short).
    """
    lvl = line["level"]
    dir_ = line["direction"]  # "long" | "short"

    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
    rng = h - l
    if rng <= 0:
        return None

    body = abs(c - o)
    body_ratio = body / rng

    small_body_max_ratio = getattr(strategy, "small_body_max_ratio", 0.25)
    wick_min_ratio = getattr(strategy, "wick_min_ratio", 0.60)

    # Require a very small body
    if body_ratio > small_body_max_ratio:
        return None

    # Wick proportions
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    upper_ratio = upper_wick / rng
    lower_ratio = lower_wick / rng

    # Directional wick dominance
    if dir_ == "long":
        # Need a dominant LOWER wick
        if lower_ratio < wick_min_ratio:
            return None
        extreme = l
    else:  # "short"
        # Need a dominant UPPER wick
        if upper_ratio < wick_min_ratio:
            return None
        extreme = h

    # Distance from line to any candle print — used as "cross_depth" so the
    # existing max_bounce_filter(max_bounce) can enforce proximity to the line.
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
        cross_depth=min_dist_to_line,  # checked by max_bounce_filter
    )


class LiquidityStrategyV2(LiquidityStrategy):
    """
    Variant of LiquidityStrategy that **does not wait for a 5m close after a retest**.
    Instead, it looks for a **single candle** with a *very small body* and a *big wick*
    near the strategy line; if filters pass (notably max_bounce), it opens.

    Differences vs v1:
      - Trigger: `wick_near_line_trigger` (one-candle, wick-dominant near the line).
      - No reliance on `has_crossed` state; proposals can occur without a retest close.
      - Risk uses the wick extreme (low for longs, high for shorts).

    Everything else (aggregation, exits, storage, events, filters, line removal policy)
    is inherited unchanged.
    """

    def __init__(
        self,
        min_stop_loss: float,
        max_bounce: float,
        socketio,
        line_repository,
        trade_repository,
        extra_sl_space: float,
        strategy_tf: str = "5m",
        options: Optional[StrategyOptions] = None,
        htf_fetcher=None,
        *,
        # V2-specific tunables
        small_body_max_ratio: float = 0.25,  # body/range must be ≤ this
        wick_min_ratio: float = 0.60,        # dominant wick side must be ≥ this of range
    ):
        super().__init__(
            min_stop_loss=min_stop_loss,
            max_bounce=max_bounce,
            socketio=socketio,
            line_repository=line_repository,
            trade_repository=trade_repository,
            extra_sl_space=extra_sl_space,
            strategy_tf=strategy_tf,
            options=options,
            htf_fetcher=htf_fetcher,
        )

        # Thresholds used by the wick trigger
        self.small_body_max_ratio = small_body_max_ratio
        self.wick_min_ratio = wick_min_ratio

        # Override default triggers: use the wick/near-line trigger unless user supplied custom ones
        self.triggers = list(self.options.triggers or [wick_near_line_trigger])
        # Keep entry filters as configured in options (e.g., open_trades_limit, max_bounce)
        self.entry_filters = list(self.options.entry_filters or [])