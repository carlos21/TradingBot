"""Unit tests for src.application.use_cases.trade_open_use_case."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.application.use_cases.trade_open_use_case import TradeOpenUseCase


@pytest.fixture
def use_case():
    repo = MagicMock()
    executor = MagicMock()
    publisher = MagicMock()
    logger = MagicMock()
    trade_logger = MagicMock()
    repo.insert_trade.return_value = MagicMock(
        trade_id="T1",
        pair="MNQ",
        trade_type="long",
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=130.0,
        risk=10.0,
        risk_dollars=1000.0,
        risk_pct=1.0,
        contracts=5.0,
        entry_time=datetime.fromtimestamp(1000.0, tz=timezone.utc),
        account="Sim101",
        signal_id="S1",
        instrument="MNQ",
    )
    return (
        TradeOpenUseCase(
            trade_repository=repo,
            trade_executor=executor,
            event_publisher=publisher,
            logger=logger,
            trade_logger=trade_logger,
            point_value=2.0,
            account_balance=100_000.0,
            risk_per_trade=500.0,
            risk_pct_per_trade=None,
            use_fractional_lots=False,
            instrument="MNQ",
            live_mode=False,
        ),
        repo,
        executor,
        publisher,
        logger,
        trade_logger,
    )


class TestTradeOpenUseCaseExecute:
    def test_execute_success(self, use_case):
        uc, repo, executor, publisher, logger, _ = use_case

        result = uc.execute(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            rr_ratio=5.0,
            source="test",
            account="Sim101",
            signal_id="S1",
        )

        assert result.trade_id == "T1"
        assert result.pair == "MNQ"
        assert result.trade_type == "long"
        assert result.contracts == 25  # 500 / (10 * 2)
        assert result.instrument == "MNQ"
        repo.insert_trade.assert_called_once()
        executor.on_trade_open.assert_called_once()
        assert publisher.emit.call_args[0][0] == "trade_open"
        payload = publisher.emit.call_args[0][1]
        assert payload["trade_id"] == "T1"
        assert payload["contracts"] == 25
        logger.info.assert_called_once()

    def test_execute_with_risk_overrides(self, use_case):
        uc, repo, executor, _, _, _ = use_case

        uc.execute(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            risk_per_trade_override=1000.0,
            risk_pct_per_trade_override=None,
        )

        forwarded = executor.on_trade_open.call_args[0][0]
        assert forwarded["risk_usd"] == 1000.0
        assert forwarded["risk_pct"] is None
        assert forwarded["contracts"] == 50

    def test_execute_with_accounts_repo(self):
        account = MagicMock()
        account.risk_usd = 250.0
        account.risk_pct = None
        accounts_repo = MagicMock()
        accounts_repo.get_account.return_value = account
        accounts_repo.list_accounts.return_value = [account]
        repo = MagicMock()
        repo.insert_trade.return_value = MagicMock(
            trade_id="T1",
            entry_time=datetime.fromtimestamp(1000.0, tz=timezone.utc),
        )
        executor = MagicMock()

        uc = TradeOpenUseCase(
            trade_repository=repo,
            trade_executor=executor,
            event_publisher=None,
            logger=None,
            point_value=2.0,
            account_balance=100_000.0,
            risk_per_trade=500.0,
            accounts_repo=accounts_repo,
        )

        uc.execute(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            account="Sim101",
        )

        forwarded = executor.on_trade_open.call_args[0][0]
        assert forwarded["risk_usd"] == 250.0
        accounts_repo.get_account.assert_called_once_with("Sim101")

    def test_execute_uses_target_account_risk_pct(self):
        account_a = MagicMock()
        account_a.risk_usd = None
        account_a.risk_pct = 2.0
        account_b = MagicMock()
        account_b.risk_usd = None
        account_b.risk_pct = 1.6
        accounts_repo = MagicMock()
        accounts_repo.get_account.side_effect = lambda name: {"Account-A": account_a, "Account-B": account_b}[name]
        accounts_repo.list_accounts.return_value = [account_a, account_b]
        repo = MagicMock()
        repo.insert_trade.return_value = MagicMock(
            trade_id="T1",
            entry_time=datetime.fromtimestamp(1000.0, tz=timezone.utc),
        )
        executor = MagicMock()

        uc = TradeOpenUseCase(
            trade_repository=repo,
            trade_executor=executor,
            event_publisher=None,
            logger=None,
            point_value=2.0,
            account_balance=100_000.0,
            accounts_repo=accounts_repo,
        )

        uc.execute(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            account="Account-B",
        )

        forwarded = executor.on_trade_open.call_args[0][0]
        # 1.6% of 100k = 1600 budget; risk_per_contract = 10*2 = 20; contracts = 80
        assert forwarded["risk_pct"] == 1.6
        assert forwarded["contracts"] == 80
        assert forwarded["risk_usd"] is None

    def test_execute_fallback_to_defaults_when_account_not_found(self):
        accounts_repo = MagicMock()
        accounts_repo.get_account.return_value = None
        accounts_repo.list_accounts.return_value = []
        repo = MagicMock()
        repo.insert_trade.return_value = MagicMock(
            trade_id="T1",
            entry_time=datetime.fromtimestamp(1000.0, tz=timezone.utc),
        )
        executor = MagicMock()

        uc = TradeOpenUseCase(
            trade_repository=repo,
            trade_executor=executor,
            event_publisher=None,
            logger=None,
            point_value=2.0,
            account_balance=100_000.0,
            risk_per_trade=500.0,
            accounts_repo=accounts_repo,
        )

        uc.execute(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            account="MissingAccount",
        )

        forwarded = executor.on_trade_open.call_args[0][0]
        # Constructor default: $500 / $20 per contract = 25 contracts
        assert forwarded["risk_usd"] == 500.0
        assert forwarded["contracts"] == 25

    def test_execute_backtest_risk_pct_requires_positive_balance(self, use_case):
        uc, _, _, _, _, _ = use_case
        uc._account_balance = 0.0

        with pytest.raises(ValueError, match="account_balance"):
            uc.execute(
                pair="MNQ",
                trade_type="long",
                entry_price=100.0,
                stop_loss=90.0,
                take_profit=130.0,
                risk=10.0,
                entry_time=1000.0,
                risk_pct_per_trade_override=1.0,
            )

    def test_execute_backtest_risk_pct_with_balance_succeeds(self, use_case):
        uc, repo, executor, _, _, _ = use_case
        uc._account_balance = 100_000.0

        uc.execute(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            risk_pct_per_trade_override=1.0,
        )

        forwarded = executor.on_trade_open.call_args[0][0]
        assert forwarded["risk_pct"] == 1.0
        assert forwarded["contracts"] == 50

    def test_execute_live_mode_allows_zero_balance(self, use_case):
        uc, repo, executor, _, _, _ = use_case
        uc._live_mode = True
        uc._account_balance = 0.0

        uc.execute(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            risk_pct_per_trade_override=1.0,
        )

        repo.insert_trade.assert_called_once()

    def test_execute_executor_failure_rolls_back_and_raises(self, use_case):
        uc, repo, executor, publisher, logger, _ = use_case
        executor.on_trade_open.side_effect = RuntimeError("executor down")

        with pytest.raises(RuntimeError, match="executor down"):
            uc.execute(
                pair="MNQ",
                trade_type="long",
                entry_price=100.0,
                stop_loss=90.0,
                take_profit=130.0,
                risk=10.0,
                entry_time=1000.0,
            )

        repo.close_trade.assert_called_once()
        publisher.emit.assert_not_called()
        logger.error.assert_called_once()

    def test_execute_executor_failure_rollback_logs_db_error(self, use_case):
        uc, repo, executor, _, logger, _ = use_case
        executor.on_trade_open.side_effect = RuntimeError("executor down")
        repo.close_trade.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError, match="executor down"):
            uc.execute(
                pair="MNQ",
                trade_type="long",
                entry_price=100.0,
                stop_loss=90.0,
                take_profit=130.0,
                risk=10.0,
                entry_time=1000.0,
            )

        assert logger.error.call_count == 2

    def test_execute_with_trade_id(self, use_case):
        uc, repo, _, _, _, _ = use_case

        uc.execute(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            trade_id="TX",
        )

        assert repo.insert_trade.call_args.kwargs["trade_id"] == "TX"

    def test_execute_with_params(self, use_case):
        uc, repo, _, _, _, _ = use_case

        uc.execute(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            params={"key": "value"},
        )

        assert repo.insert_trade.call_args.kwargs["params"] == {"key": "value"}


class TestTradeOpenUseCaseHelpers:
    def test_effective_risk_config_override_wins(self):
        repo = MagicMock()
        uc = TradeOpenUseCase(
            trade_repository=repo,
            trade_executor=MagicMock(),
            event_publisher=None,
            logger=None,
            risk_per_trade=500.0,
            risk_pct_per_trade=1.0,
        )

        assert uc._effective_risk_config(1000.0, 2.0) == (1000.0, 2.0)

    def test_effective_risk_config_partial_override(self):
        repo = MagicMock()
        uc = TradeOpenUseCase(
            trade_repository=repo,
            trade_executor=MagicMock(),
            event_publisher=None,
            logger=None,
            risk_per_trade=500.0,
            risk_pct_per_trade=1.0,
        )

        assert uc._effective_risk_config(risk_pct_per_trade_override=2.5) == (None, 2.5)

    def test_get_current_risk_from_accounts_repo(self):
        account = MagicMock()
        account.risk_usd = 300.0
        account.risk_pct = 1.5
        accounts_repo = MagicMock()
        accounts_repo.get_account.return_value = account
        accounts_repo.list_accounts.return_value = [account]
        repo = MagicMock()

        uc = TradeOpenUseCase(
            trade_repository=repo,
            trade_executor=MagicMock(),
            event_publisher=None,
            logger=None,
            risk_per_trade=500.0,
            risk_pct_per_trade=1.0,
            accounts_repo=accounts_repo,
        )

        assert uc._get_current_risk("Sim101") == (300.0, 1.5)
        accounts_repo.get_account.assert_called_once_with("Sim101")

    def test_get_current_risk_uses_requested_account(self):
        account_a = MagicMock()
        account_a.risk_usd = None
        account_a.risk_pct = 2.0
        account_b = MagicMock()
        account_b.risk_usd = 750.0
        account_b.risk_pct = 1.6
        accounts_repo = MagicMock()
        accounts_repo.get_account.side_effect = lambda name: {"Account-A": account_a, "Account-B": account_b}[name]
        accounts_repo.list_accounts.return_value = [account_a, account_b]
        repo = MagicMock()

        uc = TradeOpenUseCase(
            trade_repository=repo,
            trade_executor=MagicMock(),
            event_publisher=None,
            logger=None,
            risk_per_trade=500.0,
            risk_pct_per_trade=1.0,
            accounts_repo=accounts_repo,
        )

        assert uc._get_current_risk("Account-B") == (750.0, 1.6)
        assert uc._get_current_risk("Account-A") == (None, 2.0)

    def test_get_current_risk_no_account_name_uses_defaults(self):
        account_a = MagicMock()
        account_a.risk_usd = None
        account_a.risk_pct = 2.0
        account_b = MagicMock()
        account_b.risk_usd = None
        account_b.risk_pct = 1.6
        accounts_repo = MagicMock()
        accounts_repo.list_accounts.return_value = [account_a, account_b]
        repo = MagicMock()

        uc = TradeOpenUseCase(
            trade_repository=repo,
            trade_executor=MagicMock(),
            event_publisher=None,
            logger=None,
            risk_per_trade=500.0,
            risk_pct_per_trade=1.0,
            accounts_repo=accounts_repo,
        )

        # No account specified -> constructor defaults, never another account
        assert uc._get_current_risk() == (500.0, 1.0)
        accounts_repo.get_account.assert_not_called()

    def test_get_current_risk_repo_exception_uses_defaults(self):
        accounts_repo = MagicMock()
        accounts_repo.list_accounts.side_effect = Exception("db down")
        repo = MagicMock()

        uc = TradeOpenUseCase(
            trade_repository=repo,
            trade_executor=MagicMock(),
            event_publisher=None,
            logger=None,
            risk_per_trade=500.0,
            risk_pct_per_trade=1.0,
            accounts_repo=accounts_repo,
        )

        assert uc._get_current_risk() == (500.0, 1.0)

    def test_calc_contracts_fractional(self):
        repo = MagicMock()
        uc = TradeOpenUseCase(
            trade_repository=repo,
            trade_executor=MagicMock(),
            event_publisher=None,
            logger=None,
            account_balance=100_000.0,
            risk_per_trade=500.0,
            use_fractional_lots=True,
        )

        contracts = uc._calc_contracts(20.0)

        assert contracts == 500.0 / 20.0

    def test_update_account_balance(self):
        repo = MagicMock()
        uc = TradeOpenUseCase(
            trade_repository=repo,
            trade_executor=MagicMock(),
            event_publisher=None,
            logger=None,
            account_balance=100_000.0,
        )

        uc.update_account_balance(75_000.0)

        assert uc._account_balance == 75_000.0
