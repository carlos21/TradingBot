from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from src.strategies.base_strategy import BaseStrategy as _BaseStrategy
    from src.strategies.liquidity_v2.base_strategy import (
        BaseLiquidityStrategy as _BaseLiquidityStrategy,
    )
    _StrategyType = _BaseLiquidityStrategy | _BaseStrategy

from src.domain.types import Direction

# Map pairs to their primary trading timezone.
# This allows the filter to automatically convert UTC timestamps to the
# correct local time (e.g. NY time for Indices) without manual config.
PAIR_TIMEZONES = {
    "MNQ": "America/New_York",
    "ES": "America/New_York",
    "MES": "America/New_York",
    "YM": "America/New_York",
    "MYM": "America/New_York",
    "EURUSD": "America/New_York",
}


@dataclass
class EntryContext:
    strategy: _StrategyType
    line_id: Any
    direction: Direction      # Direction enum (LONG or SHORT)
    level: float
    bar: dict[str, Any]       # aggregated bar dict
    close: float
    low: float
    high: float
    extreme: float            # lowest (long) / highest (short) seen during cross
    cross_depth: float        # bounce depth used by filters like max_bounce
    is_reentry: bool = False  # True when the context evaluates a re-entry after SL

    @property
    def is_long(self) -> bool:
        """Check if entry is long direction."""
        return self.direction.is_long

    @property
    def is_short(self) -> bool:
        """Check if entry is short direction."""
        return self.direction.is_short

@dataclass(frozen=True)
class EntryFilter:
    """A named, pluggable entry filter. hold_on_block=True keeps the line alive on block."""
    fn: Callable[['EntryContext'], tuple[bool, str]]
    name: str
    hold_on_block: bool = False

    def __call__(self, ctx: 'EntryContext') -> tuple[bool, str]:
        return self.fn(ctx)

    @property
    def __name__(self) -> str:
        return self.name

EntryTrigger = Callable[
    ['_BaseLiquidityStrategy', Any, dict[str, Any], dict[str, Any]],
    Optional['EntryContext']
]

# ---------- Filters you can plug/unplug ----------

def open_trades_limit_filter(limit: int | None = 1) -> EntryFilter:
    """Blocks entries when total 'open' trades >= limit. None = unlimited."""
    def _f(ctx: EntryContext) -> tuple[bool, str]:
        if limit is None:
            return True, "limit: unlimited"
        count = sum(1 for t in ctx.strategy.open_trades if t.get('status') == 'open')
        return (count < limit, f"open-trades {count} >= limit {limit}")
    return EntryFilter(fn=_f, name="open_trades_limit")


def min_cross_depth_filter(min_depth: float) -> EntryFilter:
    """Blocks entries where price has not crossed the line by at least min_depth points.
    Marked as a hold filter: the line is kept alive so it can re-trigger once depth is sufficient."""
    def _f(ctx: EntryContext) -> tuple[bool, str]:
        ok = ctx.cross_depth >= min_depth
        return ok, f"level={ctx.level}, extreme={ctx.extreme:.2f}, depth={ctx.cross_depth:.2f} < min_depth={min_depth}"
    # do not remove line when this filter blocks; let depth accumulate
    return EntryFilter(fn=_f, name="min_cross_depth", hold_on_block=True)


def max_bounce_filter(max_bounce: float) -> EntryFilter:
    """Blocks entries if the cross depth exceeded a maximum bounce."""
    def _f(ctx: EntryContext) -> tuple[bool, str]:
        ok = ctx.cross_depth <= max_bounce
        return ok, f"level={ctx.level}, extreme={ctx.extreme:.2f}, depth={ctx.cross_depth:.2f} > max_bounce={max_bounce}"
    return EntryFilter(fn=_f, name="max_bounce")


def time_range_filter(start_time_str: str, end_time_str: str, timezone_str: str | None = None) -> EntryFilter:
    """
    Blocks entries outside the specific time range (inclusive).

    Supports overnight ranges such as "22:00"-"02:00".

    :param start_time_str: "HH:MM" (24-hour format), e.g., "09:30"
    :param end_str: "HH:MM" (24-hour format), e.g., "17:00"
    :param timezone_str: Optional. If None, it is automatically resolved from the pair (e.g. MNQ -> NY Time).
    """
    t_start = datetime.strptime(start_time_str, "%H:%M").time()
    t_end = datetime.strptime(end_time_str, "%H:%M").time()
    overnight = t_start > t_end

    def _in_range(bar_time) -> bool:
        if overnight:
            return bar_time >= t_start or bar_time <= t_end
        return t_start <= bar_time <= t_end

    def _f(ctx: EntryContext) -> tuple[bool, str]:
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

        if _in_range(bar_time):
            return True, "ok"

        msg = f"Time {bar_time} ({tz_name}) outside {t_start}-{t_end}"
        return False, msg
    return EntryFilter(fn=_f, name="time_range")


