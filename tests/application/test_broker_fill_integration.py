"""Integration tests for broker entry fill propagation.

These tests verify that when NinjaTrader reports an entry fill for a
per-account child trade, the visible parent signal trade is updated too.
"""

from src.config.models import AccountConfig
from src.infrastructure.gateway.executor import MultiAccountExecutor
from src.services.trade_executor import TradeExecutor
from src.services.trade_manager import TradeManager
from tests.fakes import DummySocketIO, FakeAnalyticsReporter, FakeLogger, FakeTradeRepository


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


class TestMultiAccountEntryFillPropagation:
    def test_account_fill_propagates_to_signal_trade(self):
        accounts = [AccountConfig(name="Sim101", risk_usd=500.0, rr_ratio=5.0)]
        manager, multi, repo, socketio = _make_multi_account_manager(accounts)

        signal = manager.open_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=30156.0,
            stop_loss=30136.0,
            take_profit=30256.0,
            risk=20.0,
            entry_time=1000.0,
            rr_ratio=5.0,
            source="test",
        )
        signal_trade_id = signal["trade_id"]

        # Signal trade + one account trade should be open
        assert len(manager.open_trades) == 2
        account_trade = next(
            t for t in manager.open_trades if t["trade_id"] != signal_trade_id
        )
        account_id = account_trade["trade_id"]

        # Simulate NinjaTrader entry fill on the account trade
        manager.handle_broker_entry_fill(account_id, 30157.27)

        # Both in-memory records show the real fill price
        assert account_trade["entry"] == 30157.27
        signal = next(t for t in manager.open_trades if t["trade_id"] == signal_trade_id)
        assert signal["entry"] == 30157.27

        # DB records also updated
        db_signal = repo.get_trade(signal_trade_id)
        db_account = repo.get_trade(account_id)
        assert db_signal.entry_price == 30157.27
        assert db_account.entry_price == 30157.27

        # Frontend receives trade_entry_update for both trades
        updates = [e for e in socketio.events if e[0] == "trade_entry_update"]
        trade_ids = {e[1]["trade_id"] for e in updates}
        assert signal_trade_id in trade_ids
        assert account_id in trade_ids

    def test_signal_trade_keeps_strategy_sl_tp(self):
        accounts = [AccountConfig(name="Sim101", risk_usd=500.0, rr_ratio=3.0)]
        manager, multi, repo, _ = _make_multi_account_manager(accounts)

        signal = manager.open_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=150.0,
            risk=10.0,
            entry_time=1000.0,
            rr_ratio=5.0,
            source="test",
        )
        signal_trade_id = signal["trade_id"]

        account_trade = next(
            t for t in manager.open_trades if t["trade_id"] != signal_trade_id
        )

        # Broker reports a different fill and its own SL/TP
        manager.handle_broker_entry_fill(
            account_trade["trade_id"], 101.0, stop_loss=91.0, take_profit=131.0
        )

        signal = next(t for t in manager.open_trades if t["trade_id"] == signal_trade_id)
        # Parent keeps the strategy SL/TP, only entry/risk is updated
        assert signal["entry"] == 101.0
        assert signal["stop_loss"] == 90.0
        assert signal["take_profit"] == 150.0
        assert signal["risk"] == 11.0  # |101-90|

        # Child gets broker SL/TP
        assert account_trade["entry"] == 101.0
        assert account_trade["stop_loss"] == 91.0
        assert account_trade["take_profit"] == 131.0
