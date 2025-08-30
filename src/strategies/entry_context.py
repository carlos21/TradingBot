from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Tuple, List, Dict, Any


@dataclass
class EntryContext:
    strategy: 'LiquidityStrategy'
    line_id: Any
    direction: str            # 'long' or 'short'
    level: float
    bar: Dict[str, Any]       # aggregated bar dict
    close: float
    low: float
    high: float
    extreme: float            # lowest (long) / highest (short) seen during cross
    cross_depth: float        # bounce depth used by filters like max_bounce
    
# A function that receives the entry "context" and decides if we should open.
EntryFilter = Callable[['EntryContext'], Tuple[bool, str]]

EntryTrigger = Callable[
    ['LiquidityStrategy', Any, Dict[str, Any], Dict[str, Any]],
    Optional['EntryContext']
]

# ---------- Filters you can plug/unplug ----------

def open_trades_limit_filter(limit: Optional[int] = 1) -> EntryFilter:
    """Blocks entries when total 'open' trades >= limit. None = unlimited."""
    def _f(ctx: EntryContext) -> Tuple[bool, str]:
        if limit is None:
            return True, "limit: unlimited"
        count = sum(1 for t in ctx.strategy.open_trades if t['status'] == 'open')
        return (count < limit, f"open-trades {count} >= limit {limit}")
    return _f


def max_bounce_filter(max_bounce: float) -> EntryFilter:
    """Blocks entries if the cross depth exceeded a maximum bounce."""
    def _f(ctx: EntryContext) -> Tuple[bool, str]:
        ok = ctx.cross_depth <= max_bounce
        return ok, f"depth {ctx.cross_depth:.5f} > max_bounce {max_bounce}"
    return _f


def htf_big_body_exception_filter(
    fetch_htf_bar,      # Callable(pair: str, tf: str) -> Optional[dict]
    tf: str = '15m',
    body_ratio: float = 0.7  # body / range threshold to call it “big body”
) -> EntryFilter:
    """
    Blocks longs on strongly bearish HTF bars and blocks shorts on strongly bullish HTF bars.
    You supply `fetch_htf_bar(pair, tf)` that returns {'open','high','low','close'} or None.
    """
    def _f(ctx: EntryContext) -> Tuple[bool, str]:
        h = fetch_htf_bar(ctx.bar['pair'], tf)
        if not h:
            return True, "no-htf"

        rng = (h['high'] - h['low'])
        if rng <= 0:
            return True, "zero-range-htf"

        body = abs(h['close'] - h['open'])
        ratio = body / rng

        # classify bar direction
        bull = h['close'] > h['open']
        bear = h['close'] < h['open']

        if ratio >= body_ratio:
            if ctx.direction == 'long' and bear:
                return False, f"HTF({tf}) big bearish body ratio={ratio:.2f}"
            if ctx.direction == 'short' and bull:
                return False, f"HTF({tf}) big bullish body ratio={ratio:.2f}"
        return True, "ok"
    return _f

def retest_cross_trigger(strategy, line_id, line, bar) -> Optional[EntryContext]:
    """
    Stateful trigger that:
      - tracks cross against the level
      - records the extreme (min for long, max for short) while across the level
      - proposes an entry when price returns through the level
    Mirrors existing behavior.
    """
    lvl       = line['level']
    dir_      = line['direction']  # 'long' | 'short'
    close     = bar['close']
    low, high = bar['low'], bar['high']

    if dir_ == 'long':
        # Track cross + extreme
        if close < lvl:
            line['has_crossed'] = True
            line['extreme']     = min(line['extreme'], low)
        # Propose when retests back above
        if line.get('has_crossed') and close >= lvl:
            depth = lvl - line['extreme']  # how deep below the level
            return EntryContext(
                strategy=strategy,
                line_id=line_id,
                direction='long',
                level=lvl,
                bar=bar,
                close=close,
                low=low,
                high=high,
                extreme=line['extreme'],
                cross_depth=depth
            )
        return None

    else:  # short
        if close > lvl:
            line['has_crossed'] = True
            line['extreme']     = max(line['extreme'], high)
        if line.get('has_crossed') and close <= lvl:
            depth = line['extreme'] - lvl  # how deep above the level
            return EntryContext(
                strategy=strategy,
                line_id=line_id,
                direction='short',
                level=lvl,
                bar=bar,
                close=close,
                low=low,
                high=high,
                extreme=line['extreme'],
                cross_depth=depth
            )
        return None