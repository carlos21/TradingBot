"""Tests for src/services/trade_close_service.py."""

from datetime import datetime, timezone

from src.services.trade_close_service import (
    CloseReason,
    NoOpTradeEventPublisher,
    TradeCloseResult,
    TradeCloseService,
)
from tests.fakes import FakeTradeEventPublisher, FakeTradeRepository


def _make_trade(trade_type="long", entry=100.0, stop_loss=90.0, take_profit=130.0, risk=10.0, contracts=1.0):
    return {
        "trade_id": "T1",
        "type": trade_type,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk": risk,
        "contracts": contracts,
        "pair": "MNQ",
    }


class TestNoOpTradeEventPublisher:

    def test_emit_trade_closed_no_op(self):
        pub = NoOpTradeEventPublisher()
        pub.emit_trade_closed({"trade_id": "T1"})

    def test_emit_trade_updated_no_op(self):
        pub = NoOpTradeEventPublisher()
        pub.emit_trade_updated("T1", {"stop_loss": 95.0})


class TestTradeCloseServiceCloseTrade:

    def test_close_trade_long_tp(self):
        repo = FakeTradeRepository()
        pub = FakeTradeEventPublisher()
        svc = TradeCloseService(trade_repository=repo, event_publisher=pub, point_value=2.0)

        trade = _make_trade(trade_type="long", entry=100.0, stop_loss=90.0, take_profit=130.0, risk=10.0)
        result = svc.close_trade(
            trade=trade,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            close_reason=CloseReason.TAKE_PROFIT_HIT,
        )

        assert isinstance(result, TradeCloseResult)
        assert result.trade_id == "T1"
        assert result.exit_price == 130.0
        assert result.result_type == "TP"
        assert result.close_reason == CloseReason.TAKE_PROFIT_HIT
        assert result.fees > 0

        # Verify persistence
        assert len(repo.closed) == 1
        assert repo.closed[0]["trade_id"] == "T1"
        assert repo.closed[0]["result_type"] == "TP"

        # Verify event emission
        assert len(pub.closed_events) == 1
        assert pub.closed_events[0]["trade_id"] == "T1"
        assert pub.closed_events[0]["close_reason"] == "TAKE_PROFIT_HIT"

    def test_close_trade_long_sl(self):
        repo = FakeTradeRepository()
        svc = TradeCloseService(trade_repository=repo, point_value=2.0)

        trade = _make_trade(trade_type="long", entry=100.0, stop_loss=90.0, take_profit=130.0, risk=10.0)
        result = svc.close_trade(
            trade=trade,
            exit_price=90.0,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            close_reason=CloseReason.STOP_LOSS_HIT,
        )

        assert result.result_type == "SL"
        assert result.result_r == -1.0
        assert len(repo.closed) == 1

    def test_close_trade_short_tp(self):
        repo = FakeTradeRepository()
        svc = TradeCloseService(trade_repository=repo, point_value=2.0)

        trade = _make_trade(trade_type="short", entry=100.0, stop_loss=110.0, take_profit=70.0, risk=10.0)
        result = svc.close_trade(
            trade=trade,
            exit_price=70.0,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            close_reason=CloseReason.TAKE_PROFIT_HIT,
        )

        assert result.result_type == "TP"
        assert result.result_r == 3.0

    def test_close_trade_short_sl(self):
        repo = FakeTradeRepository()
        svc = TradeCloseService(trade_repository=repo, point_value=2.0)

        trade = _make_trade(trade_type="short", entry=100.0, stop_loss=110.0, take_profit=70.0, risk=10.0)
        result = svc.close_trade(
            trade=trade,
            exit_price=110.0,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            close_reason=CloseReason.STOP_LOSS_HIT,
        )

        assert result.result_type == "SL"
        assert result.result_r == -1.0

    def test_close_trade_session_end_be(self):
        repo = FakeTradeRepository()
        svc = TradeCloseService(trade_repository=repo, point_value=2.0)

        trade = _make_trade(trade_type="long", entry=100.0, stop_loss=90.0, take_profit=130.0, risk=10.0)
        # Close near breakeven (within BE threshold)
        result = svc.close_trade(
            trade=trade,
            exit_price=100.5,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            close_reason=CloseReason.SESSION_END,
        )

        # Session end uses calculate_session_end_result_type
        assert result.result_type == "BE"

    def test_close_trade_manual_close(self):
        repo = FakeTradeRepository()
        svc = TradeCloseService(trade_repository=repo, point_value=2.0)

        trade = _make_trade(trade_type="long", entry=100.0, stop_loss=90.0, take_profit=130.0, risk=10.0)
        result = svc.close_trade(
            trade=trade,
            exit_price=110.0,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            close_reason=CloseReason.MANUAL_CLOSE,
        )

        assert result.result_r == 1.0
        assert result.close_reason == CloseReason.MANUAL_CLOSE

    def test_close_trade_with_contracts(self):
        repo = FakeTradeRepository()
        svc = TradeCloseService(trade_repository=repo, point_value=2.0)

        trade = _make_trade(trade_type="long", entry=100.0, stop_loss=90.0, take_profit=130.0, risk=10.0, contracts=2.0)
        result = svc.close_trade(
            trade=trade,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            close_reason=CloseReason.TAKE_PROFIT_HIT,
        )

        # PnL = contracts * actual_r * sl_pts * point_value - fees
        # = 2 * 3.0 * 10.0 * 2.0 - 3.0 = 120 - 3 = 117
        assert result.pnl_usd == 117.0
        assert result.fees == 3.0  # 2 contracts * $1.50 fee per RT

    def test_close_trade_zero_risk_defaults(self):
        repo = FakeTradeRepository()
        svc = TradeCloseService(trade_repository=repo, point_value=2.0)

        trade = _make_trade(trade_type="long", entry=100.0, stop_loss=90.0, take_profit=130.0, risk=0.0)
        result = svc.close_trade(
            trade=trade,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            close_reason=CloseReason.TAKE_PROFIT_HIT,
        )

        # When risk is 0, it defaults to 1.0 internally
        assert result.result_r > 0


