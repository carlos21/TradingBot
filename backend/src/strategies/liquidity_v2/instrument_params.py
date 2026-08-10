"""Hardcoded catalog of supported instruments and their per-instrument parameters.

All point-denominated strategy parameters (stop-loss tiers, bounce/cross-depth
thresholds, velocity gates, close-classification tolerances, trading windows)
are instrument-dependent: values tuned for MNQ are wrong for MES and vice
versa. Because adding an instrument requires tuned parameters (code review +
backtest), the supported list lives in code — the admin UI can only edit each
instrument's ``full_name`` (which changes at every contract rollover).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.models import Instrument
from src.strategies.entry_context import TradingWindow


@dataclass(frozen=True)
class InstrumentParams:
    """Point-denominated strategy parameters for one instrument."""

    symbol: str
    default_full_name: str
    point_value: float               # dollar value per point (MNQ = $2, MES = $5)

    # Stop-loss tiering (points)
    min_stop_loss: float
    max_bounce: float
    extra_sl_space: float
    fixed_stop_loss: float | None
    max_stop_loss: float | None
    sl_levels: list[float]
    sl_level_tolerance: float

    # Entry distance / cross depth (points)
    min_cross_depth: float
    max_entry_distance: float

    # Re-entry (points past the line)
    reentry_threshold: float

    # Velocity-adaptive trigger (points/min volatility gates, points distance)
    fast_threshold: float
    slow_threshold: float
    post_cross1_max_dist: float

    # Trade-close classification tolerances (points)
    be_threshold_points: float
    sl_tp_tolerance: float

    # Global concurrent-open-trades cap for this instrument's session
    # (applies to initial entries and re-entries alike, across all windows).
    max_open_trades: int

    # Extra room (pts) required beyond the observed sweep extreme when the
    # tiered SL is placed. 0 keeps the legacy behaviour (SL may sit right at
    # the extreme), which is vulnerable to retest wicks.
    sl_buffer_pts: float = 0.0

    # Session windows (local times, per-window initial-entry limits)
    trading_windows: list[TradingWindow] = field(default_factory=list)


_DEFAULT_WINDOWS = [
    TradingWindow("08:00", "15:30", max_trades=1),
    # TradingWindow("01:00", "07:59", max_trades=1)
]

INSTRUMENT_PARAMS: dict[str, InstrumentParams] = {
    "MNQ": InstrumentParams(
        symbol="MNQ",
        default_full_name="MNQ 09-26",
        point_value=2.0,
        min_stop_loss=10.0,
        max_bounce=120.0,
        extra_sl_space=0.0,
        fixed_stop_loss=None,
        max_stop_loss=None,
        sl_levels=[10.0, 15.0, 20.0, 30.0],
        sl_level_tolerance=3.0,
        min_cross_depth=5.0,
        max_entry_distance=50.0,
        reentry_threshold=90.0,
        fast_threshold=28.0,
        slow_threshold=15.0,
        post_cross1_max_dist=80.0,
        be_threshold_points=2.0,
        sl_tp_tolerance=0.5,
        max_open_trades=2,
        trading_windows=list(_DEFAULT_WINDOWS),
    ),
    # TODO(tune): MES point values are scaled from MNQ (~0.3x) as placeholders.
    # Backtest and adjust before trading MES live.
    "MES": InstrumentParams(
        symbol="MES",
        default_full_name="MES 09-26",
        point_value=5.0,
        min_stop_loss=3.0,
        max_bounce=30.0,
        extra_sl_space=0.0,
        fixed_stop_loss=None,
        max_stop_loss=None,
        sl_levels=[5.0, 7.0, 10.0, 14.0],
        sl_level_tolerance=1.0,
        min_cross_depth=1.5,
        max_entry_distance=15.0,
        reentry_threshold=30.0,
        fast_threshold=9.0,
        slow_threshold=5.0,
        post_cross1_max_dist=25.0,
        be_threshold_points=0.75,
        sl_tp_tolerance=0.25,
        max_open_trades=2,
        trading_windows=list(_DEFAULT_WINDOWS),
    ),
}

def supported_symbols() -> list[str]:
    """Symbols of all supported (hardcoded) instruments, in catalog order."""
    return list(INSTRUMENT_PARAMS)


def get_instrument_params(symbol: str | None) -> InstrumentParams:
    """Return the parameters for ``symbol``.

    Raises ``ValueError`` for unknown or missing symbols — there is no
    default instrument, so callers must pass an explicit registered symbol.
    """
    params = INSTRUMENT_PARAMS.get(symbol or "")
    if params is None:
        raise ValueError(
            f"Unknown instrument symbol: {symbol!r} "
            f"(supported: {', '.join(INSTRUMENT_PARAMS)})"
        )
    return params


class HardcodedInstrumentCatalog:
    """``InstrumentCatalog`` implementation backed by ``INSTRUMENT_PARAMS``."""

    def get_defaults(self) -> list[Instrument]:
        return [
            Instrument(
                symbol=params.symbol,
                full_name=params.default_full_name,
                point_value=params.point_value,
            )
            for params in INSTRUMENT_PARAMS.values()
        ]
