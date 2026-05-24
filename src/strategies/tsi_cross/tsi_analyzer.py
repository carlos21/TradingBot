"""TSI cross detection — pure, stateless, testable."""

from dataclasses import dataclass

from src.domain.types import Direction
from src.strategies.indicators.tsi import calculate_tsi_series


@dataclass(frozen=True)
class TsiCrossResult:
    direction: Direction | None   # LONG if bullish cross, SHORT if bearish, None if no cross
    tsi_value: float = 0.0
    signal_value: float = 0.0


class TsiAnalyzer:
    """Wraps TSI calculation and cross detection.

    Stateless — call ``analyze()`` with a fresh list of closes every bar.
    """

    def __init__(
        self,
        long_len: int = 6,
        short_len: int = 13,
        signal_len: int = 4,
        confirmation_bars: int = 0,
    ):
        self.long_len = long_len
        self.short_len = short_len
        self.signal_len = signal_len
        self.confirmation_bars = confirmation_bars

    def analyze(self, closes: list[float]) -> TsiCrossResult:
        tsi_vals, sig_vals = calculate_tsi_series(
            closes,
            self.long_len,
            self.short_len,
            self.signal_len,
        )
        if len(tsi_vals) < 2 or len(sig_vals) < 2:
            return TsiCrossResult(direction=None)

        prev_tsi, curr_tsi = tsi_vals[-2], tsi_vals[-1]
        prev_sig, curr_sig = sig_vals[-2], sig_vals[-1]

        # Strict inequality on the previous bar ensures the lines actually
        # crossed, not just touched.
        bullish = prev_tsi < prev_sig and curr_tsi > curr_sig
        bearish = prev_tsi > prev_sig and curr_tsi < curr_sig

        if not bullish and not bearish:
            return TsiCrossResult(direction=None, tsi_value=curr_tsi, signal_value=curr_sig)

        # Confirmation: TSI must have been on the "from" side for N consecutive
        # bars before the cross bar.
        if self.confirmation_bars > 0:
            need = self.confirmation_bars
            if len(tsi_vals) < 2 + need:
                return TsiCrossResult(direction=None, tsi_value=curr_tsi, signal_value=curr_sig)

            if bullish:
                # Must have been below signal for ``need`` consecutive bars
                for i in range(1, need + 1):
                    idx = -(2 + i)
                    if tsi_vals[idx] >= sig_vals[idx]:
                        return TsiCrossResult(direction=None, tsi_value=curr_tsi, signal_value=curr_sig)
            else:
                # Must have been above signal for ``need`` consecutive bars
                for i in range(1, need + 1):
                    idx = -(2 + i)
                    if tsi_vals[idx] <= sig_vals[idx]:
                        return TsiCrossResult(direction=None, tsi_value=curr_tsi, signal_value=curr_sig)

        direction = Direction.LONG if bullish else Direction.SHORT
        return TsiCrossResult(
            direction=direction,
            tsi_value=curr_tsi,
            signal_value=curr_sig,
        )
