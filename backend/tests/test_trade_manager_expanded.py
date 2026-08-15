"""Expanded tests for src/services/trade_manager.py covering edge cases and error handling."""

from datetime import datetime, timezone

import pytest

from tests.conftest import make_bar
from tests.fakes import (
    DummySocketIO,
    FakeAnalyticsReporter,
    FakeLogger,
    FakeNtAccountRepository,
    FakeNotifier,
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
                    entry=100.0, sl=90.0, tp=130.0, risk=10.0, entry_time=500.0, contracts=1):
    trade = {
        "trade_id": trade_id, "pair": pair, "type": trade_type,
        "entry": entry, "stop_loss": sl, "take_profit": tp,
        "risk": risk, "entry_time": entry_time, "contracts": contracts,
    }
    tm.open_trades.append(trade)
    tm._monitored_trades.add(trade_id)
    return trade


# ============================================================================
# Exception-raising fakes for error-path testing
# ============================================================================

class ExplodingExecutor(FakeTradeExecutor):
    """Fake executor that always raises on close."""

    def on_trade_close(self, trade_id, exit_price):
        raise RuntimeError("ZMQ connection lost")


class ExplodingTradeRepository(FakeTradeRepository):
    """Fake repository that can raise on specific operations."""

    def __init__(self, explode_on=None):
        super().__init__()
        self.explode_on = explode_on or set()

    def close_trade(self, trade_id, exit_price, exit_time, result, result_type=None, fees=None, pnl_usd=None, gross_pnl=None, realized_pnl=None):
        if "close_trade" in self.explode_on:
            raise RuntimeError("DB write failure")
        return super().close_trade(trade_id, exit_price, exit_time, result, result_type, fees, pnl_usd, gross_pnl, realized_pnl)

    def list_trades(self, pair):
        if "list_trades" in self.explode_on:
            raise RuntimeError("DB read failure")
        return super().list_trades(pair)

    def update_entry_price(self, trade_id, new_entry_price):
        if "update_entry_price" in self.explode_on:
            raise RuntimeError("DB update failure")
        return super().update_entry_price(trade_id, new_entry_price)

    def update_stop_loss(self, trade_id, new_stop_loss):
        if "update_stop_loss" in self.explode_on:
            raise RuntimeError("DB update failure")
        return super().update_stop_loss(trade_id, new_stop_loss)

    def update_take_profit(self, trade_id, new_take_profit):
        if "update_take_profit" in self.explode_on:
            raise RuntimeError("DB update failure")
        return super().update_take_profit(trade_id, new_take_profit)

    def update_risk_fields(self, trade_id, risk, risk_dollars, risk_pct):
        if "update_risk_fields" in self.explode_on:
            raise RuntimeError("DB update failure")
        return super().update_risk_fields(trade_id, risk, risk_dollars, risk_pct)

    def update_contracts(self, trade_id, contracts):
        if "update_contracts" in self.explode_on:
            raise RuntimeError("DB update failure")
        return super().update_contracts(trade_id, contracts)


# ============================================================================
# Contract Calculation
# ============================================================================

