"""Production-style config builder for the TSI Cross strategy.

Analogous to ``src.prod_config`` but tailored for ``TsiCrossStrategy``.
All parameters are overridable so you can tweak behaviour without touching
strategy logic.
"""

from __future__ import annotations

from typing import Any

from src.strategies.entry_context import (
    daily_trades_limit_filter,
    open_trades_limit_filter,
    rollover_filter,
    time_range_filter,
)
from src.strategies.tsi_cross.config import TsiCrossConfig, TsiCrossNumbers


def get_tsi_cross_numbers(
    rr_ratio: float = 5.0,
    risk_per_trade: float | None = None,
    risk_pct_per_trade: float | None = None,
    account_balance: float = 100000.0,
    fixed_stop_loss: float | None = None,
    close_on_opposite_cross: bool = False,
    point_value: float = 2.0,
    fee_per_rt: float = 4.24,
    be_threshold_points: float = 2.0,
    sl_tp_tolerance: float = 0.5,
) -> TsiCrossNumbers:
    """Build ``TsiCrossNumbers`` with sensible defaults.

    Args:
        rr_ratio: Risk:Reward ratio for TP calculation (ignored when
            ``close_on_opposite_cross=True``).
        risk_per_trade: Fixed dollar risk per trade (overrides risk-%).
        risk_pct_per_trade: Percentage of account to risk per trade.
        account_balance: Starting account balance.
        fixed_stop_loss: Fixed SL distance in points (overrides bounce-based SL).
        close_on_opposite_cross: If True, trade exits at the next opposite TSI
            cross instead of a fixed take-profit.
        point_value: $ per point (MNQ = $2).
        fee_per_rt: Broker fee per round-trip per contract.
        be_threshold_points: Exit within this many points of entry is BE.
        sl_tp_tolerance: Points tolerance for SL/TP hit detection.
    """
    return TsiCrossNumbers(
        min_stop_loss=10.0,
        extra_sl_space=0.0,
        fixed_stop_loss=fixed_stop_loss,
        max_stop_loss=None,
        sl_levels=None,
        rr_ratio=rr_ratio,
        point_value=point_value,
        account_balance=account_balance,
        risk_per_trade=risk_per_trade,
        risk_pct_per_trade=risk_pct_per_trade,
        fee_per_rt=fee_per_rt,
        broker_spread=0.0,
        use_fractional_lots=False,
        close_on_opposite_cross=close_on_opposite_cross,
        be_threshold_points=be_threshold_points,
        sl_tp_tolerance=sl_tp_tolerance,
    )


def get_tsi_cross_config(
    time_range: tuple[str, str] = ("08:30", "15:30"),
    daily_trades_limit: int = 5,
    skip_rollover: bool = False,
    tsi_long_len: int = 6,
    tsi_short_len: int = 13,
    tsi_signal_len: int = 4,
    timeframe: str = "5m",
    bounce_lookback: int = 20,
    bounce_neighbor_bars: int = 1,
    breakeven: Any = None,
    cross_confirmation_bars: int = 0,
) -> TsiCrossConfig:
    """Build ``TsiCrossConfig`` with customizable entry filters.

    Args:
        time_range: (start, end) session window as "HH:MM" strings.
        daily_trades_limit: Hard cap on trades per day.
        skip_rollover: Skip days near futures rollover.
        tsi_long_len: TSI long EMA length.
        tsi_short_len: TSI short EMA length.
        tsi_signal_len: TSI signal EMA length.
        timeframe: Bar aggregation timeframe (e.g. "5m").
        bounce_lookback: Bars to scan for swing bounce (used when SL is on).
        bounce_neighbor_bars: Neighbour bars each side to confirm extremum.
        breakeven: ``BreakevenConfig`` or ``None`` to disable.
        cross_confirmation_bars: Minimum consecutive bars TSI must be on the
            "from" side before a cross is valid. 0 = strict cross only.
    """
    return TsiCrossConfig(
        tsi_long_len=tsi_long_len,
        tsi_short_len=tsi_short_len,
        tsi_signal_len=tsi_signal_len,
        timeframe=timeframe,
        bounce_lookback=bounce_lookback,
        bounce_neighbor_bars=bounce_neighbor_bars,
        entry_filters=[
            open_trades_limit_filter(1),
            time_range_filter(time_range[0], time_range[1]),
            daily_trades_limit_filter(max_trades_per_day=daily_trades_limit),
            rollover_filter(enabled=skip_rollover),
        ],
        breakeven=breakeven,
        cross_confirmation_bars=cross_confirmation_bars,
    )
