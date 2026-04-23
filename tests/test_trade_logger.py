"""Tests for src/services/trade_logger.py."""

from datetime import datetime, timezone
from src.services.trade_logger import TradeLogger
from src.models import TradeData
from tests.fakes import FakeTradeRepository


class TestTradeLogger:

    def test_log_appends_to_repository(self):
        repo = FakeTradeRepository()
        repo.insert_trade("MNQ", "long", 100, 90, 130, 10,
                          datetime(2025, 1, 1, tzinfo=timezone.utc))
        logger = TradeLogger(repo)
        logger.log("T1", "OPEN", "Opened at 100")
        logs = repo.get_trade_logs("T1")
        assert len(logs) == 1
        assert logs[0]["event"] == "OPEN"

    def test_multiple_logs(self):
        repo = FakeTradeRepository()
        repo.insert_trade("MNQ", "long", 100, 90, 130, 10,
                          datetime(2025, 1, 1, tzinfo=timezone.utc))
        logger = TradeLogger(repo)
        logger.log("T1", "OPEN", "msg1")
        logger.log("T1", "SL_HIT", "msg2")
        logs = repo.get_trade_logs("T1")
        assert len(logs) == 2

    def test_format_logs_with_entries(self):
        td = TradeData(
            trade_id="abc12345-def",
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            risk_dollars=None,
            risk_pct=None,
            contracts=None,
            entry_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
            exit_price=None,
            exit_time=None,
            result=None,
            result_type=None,
            fees=None,
            pnl_usd=None,
            params=None,
            logs=[
                {"ts": "2025-01-01T10:00:00Z", "event": "OPEN", "msg": "Opened"},
                {"ts": "2025-01-01T11:00:00Z", "event": "SL_HIT", "msg": "Hit SL"},
            ],
        )
        output = TradeLogger.format_logs(td)
        assert "abc12345" in output
        assert "OPEN" in output
        assert "SL_HIT" in output

    def test_format_logs_no_entries(self):
        td = TradeData(
            trade_id="abc12345",
            pair="MNQ",
            trade_type="short",
            entry_price=100.0,
            stop_loss=110.0,
            take_profit=70.0,
            risk=10.0,
            risk_dollars=None,
            risk_pct=None,
            contracts=None,
            entry_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
            exit_price=None,
            exit_time=None,
            result=None,
            result_type=None,
            fees=None,
            pnl_usd=None,
            params=None,
            logs=[],
        )
        output = TradeLogger.format_logs(td)
        assert "no log entries" in output