class TestTradeCloseServiceCheckSLTPHit:

    def test_long_sl_hit(self):
        svc = TradeCloseService(trade_repository=FakeTradeRepository())
        trade = _make_trade(trade_type="long", stop_loss=90.0, take_profit=130.0)
        bar = {"high": 95.0, "low": 89.0}
        result = svc.check_sl_tp_hit(trade, bar)
        assert result == (90.0, CloseReason.STOP_LOSS_HIT)

    def test_long_tp_hit(self):
        svc = TradeCloseService(trade_repository=FakeTradeRepository())
        trade = _make_trade(trade_type="long", stop_loss=90.0, take_profit=130.0)
        bar = {"high": 131.0, "low": 95.0}
        result = svc.check_sl_tp_hit(trade, bar)
        assert result == (130.0, CloseReason.TAKE_PROFIT_HIT)

    def test_long_no_hit(self):
        svc = TradeCloseService(trade_repository=FakeTradeRepository())
        trade = _make_trade(trade_type="long", stop_loss=90.0, take_profit=130.0)
        bar = {"high": 95.0, "low": 95.0}
        result = svc.check_sl_tp_hit(trade, bar)
        assert result is None

    def test_short_sl_hit(self):
        svc = TradeCloseService(trade_repository=FakeTradeRepository())
        trade = _make_trade(trade_type="short", stop_loss=110.0, take_profit=70.0)
        bar = {"high": 111.0, "low": 95.0}
        result = svc.check_sl_tp_hit(trade, bar)
        assert result == (110.0, CloseReason.STOP_LOSS_HIT)

    def test_short_tp_hit(self):
        svc = TradeCloseService(trade_repository=FakeTradeRepository())
        trade = _make_trade(trade_type="short", stop_loss=110.0, take_profit=70.0)
        bar = {"high": 95.0, "low": 69.0}
        result = svc.check_sl_tp_hit(trade, bar)
        assert result == (70.0, CloseReason.TAKE_PROFIT_HIT)

    def test_short_no_hit(self):
        svc = TradeCloseService(trade_repository=FakeTradeRepository())
        trade = _make_trade(trade_type="short", stop_loss=110.0, take_profit=70.0)
        bar = {"high": 95.0, "low": 95.0}
        result = svc.check_sl_tp_hit(trade, bar)
        assert result is None

    def test_both_sl_and_tp_hit_prefers_sl(self):
        svc = TradeCloseService(trade_repository=FakeTradeRepository())
        trade = _make_trade(trade_type="long", stop_loss=90.0, take_profit=130.0)
        # Bar that hits both SL and TP — SL should be checked first
        bar = {"high": 131.0, "low": 89.0}
        result = svc.check_sl_tp_hit(trade, bar)
        assert result == (90.0, CloseReason.STOP_LOSS_HIT)


class TestTradeCloseServiceConvenienceMethods:

    def test_close_at_session_end(self):
        repo = FakeTradeRepository()
        svc = TradeCloseService(trade_repository=repo, point_value=2.0)
        trade = _make_trade(trade_type="long", entry=100.0, stop_loss=90.0, take_profit=130.0)

        result = svc.close_at_session_end(
            trade=trade,
            exit_price=100.5,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        )

        assert result.close_reason == CloseReason.SESSION_END
        assert result.result_type == "BE"

    def test_close_on_broker_fill(self):
        repo = FakeTradeRepository()
        svc = TradeCloseService(trade_repository=repo, point_value=2.0)
        trade = _make_trade(trade_type="long", entry=100.0, stop_loss=90.0, take_profit=130.0)

        result = svc.close_on_broker_fill(
            trade=trade,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            result_type="TP",
        )

        assert result.close_reason == CloseReason.BROKER_FILL
        assert result.result_type == "TP"

    def test_close_on_broker_fill_no_override(self):
        repo = FakeTradeRepository()
        svc = TradeCloseService(trade_repository=repo, point_value=2.0)
        trade = _make_trade(trade_type="long", entry=100.0, stop_loss=90.0, take_profit=130.0)

        result = svc.close_on_broker_fill(
            trade=trade,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        )

        # No override, so result_type comes from FinancialCalc
        assert result.result_type == "TP"