def rollover_filter(enabled: bool = False, timezone_str: str | None = None) -> EntryFilter:
    """
    Blocks entries on CME futures rollover days (2nd Thursday before quarterly expiration).
    Rollover months: March, June, September, December.
    The rollover day is the Thursday that falls 8 days before the 3rd Friday of the expiration month.
    """
    import calendar

    def _third_friday(year: int, month: int) -> int:
        """Return the day-of-month of the 3rd Friday."""
        cal = calendar.monthcalendar(year, month)
        # Friday is index 4 in monthcalendar rows
        fridays = [week[calendar.FRIDAY] for week in cal if week[calendar.FRIDAY] != 0]
        return fridays[2]  # 3rd Friday (0-indexed)

    def _rollover_dates_for_year(year: int) -> set:
        """Return set of (month, day) tuples for rollover days in a given year."""
        from datetime import date, timedelta
        dates = set()
        for month in (3, 6, 9, 12):
            third_fri = date(year, month, _third_friday(year, month))
            # Rollover is the 2nd Thursday before expiration Friday = 8 days before
            rollover = third_fri - timedelta(days=8)
            dates.add((rollover.month, rollover.day))
        return dates

    _cache: dict[int, set] = {}

    def _f(ctx: EntryContext) -> tuple[bool, str]:
        if not enabled:
            return True, "rollover filter disabled"

        tz_name = timezone_str
        if not tz_name:
            pair = ctx.bar.get('pair', '')
            tz_name = PAIR_TIMEZONES.get(pair, "UTC")

        tz = ZoneInfo(tz_name)
        bar_dt = datetime.fromtimestamp(ctx.bar['time'], tz=tz)
        year = bar_dt.year

        if year not in _cache:
            _cache[year] = _rollover_dates_for_year(year)

        if (bar_dt.month, bar_dt.day) in _cache[year]:
            return False, f"Rollover day {bar_dt.date()} ({tz_name}) - no trades allowed"
        return True, "ok"
    return EntryFilter(fn=_f, name="rollover")


def daily_trades_limit_filter(max_trades_per_day: int, timezone_str: str | None = None) -> EntryFilter:
    """
    Blocks entries if the number of strategy trades taken TODAY (in the given timezone) >= limit.
    Counts both open and closed trades. Manual, test, and broker-sync trades are excluded.

    If ``timezone_str`` is None, the timezone is resolved from ``PAIR_TIMEZONES`` using the bar's pair.
    """
    def _f(ctx: EntryContext) -> tuple[bool, str]:
        # 1. Resolve timezone
        tz_name = timezone_str
        if not tz_name:
            pair = ctx.bar.get('pair', '')
            tz_name = PAIR_TIMEZONES.get(pair, "UTC")
        tz = ZoneInfo(tz_name)

        # 2. Determine the "current day" of the bar being processed
        current_bar_dt = datetime.fromtimestamp(ctx.bar['time'], tz=tz)
        current_day_date = current_bar_dt.date()

        # 3. Fetch all trades from repository (includes open and closed)
        all_trades = ctx.strategy.trade_repository.list_trades(ctx.bar['pair'])

        # 4. Count only strategy-automated trades that occurred on this specific day
        daily_count = 0
        for t in all_trades:
            # Skip manual, test, and broker-sync trades
            trade_source = str(t.source or "strategy").lower()
            if trade_source in ("manual", "test"):
                continue

            # t.entry_time may be naive or aware. Make it aware in UTC first.
            entry_time = t.entry_time
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone.utc)
            trade_local_dt = entry_time.astimezone(tz)
            if trade_local_dt.date() == current_day_date:
                daily_count += 1

        if daily_count < max_trades_per_day:
            return True, f"daily_count {daily_count} < {max_trades_per_day}"

        return False, f"Daily limit reached: {daily_count} >= {max_trades_per_day}"
    return EntryFilter(fn=_f, name="daily_trades_limit")


@dataclass(frozen=True)
class TradingWindow:
    """A trading session window with its own entry limits.

    ``max_trades`` counts only INITIAL entries whose entry time falls inside this
    window; re-entry trades never consume window slots (each initial trade keeps
    its own re-entry chain, bounded by the strategy's max_reentry_attempts).

    ``max_open_trades`` caps concurrent open trades attributed to this window,
    where an open trade is attributed to the window instance in which its
    CURRENT leg (initial entry or re-entry) was opened. Two concurrent trades
    are therefore only possible when their active legs live in DIFFERENT
    windows; the total across windows is additionally bounded by the global
    ``open_trades_limit_filter``.
    """
    start: str                # "HH:MM", local time
    end: str                  # "HH:MM"; overnight ("22:00"->"02:00") supported
    max_open_trades: int = 1  # concurrent open trades whose current leg opened inside this window
    max_trades: int = 1       # max INITIAL entries inside this window (re-entries excluded)


