"""Unit tests for MarketClosureFilter.

Tests CME futures market closure detection for MNQ.
"""

from datetime import datetime, timezone

from src.infrastructure.market_closure_filter import MarketClosureFilter


class TestMarketClosureFilter:
    """CME MNQ market closure rules."""

    def test_cme_maintenance_window(self):
        """Gap from 16:00–17:01 CDT should be flagged as market closed."""
        f = MarketClosureFilter()
        # 2024-06-10 16:00 CDT = 21:00 UTC
        dt_start = datetime(2024, 6, 10, 21, 0, 0, tzinfo=timezone.utc)
        dt_end = datetime(2024, 6, 10, 22, 1, 0, tzinfo=timezone.utc)
        assert f.is_market_closed_gap(int(dt_start.timestamp()), int(dt_end.timestamp())) is True

    def test_normal_trading_hours(self):
        """Gap at 10:30 CDT should NOT be flagged as market closed."""
        f = MarketClosureFilter()
        dt_start = datetime(2024, 6, 10, 15, 30, 0, tzinfo=timezone.utc)  # 10:30 CDT
        dt_end = datetime(2024, 6, 10, 15, 31, 0, tzinfo=timezone.utc)
        assert f.is_market_closed_gap(int(dt_start.timestamp()), int(dt_end.timestamp())) is False

    def test_saturday_gap(self):
        """Any gap touching Saturday should be flagged as market closed."""
        f = MarketClosureFilter()
        # 2024-06-08 is a Saturday
        dt_start = datetime(2024, 6, 8, 10, 0, 0, tzinfo=timezone.utc)
        dt_end = datetime(2024, 6, 8, 10, 1, 0, tzinfo=timezone.utc)
        assert f.is_market_closed_gap(int(dt_start.timestamp()), int(dt_end.timestamp())) is True

    def test_sunday_before_open(self):
        """Gap before Sunday 17:00 CDT should be flagged as market closed."""
        f = MarketClosureFilter()
        # 2024-06-09 is a Sunday, 16:00 CDT = 21:00 UTC (before 17:00 open)
        dt_start = datetime(2024, 6, 9, 21, 0, 0, tzinfo=timezone.utc)
        dt_end = datetime(2024, 6, 9, 21, 1, 0, tzinfo=timezone.utc)
        assert f.is_market_closed_gap(int(dt_start.timestamp()), int(dt_end.timestamp())) is True

    def test_sunday_after_open(self):
        """Gap after Sunday 17:00 CDT should NOT be flagged as market closed."""
        f = MarketClosureFilter()
        # 2024-06-09 18:00 CDT = 23:00 UTC (after 17:00 open)
        dt_start = datetime(2024, 6, 9, 23, 0, 0, tzinfo=timezone.utc)
        dt_end = datetime(2024, 6, 9, 23, 1, 0, tzinfo=timezone.utc)
        assert f.is_market_closed_gap(int(dt_start.timestamp()), int(dt_end.timestamp())) is False

    def test_friday_after_close(self):
        """Gap after Friday 16:00 CDT should be flagged as market closed."""
        f = MarketClosureFilter()
        # 2024-06-07 is a Friday, 17:00 CDT = 22:00 UTC (after 16:00 close)
        dt_start = datetime(2024, 6, 7, 22, 0, 0, tzinfo=timezone.utc)
        dt_end = datetime(2024, 6, 7, 22, 1, 0, tzinfo=timezone.utc)
        assert f.is_market_closed_gap(int(dt_start.timestamp()), int(dt_end.timestamp())) is True

    def test_friday_during_trading(self):
        """Gap during Friday trading hours should NOT be flagged."""
        f = MarketClosureFilter()
        dt_start = datetime(2024, 6, 7, 15, 0, 0, tzinfo=timezone.utc)  # 10:00 CDT Friday
        dt_end = datetime(2024, 6, 7, 15, 1, 0, tzinfo=timezone.utc)
        assert f.is_market_closed_gap(int(dt_start.timestamp()), int(dt_end.timestamp())) is False

    def test_classic_3660s_gap(self):
        """The exact 61-minute CME maintenance gap should be detected."""
        f = MarketClosureFilter()
        dt_start = datetime(2024, 6, 10, 21, 0, 0, tzinfo=timezone.utc)  # 16:00 CDT
        dt_end = datetime(2024, 6, 10, 22, 1, 0, tzinfo=timezone.utc)    # 17:01 CDT
        assert f.is_market_closed_gap(int(dt_start.timestamp()), int(dt_end.timestamp())) is True

    def test_62_minute_maintenance_gap_with_early_start(self):
        """A 62-minute gap starting before 16:00 CDT should still be flagged."""
        f = MarketClosureFilter()
        dt_start = datetime(2024, 6, 10, 20, 59, 0, tzinfo=timezone.utc)  # 15:59 CDT
        dt_end = datetime(2024, 6, 10, 22, 1, 0, tzinfo=timezone.utc)    # 17:01 CDT
        assert f.is_market_closed_gap(int(dt_start.timestamp()), int(dt_end.timestamp())) is True

    def test_62_minute_maintenance_gap_with_late_end(self):
        """A 62-minute gap ending after 17:00 CDT should still be flagged."""
        f = MarketClosureFilter()
        dt_start = datetime(2024, 6, 10, 21, 0, 0, tzinfo=timezone.utc)  # 16:00 CDT
        dt_end = datetime(2024, 6, 10, 22, 2, 0, tzinfo=timezone.utc)    # 17:02 CDT
        assert f.is_market_closed_gap(int(dt_start.timestamp()), int(dt_end.timestamp())) is True
