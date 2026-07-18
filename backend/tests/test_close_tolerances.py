"""Tests for per-instrument close-classification tolerances.

Proves that be_threshold_points / sl_tp_tolerance are honored end-to-end:
(a) TradeCloseService classifies BE with its own threshold.
(b) DefaultResultTypeClassifier passes its tolerances to FinancialCalc.
(c) BaseLiquidityStrategy stores the values and wires them into its
    StrategyTradeService.
"""

from datetime import datetime, timezone

from src.domain.result_type_classifier import (
    ClassificationContext,
    DefaultResultTypeClassifier,
)
from src.domain.types import CloseReason, Direction, ResultType
from src.services.trade_close_service import TradeCloseService
from src.services.trade_manager import TradeManager
from src.strategies.liquidity_v2.base_strategy import BaseLiquidityStrategy
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from tests.fakes import (
    DummySocketIO,
    FakeLineRepository,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
)


def _make_trade(trade_type="long", entry=100.0, stop_loss=90.0, take_profit=130.0, risk=10.0, contracts=1.0):
    return {
        "trade_id": "T1",
        "type": trade_type,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk": risk,
        "contracts": contracts,
        "pair": "MNQ",
    }


class TestTradeCloseServiceTolerances:
    """(a) TradeCloseService honors its own be_threshold_points."""

    def _close(self, svc: TradeCloseService, exit_price: float):
        return svc.close_trade(
            trade=_make_trade(),
            exit_price=exit_price,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            close_reason=CloseReason.MANUAL_CLOSE,
        )

    def test_exit_1pt_from_entry_is_be_with_default_threshold(self):
        svc = TradeCloseService(trade_repository=FakeTradeRepository(), point_value=2.0)
        result = self._close(svc, exit_price=101.0)
        assert result.result_type == "BE"

    def test_exit_1pt_from_entry_is_not_be_with_tight_threshold(self):
        svc = TradeCloseService(
            trade_repository=FakeTradeRepository(),
            point_value=2.0,
            be_threshold_points=0.75,
        )
        result = self._close(svc, exit_price=101.0)
        assert result.result_type != "BE"
        assert result.result_type == "CLOSE"

    def test_tolerances_stored_on_service(self):
        svc = TradeCloseService(
            trade_repository=FakeTradeRepository(),
            be_threshold_points=0.75,
            sl_tp_tolerance=0.25,
        )
        assert svc.be_threshold_points == 0.75
        assert svc.sl_tp_tolerance == 0.25


class TestResultTypeClassifierTolerances:
    """(b) DefaultResultTypeClassifier passes its tolerances through."""

    def _ctx(self, exit_: float, sl: float | None = 90.0) -> ClassificationContext:
        return ClassificationContext(
            direction=Direction.LONG,
            entry_price=100.0,
            exit_price=exit_,
            stop_loss=sl,
            take_profit=130.0,
        )

    def test_sl_proximity_default_tolerance(self):
        # Exit 0.4 pts from SL: inside the default 0.5 tolerance -> SL.
        classifier = DefaultResultTypeClassifier()
        assert classifier.classify(self._ctx(exit_=90.4)) == ResultType.STOP_LOSS

    def test_sl_proximity_tight_tolerance_flips_classification(self):
        # Same exit with 0.25 tolerance: 0.4 pts is too far -> not SL.
        classifier = DefaultResultTypeClassifier(sl_tp_tolerance=0.25)
        assert classifier.classify(self._ctx(exit_=90.4)) == ResultType.MANUAL_CLOSE

    def test_be_threshold_default(self):
        # Exit 1.0 pt from entry: inside the default 2.0 threshold -> BE.
        classifier = DefaultResultTypeClassifier()
        assert classifier.classify(self._ctx(exit_=101.0)) == ResultType.BREAKEVEN

    def test_be_threshold_tight_flips_classification(self):
        # Same exit with 0.75 threshold: 1.0 pt is too far -> not BE.
        classifier = DefaultResultTypeClassifier(be_threshold_points=0.75)
        assert classifier.classify(self._ctx(exit_=101.0)) == ResultType.MANUAL_CLOSE


class TestBaseLiquidityStrategyTolerances:
    """(c) BaseLiquidityStrategy stores tolerances and wires them into its trade service."""

    def _make_strategy(self, **kwargs) -> BaseLiquidityStrategy:
        trade_repo = FakeTradeRepository()
        trade_manager = TradeManager(
            trade_repo,
            DummySocketIO(),
            trade_executor=FakeTradeExecutor(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
        )
        return BaseLiquidityStrategy(
            min_stop_loss=10.0,
            max_bounce=90.0,
            event_publisher=DummySocketIO(),
            line_repository=FakeLineRepository(),
            trade_repository=trade_repo,
            trade_manager=trade_manager,
            extra_sl_space=0.0,
            options=DEFAULT_STRATEGY_OPTIONS,
            rr_ratio=3.3,
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
            **kwargs,
        )

    def test_defaults_match_global_values(self):
        strategy = self._make_strategy()
        assert strategy.be_threshold_points == 2.0
        assert strategy.sl_tp_tolerance == 0.5

    def test_custom_tolerances_stored_and_propagated(self):
        strategy = self._make_strategy(be_threshold_points=0.75, sl_tp_tolerance=0.25)
        assert strategy.be_threshold_points == 0.75
        assert strategy.sl_tp_tolerance == 0.25
        # Wired into the strategy's own trade service (and its classifier).
        assert strategy._trade_service._be_threshold_points == 0.75
        assert strategy._trade_service._sl_tp_tolerance == 0.25
