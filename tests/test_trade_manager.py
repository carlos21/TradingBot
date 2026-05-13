"""Tests for src/services/trade_manager.py — SL/TP hit detection, session close, trade lifecycle."""

from datetime import datetime

from tests.conftest import make_bar
from tests.fakes import (
    DummySocketIO,
    FakeAnalyticsReporter,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
)


def _make_manager(**overrides):
    from src.services.trade_manager import TradeManager
    defaults = {
        "trade_repository": FakeTradeRepository(),
        "socketio": DummySocketIO(),
        "pair": "MNQ",
        "trade_executor": FakeTradeExecutor(),
        "analytics": FakeAnalyticsReporter(),
        "point_value": 2.0,
        "account_balance": 100000.0,
        "logger": FakeLogger(),
    }
    defaults.update(overrides)
    return TradeManager(**defaults)


def _add_open_trade(tm, trade_id="T1", pair="MNQ", trade_type="long",
                    entry=100.0, sl=90.0, tp=130.0, risk=10.0, entry_time=500.0):
    trade = {
        "trade_id": trade_id, "pair": pair, "type": trade_type,
        "entry": entry, "stop_loss": sl, "take_profit": tp,
        "risk": risk, "entry_time": entry_time,
    }
    tm.open_trades.append(trade)
    tm._monitored_trades.add(trade_id)
    return trade


