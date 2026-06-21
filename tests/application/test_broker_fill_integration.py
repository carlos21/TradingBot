"""Integration tests for broker entry fill handling.

Every trade is independent; a fill for one account trade must not leak into
another account's trade.
"""

from src.config.models import AccountConfig
from src.infrastructure.gateway.executor import MultiAccountExecutor
from src.services.trade_executor import TradeExecutor
from src.services.trade_manager import TradeManager
from tests.fakes import (
    DummySocketIO,
    FakeAnalyticsReporter,
    FakeLogger,
    FakeTradeRepository,
)


class RecordingGatewayExecutor(TradeExecutor):
    """Minimal fake gateway executor used by MultiAccountExecutor in tests."""

    def __init__(self):
        self.opens = []
        self.closes = []
        self.sl_updates = []
        self._gateway = self  # MultiAccountExecutor accesses _gateway directly

    def on_trade_open(self, trade):
        self.opens.append(trade)

    def on_trade_close(self, trade_id, exit_price):
        self.closes.append((trade_id, exit_price))

    def on_sl_update(self, trade_id, new_sl):
        self.sl_updates.append((trade_id, new_sl))

    def send_close_order(self, trade_id, reason, account=None):
        self.closes.append((trade_id, reason, account))

    def send_modify_order(self, trade_id, stop_loss, account=None):
        self.sl_updates.append((trade_id, stop_loss, account))


def _make_multi_account_manager(accounts):
    repo = FakeTradeRepository()
    socketio = DummySocketIO()
    gateway_exec = RecordingGatewayExecutor()
    multi = MultiAccountExecutor(
        trade_manager=None,
        account_configs=accounts,
        gateway_executor=gateway_exec,
        logger=FakeLogger(),
    )
    manager = TradeManager(
        trade_repository=repo,
        socketio=socketio,
        pair="MNQ",
        trade_executor=multi,
        analytics=FakeAnalyticsReporter(),
        point_value=2.0,
        account_balance=100000.0,
        logger=FakeLogger(),
    )
    multi.trade_manager = manager
    return manager, multi, repo, socketio


class TestBrokerEntryFillIsolation:
    def test_fill_updates_only_target_trade(self):
        accounts = [
            AccountConfig(name="Sim101", risk_usd=500.0, rr_ratio=5.0),
            AccountConfig(name="Sim102", risk_usd=500.0, rr_ratio=5.0),
        ]
        manager, multi, repo, socketio = _make_multi_account_manager(accounts)

        t1 = manager.open_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=30156.0,
            stop_loss=30136.0,
            take_profit=30256.0,
            risk=20.0,
            entry_time=1000.0,
            rr_ratio=5.0,
            source="test",
            account="Sim101",
        )
        t2 = manager.open_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=30156.0,
            stop_loss=30136.0,
            take_profit=30256.0,
            risk=20.0,
            entry_time=1000.0,
            rr_ratio=5.0,
            source="test",
            account="Sim102",
        )

        manager.handle_broker_entry_fill(t1["trade_id"], 30157.27)

        assert manager.open_trades[0]["entry"] == 30157.27
        assert manager.open_trades[1]["entry"] == 30156.0

        db1 = repo.get_trade(t1["trade_id"])
        db2 = repo.get_trade(t2["trade_id"])
        assert db1.entry_price == 30157.27
        assert db2.entry_price == 30156.0

        updates = [e for e in socketio.events if e[0] == "trade_entry_update"]
        trade_ids = {e[1]["trade_id"] for e in updates}
        assert t1["trade_id"] in trade_ids
        assert t2["trade_id"] not in trade_ids

    def test_fill_keeps_strategy_sl_tp(self):
        accounts = [AccountConfig(name="Sim101", risk_usd=500.0, rr_ratio=3.0)]
        manager, multi, repo, _ = _make_multi_account_manager(accounts)

        trade = manager.open_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=150.0,
            risk=10.0,
            entry_time=1000.0,
            rr_ratio=5.0,
            source="test",
            account="Sim101",
        )

        manager.handle_broker_entry_fill(
            trade["trade_id"], 101.0, stop_loss=91.0, take_profit=131.0
        )

        updated = manager.open_trades[0]
        assert updated["entry"] == 101.0
        assert updated["stop_loss"] == 91.0
        assert updated["take_profit"] == 131.0