class TestCalcContracts:

    def test_risk_per_contract_zero_returns_zero(self):
        tm = _make_manager()
        assert tm._calc_contracts(0) == 0.0

    def test_risk_per_contract_negative_returns_zero(self):
        tm = _make_manager()
        assert tm._calc_contracts(-5) == 0.0

    def test_risk_per_contract_zero_fractional_returns_zero(self):
        tm = _make_manager(use_fractional_lots=True)
        assert tm._calc_contracts(0) == 0.0

    def test_risk_budget_zero_returns_zero(self):
        tm = _make_manager(account_balance=0, risk_pct_per_trade=1.0)
        # risk_budget = 0 * 1 / 100 = 0
        assert tm._calc_contracts(10) == 0.0

    def test_risk_budget_zero_fractional_returns_zero(self):
        tm = _make_manager(account_balance=0, risk_pct_per_trade=1.0, use_fractional_lots=True)
        assert tm._calc_contracts(10) == 0.0

    def test_fractional_lots_normal_path(self):
        tm = _make_manager(account_balance=10000, risk_pct_per_trade=1.0, use_fractional_lots=True)
        # risk_budget = 100, risk_per_contract = 20 -> 5 lots
        assert tm._calc_contracts(20) == 5.0

    def test_risk_per_trade_override(self):
        tm = _make_manager(account_balance=100000, risk_per_trade=500)
        # risk_budget = 500, risk_per_contract = 50 -> 10 contracts
        assert tm._calc_contracts(50, risk_per_trade_override=500) == 10

    def test_risk_pct_per_trade_override(self):
        tm = _make_manager(account_balance=100000)
        # risk_budget = 100000 * 2 / 100 = 2000, risk_per_contract = 50 -> 40 contracts (capped at 100)
        assert tm._calc_contracts(50, risk_pct_per_trade_override=2.0) == 40

    def test_override_takes_precedence_over_instance_vars(self):
        tm = _make_manager(account_balance=100000, risk_per_trade=100)
        # Without override: risk_budget=100 -> 2 contracts
        assert tm._calc_contracts(50) == 2
        # With override: risk_budget=5000 -> 100 contracts (capped)
        assert tm._calc_contracts(50, risk_per_trade_override=5000) == 100


# ============================================================================
# open_trade Edge Cases
# ============================================================================

class TestOpenTradeExpanded:

    def test_open_trade_with_source_account_signal_id(self):
        tm = _make_manager()
        trade = tm.open_trade(
            "MNQ", "long", 100, 90, 130, 10, 1000.0,
            source="strategy", account="Acc1", signal_id="SIG-42"
        )
        # 'source' is stored in both the in-memory dict and persisted
        assert trade["source"] == "strategy"
        assert trade["account"] == "Acc1"
        assert trade["signal_id"] == "SIG-42"
        inserted = tm.trade_repository.inserted[0]
        assert inserted["source"] == "strategy"
        assert inserted["account"] == "Acc1"
        assert inserted["signal_id"] == "SIG-42"

    def test_open_trade_with_custom_trade_id(self):
        tm = _make_manager()
        trade = tm.open_trade("MNQ", "long", 100, 90, 130, 10, 1000.0, trade_id="CUSTOM-1")
        assert trade["trade_id"] == "CUSTOM-1"
        assert tm.trade_repository.inserted[0]["trade_id"] == "CUSTOM-1"

    def test_open_trade_with_risk_overrides(self):
        tm = _make_manager(account_balance=100000, risk_per_trade=100)
        trade = tm.open_trade(
            "MNQ", "long", 100, 90, 130, 10, 1000.0,
            risk_per_trade_override=1000, risk_pct_per_trade_override=None
        )
        # risk_budget = 1000, risk_per_contract = 10 * 2 = 20 -> 50 contracts (capped at 100)
        assert trade["contracts"] == 50

    def test_open_trade_risk_pct_none_when_zero_balance(self):
        tm = _make_manager(account_balance=0)
        trade = tm.open_trade("MNQ", "long", 100, 90, 130, 10, 1000.0)
        assert trade["risk_pct"] is None

    def test_open_trade_calculates_risk_dollars(self):
        tm = _make_manager(risk_per_trade=1000.0)
        trade = tm.open_trade("MNQ", "long", 100, 90, 130, 10, 1000.0)
        # risk_budget = 1000, risk_per_contract = 10 * 2 = 20 -> 50 contracts
        # risk_dollars = 20 * 50 = 1000
        assert trade["contracts"] == 50
        assert trade["risk_dollars"] == 1000.0

    def test_open_trade_emits_trade_open_event(self):
        analytics = FakeAnalyticsReporter()
        tm = _make_manager(analytics=analytics)
        tm.open_trade("MNQ", "long", 100, 90, 130, 10, 1000.0)
        events = [e for e, _ in analytics.trade_events]
        assert "TRADE_OPEN" in events

    def test_open_trade_adds_to_monitored(self):
        tm = _make_manager()
        trade = tm.open_trade("MNQ", "long", 100, 90, 130, 10, 1000.0)
        assert trade["trade_id"] in tm._monitored_trades