class TestSLTPDetection:

    def test_long_sl_hit(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        bar = make_bar(time=1000, low=89, high=95, close=90, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 0
        assert len(tm.trade_repository.closed) == 1
        assert tm.trade_repository.closed[0]["result_type"] == "SL"

    def test_long_tp_hit(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        bar = make_bar(time=1000, low=100, high=135, close=132, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 0
        assert tm.trade_repository.closed[0]["result_type"] == "TP"

    def test_short_sl_hit(self):
        tm = _make_manager()
        _add_open_trade(tm, trade_type="short", entry=100, sl=110, tp=70, risk=10)
        bar = make_bar(time=1000, low=99, high=115, close=112, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 0
        assert tm.trade_repository.closed[0]["result_type"] == "SL"

    def test_short_tp_hit(self):
        tm = _make_manager()
        _add_open_trade(tm, trade_type="short", entry=100, sl=110, tp=70, risk=10)
        bar = make_bar(time=1000, low=65, high=80, close=68, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 0
        assert tm.trade_repository.closed[0]["result_type"] == "TP"

    def test_no_hit_trade_stays_open(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        bar = make_bar(time=1000, low=95, high=110, close=105, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 1
        assert len(tm.trade_repository.closed) == 0

    def test_skips_bars_before_entry_time(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, entry_time=2000)
        bar = make_bar(time=1000, low=80, high=140, close=100, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 1

    def test_skips_same_bar_sl_tp_check(self):
        """Trades entered on a bar should not be checked for SL/TP on that same bar,
        since entry happens at bar close in backtest."""
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, entry_time=1000)
        # Same bar as entry: low below SL, high above TP
        bar = make_bar(time=1000, low=80, high=140, close=100, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        # Trade should stay open; SL/TP checked on next bars only
        assert len(tm.open_trades) == 1
        assert len(tm.trade_repository.closed) == 0

    def test_skips_different_pair(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, pair="MNQ")
        bar = make_bar(time=1000, low=80, high=140, close=100, pair="ES")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 1


class TestResultCalculation:

    def test_long_sl_result_is_negative(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        result = tm.trade_repository.closed[0]["result"]
        assert result < 0

    def test_long_tp_result_is_positive(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        bar = make_bar(time=1000, low=100, high=135, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        result = tm.trade_repository.closed[0]["result"]
        assert result > 0

    def test_r_multiple_calculation(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        bar = make_bar(time=1000, low=100, high=135, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        # TP hit: exit=130, pnl=30, risk=10 -> 3R
        result = tm.trade_repository.closed[0]["result"]
        assert abs(result - 3.0) < 0.01

    def test_zero_risk_defaults_to_one(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=0)
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        # Should not crash; risk treated as 1.0
        assert tm.trade_repository.closed[0]["result"] is not None


class TestCFDMode:

    def test_cfd_spread_does_not_adjust_sl_detection(self):
        tm = _make_manager(broker_mode="cfd", broker_spread=2.0)
        _add_open_trade(tm, trade_type="long", entry=100, sl=90, tp=130, risk=10)
        # Low = 91 -> above original SL of 90, so trade stays open
        bar = make_bar(time=1000, low=91, high=95, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 1

    def test_cfd_spread_baked_into_pnl(self):
        tm = _make_manager(broker_mode="cfd", broker_spread=2.0)
        _add_open_trade(tm, trade_type="long", entry=100, sl=90, tp=130, risk=10)
        # Low = 88 -> hits original SL of 90
        bar = make_bar(time=1000, low=88, high=95, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 0
        closed = tm.trade_repository.closed[0]
        # result_r stays theoretical: (90 - 100) / 10 = -1.0
        assert closed["result"] == -1.0
        # pnl reflects spread cost deducted
        # contracts = 1, sl_pts = 10, point_value = 2, spread = 2
        # gross = 1 * -1.0 * 10 * 2 = -20
        # spread_cost = 1 * 2 * 2 = 4
        # fees = 1.50 + 4 = 5.50
        # pnl = -20 - 5.50 = -25.50
        assert closed["pnl_usd"] == -25.5


class TestSessionEndClose:

    def test_closes_at_session_end(self):
        tm = _make_manager(session_end_time="15:00", session_tz="America/New_York")
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, entry_time=500)
        # 15:01 NY -> should close
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
        bar_dt = datetime(2025, 6, 15, 15, 1, tzinfo=ny)
        bar = make_bar(time=int(bar_dt.timestamp()), close=105, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 0

    def test_skips_same_bar_session_end(self):
        tm = _make_manager(session_end_time="15:00", session_tz="America/New_York")
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
        bar_dt = datetime(2025, 6, 15, 15, 1, tzinfo=ny)
        entry_time = int(bar_dt.timestamp())
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, entry_time=entry_time)
        bar = make_bar(time=entry_time, close=105, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        # Trade opened on session-end bar should NOT be closed on same bar
        assert len(tm.open_trades) == 1

    def test_does_not_close_before_session_end(self):
        tm = _make_manager(session_end_time="15:00", session_tz="America/New_York")
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, entry_time=500)
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
        bar_dt = datetime(2025, 6, 15, 14, 59, tzinfo=ny)
        bar = make_bar(time=int(bar_dt.timestamp()), close=105, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 1


class TestStreamEndClose:

    def test_skips_same_bar_stream_end(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, entry_time=1000)
        tm.close_remaining_trades_at_stream_end(105.0, 1000)
        # Trade opened at stream-end time should NOT be closed on same bar
        assert len(tm.open_trades) == 1

    def test_closes_trades_at_stream_end(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, entry_time=500)
        tm.close_remaining_trades_at_stream_end(105.0, 1000)
        assert len(tm.open_trades) == 0


class TestSocketIOEmissions:

    def test_emits_trade_close_on_sl_hit(self):
        sio = DummySocketIO()
        tm = _make_manager(socketio=sio)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        close_events = [e for e in sio.events if e[0] == "trade_close"]
        assert len(close_events) == 1
        assert close_events[0][1]["result_type"] == "SL"

    def test_emits_trade_open_on_open(self):
        sio = DummySocketIO()
        tm = _make_manager(socketio=sio)
        tm.open_trade("MNQ", "long", 100, 90, 130, 10, 1000.0)
        open_events = [e for e in sio.events if e[0] == "trade_open"]
        assert len(open_events) == 1


class TestTradeExecutorCallbacks:

    def test_executor_called_on_close(self):
        executor = FakeTradeExecutor()
        tm = _make_manager(trade_executor=executor)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(executor.closes) == 1

    def test_executor_called_on_manual_close(self):
        executor = FakeTradeExecutor()
        tm = _make_manager(trade_executor=executor)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.close_trade("T1", 105, 2000.0)
        assert len(executor.closes) == 1


class TestOpenTrade:

    def test_open_trade_persists_and_tracks(self):
        tm = _make_manager()
        trade = tm.open_trade("MNQ", "long", 100, 90, 130, 10, 1000.0)
        assert len(tm.open_trades) == 1
        assert trade["trade_id"] is not None
        assert len(tm.trade_repository.inserted) == 1

    def test_open_trade_marks_as_monitored(self):
        tm = _make_manager()
        trade = tm.open_trade("MNQ", "long", 100, 90, 130, 10, 1000.0)
        assert trade["trade_id"] in tm._monitored_trades


class TestCloseTrade:

    def test_close_known_trade(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        payload = tm.close_trade("T1", 110, 2000.0)
        assert len(tm.open_trades) == 0
        assert payload["result"] == 1.0  # (110-100)/10

    def test_close_known_trade_updates_account_balance(self):
        tm = _make_manager(account_balance=100000.0)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.close_trade("T1", 110, 2000.0)
        # 1R win: result=1.0, pnl = 1 * 10 * 2 = 20, fees = 1.50
        # account_balance should increase by pnl_usd (fees already deducted from pnl)
        assert tm.account_balance > 100000.0

    def test_close_unknown_trade_fallback(self):
        tm = _make_manager()
        payload = tm.close_trade("T_UNKNOWN", 110, 2000.0)
        assert payload["result"] == 0.0

    def test_close_already_closed_trade_in_db_does_not_call_executor(self):
        """Regression: close_trade() must NOT call executor for already-closed trades."""
        executor = FakeTradeExecutor()
        repo = FakeTradeRepository()
        tm = _make_manager(trade_executor=executor, trade_repository=repo)

        # Seed a trade that is already closed in the repository
        trade = tm.open_trade("MNQ", "long", 100.0, 90.0, 130.0, 10.0, 500.0, 5.0)
        repo.close_trade(
            trade_id=trade["trade_id"],
            exit_price=95.0,
            exit_time=datetime.now(),
            result=-0.5,
            result_type="SL",
            fees=1.5,
            pnl_usd=-11.5,
        )
        # Remove from open_trades so close_trade falls back to DB
        tm.open_trades.clear()

        payload = tm.close_trade(trade["trade_id"], 95.0, 2000.0)
        # Should return the existing result, NOT call executor
        assert payload["result"] == -0.5
        assert payload["result_type"] == "SL"
        assert len(executor.closes) == 0


class TestBrokerFill:

    def test_updates_account_balance(self):
        """Regression: handle_broker_fill() must update account_balance."""
        tm = _make_manager(account_balance=100000.0)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_fill("T1", 110.0, "TP")
        # 1R win on TP: result=1.0, pnl = 1 * 10 * 2 = 20, fees = 1.50
        assert tm.account_balance > 100000.0

    def test_removes_trade_from_open_trades(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_fill("T1", 110.0, "TP")
        assert len(tm.open_trades) == 0

    def test_persists_close_to_db(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_fill("T1", 110.0, "TP")
        assert len(tm.trade_repository.closed) == 1


class TestNotifyStrategyClose:

    def test_removes_trade_and_updates_balance(self):
        """Regression: notify_strategy_close() must sync TradeManager state."""
        executor = FakeTradeExecutor()
        tm = _make_manager(trade_executor=executor, account_balance=100000.0)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)

        tm.notify_strategy_close(
            trade_id="T1",
            exit_price=110.0,
            result=1.0,
            pnl_usd=18.5,
            fees=1.5,
            result_type="TP",
            exit_time=2000.0,
        )

        assert len(tm.open_trades) == 0
        assert tm.account_balance == 100018.5
        # Should NOT call executor
        assert len(executor.closes) == 0

    def test_noop_when_trade_already_removed(self):
        """notify_strategy_close() must be safe to call twice."""
        tm = _make_manager(account_balance=100000.0)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)

        tm.notify_strategy_close("T1", 110.0, 1.0, 18.5, 1.5, "TP", 2000.0)
        # Second call should not crash or double-count balance
        tm.notify_strategy_close("T1", 110.0, 1.0, 18.5, 1.5, "TP", 2000.0)

        assert tm.account_balance == 100018.5

    def test_captures_analytics(self):
        analytics = FakeAnalyticsReporter()
        tm = _make_manager(analytics=analytics)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)

        tm.notify_strategy_close("T1", 110.0, 1.0, 18.5, 1.5, "TP", 2000.0)

        events = [e for e, _ in analytics.trade_events]
        assert "TP_HIT" in events


class TestStreamEndClose:

    def test_closes_remaining_trades(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.close_remaining_trades_at_stream_end(105, 3000.0)
        assert len(tm.open_trades) == 0
        assert len(tm.trade_repository.closed) == 1

    def test_stream_end_result_type_sp(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.close_remaining_trades_at_stream_end(110, 3000.0)
        assert tm.trade_repository.closed[0]["result_type"] == "SP"

    def test_stream_end_result_type_be(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        # exit at entry -> result = 0 -> BE
        tm.close_remaining_trades_at_stream_end(100, 3000.0)
        assert tm.trade_repository.closed[0]["result_type"] == "BE"


class TestUpdateLocalTradeSL:

    def test_updates_sl(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.update_local_trade_sl("T1", 95.0)
        assert tm.open_trades[0]["stop_loss"] == 95.0

    def test_ignores_unknown_trade(self):
        tm = _make_manager()
        tm.update_local_trade_sl("T_UNKNOWN", 95.0)  # should not crash


class TestBrokerEntryFill:

    def test_updates_entry_and_risk(self):
        tm = _make_manager()
        _add_open_trade(tm, trade_type="long", entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_entry_fill("T1", 101.0)
        assert tm.open_trades[0]["entry"] == 101.0
        assert tm.open_trades[0]["risk"] == 11.0  # |101-90|

    def test_updates_entry_sl_tp_from_broker(self):
        tm = _make_manager()
        _add_open_trade(tm, trade_type="long", entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_entry_fill("T1", 101.0, stop_loss=91.0, take_profit=131.0)
        t = tm.open_trades[0]
        assert t["entry"] == 101.0
        assert t["stop_loss"] == 91.0
        assert t["take_profit"] == 131.0
        assert t["risk"] == 10.0  # |101-91|

    def test_updates_short_trade_sl_tp_from_broker(self):
        tm = _make_manager()
        _add_open_trade(tm, trade_type="short", entry=100, sl=110, tp=70, risk=10)
        tm.handle_broker_entry_fill("T1", 99.0, stop_loss=109.0, take_profit=69.0)
        t = tm.open_trades[0]
        assert t["entry"] == 99.0
        assert t["stop_loss"] == 109.0
        assert t["take_profit"] == 69.0
        assert t["risk"] == 10.0  # |109-99|
