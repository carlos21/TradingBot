"""Tests for src/services/trade_executor.py and src/gateway/executor.py."""

from src.infrastructure.gateway.executor import MultiAccountExecutor
from src.services.trade_executor import NoOpExecutor
from src.services.trade_manager import TradeManager
from tests.fakes import (
    DummySocketIO,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
)


def _make_trade_manager():
    return TradeManager(
        trade_repository=FakeTradeRepository(),
        socketio=DummySocketIO(),
        trade_executor=FakeTradeExecutor(),
        point_value=2.0,
        account_balance=100000.0,
        logger=FakeLogger(),
    )


class TestNoOpExecutor:

    def test_on_trade_open_no_error(self):
        ex = NoOpExecutor()
        ex.on_trade_open({"trade_id": "T1"})

    def test_on_trade_close_no_error(self):
        ex = NoOpExecutor()
        ex.on_trade_close("T1", 100.0)

    def test_on_sl_update_no_error(self):
        ex = NoOpExecutor()
        ex.on_sl_update("T1", 95.0)


class TestMultiAccountExecutor:

    def test_on_trade_open_continues_on_single_account_failure(self):
        """Regression: one account failing must not abort the entire expansion."""
        tm = _make_trade_manager()
        gateway = FakeTradeExecutor()

        class BadAccountConfig:
            name = "bad_account"
            risk_usd = None
            risk_pct = None
            rr_ratio = 5.0

        class GoodAccountConfig:
            name = "good_account"
            risk_usd = None
            risk_pct = None
            rr_ratio = 5.0

        # Patch trade_manager.open_trade to fail for the bad account
        original_open_trade = tm.open_trade
        def failing_open_trade(*args, account=None, **kwargs):
            if account == "bad_account":
                raise RuntimeError("Simulated DB failure")
            return original_open_trade(*args, account=account, **kwargs)
        tm.open_trade = failing_open_trade

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[BadAccountConfig(), GoodAccountConfig()],
            gateway_executor=gateway,
            logger=FakeLogger(),
        )

        signal_trade = {
            "trade_id": "S1",
            "pair": "MNQ",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "entry_time": 1000.0,
            "rr_ratio": 5.0,
        }
        executor.on_trade_open(signal_trade)

        # Should have exactly 1 account trade (good_account)
        assert len(executor.signal_to_accounts["S1"]) == 1
        # The single account trade should be the one that succeeded
        account_trade_id = executor.signal_to_accounts["S1"][0]
        assert account_trade_id in executor.account_to_signal
        assert executor.account_to_signal[account_trade_id] == "S1"
        # Gateway should have received exactly 1 open order
        assert len(gateway.opens) == 1

    def test_on_trade_close_resolves_signal_to_accounts(self):
        """Closing a signal trade should expand to all account trades."""
        tm = _make_trade_manager()

        class FakeGatewayExecutor:
            def __init__(self):
                self.closes = []
                self._gateway = FakeGateway()

            def on_trade_open(self, trade):
                pass

            def on_trade_close(self, trade_id, exit_price):
                pass

        class FakeGateway:
            def send_close_order(self, trade_id, reason, account=None):
                FakeGatewayExecutor.closes.append((trade_id, account))

        gateway_ex = FakeGatewayExecutor()
        gateway_ex._gateway = FakeGateway()
        # Monkey-patch the class method to record on the instance
        def capture_send_close(self, trade_id, reason, account=None):
            gateway_ex.closes.append((trade_id, account))
        FakeGateway.send_close_order = capture_send_close

        class AccountConfig:
            name = "acct1"
            risk_usd = None
            risk_pct = None
            rr_ratio = 5.0

        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[AccountConfig()],
            gateway_executor=gateway_ex,
            logger=FakeLogger(),
        )

        signal_trade = {
            "trade_id": "S1",
            "pair": "MNQ",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "entry_time": 1000.0,
            "rr_ratio": 5.0,
        }
        executor.on_trade_open(signal_trade)

        # Close by signal ID
        executor.on_trade_close("S1", 110.0)
        assert len(gateway_ex.closes) == 1
        # The close should be for the account trade, not the signal ID
        assert gateway_ex.closes[0][0] != "S1"
        assert gateway_ex.closes[0][0].startswith("T")
        assert gateway_ex.closes[0][1] == "acct1"


