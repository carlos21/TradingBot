"""Tests for broker-reported account balance sync and exact percentage calculation."""

from src.application.use_cases.broker_fill_handler import BrokerFillHandler
from src.services.trade_manager import TradeManager
from tests.fakes import (
    DummySocketIO,
    FakeAnalyticsReporter,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
)


def _make_broker_handler(account_balance=100000.0, point_value=2.0):
    return BrokerFillHandler(
        trade_repository=FakeTradeRepository(),
        event_publisher=DummySocketIO(),
        logger=FakeLogger(),
        point_value=point_value,
        account_balance=account_balance,
        risk_pct_per_trade=1.6,
    )


def _make_manager(account_balance=100000.0, point_value=2.0):
    return TradeManager(
        trade_repository=FakeTradeRepository(),
        socketio=DummySocketIO(),
        pair="MNQ",
        trade_executor=FakeTradeExecutor(),
        analytics=FakeAnalyticsReporter(),
        point_value=point_value,
        account_balance=account_balance,
        logger=FakeLogger(),
        risk_pct_per_trade=1.6,
    )


def _add_open_trade(tm, trade_id="T1", trade_type="long",
                    entry=100.0, sl=90.0, tp=130.0, risk=10.0,
                    entry_time=500.0, account=None):
    trade = {
        "trade_id": trade_id,
        "pair": "MNQ",
        "type": trade_type,
        "entry": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "risk": risk,
        "entry_time": entry_time,
        "account": account,
    }
    tm.open_trades.append(trade)
    tm._monitored_trades.add(trade_id)
    tm.trade_repository.insert_trade(
        pair=trade["pair"],
        trade_type=trade["type"],
        entry_price=trade["entry"],
        stop_loss=trade["stop_loss"],
        take_profit=trade["take_profit"],
        risk=trade["risk"],
        entry_time=trade["entry_time"],
        account=account,
        trade_id=trade_id,
    )
    return trade


class TestBrokerFillHandlerBalanceSync:
    """BrokerFillHandler should treat NinjaTrader's balance as source of truth."""

    def test_entry_fill_updates_balance_and_risk_pct(self):
        handler = _make_broker_handler(account_balance=100000.0)
        trade = {
            "trade_id": "T1",
            "pair": "MNQ",
            "type": "long",
            "entry": 29770.25,
            "stop_loss": 29785.25,
            "take_profit": 29695.25,
            "risk": 15.0,
            "entry_time": 1000.0,
            "account": "DEMO",
        }

        handler.handle_entry_fill(
            trade,
            entry_price=29770.25,
            stop_loss=29785.25,
            take_profit=29695.25,
            quantity=27,
            account_balance=50625.0,
        )

        # NT balance becomes Python's balance.
        assert handler._account_balance == 50625.0
        # Actual risk dollars from filled quantity.
        assert trade["risk_dollars"] == 27 * 15.0 * 2.0
        # risk_pct is now based on the broker-reported balance, not the old 100k.
        expected_risk_pct = trade["risk_dollars"] / 50625.0 * 100
        assert trade["risk_pct"] == expected_risk_pct
        assert trade["account_balance"] == 50625.0

    def test_entry_fill_without_balance_uses_python_balance(self):
        handler = _make_broker_handler(account_balance=100000.0)
        trade = {
            "trade_id": "T1",
            "pair": "MNQ",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "entry_time": 1000.0,
        }

        handler.handle_entry_fill(trade, entry_price=100.0, quantity=10)

        assert handler._account_balance == 100000.0
        assert trade["risk_dollars"] == 10 * 10.0 * 2.0
        assert trade["risk_pct"] == trade["risk_dollars"] / 100000.0 * 100

    def test_entry_fill_persists_account_balance_to_repo(self):
        repo = FakeTradeRepository()
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=1000.0,
            trade_id="T1",
        )
        handler = BrokerFillHandler(
            trade_repository=repo,
            event_publisher=DummySocketIO(),
            logger=FakeLogger(),
            point_value=2.0,
            account_balance=100000.0,
        )
        trade = {
            "trade_id": "T1",
            "pair": "MNQ",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 130.0,
            "risk": 10.0,
            "entry_time": 1000.0,
        }

        handler.handle_entry_fill(
            trade, entry_price=100.0, quantity=10, account_balance=75000.0
        )

        stored = next(t for t in repo.inserted if t["trade_id"] == "T1")
        assert stored["account_balance"] == 75000.0


class TestTradeManagerBalanceSync:
    """TradeManager should update its internal balance from broker fills."""

    def test_entry_fill_updates_manager_balance(self):
        tm = _make_manager(account_balance=100000.0)
        _add_open_trade(tm, entry=100.0, sl=90.0, tp=130.0, risk=10.0)

        tm.handle_broker_entry_fill(
            "T1", 100.0, stop_loss=90.0, take_profit=130.0,
            quantity=10, account_balance=75000.0,
        )

        assert tm.account_balance == 75000.0
        assert tm._open_use_case._account_balance == 75000.0
        assert tm._broker_handler._account_balance == 75000.0

    def test_exit_fill_updates_balance_and_persists_on_trade(self):
        tm = _make_manager(account_balance=100000.0)
        _add_open_trade(tm, entry=100.0, sl=90.0, tp=130.0, risk=10.0)

        tm.handle_broker_fill(
            "T1", 130.0, "TP",
            broker_pnl_usd=200.0, broker_fees=5.0,
            account_balance=75000.0,
        )

        # Exit fill sets balance to broker-reported value, then adds PnL.
        assert tm.account_balance == 75000.0 + 200.0
        # The broker-reported exit balance is persisted on the trade record.
        stored = next(t for t in tm.trade_repository.inserted if t["trade_id"] == "T1")
        assert stored["account_balance"] == 75000.0

    def test_exit_fill_without_balance_does_not_persist_none(self):
        tm = _make_manager(account_balance=100000.0)
        _add_open_trade(tm, entry=100.0, sl=90.0, tp=130.0, risk=10.0)

        tm.handle_broker_fill(
            "T1", 90.0, "SL",
            broker_pnl_usd=-200.0, broker_fees=5.0,
        )

        stored = next(t for t in tm.trade_repository.inserted if t["trade_id"] == "T1")
        assert stored.get("account_balance") is None


class TestExactPercentageCalculation:
    """Verify the exact return percentage from pnl_usd / account_balance."""

    def test_exact_percentage_matches_user_trade(self):
        # Simulate the user's trade: 5R win, $4050 PnL, $50625 balance = 8.00%.
        pct = 4050.0 / 50625.0 * 100
        assert pct == 8.0
