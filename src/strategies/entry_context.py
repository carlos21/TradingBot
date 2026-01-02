from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Sequence, Optional, Tuple, Dict, Any, List
from zoneinfo import ZoneInfo


# Map pairs to their primary trading timezone.
# This allows the filter to automatically convert UTC timestamps to the 
# correct local time (e.g. NY time for Indices) without manual config.
PAIR_TIMEZONES = {
    "NQ": "America/New_York",
    "ES": "America/New_York",
    "YM": "America/New_York",
    "EURUSD": "America/New_York",
}


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


def time_range_filter(start_time_str: str, end_time_str: str, timezone_str: Optional[str] = None) -> EntryFilter:
    """
    Blocks entries outside the specific time range (inclusive).
    
    :param start_time_str: "HH:MM" (24-hour format), e.g., "09:30"
    :param end_time_str: "HH:MM" (24-hour format), e.g., "17:00"
    :param timezone_str: Optional. If None, it is automatically resolved from the pair (e.g. NQ -> NY Time).
    """
    t_start = datetime.strptime(start_time_str, "%H:%M").time()
    t_end = datetime.strptime(end_time_str, "%H:%M").time()

    def _f(ctx: EntryContext) -> Tuple[bool, str]:
        # 1. Determine Timezone
        tz_name = timezone_str
        if not tz_name:
            pair = ctx.bar.get('pair', '')
            # Default to UTC if pair not found in map
            tz_name = PAIR_TIMEZONES.get(pair, "UTC")
        
        tz = ZoneInfo(tz_name)

        # 2. Convert bar timestamp (epoch) to target timezone
        bar_dt = datetime.fromtimestamp(ctx.bar['time'], tz=tz)
        bar_time = bar_dt.time()

        if t_start <= bar_time <= t_end:
            return True, "ok"
        
        msg = f"Time {bar_time} ({tz_name}) outside {t_start}-{t_end}"
        # Uncomment the next line to debug blocked trades in console
        # print(f"[Filter] ⛔ BLOCKED: {msg} | UTC Epoch: {ctx.bar['time']}")
        return False, msg
    return _f


def daily_trades_limit_filter(max_trades_per_day: int, timezone_str: str = "America/Chicago") -> EntryFilter:
    """
    Blocks entries if the number of trades taken TODAY (in the given timezone) >= limit.
    Counts both open and closed trades.
    """
    tz = ZoneInfo(timezone_str)

    def _f(ctx: EntryContext) -> Tuple[bool, str]:
        # 1. Determine the "current day" of the bar being processed
        current_bar_dt = datetime.fromtimestamp(ctx.bar['time'], tz=tz)
        current_day_date = current_bar_dt.date()

        # 2. Fetch all trades from repository (includes open and closed)
        all_trades = ctx.strategy.trade_repository.list_trades(ctx.bar['pair'])

        # 3. Count trades that occurred on this specific day
        daily_count = 0
        for t in all_trades:
            # t.entry_time is UTC-aware datetime. Convert to strategy timezone.
            trade_local_dt = t.entry_time.astimezone(tz)
            if trade_local_dt.date() == current_day_date:
                daily_count += 1

        if daily_count < max_trades_per_day:
            return True, f"daily_count {daily_count} < {max_trades_per_day}"
        
        return False, f"Daily limit reached: {daily_count} >= {max_trades_per_day}"
    return _f