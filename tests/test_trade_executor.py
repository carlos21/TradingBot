"""Tests for src/services/trade_executor.py and src/gateway/executor.py."""

from src.infrastructure.gateway.executor import MultiAccountExecutor
from src.services.trade_executor import NoOpExecutor, TradeExecutor
from src.services.trade_manager import TradeManager
from tests.fakes import (
    DummySocketIO,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
)


class FakeGateway:
    def __init__(self):
        self.closes = []
        self.modifies = []

    def send_close_order(self, trade_id, reason, account=None):
        self.closes.append((trade_id, account))

    def send_modify_order(self, trade_id, stop_loss, account=None):
        self.modifies.append((trade_id, stop_loss, account))


class FakeGatewayExecutor(TradeExecutor):
    def __init__(self):
        self._gateway = FakeGateway()
        self.opens = []

    def on_trade_open(self, trade):
        self.opens.append(trade)

    def on_trade_close(self, trade_id, exit_price, account=None):
        self._gateway.send_close_order(trade_id, reason="strategy", account=account)

    def on_sl_update(self, trade_id, new_sl, account=None):
        self._gateway.send_modify_order(trade_id, stop_loss=new_sl, account=account)


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

    def test_on_trade_open_forwards_with_account(self):
        """Trades with an account are forwarded to the gateway executor."""
        tm = _make_trade_manager()
        gateway = FakeGatewayExecutor()
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[],
            gateway_executor=gateway,
            logger=FakeLogger(),
        )

        trade = {
            "trade_id": "T1",
            "pair": "MNQ",
            "type": "long",
            "entry": 100.0,
            "account": "acct1",
        }
        executor.on_trade_open(trade)

        assert len(gateway.opens) == 1
        assert gateway.opens[0]["trade_id"] == "T1"

    def test_on_trade_open_rejects_missing_account(self):
        """Trades without an account are rejected instead of recursing."""
        tm = _make_trade_manager()
        gateway = FakeGatewayExecutor()
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[],
            gateway_executor=gateway,
            logger=FakeLogger(),
        )

        trade = {"trade_id": "T1", "pair": "MNQ", "type": "long"}
        try:
            executor.on_trade_open(trade)
        except RuntimeError as e:
            assert "no account" in str(e)
        else:
            raise AssertionError("Expected RuntimeError for missing account")

    def test_on_trade_close_looks_up_account_from_db(self):
        """Close uses the account stored on the DB trade record."""
        tm = _make_trade_manager()
        repo = tm.trade_repository
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            trade_id="T1",
            account="acct1",
        )

        gateway_ex = FakeGatewayExecutor()
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[],
            gateway_executor=gateway_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_close("T1", 110.0)
        assert len(gateway_ex._gateway.closes) == 1
        assert gateway_ex._gateway.closes[0] == ("T1", "acct1")

    def test_on_trade_close_dedup_within_window(self):
        """Duplicate close commands within the dedup window are ignored."""
        tm = _make_trade_manager()
        repo = tm.trade_repository
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            trade_id="T1",
            account="acct1",
        )

        gateway_ex = FakeGatewayExecutor()
        executor = MultiAccountExecutor(
            trade_manager=tm,
            account_configs=[],
            gateway_executor=gateway_ex,
            logger=FakeLogger(),
        )

        executor.on_trade_close("T1", 110.0)
        executor.on_trade_close("T1", 110.0)
        assert len(gateway_ex._gateway.closes) == 1


