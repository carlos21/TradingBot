"""Trade result-type classification (pure domain logic).

This module owns the single decision: "given the trade parameters, exit price,
and why/how the trade closed, what canonical result type should be stored?"
"""

from dataclasses import dataclass
from typing import Protocol

from src.domain.types import CloseReason, Direction, ResultType
from src.financial_calc import FinancialCalc


@dataclass(frozen=True)
class ClassificationContext:
    """Inputs needed to classify a closed trade's result type.

    The dataclass is intentionally plain (no trade entity dependency) so it
    can be built from dictionaries, ORM rows, or broker messages without
    dragging infrastructure into the domain.
    """

    direction: Direction
    entry_price: float
    exit_price: float
    stop_loss: float | None
    take_profit: float | None
    close_reason: CloseReason | None = None
    broker_result_type: str | None = None


class ResultTypeClassifier(Protocol):
    """Protocol for result-type classification."""

    def classify(self, ctx: ClassificationContext) -> ResultType:
        """Return the canonical result type for a closed trade."""
        ...


class DefaultResultTypeClassifier:
    """Default classification rules.

    Precedence:
    1. Broker-provided canonical labels (SL, TP, BE, SP, CLOSE).
    2. Explicit close reason for SL/TP hits.
    3. Price-based detection (BE/SL/TP) via FinancialCalc.
    4. Default based on close reason:
       - SESSION_END / STREAM_END -> SP
       - MANUAL_CLOSE / BROKER_FILL -> CLOSE
       - fallback -> CLOSE
    """

    def classify(self, ctx: ClassificationContext) -> ResultType:
        # 1. Broker label takes precedence — platforms like NinjaTrader send
        #    explicit result_type values.
        if ctx.broker_result_type:
            try:
                return ResultType.from_string(ctx.broker_result_type)
            except ValueError:
                pass  # Unknown broker label; fall through to detection.

        # 2. Explicit close reason for known hits.
        if ctx.close_reason == CloseReason.STOP_LOSS_HIT:
            return ResultType.STOP_LOSS
        if ctx.close_reason == CloseReason.TAKE_PROFIT_HIT:
            return ResultType.TAKE_PROFIT

        # 3. Price-based detection (SL/TP/BE). Session-end closes are an
        # exception: they should only be BE or SP, never SL/TP, because the
        # platform forced the exit rather than the level being hit.
        detected = FinancialCalc.determine_result_type(
            exit_price=ctx.exit_price,
            entry_price=ctx.entry_price,
            stop_loss=ctx.stop_loss,
            take_profit=ctx.take_profit,
        )
        if detected == "BE":
            return ResultType.BREAKEVEN
        if ctx.close_reason not in (CloseReason.SESSION_END, CloseReason.STREAM_END):
            if detected == "SL":
                return ResultType.STOP_LOSS
            if detected == "TP":
                return ResultType.TAKE_PROFIT

        # 4. Reason-based default when price alone is inconclusive.
        if ctx.close_reason in (CloseReason.SESSION_END, CloseReason.STREAM_END):
            return ResultType.MANUAL  # SP

        return ResultType.MANUAL_CLOSE  # CLOSE
