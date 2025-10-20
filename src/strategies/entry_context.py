from dataclasses import dataclass
from enum import Enum
from typing import Callable, Sequence, Optional, Tuple, Dict, Any, List


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
    

def candle_pattern_near_line_filter(
    fetch_htf_bars: Callable[[str, str, int, Optional[int]], List[Dict[str, Any]]],
    tfs: Sequence[str] = ('5m', '15m', '1h'),
    proximity_abs: Optional[float] = None,     # if None → falls back to strategy.min_stop_loss
    strong_body_min_ratio: float = 0.75,       # candle1: body/range ≥ this
    small_body_max_ratio: float  = 0.25,       # candle2: body/range ≤ this
    wick_ratio: float            = 0.25,       # candle2: "small" wick / range ≤ this
) -> 'EntryFilter':
    """
    Two-candle pattern near the line, simplified:

      SHORT:
        C1 strong bullish body; C2 tiny body; C2 *upper* wick <= wick_ratio (body at top)

      LONG:
        C1 strong bearish body; C2 tiny body; C2 *lower* wick <= wick_ratio (body at bottom)

    Pattern must appear on any TF in `tfs`, and the second candle must be within `proximity_abs`
    of the line level (distance to any of O/H/L/C). `fetch_htf_bars(pair, tf, n=2, upto)`
    returns last 2 bars up to `upto` inclusive.
    """
    def _body(o, c): return abs(c - o)
    def _range(h, l): return max(h - l, 0.0)
    def _upper_wick(o, h, c): return h - max(o, c)
    def _lower_wick(o, l, c): return min(o, c) - l

    def _near(level: float, b: Dict[str, Any], prox: float) -> float:
        # min distance from level to any candle print
        return min(abs(level - b[k]) for k in ('open', 'high', 'low', 'close'))

    def _strong_c1(direction: str, b: Dict[str, Any]) -> bool:
        o, h, l, c = b['open'], b['high'], b['low'], b['close']
        rng = _range(h, l)
        if rng <= 0: return False
        bod_ratio = _body(o, c) / rng
        if direction == 'short':   # want strong bullish first candle
            return c > o and bod_ratio >= strong_body_min_ratio
        else:                      # 'long': want strong bearish first candle
            return c < o and bod_ratio >= strong_body_min_ratio

    def _tiny_c2_with_small_wick(direction: str, b: Dict[str, Any]) -> bool:
        o, h, l, c = b['open'], b['high'], b['low'], b['close']
        rng = _range(h, l)
        if rng <= 0: return False
        bod_ratio = _body(o, c) / rng
        if bod_ratio > small_body_max_ratio:
            return False
        if direction == 'short':
            uw_ratio = _upper_wick(o, h, c) / rng
            return uw_ratio <= wick_ratio
        else:  # long
            lw_ratio = _lower_wick(o, l, c) / rng
            return lw_ratio <= wick_ratio

    def _f(ctx: 'EntryContext') -> Tuple[bool, str]:
        pair   = ctx.bar['pair']
        now_ts = ctx.bar['time']
        level  = ctx.level
        prox   = proximity_abs if proximity_abs is not None else getattr(ctx.strategy, 'min_stop_loss', 0.0)

        for tf in tfs:
            bars = fetch_htf_bars(pair, tf, n=2, upto=now_ts) or []
            if len(bars) < 2:
                continue
            c1, c2 = bars[-2], bars[-1]

            if not _strong_c1(ctx.direction, c1):
                continue
            if not _tiny_c2_with_small_wick(ctx.direction, c2):
                continue

            if _near(level, c2, prox) <= prox:
                return True, f"pattern ok on {tf} within {prox}"
        return False, "no simple two-candle pattern near line"

    return _f