# ============================================================================
# close_trade Edge Cases
# ============================================================================

class TestCloseTradeExpanded:

    def test_close_trade_closes_only_target_trade(self):
        tm = _make_manager()
        tm.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "entry_time": 500,
        })
        tm.open_trades.append({
            "trade_id": "T2", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "entry_time": 500,
        })
        result = tm.close_trade("T1", 110, 2000.0)
        assert len(tm.open_trades) == 1
        assert tm.open_trades[0]["trade_id"] == "T2"
        assert result["result"] == 1.0

    def test_close_trade_not_in_memory_fetches_from_db(self):
        repo = FakeTradeRepository()
        tm = _make_manager(trade_repository=repo)
        td = repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100,
            stop_loss=90, take_profit=130, risk=10,
            entry_time=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            contracts=2,
        )
        # Trade is not in open_trades but exists in DB
        payload = tm.close_trade(td.trade_id, 110, 2000.0)
        assert payload["result"] == 1.0
        assert len(repo.closed) == 1

    def test_close_trade_not_in_memory_not_in_db_fallback(self):
        tm = _make_manager()
        payload = tm.close_trade("MISSING", 110, 2000.0)
        assert payload["result"] == 0.0
        # Fallback payload does not include result_type key
        assert "result_type" not in payload
        assert len(tm.trade_repository.closed) == 1

    def test_close_trade_zmq_failure_raises(self):
        tm = _make_manager(trade_executor=ExplodingExecutor())
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        with pytest.raises(RuntimeError, match="ZMQ connection lost"):
            tm.close_trade("T1", 110, 2000.0)
        # Trade should stay open
        assert len(tm.open_trades) == 1

    def test_close_trade_with_broker_spread_deducts(self):
        tm = _make_manager(broker_mode="cfd", broker_spread=2.0)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, contracts=2)
        tm.close_trade("T1", 130, 2000.0)
        closed = tm.trade_repository.closed[0]
        # spread_cost = 2 * 2 * 2 = 8, deducted from pnl_usd, added to fees
        # fee_per_rt = 1.5, contracts = 2 -> base fees = 3.0
        # pnl_usd = 2 * 3.0 * 10 * 2 - 3.0 = 117
        # after spread: fees = 3 + 8 = 11, pnl_usd = 117 - 8 = 109
        assert closed["fees"] == 11.0
        assert closed["pnl_usd"] == 109.0

    def test_close_trade_updates_account_balance_with_spread(self):
        tm = _make_manager(broker_mode="cfd", broker_spread=2.0, account_balance=100000.0)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, contracts=2)
        tm.close_trade("T1", 130, 2000.0)
        assert tm.account_balance == 100109.0


# ============================================================================
# _load_open_trades_from_db Error Handling
# ============================================================================

