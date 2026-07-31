"""Unit tests for src.application.use_cases.trade_close_use_case."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.application.use_cases.trade_close_use_case import TradeCloseUseCase
from src.domain.result_type_classifier import ClassificationContext, DefaultResultTypeClassifier
from src.domain.types import Direction, ResultType


@pytest.fixture
def use_case():
    repo = MagicMock()
    executor = MagicMock()
    publisher = MagicMock()
    logger = MagicMock()
    trade_logger = MagicMock()
    classifier = DefaultResultTypeClassifier()
    return (
        TradeCloseUseCase(
            trade_repository=repo,
            trade_executor=executor,
            event_publisher=publisher,
            logger=logger,
            trade_logger=trade_logger,
            point_value=2.0,
            fee_per_rt=1.5,
            broker_spread=0.0,
            result_type_classifier=classifier,
        ),
        repo,
        executor,
        publisher,
        logger,
        trade_logger,
    )


def _make_trade():
    return {
        "trade_id": "T1",
        "pair": "MNQ",
        "type": "long",
        "entry": 100.0,
        "entry_price": 100.0,
        "stop_loss": 90.0,
        "take_profit": 130.0,
        "risk": 10.0,
        "contracts": 5.0,
        "line_level": 95.0,
        "is_reentry": True,
        "is_phantom": False,
        "signal_id": "S1",
    }


class TestTradeCloseUseCaseExecute:
    def test_execute_success(self, use_case):
        uc, repo, executor, publisher, logger, trade_logger = use_case
        trade = _make_trade()

        result = uc.execute(trade, 110.0, 2000.0)

        assert result.trade_id == "T1"
        assert result.exit_price == 110.0
        assert result.result == 1.0
        assert result.result_type == "CLOSE"
        assert result.fees == 7.5
        executor.on_trade_close.assert_called_once_with("T1", 110.0)
        repo.close_trade.assert_called_once()
        assert publisher.emit.call_args[0][0] == "trade_close"
        payload = publisher.emit.call_args[0][1]
        assert payload["trade_id"] == "T1"
        assert payload["result"] == 1.0
        logger.info.assert_called_once()
        assert trade_logger.log.call_count == 2

    def test_execute_hits_take_profit(self, use_case):
        uc, repo, _, _, _, _ = use_case
        trade = _make_trade()

        result = uc.execute(trade, 130.0, 2000.0)

        assert result.result_type == "TP"
        assert result.result == 3.0

    def test_execute_hits_stop_loss(self, use_case):
        uc, repo, _, _, _, _ = use_case
        trade = _make_trade()

        result = uc.execute(trade, 90.0, 2000.0)

        assert result.result_type == "SL"
        assert result.result == -1.0

    def test_execute_short_trade(self, use_case):
        uc, repo, _, _, _, _ = use_case
        trade = _make_trade()
        trade["type"] = "short"

        result = uc.execute(trade, 90.0, 2000.0)

        assert result.result == 1.0

    def test_execute_broker_pnl_source_of_truth(self, use_case):
        uc, repo, _, _, _, _ = use_case
        trade = _make_trade()

        result = uc.execute(trade, 110.0, 2000.0, broker_pnl_usd=123.45, broker_fees=2.5)

        # broker_pnl_usd is treated as gross realized PnL; pnl_usd is net after fees.
        assert result.gross_pnl == 123.45
        assert result.fees == 2.5
        assert result.pnl_usd == 120.95
        assert result.realized_pnl == 120.95
        assert result.result == pytest.approx(123.45 / (5.0 * 10.0 * 2.0))

    def test_execute_result_type_override(self, use_case):
        uc, repo, _, _, _, _ = use_case
        trade = _make_trade()

        result = uc.execute(trade, 110.0, 2000.0, result_type_override="CLOSE")

        assert result.result_type == "CLOSE"

    def test_execute_skip_executor(self, use_case):
        uc, repo, executor, _, _, _ = use_case
        trade = _make_trade()

        result = uc.execute(trade, 110.0, 2000.0, skip_executor=True)

        assert result is not None
        executor.on_trade_close.assert_not_called()
        repo.close_trade.assert_called_once()

    def test_execute_executor_failure_raises(self, use_case):
        uc, repo, executor, _, logger, trade_logger = use_case
        executor.on_trade_close.side_effect = RuntimeError("executor down")
        trade = _make_trade()

        with pytest.raises(RuntimeError, match="executor down"):
            uc.execute(trade, 110.0, 2000.0)

        repo.close_trade.assert_not_called()
        logger.error.assert_called_once()
        trade_logger.log.assert_called_once()

    def test_execute_db_error_raise_true(self, use_case):
        uc, repo, _, publisher, logger, trade_logger = use_case
        repo.close_trade.side_effect = RuntimeError("db down")
        trade = _make_trade()

        with pytest.raises(RuntimeError, match="db down"):
            uc.execute(trade, 110.0, 2000.0)

        publisher.emit.assert_not_called()
        logger.error.assert_called_once()
        trade_logger.log.assert_called_once()

    def test_execute_db_error_raise_false_returns_none(self, use_case):
        uc, repo, publisher, _, _, _ = use_case
        repo.close_trade.side_effect = RuntimeError("db down")
        trade = _make_trade()

        result = uc.execute(trade, 110.0, 2000.0, raise_on_db_error=False)

        assert result is None
        publisher.emit.assert_not_called()

    def test_execute_extreme_excursion_in_payload(self, use_case):
        uc, repo, _, publisher, _, _ = use_case
        trade = _make_trade()

        uc.execute(trade, 110.0, 2000.0, extreme_excursion=85.0)

        payload = publisher.emit.call_args[0][1]
        assert payload["extreme_excursion"] == 85.0

    def test_execute_default_extreme_excursion_is_exit_price(self, use_case):
        uc, repo, _, publisher, _, _ = use_case
        trade = _make_trade()

        uc.execute(trade, 110.0, 2000.0)

        payload = publisher.emit.call_args[0][1]
        assert payload["extreme_excursion"] == 110.0

    def test_execute_with_datetime_exit_time(self, use_case):
        uc, repo, _, publisher, _, _ = use_case
        trade = _make_trade()
        exit_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        result = uc.execute(trade, 110.0, exit_dt)

        assert result.exit_time == exit_dt.timestamp()
        repo.close_trade.assert_called_once()
        passed_exit_time = repo.close_trade.call_args.kwargs["exit_time"]
        assert passed_exit_time == exit_dt
        payload = publisher.emit.call_args[0][1]
        assert payload["exit_time"] == exit_dt.timestamp()

    def test_execute_with_zero_risk_defaults_to_one(self, use_case):
        uc, repo, _, _, _, _ = use_case
        trade = _make_trade()
        trade["risk"] = 0

        result = uc.execute(trade, 110.0, 2000.0)

        assert result.result == 10.0  # (110 - 100) / 1

    def test_execute_broker_spread_adjustment(self, use_case):
        uc, repo, _, _, _, _ = use_case
        uc._broker_spread = 1.0
        trade = _make_trade()

        result = uc.execute(trade, 110.0, 2000.0)

        assert result.fees == 7.5 + 5.0 * 1.0 * 2.0
        assert result.pnl_usd == 100.0 - 7.5 - 10.0

    def test_execute_broker_spread_ignored_when_broker_pnl_given(self, use_case):
        uc, repo, _, _, _, _ = use_case
        uc._broker_spread = 1.0
        trade = _make_trade()

        result = uc.execute(trade, 110.0, 2000.0, broker_pnl_usd=50.0)

        assert result.pnl_usd == 50.0

    def test_execute_custom_log_event_and_message(self, use_case):
        uc, repo, _, _, _, trade_logger = use_case
        trade = _make_trade()

        uc.execute(trade, 110.0, 2000.0, log_event="SESSION_END", log_message="custom msg")

        trade_logger.log.assert_any_call("T1", "SESSION_END", "custom msg")
        trade_logger.log.assert_any_call("T1", "CLOSE", "Persisted to DB")

    def test_execute_close_result_fields(self, use_case):
        uc, repo, _, _, _, _ = use_case
        trade = _make_trade()

        result = uc.execute(trade, 110.0, 2000.0)

        assert result.pair == "MNQ"
        assert result.trade_type == "long"
        assert result.exit_time == 2000.0
        assert result.fees is not None
        assert result.pnl_usd is not None


class TestTradeCloseUseCaseClassifier:
    def test_custom_classifier_used(self):
        class AlwaysTp:
            def classify(self, ctx):
                return ResultType.TAKE_PROFIT

        repo = MagicMock()
        executor = MagicMock()
        uc = TradeCloseUseCase(
            trade_repository=repo,
            trade_executor=executor,
            event_publisher=None,
            logger=None,
            result_type_classifier=AlwaysTp(),
        )
        trade = _make_trade()

        result = uc.execute(trade, 110.0, 2000.0)

        assert result.result_type == "TP"

    def test_default_classifier_with_broker_label(self):
        classifier = DefaultResultTypeClassifier()
        ctx = ClassificationContext(
            direction=Direction.LONG,
            entry_price=100.0,
            exit_price=90.0,
            stop_loss=90.0,
            take_profit=130.0,
            broker_result_type="BE",
        )

        assert classifier.classify(ctx) == ResultType.BREAKEVEN

    def test_default_classifier_invalid_broker_label_falls_back(self):
        classifier = DefaultResultTypeClassifier()
        ctx = ClassificationContext(
            direction=Direction.LONG,
            entry_price=100.0,
            exit_price=90.0,
            stop_loss=90.0,
            take_profit=130.0,
            broker_result_type="UNKNOWN",
        )

        assert classifier.classify(ctx) == ResultType.STOP_LOSS
