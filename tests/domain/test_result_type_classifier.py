"""Tests for the result-type classifier."""

import pytest

from src.domain.result_type_classifier import ClassificationContext, DefaultResultTypeClassifier
from src.domain.types import CloseReason, Direction, ResultType


@pytest.fixture
def classifier():
    return DefaultResultTypeClassifier()


class TestDefaultResultTypeClassifier:
    """Behavioural coverage for the canonical result-type decision."""

    def _ctx(
        self,
        direction: Direction = Direction.LONG,
        entry: float = 100.0,
        exit_: float = 130.0,
        sl: float | None = 90.0,
        tp: float | None = 130.0,
        close_reason: CloseReason | None = None,
        broker_result_type: str | None = None,
    ) -> ClassificationContext:
        return ClassificationContext(
            direction=direction,
            entry_price=entry,
            exit_price=exit_,
            stop_loss=sl,
            take_profit=tp,
            close_reason=close_reason,
            broker_result_type=broker_result_type,
        )

    def test_broker_label_close_maps_to_manual_close(self, classifier):
        ctx = self._ctx(broker_result_type="CLOSE")
        assert classifier.classify(ctx) == ResultType.MANUAL_CLOSE

    def test_broker_label_sl_maps_to_stop_loss(self, classifier):
        ctx = self._ctx(broker_result_type="SL")
        assert classifier.classify(ctx) == ResultType.STOP_LOSS

    def test_broker_label_tp_maps_to_take_profit(self, classifier):
        ctx = self._ctx(broker_result_type="TP")
        assert classifier.classify(ctx) == ResultType.TAKE_PROFIT

    def test_broker_label_be_maps_to_breakeven(self, classifier):
        ctx = self._ctx(broker_result_type="BE")
        assert classifier.classify(ctx) == ResultType.BREAKEVEN

    def test_broker_label_sp_maps_to_manual(self, classifier):
        ctx = self._ctx(broker_result_type="SP")
        assert classifier.classify(ctx) == ResultType.MANUAL

    def test_unknown_broker_label_falls_through_to_detection(self, classifier):
        ctx = self._ctx(broker_result_type="UNKNOWN")
        assert classifier.classify(ctx) == ResultType.TAKE_PROFIT

    def test_close_reason_stop_loss_wins_over_price(self, classifier):
        # Exit is at TP price, but reason says SL was hit first.
        ctx = self._ctx(exit_=130.0, close_reason=CloseReason.STOP_LOSS_HIT)
        assert classifier.classify(ctx) == ResultType.STOP_LOSS

    def test_close_reason_take_profit_wins_over_price(self, classifier):
        ctx = self._ctx(exit_=90.0, close_reason=CloseReason.TAKE_PROFIT_HIT)
        assert classifier.classify(ctx) == ResultType.TAKE_PROFIT

    def test_price_based_take_profit(self, classifier):
        ctx = self._ctx(exit_=130.0)
        assert classifier.classify(ctx) == ResultType.TAKE_PROFIT

    def test_price_based_stop_loss(self, classifier):
        ctx = self._ctx(exit_=90.0)
        assert classifier.classify(ctx) == ResultType.STOP_LOSS

    def test_price_based_breakeven(self, classifier):
        ctx = self._ctx(exit_=100.0)
        assert classifier.classify(ctx) == ResultType.BREAKEVEN

    def test_session_end_is_never_sl_tp(self, classifier):
        # Exit is exactly at TP, but session end should not be classified as TP.
        ctx = self._ctx(exit_=130.0, close_reason=CloseReason.SESSION_END)
        assert classifier.classify(ctx) == ResultType.MANUAL

    def test_session_end_breakeven_still_detected(self, classifier):
        ctx = self._ctx(exit_=100.0, close_reason=CloseReason.SESSION_END)
        assert classifier.classify(ctx) == ResultType.BREAKEVEN

    def test_stream_end_defaults_to_manual(self, classifier):
        ctx = self._ctx(exit_=110.0, close_reason=CloseReason.STREAM_END)
        assert classifier.classify(ctx) == ResultType.MANUAL

    def test_broker_fill_without_label_defaults_to_manual_close(self, classifier):
        ctx = self._ctx(exit_=110.0, close_reason=CloseReason.BROKER_FILL)
        assert classifier.classify(ctx) == ResultType.MANUAL_CLOSE

    def test_manual_close_reason_defaults_to_manual_close(self, classifier):
        ctx = self._ctx(exit_=110.0, close_reason=CloseReason.MANUAL_CLOSE)
        assert classifier.classify(ctx) == ResultType.MANUAL_CLOSE

    def test_no_reason_no_label_price_inconclusive_defaults_to_manual_close(self, classifier):
        ctx = self._ctx(exit_=110.0)
        assert classifier.classify(ctx) == ResultType.MANUAL_CLOSE

    def test_short_direction_tp(self, classifier):
        ctx = self._ctx(
            direction=Direction.SHORT,
            entry=100.0,
            exit_=90.0,
            sl=110.0,
            tp=90.0,
        )
        assert classifier.classify(ctx) == ResultType.TAKE_PROFIT