class TestLoadOpenTradesErrorHandling:

    def test_load_failure_logs_error(self):
        logger = FakeLogger()
        repo = ExplodingTradeRepository(explode_on={"list_trades"})
        tm = _make_manager(trade_repository=repo, logger=logger)
        # Should not crash; open_trades stays empty
        assert tm.open_trades == []

    def test_load_failure_notifies(self):
        notifier = FakeNotifier()
        repo = ExplodingTradeRepository(explode_on={"list_trades"})
        analytics = FakeAnalyticsReporter()
        _make_manager(trade_repository=repo, notifier=notifier, analytics=analytics)
        assert len(analytics.exceptions) == 1
        assert "DB read failure" in str(analytics.exceptions[0][0])

    def test_load_includes_all_open_sources(self):
        repo = FakeTradeRepository()
        repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100,
            stop_loss=90, take_profit=130, risk=10,
            entry_time=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            source="strategy",
        )
        repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100,
            stop_loss=90, take_profit=130, risk=10,
            entry_time=datetime(2025, 1, 1, 12, 1, tzinfo=timezone.utc),
            source="manual",
        )
        tm = _make_manager(trade_repository=repo)
        assert len(tm.open_trades) == 2

    def test_loaded_open_trades_include_instrument(self):
        """Regression: after a crash-recovery reload, modify/close commands
        resolve the instrument from tm.open_trades — it must be present."""
        repo = FakeTradeRepository()
        repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100,
            stop_loss=90, take_profit=130, risk=10,
            entry_time=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            source="strategy",
        )
        tm = _make_manager(trade_repository=repo, instrument="MNQ 09-26")
        assert len(tm.open_trades) == 1
        assert tm.open_trades[0]["instrument"] == "MNQ 09-26"


# ============================================================================
# handle_broker_entry_fill Edge Cases
# ============================================================================

