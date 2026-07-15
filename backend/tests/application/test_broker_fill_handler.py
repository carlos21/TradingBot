"""Unit tests for src.application.use_cases.broker_fill_handler."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.application.use_cases.broker_fill_handler import BrokerFillHandler
from src.financial_calc import FinancialCalc


@pytest.fixture
def handler():
    repo = MagicMock()
    publisher = MagicMock()
    logger = MagicMock()
    trade_logger = MagicMock()
    analytics = MagicMock()
    return BrokerFillHandler(
        trade_repository=repo,
        event_publisher=publisher,
        logger=logger,
        trade_logger=trade_logger,
        analytics=analytics,
        point_value=2.0,
        account_balance=100_000.0,
        risk_per_trade=500.0,
        risk_pct_per_trade=None,
        use_fractional_lots=False,
    ), repo, publisher, logger, trade_logger, analytics


def _make_trade():
    return {
        "trade_id": "T1",
        "signal_id": "S1",
        "type": "long",
        "entry": 100.0,
        "stop_loss": 90.0,
        "take_profit": 130.0,
        "risk": 10.0,
        "contracts": 1.0,
        "account": "Sim101",
    }


class TestBrokerFillHandlerEntryFill:
    def test_handle_entry_fill_updates_trade_and_persists(self, handler):
        h, repo, publisher, logger, trade_logger, analytics = handler
        trade = _make_trade()

        result = h.handle_entry_fill(trade, 101.0)

        assert result["entry"] == 101.0
        assert result["contracts"] == 23  # risk 11 (101-90), 500/(11*2) -> 23
        assert "account_balance" not in result
        repo.update_entry_price.assert_called_once_with("T1", 101.0)
        repo.update_risk_fields.assert_called_once()
        repo.update_contracts.assert_called_once_with("T1", 23)
        repo.update_account_balance.assert_not_called()
        publisher.emit.assert_called_once()
        assert publisher.emit.call_args[0][0] == "trade_entry_update"
        logger.info.assert_called_once()
        trade_logger.log.assert_called_once()
        analytics.capture_exception.assert_not_called()

    def test_handle_entry_fill_uses_broker_quantity(self, handler):
        h, repo, _, _, _, _ = handler
        trade = _make_trade()

        result = h.handle_entry_fill(trade, 101.0, quantity=7.0)

        assert result["contracts"] == 7.0
        repo.update_contracts.assert_called_once_with("T1", 7.0)

    def test_handle_entry_fill_zero_quantity_falls_back_to_risk(self, handler):
        h, repo, _, _, _, _ = handler
        trade = _make_trade()

        result = h.handle_entry_fill(trade, 101.0, quantity=0)

        assert result["contracts"] == 23
        repo.update_contracts.assert_called_once_with("T1", 23)

    def test_handle_entry_fill_updates_account_balance(self, handler):
        h, repo, _, _, _, _ = handler
        trade = _make_trade()

        result = h.handle_entry_fill(trade, 101.0, account_balance=50_000.0)

        assert h._account_balance == 50_000.0
        assert result["account_balance"] == 50_000.0
        repo.update_account_balance.assert_called_once_with("T1", 50_000.0)

    def test_handle_entry_fill_negative_balance_ignored(self, handler):
        h, repo, _, _, _, _ = handler
        trade = _make_trade()

        result = h.handle_entry_fill(trade, 101.0, account_balance=-1.0)

        assert h._account_balance == 100_000.0
        assert "account_balance" not in result
        repo.update_account_balance.assert_not_called()

    def test_handle_entry_fill_updates_sl_tp(self, handler):
        h, repo, _, _, _, _ = handler
        trade = _make_trade()

        result = h.handle_entry_fill(trade, 101.0, stop_loss=91.0, take_profit=121.0)

        assert result["stop_loss"] == 91.0
        assert result["take_profit"] == 121.0
        assert result["risk"] == 10.0
        repo.update_stop_loss.assert_called_once_with("T1", 91.0)
        repo.update_take_profit.assert_called_once_with("T1", 121.0)

    def test_handle_entry_fill_short_risk_calculation(self, handler):
        h, repo, _, _, _, _ = handler
        trade = _make_trade()
        trade["type"] = "short"
        trade["entry"] = 100.0
        trade["stop_loss"] = 110.0

        result = h.handle_entry_fill(trade, 101.0)

        assert result["risk"] == 9.0
        repo.update_risk_fields.assert_called_once()

    def test_handle_entry_fill_no_stop_loss_keeps_existing_risk(self, handler):
        h, repo, _, _, _, _ = handler
        trade = _make_trade()
        trade.pop("stop_loss")

        result = h.handle_entry_fill(trade, 101.0)

        assert result["risk"] == 10.0
        repo.update_risk_fields.assert_called_once()

    def test_handle_entry_fill_no_existing_risk_defaults_to_zero(self, handler):
        h, repo, _, _, _, _ = handler
        trade = _make_trade()
        trade.pop("stop_loss")
        trade.pop("risk")

        result = h.handle_entry_fill(trade, 101.0)

        assert result["risk"] == 0
        repo.update_risk_fields.assert_called_once()

    def test_handle_entry_fill_fractional_lots(self, handler):
        h, _, _, _, _, _ = handler
        h._use_fractional_lots = True
        trade = _make_trade()

        result = h.handle_entry_fill(trade, 101.0)

        expected = FinancialCalc.lots(500.0, 11.0 * 2.0)
        assert result["contracts"] == expected

    def test_handle_entry_fill_db_error_swallows_and_reports(self, handler):
        h, repo, publisher, logger, _, analytics = handler
        repo.update_entry_price.side_effect = RuntimeError("db down")
        trade = _make_trade()

        result = h.handle_entry_fill(trade, 101.0)

        assert result["entry"] == 101.0
        publisher.emit.assert_not_called()
        logger.error.assert_called_once()
        analytics.capture_exception.assert_called_once()


class TestBrokerFillHandlerRiskResolution:
    def test_get_current_risk_by_account_name(self):
        account = MagicMock()
        account.risk_usd = 300.0
        account.risk_pct = 1.5
        accounts_repo = MagicMock()
        accounts_repo.get_account_by_name.return_value = account
        repo = MagicMock()

        h = BrokerFillHandler(
            trade_repository=repo,
            event_publisher=None,
            logger=None,
            accounts_repo=accounts_repo,
            risk_per_trade=500.0,
            risk_pct_per_trade=1.0,
        )

        assert h._get_current_risk("Sim101") == (300.0, 1.5)
        accounts_repo.get_account_by_name.assert_called_once_with("Sim101")

    def test_get_current_risk_by_account_name_exception_falls_back_to_first_account(self):
        account = MagicMock()
        account.risk_usd = 400.0
        account.risk_pct = 2.0
        accounts_repo = MagicMock()
        accounts_repo.get_account_by_name.side_effect = Exception("not found")
        accounts_repo.list_accounts.return_value = [account]
        repo = MagicMock()

        h = BrokerFillHandler(
            trade_repository=repo,
            event_publisher=None,
            logger=None,
            accounts_repo=accounts_repo,
            risk_per_trade=500.0,
            risk_pct_per_trade=1.0,
        )

        assert h._get_current_risk("Sim101") == (400.0, 2.0)

    def test_get_current_risk_first_account_exception_falls_back_to_defaults(self):
        accounts_repo = MagicMock()
        accounts_repo.get_account_by_name.side_effect = Exception("not found")
        accounts_repo.list_accounts.side_effect = Exception("db down")
        repo = MagicMock()

        h = BrokerFillHandler(
            trade_repository=repo,
            event_publisher=None,
            logger=None,
            accounts_repo=accounts_repo,
            risk_per_trade=500.0,
            risk_pct_per_trade=1.0,
        )

        assert h._get_current_risk("Sim101") == (500.0, 1.0)

    def test_get_current_risk_no_account_name_uses_first_account(self):
        account = MagicMock()
        account.risk_usd = 300.0
        account.risk_pct = 1.5
        accounts_repo = MagicMock()
        accounts_repo.list_accounts.return_value = [account]
        repo = MagicMock()

        h = BrokerFillHandler(
            trade_repository=repo,
            event_publisher=None,
            logger=None,
            accounts_repo=accounts_repo,
            risk_per_trade=500.0,
            risk_pct_per_trade=1.0,
        )

        assert h._get_current_risk() == (300.0, 1.5)
        accounts_repo.list_accounts.assert_called_once()
        accounts_repo.get_account_by_name.assert_not_called()

    def test_get_current_risk_no_accounts_repo_uses_defaults(self):
        repo = MagicMock()
        h = BrokerFillHandler(
            trade_repository=repo,
            event_publisher=None,
            logger=None,
            accounts_repo=None,
            risk_per_trade=500.0,
            risk_pct_per_trade=1.0,
        )

        assert h._get_current_risk("Sim101") == (500.0, 1.0)

    def test_get_current_risk_empty_accounts_falls_back_to_defaults(self):
        accounts_repo = MagicMock()
        accounts_repo.list_accounts.return_value = []
        repo = MagicMock()

        h = BrokerFillHandler(
            trade_repository=repo,
            event_publisher=None,
            logger=None,
            accounts_repo=accounts_repo,
            risk_per_trade=500.0,
            risk_pct_per_trade=1.0,
        )

        assert h._get_current_risk() == (500.0, 1.0)


class TestBrokerFillHandlerBalance:
    def test_update_account_balance(self):
        repo = MagicMock()
        h = BrokerFillHandler(
            trade_repository=repo,
            event_publisher=None,
            logger=None,
            account_balance=100_000.0,
        )

        h.update_balance(75_000.0)

        assert h._account_balance == 75_000.0