def trading_windows_filter(windows: list[TradingWindow], timezone_str: str | None = None) -> EntryFilter:
    """
    Blocks entries outside all trading windows, and enforces per-window limits.

    For the window containing the current bar:
    - blocks when the number of open trades attributed to this window instance
      >= window.max_open_trades (applies to initial entries and re-entries
      alike). An open trade is attributed to the window instance in which its
      CURRENT leg was opened (a London-born re-entry leg evaluated inside the
      NY window counts as a NY-window trade); trades without a usable
      ``entry_time`` are not attributed to any window. Concurrency across
      DIFFERENT windows is capped separately by the global
      ``open_trades_limit_filter``;
    - blocks INITIAL entries when the number of initial strategy entries already
      inside this window instance >= window.max_trades. Re-entry evaluations
      (``ctx.is_reentry``) skip this check, and re-entry trades are excluded from
      the count, so every initial trade keeps its own re-entry chain.

    Supports overnight windows such as "22:00"-"02:00"; the window instance is
    resolved as an absolute interval so post-midnight entries count toward the
    same instance that started the previous day.

    If ``timezone_str`` is None, the timezone is resolved from ``PAIR_TIMEZONES``
    using the bar's pair.
    """
    parsed = [
        (w, datetime.strptime(w.start, "%H:%M").time(), datetime.strptime(w.end, "%H:%M").time())
        for w in windows
    ]

    def _window_instances(w, t_start, t_end, day, tz):
        """Absolute [start_dt, end_dt] intervals of `w` that may contain a bar on `day`."""
        start_dt = datetime.combine(day, t_start, tzinfo=tz)
        if t_start <= t_end:
            return [(start_dt, datetime.combine(day, t_end, tzinfo=tz))]
        # Overnight: instance starting today ends tomorrow; also the one started yesterday.
        return [
            (start_dt, datetime.combine(day + timedelta(days=1), t_end, tzinfo=tz)),
            (start_dt - timedelta(days=1), datetime.combine(day, t_end, tzinfo=tz)),
        ]

    def _f(ctx: EntryContext) -> tuple[bool, str]:
        # 1. Resolve timezone
        tz_name = timezone_str
        if not tz_name:
            pair = ctx.bar.get('pair', '')
            tz_name = PAIR_TIMEZONES.get(pair, "UTC")
        tz = ZoneInfo(tz_name)

        # 2. Current bar in local time
        bar_dt = datetime.fromtimestamp(ctx.bar['time'], tz=tz)

        # 3. Find the window instance containing this bar
        matched = None
        for w, t_start, t_end in parsed:
            for start_dt, end_dt in _window_instances(w, t_start, t_end, bar_dt.date(), tz):
                if start_dt <= bar_dt <= end_dt:
                    matched = (w, start_dt, end_dt)
                    break
            if matched:
                break

        if not matched:
            return False, f"Time {bar_dt.time()} ({tz_name}) outside all trading windows"

        window, win_start, win_end = matched

        # 4. Per-window concurrent-open limit (initial entries and re-entries
        # alike). An open trade is attributed to the window instance in which
        # its CURRENT leg was opened; trades without a usable entry_time are
        # left to the global open_trades_limit_filter.
        open_count = 0
        for t in ctx.strategy.open_trades:
            if t.get('status') != 'open':
                continue
            leg_entry = t.get('entry_time')
            if leg_entry is None:
                continue
            if isinstance(leg_entry, datetime):
                if leg_entry.tzinfo is None:
                    leg_entry = leg_entry.replace(tzinfo=timezone.utc)
                leg_dt = leg_entry.astimezone(tz)
            else:
                leg_dt = datetime.fromtimestamp(leg_entry, tz=tz)
            if win_start <= leg_dt <= win_end:
                open_count += 1
        if open_count >= window.max_open_trades:
            return False, (f"open-trades {open_count} >= limit {window.max_open_trades} "
                           f"in window {window.start}-{window.end}")

        # 5. Per-window initial-entry limit (re-entries bypass and are excluded)
        if ctx.is_reentry:
            return True, "re-entry: window ok, trade count not applied"

        all_trades = ctx.strategy.trade_repository.list_trades(ctx.bar['pair'])
        count = 0
        for t in all_trades:
            trade_source = str(t.source or "strategy").lower()
            if trade_source in ("manual", "test"):
                continue
            params = t.params or {}
            if params.get("is_reentry") or params.get("reentry_attempt", 0) > 0:
                continue
            entry_time = t.entry_time
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone.utc)
            if win_start <= entry_time.astimezone(tz) <= win_end:
                count += 1

        if count >= window.max_trades:
            return False, (f"Window {window.start}-{window.end} limit reached: "
                           f"{count} >= {window.max_trades}")
        return True, f"window {window.start}-{window.end}: {count} < {window.max_trades}"
    return EntryFilter(fn=_f, name="trading_windows")
