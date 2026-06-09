"""Unit tests for BarComparer and NinjaTraderBarAuditor."""

from unittest.mock import MagicMock

import pytest

from src.infrastructure.bar_auditor import BarComparer, NinjaTraderBarAuditor


class TestBarComparer:
    def _bar(self, time, open_, high, low, close, volume=1, pair="MNQ"):
        return {
            "time": time,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "pair": pair,
        }

    def test_identical_bars_no_drift(self):
        local = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(160, 10.5, 12.0, 10.0, 11.5, 200),
        ]
        remote = list(local)
        comparer = BarComparer()
        result = comparer.compare(local, remote)

        assert not result.has_drift
        assert result.missing_count == 0
        assert result.extra_count == 0
        assert result.mismatch_count == 0

    def test_missing_bar(self):
        local = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(160, 10.5, 12.0, 10.0, 11.5, 200),
        ]
        remote = [local[0]]  # missing bar at 160
        comparer = BarComparer()
        result = comparer.compare(local, remote)

        assert result.has_drift
        assert result.missing_count == 1
        assert result.extra_count == 0
        assert result.mismatch_count == 0
        assert result.details[0].time == 160
        assert result.details[0].local_bar is not None
        assert result.details[0].remote_bar is None

    def test_extra_bar(self):
        local = [self._bar(100, 10.0, 11.0, 9.0, 10.5, 100)]
        remote = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(160, 10.5, 12.0, 10.0, 11.5, 200),
        ]
        comparer = BarComparer()
        result = comparer.compare(local, remote)

        assert result.has_drift
        assert result.missing_count == 0
        assert result.extra_count == 1
        assert result.mismatch_count == 0
        assert result.details[0].time == 160
        assert result.details[0].local_bar is None
        assert result.details[0].remote_bar is not None

    def test_mismatched_ohlcv(self):
        local = [self._bar(100, 10.0, 11.0, 9.0, 10.5, 100)]
        remote = [self._bar(100, 10.0, 11.5, 9.0, 10.5, 100)]
        comparer = BarComparer()
        result = comparer.compare(local, remote)

        assert result.has_drift
        assert result.missing_count == 0
        assert result.extra_count == 0
        assert result.mismatch_count == 1
        assert result.details[0].time == 100
        assert "high" in result.details[0].field_differences
        assert result.details[0].field_differences["high"] == (11.0, 11.5)

    def test_multiple_issues(self):
        local = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(160, 10.5, 12.0, 10.0, 11.5, 200),
        ]
        remote = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(160, 10.5, 12.5, 10.0, 11.5, 200),  # mismatch high
            self._bar(220, 11.5, 13.0, 11.0, 12.5, 300),  # extra
        ]
        comparer = BarComparer()
        result = comparer.compare(local, remote)

        assert result.has_drift
        assert result.missing_count == 0
        assert result.extra_count == 1
        assert result.mismatch_count == 1
        assert len(result.details) == 2

    def test_empty_lists(self):
        comparer = BarComparer()
        result = comparer.compare([], [])

        assert not result.has_drift
        assert result.missing_count == 0
        assert result.extra_count == 0
        assert result.mismatch_count == 0


class TestNinjaTraderBarAuditorLifecycle:
    def test_start_stop(self):
        gateway = MagicMock()
        data_source = MagicMock()
        logger = MagicMock()

        data_source.load_historical_bars.return_value = []

        auditor = NinjaTraderBarAuditor(
            gateway=gateway,
            data_source=data_source,
            logger=logger,
            interval_minutes=0,  # will fire immediately
            bars_back=10,
        )
        auditor.start()
        # give the timer a moment to fire
        import time

        time.sleep(0.1)
        auditor.stop()

        gateway.on_audit_response.assert_called_once()
        assert logger.info.called or logger.warning.called or logger.error.called
