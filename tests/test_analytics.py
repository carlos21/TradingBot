"""Tests for src/analytics.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.analytics import (
    AnalyticsReporter,
    DistributionData,
    NoOpReporter,
    SentryReporter,
    TimeSeriesData,
    TradeDetail,
    TradeStatistics,
)


class TestAnalyticsReporter:

    def test_is_abstract(self):
        assert hasattr(AnalyticsReporter, "__abstractmethods__")


class TestNoOpReporter:

    def test_all_methods_no_op(self):
        reporter = NoOpReporter()
        reporter.capture_exception(Exception("test"))
        reporter.capture_trade_event("open", {})
        reporter.capture_signal_event("trigger", {})
        reporter.set_context("test", {})


class TestSentryReporter:

    @patch("sentry_sdk.init")
    def test_init(self, mock_init):
        reporter = SentryReporter("https://test@sentry.io/1")
        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args.kwargs
        assert call_kwargs["dsn"] == "https://test@sentry.io/1"
        assert call_kwargs["traces_sample_rate"] == 0.0
        assert call_kwargs["environment"] == "live"

    @patch("sentry_sdk.set_context")
    @patch("sentry_sdk.capture_exception")
    def test_capture_exception_with_context(self, mock_capture, mock_set_context):
        reporter = SentryReporter("https://test@sentry.io/1")
        exc = ValueError("test error")
        reporter.capture_exception(exc, context={"key": "value"})
        mock_set_context.assert_called_once_with("error_context", {"key": "value"})
        mock_capture.assert_called_once_with(exc)

    @patch("sentry_sdk.capture_exception")
    def test_capture_exception_without_context(self, mock_capture):
        reporter = SentryReporter("https://test@sentry.io/1")
        exc = ValueError("test error")
        reporter.capture_exception(exc)
        mock_capture.assert_called_once_with(exc)

    @patch("sentry_sdk.add_breadcrumb")
    def test_capture_trade_event(self, mock_breadcrumb):
        reporter = SentryReporter("https://test@sentry.io/1")
        reporter.capture_trade_event("open", {"trade_id": "T1"})
        mock_breadcrumb.assert_called_once()
        call_kwargs = mock_breadcrumb.call_args.kwargs
        assert call_kwargs["category"] == "trade"
        assert call_kwargs["message"] == "open"
        assert call_kwargs["data"] == {"trade_id": "T1"}

    @patch("sentry_sdk.add_breadcrumb")
    def test_capture_signal_event(self, mock_breadcrumb):
        reporter = SentryReporter("https://test@sentry.io/1")
        reporter.capture_signal_event("trigger", {"line_id": "L1"})
        mock_breadcrumb.assert_called_once()
        call_kwargs = mock_breadcrumb.call_args.kwargs
        assert call_kwargs["category"] == "strategy"

    @patch("sentry_sdk.set_context")
    def test_set_context(self, mock_set_context):
        reporter = SentryReporter("https://test@sentry.io/1")
        reporter.set_context("user", {"id": "123"})
        mock_set_context.assert_called_once_with("user", {"id": "123"})


class TestTradeStatistics:

    def test_to_dict_basic(self):
        stats = TradeStatistics(
            total_trades=10,
            open_trades=2,
            winning_trades=6,
            losing_trades=4,
            win_rate=0.6,
            total_pnl=12.5,
            total_pnl_usd=2500.0,
            avg_pnl=1.25,
            avg_pnl_usd=250.0,
            avg_win=3.0,
            avg_loss=-1.5,
            profit_factor=2.0,
            avg_r_multiple=1.25,
            avg_profit_monthly=500.0,
        )
        d = stats.to_dict()
        assert d["total_trades"] == 10
        assert d["win_rate"] == 0.6
        assert d["total_pnl_usd"] == 2500.0
        assert d["avg_profit_monthly"] == 500.0

    def test_to_dict_infinite_profit_factor(self):
        stats = TradeStatistics(
            total_trades=1,
            open_trades=0,
            winning_trades=1,
            losing_trades=0,
            win_rate=1.0,
            total_pnl=3.0,
            total_pnl_usd=600.0,
            avg_pnl=3.0,
            avg_pnl_usd=600.0,
            avg_win=3.0,
            avg_loss=0.0,
            profit_factor=float("inf"),
            avg_r_multiple=3.0,
        )
        d = stats.to_dict()
        assert d["profit_factor"] is None


class TestTimeSeriesData:

    def test_to_dict(self):
        ts = TimeSeriesData(labels=["Jan", "Feb"], values=[10.0, 20.0])
        d = ts.to_dict()
        assert d == {"labels": ["Jan", "Feb"], "data": [10.0, 20.0]}


class TestDistributionData:

    def test_to_dict(self):
        dist = DistributionData(labels=["A", "B"], values=[5, 10])
        d = dist.to_dict()
        assert d == {"labels": ["A", "B"], "data": [5, 10]}


class TestTradeDetail:

    def test_to_dict(self):
        detail = TradeDetail(
            trade_id="T1",
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            risk_dollars=200.0,
            risk_pct=0.2,
            contracts=2.0,
            entry_time=1700000000.0,
            exit_price=130.0,
            exit_time=1700003600.0,
            result=3.0,
            result_type="TP",
            fees=4.0,
            pnl_usd=596.0,
            status="closed",
            logs=[],
        )
        d = detail.to_dict()
        assert d["trade_id"] == "T1"
        assert d["status"] == "closed"