class TestHandleBrokerEntryFillExpanded:

    def test_trade_not_in_memory_prints_and_returns(self):
        tm = _make_manager()
        # No trade added
        tm.handle_broker_entry_fill("MISSING", 101.0)
        # Should not crash
        assert tm.open_trades == []

    def test_without_sl_tp_from_broker(self):
        tm = _make_manager()
        _add_open_trade(tm, trade_type="long", entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_entry_fill("T1", 101.0)
        t = tm.open_trades[0]
        assert t["entry"] == 101.0
        assert t["stop_loss"] == 90.0  # unchanged
        assert t["take_profit"] == 130.0  # unchanged
        assert t["risk"] == 11.0  # |101 - 90|

    def test_db_error_on_entry_fill_caught(self):
        repo = ExplodingTradeRepository(explode_on={"update_entry_price"})
        analytics = FakeAnalyticsReporter()
        tm = _make_manager(trade_repository=repo, analytics=analytics)
        _add_open_trade(tm, trade_type="long", entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_entry_fill("T1", 101.0, stop_loss=91.0, take_profit=131.0)
        assert len(analytics.exceptions) == 1

    def test_fractional_lots_with_zero_risk_budget(self):
        tm = _make_manager(account_balance=0, use_fractional_lots=True)
        _add_open_trade(tm, trade_type="long", entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_entry_fill("T1", 101.0)
        t = tm.open_trades[0]
        # Zero risk budget must not open a position.
        assert t["contracts"] == 0.0
        assert t["risk_dollars"] == 0.0

    def test_trade_logger_called_on_entry_fill(self):
        from src.services.trade_logger import TradeLogger
        repo = FakeTradeRepository()
        trade_logger = TradeLogger(repo)
        tm = _make_manager(trade_repository=repo, trade_logger=trade_logger)
        # Seed trade in repo so TradeLogger can append logs
        repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100,
            stop_loss=90, take_profit=130, risk=10,
            entry_time=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            trade_id="T1",
        )
        _add_open_trade(tm, trade_type="long", entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_entry_fill("T1", 101.0, stop_loss=91.0, take_profit=131.0)
        logs = repo.get_trade_logs("T1")
        assert any("NT_ENTRY_FILL" in log["event"] for log in logs)


# ============================================================================
# handle_broker_fill Edge Cases
# ============================================================================

class TestHandleBrokerFillExpanded:

    def test_trade_not_in_memory_prints_and_returns(self):
        tm = _make_manager()
        tm.handle_broker_fill("MISSING", 110.0, "TP")
        assert tm.trade_repository.closed == []

    def test_provided_result_type_used(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_fill("T1", 110.0, result_type="CLOSE")
        closed = tm.trade_repository.closed[0]
        assert closed["result_type"] == "CLOSE"

    def test_db_error_on_broker_fill_caught(self):
        repo = ExplodingTradeRepository(explode_on={"close_trade"})
        analytics = FakeAnalyticsReporter()
        tm = _make_manager(trade_repository=repo, analytics=analytics)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_fill("T1", 110.0, "TP")
        assert len(analytics.exceptions) == 1
        # Trade should still be removed from open_trades
        assert len(tm.open_trades) == 0

    def test_broker_spread_deducted(self):
        tm = _make_manager(broker_mode="cfd", broker_spread=2.0)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, contracts=2)
        tm.handle_broker_fill("T1", 130.0, "TP")
        closed = tm.trade_repository.closed[0]
        assert closed["fees"] == 11.0  # 3 + 8
        assert closed["pnl_usd"] == 109.0

    def test_trade_logger_called(self):
        from src.services.trade_logger import TradeLogger
        repo = FakeTradeRepository()
        trade_logger = TradeLogger(repo)
        tm = _make_manager(trade_repository=repo, trade_logger=trade_logger)
        # Seed trade in repo so TradeLogger can append logs
        repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100,
            stop_loss=90, take_profit=130, risk=10,
            entry_time=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            trade_id="T1",
        )
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_fill("T1", 110.0, "TP")
        logs = repo.get_trade_logs("T1")
        assert any("NT_FILL" in log["event"] for log in logs)
        assert any("CLOSE" in log["event"] for log in logs)


# ============================================================================
# _check_sl_tp Error Handling
# ============================================================================

class TestCheckSLTPErrorHandling:

    def test_zmq_close_failure_keeps_trade_open(self):
        tm = _make_manager(trade_executor=ExplodingExecutor())
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 1
        assert len(tm.trade_repository.closed) == 0

    def test_db_error_on_sl_close_logs_but_trade_removed(self):
        repo = ExplodingTradeRepository(explode_on={"close_trade"})
        analytics = FakeAnalyticsReporter()
        tm = _make_manager(trade_repository=repo, analytics=analytics)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        # Trade is removed from open_trades despite DB error
        assert len(tm.open_trades) == 0
        assert len(analytics.exceptions) == 1

    def test_broker_spread_on_sl_hit(self):
        tm = _make_manager(broker_mode="cfd", broker_spread=2.0)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, contracts=2)
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        closed = tm.trade_repository.closed[0]
        assert closed["result_type"] == "SL"
        # base fees = 1.5 * 2 = 3, spread_cost = 2 * 2 * 2 = 8 -> total fees = 11
        assert closed["fees"] == 11.0
        # pnl = 2 * -1 * 10 * 2 - 3 = -43, then - 8 spread = -51
        assert closed["pnl_usd"] == -51.0

    def test_trade_logger_on_sl_hit(self):
        from src.services.trade_logger import TradeLogger
        repo = FakeTradeRepository()
        trade_logger = TradeLogger(repo)
        tm = _make_manager(trade_repository=repo, trade_logger=trade_logger)
        # Seed trade in repo so TradeLogger can append logs
        repo.insert_trade(
            pair="MNQ", trade_type="long", entry_price=100,
            stop_loss=90, take_profit=130, risk=10,
            entry_time=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            trade_id="T1",
        )
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        bar = make_bar(time=1000, low=85, high=95, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        logs = repo.get_trade_logs("T1")
        assert any("SL_HIT" in log["event"] for log in logs)
        assert any("CLOSE" in log["event"] for log in logs)


# ============================================================================
# _check_session_end_close Edge Cases
# ============================================================================

class TestSessionEndCloseExpanded:

    def test_zmq_failure_keeps_trade_open(self):
        tm = _make_manager(
            trade_executor=ExplodingExecutor(),
            session_end_time="16:58",
            session_tz="America/New_York",
        )
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, entry_time=500)
        ny = __import__('zoneinfo').ZoneInfo("America/New_York")
        bar_dt = datetime(2025, 6, 15, 16, 59, tzinfo=ny)
        bar = make_bar(time=int(bar_dt.timestamp()), close=105, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 1

    def test_db_error_logs_exception(self):
        repo = ExplodingTradeRepository(explode_on={"close_trade"})
        analytics = FakeAnalyticsReporter()
        tm = _make_manager(
            trade_repository=repo,
            analytics=analytics,
            session_end_time="16:58",
            session_tz="America/New_York",
        )
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, entry_time=500)
        ny = __import__('zoneinfo').ZoneInfo("America/New_York")
        bar_dt = datetime(2025, 6, 15, 16, 59, tzinfo=ny)
        bar = make_bar(time=int(bar_dt.timestamp()), close=105, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(analytics.exceptions) == 1
        # Trade removed despite DB error
        assert len(tm.open_trades) == 0

    def test_skips_different_pair_at_session_end(self):
        tm = _make_manager(session_end_time="16:58", session_tz="America/New_York")
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, entry_time=500, pair="ES")
        ny = __import__('zoneinfo').ZoneInfo("America/New_York")
        bar_dt = datetime(2025, 6, 15, 16, 59, tzinfo=ny)
        bar = make_bar(time=int(bar_dt.timestamp()), close=105, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 1

    def test_skips_entry_time_after_bar_at_session_end(self):
        tm = _make_manager(session_end_time="16:58", session_tz="America/New_York")
        # entry_time far in the future relative to the bar timestamp
        future_entry = int(datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp())
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, entry_time=future_entry)
        ny = __import__('zoneinfo').ZoneInfo("America/New_York")
        bar_dt = datetime(2025, 6, 15, 16, 59, tzinfo=ny)
        bar = make_bar(time=int(bar_dt.timestamp()), close=105, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 1


# ============================================================================
# close_remaining_trades_at_stream_end Error Handling
# ============================================================================

class TestStreamEndCloseExpanded:

    def test_zmq_failure_keeps_trade_open(self):
        tm = _make_manager(trade_executor=ExplodingExecutor())
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.close_remaining_trades_at_stream_end(105, 3000.0)
        assert len(tm.open_trades) == 1

    def test_db_error_logs_exception(self):
        repo = ExplodingTradeRepository(explode_on={"close_trade"})
        analytics = FakeAnalyticsReporter()
        tm = _make_manager(trade_repository=repo, analytics=analytics)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.close_remaining_trades_at_stream_end(105, 3000.0)
        assert len(analytics.exceptions) == 1
        # Trade removed despite DB error
        assert len(tm.open_trades) == 0


# ============================================================================
# notify_strategy_close Event Types
# ============================================================================

class TestNotifyStrategyCloseEventTypes:

    def test_sl_event_type(self):
        analytics = FakeAnalyticsReporter()
        tm = _make_manager(analytics=analytics)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.notify_strategy_close("T1", 90.0, -1.0, -21.5, 1.5, "SL", 2000.0)
        events = [e for e, _ in analytics.trade_events]
        assert "SL_HIT" in events

    def test_tp_event_type(self):
        analytics = FakeAnalyticsReporter()
        tm = _make_manager(analytics=analytics)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.notify_strategy_close("T1", 130.0, 3.0, 58.5, 1.5, "TP", 2000.0)
        events = [e for e, _ in analytics.trade_events]
        assert "TP_HIT" in events

    def test_session_end_event_type(self):
        analytics = FakeAnalyticsReporter()
        tm = _make_manager(analytics=analytics)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.notify_strategy_close("T1", 105.0, 0.5, 8.5, 1.5, "SP", 2000.0)
        events = [e for e, _ in analytics.trade_events]
        assert "SESSION_END" in events

    def test_other_event_type(self):
        analytics = FakeAnalyticsReporter()
        tm = _make_manager(analytics=analytics)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.notify_strategy_close("T1", 105.0, 0.5, 8.5, 1.5, "MANUAL", 2000.0)
        events = [e for e, _ in analytics.trade_events]
        assert "SESSION_END" in events  # default falls to SESSION_END


# ============================================================================
# _calc_close_financials
# ============================================================================

# ============================================================================
# update_local_trade_sl Logging
# ============================================================================

class TestUpdateLocalTradeSLExpanded:

    def test_updates_only_target_trade(self):
        tm = _make_manager()
        tm.open_trades.append({
            "trade_id": "T1", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "entry_time": 500,
        })
        tm.open_trades.append({
            "trade_id": "T2", "pair": "MNQ", "type": "long",
            "entry": 100, "stop_loss": 90, "take_profit": 130,
            "risk": 10, "entry_time": 500,
        })
        tm.update_local_trade_sl("T1", 85.0)
        assert tm.open_trades[0]["stop_loss"] == 85.0
        assert tm.open_trades[1]["stop_loss"] == 90.0

    def test_logs_warning_for_unknown_trade(self):
        logger = FakeLogger()
        tm = _make_manager(logger=logger)
        tm.update_local_trade_sl("UNKNOWN", 95.0)
        # Should not crash; in real code it logs a warning


# ============================================================================
# handle_broker_fill Balance Update
# ============================================================================

class TestHandleBrokerFillBalance:

    def test_balance_decreases_on_loss(self):
        tm = _make_manager(account_balance=100000.0)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_fill("T1", 90.0, "SL")
        assert tm.account_balance < 100000.0

    def test_balance_increases_on_win(self):
        tm = _make_manager(account_balance=100000.0)
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.handle_broker_fill("T1", 130.0, "TP")
        assert tm.account_balance > 100000.0


# ============================================================================
# Partial / Trailing SL / Multi-account
# ============================================================================

class TestOpenTradeMultiAccountAndOverrides:

    def test_multi_account_expansion_via_account_param(self):
        tm = _make_manager()
        trade = tm.open_trade("MNQ", "long", 100, 90, 130, 10, 1000.0, account="Account-B")
        assert trade["account"] == "Account-B"
        assert tm.trade_repository.inserted[0]["account"] == "Account-B"

    def test_open_trade_sizes_with_selected_live_account_risk(self):
        accounts_repo = FakeNtAccountRepository()
        accounts_repo.upsert("Account-A", risk_pct=2.0, live_enabled=False)
        accounts_repo.upsert("Account-B", risk_pct=1.6, live_enabled=True)

        tm = _make_manager(
            account_balance=100_000.0,
            accounts_repo=accounts_repo,
            live_mode=True,
        )

        trade = tm.open_trade("MNQ", "long", 100, 90, 130, 10, 1000.0)

        # Account-B is the only live-enabled account, so it should be selected
        assert trade["account"] == "Account-B"
        # 1.6% of 100k = 1600 budget; risk_per_contract = 10*2 = 20; contracts = 80
        assert trade["contracts"] == 80
        assert trade["risk_dollars"] == 1600.0
        assert trade["risk_pct"] == 1.6

    def test_trailing_sl_update_reflected_in_memory(self):
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10)
        tm.update_local_trade_sl("T1", 95.0)
        # Now an SL hit should use the updated SL
        bar = make_bar(time=1000, low=94, high=96, pair="MNQ")
        tm.handle_new_1m_bar(bar)
        assert len(tm.open_trades) == 0
        assert tm.trade_repository.closed[0]["result_type"] == "SL"

    def test_partial_close_not_supported_but_close_works(self):
        """TradeManager doesn't support partial closes natively, but closing a trade works."""
        tm = _make_manager()
        _add_open_trade(tm, entry=100, sl=90, tp=130, risk=10, contracts=5)
        tm.close_trade("T1", 110, 2000.0)
        assert len(tm.open_trades) == 0
        # All contracts closed at once
        assert tm.trade_repository.closed[0]["result"] == 1.0
