"""Unit tests for BarComparer and NinjaTraderBarAuditor."""

from unittest.mock import MagicMock

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

    def test_grace_delay_re_fetch_reports_ok_when_caught_up(self, monkeypatch):
        """
        After the grace delay, the local cache is re-fetched.
        If the in-flight bar has arrived by then, no drift is reported.
        """
        gateway = MagicMock()
        data_source = MagicMock()
        logger = MagicMock()

        bar_100 = {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100, "pair": "MNQ"}
        bar_160 = {"time": 160, "open": 10.5, "high": 12.0, "low": 10.0, "close": 11.5, "volume": 200, "pair": "MNQ"}
        bar_220 = {"time": 220, "open": 11.5, "high": 13.0, "low": 11.0, "close": 12.5, "volume": 300, "pair": "MNQ"}

        # After grace delay the local cache is fully caught up
        data_source.load_historical_bars.return_value = [bar_100, bar_160, bar_220]

        auditor = NinjaTraderBarAuditor(
            gateway=gateway,
            data_source=data_source,
            logger=logger,
            interval_minutes=5,
            bars_back=10,
        )

        def mock_send_audit_request(bars_back):
            auditor._on_audit_response({
                "bars": [bar_100, bar_160, bar_220],
            })

        gateway.send_audit_request.side_effect = mock_send_audit_request

        # Patch sleep to avoid real 1.5s delay in unit tests
        monkeypatch.setattr("time.sleep", lambda _x: None)

        # Run the audit directly; timer-based scheduling has a 1-minute minimum
        auditor._run_audit()

        # Should NOT report a drift error
        drift_errors = [call for call in logger.error.call_args_list if "DRIFT" in str(call)]
        assert len(drift_errors) == 0, f"Expected no drift errors, got: {drift_errors}"

        # Should log OK because the re-fetch after grace delay matches
        ok_calls = [call for call in logger.info.call_args_list if "[BarAuditor] OK" in str(call)]
        assert len(ok_calls) == 1, f"Expected one OK log, got: {ok_calls}"

    def test_grace_delay_still_reports_drift_if_bar_never_arrives(self, monkeypatch):
        """
        If the missing bar is genuine (not just in-flight), the grace delay
        does not suppress the drift — it is still reported.
        """
        gateway = MagicMock()
        data_source = MagicMock()
        logger = MagicMock()

        bar_100 = {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100, "pair": "MNQ"}
        bar_160 = {"time": 160, "open": 10.5, "high": 12.0, "low": 10.0, "close": 11.5, "volume": 200, "pair": "MNQ"}
        bar_220 = {"time": 220, "open": 11.5, "high": 13.0, "low": 11.0, "close": 12.5, "volume": 300, "pair": "MNQ"}

        # Local cache is permanently missing the newest bar
        data_source.load_historical_bars.return_value = [bar_100, bar_160]

        auditor = NinjaTraderBarAuditor(
            gateway=gateway,
            data_source=data_source,
            logger=logger,
            interval_minutes=5,
            bars_back=10,
        )

        def mock_send_audit_request(bars_back):
            auditor._on_audit_response({
                "bars": [bar_100, bar_160, bar_220],
            })

        gateway.send_audit_request.side_effect = mock_send_audit_request

        # Patch sleep to avoid real 1.5s delay in unit tests
        monkeypatch.setattr("time.sleep", lambda _x: None)

        auditor._run_audit()

        # Should still report drift because the bar never arrived
        drift_errors = [call for call in logger.error.call_args_list if "DRIFT" in str(call)]
        assert len(drift_errors) == 1, f"Expected exactly one drift error, got: {drift_errors}"


    def test_edge_only_drift_downgraded_to_warning(self, monkeypatch):
        """
        A transient window-edge mismatch (oldest local extra + newest remote missing,
        typically caused by the forming/partial bar) should be logged as a warning,
        not a critical drift error, and must not fire the on_drift callback.
        """
        gateway = MagicMock()
        data_source = MagicMock()
        logger = MagicMock()
        on_drift = MagicMock()

        # Local has bars [100, 160] (2 bars, simulating last-60 window [T-1, T])
        bar_100 = {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100, "pair": "MNQ"}
        bar_160 = {"time": 160, "open": 10.5, "high": 12.0, "low": 10.0, "close": 11.5, "volume": 200, "pair": "MNQ"}

        # Remote has bars [160, 220] — newest 220 is the forming bar not yet in local,
        # oldest 100 has fallen out of remote's 2-bar window.
        bar_220 = {"time": 220, "open": 11.5, "high": 13.0, "low": 11.0, "close": 12.5, "volume": 300, "pair": "MNQ"}

        data_source.load_historical_bars.return_value = [bar_100, bar_160]

        auditor = NinjaTraderBarAuditor(
            gateway=gateway,
            data_source=data_source,
            logger=logger,
            interval_minutes=5,
            bars_back=2,
            on_drift=on_drift,
        )

        def mock_send_audit_request(bars_back):
            auditor._on_audit_response({
                "bars": [bar_160, bar_220],
            })

        gateway.send_audit_request.side_effect = mock_send_audit_request

        # Patch sleep to avoid real 1.5s delay in unit tests
        monkeypatch.setattr("time.sleep", lambda _x: None)

        auditor._run_audit()

        # Should NOT log a DRIFT error
        drift_errors = [call for call in logger.error.call_args_list if "DRIFT DETECTED" in str(call)]
        assert len(drift_errors) == 0, f"Expected no drift errors, got: {drift_errors}"

        # Should log a warning about transient window-edge drift
        warning_calls = [call for call in logger.warning.call_args_list if "window-edge drift" in str(call)]
        assert len(warning_calls) == 1, f"Expected one window-edge warning, got: {warning_calls}"

        # on_drift callback must NOT fire for edge-only drift
        on_drift.assert_not_called()

    def test_true_drift_still_logged_as_error(self, monkeypatch):
        """
        A genuine mismatch in the middle of the window or OHLCV difference must
        still be logged as a critical drift error and fire the on_drift callback.
        """
        gateway = MagicMock()
        data_source = MagicMock()
        logger = MagicMock()
        on_drift = MagicMock()

        bar_100 = {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100, "pair": "MNQ"}
        bar_160 = {"time": 160, "open": 10.5, "high": 12.0, "low": 10.0, "close": 11.5, "volume": 200, "pair": "MNQ"}
        bar_220 = {"time": 220, "open": 11.5, "high": 13.0, "low": 11.0, "close": 12.5, "volume": 300, "pair": "MNQ"}

        # Local is missing the middle bar 160 — this is a genuine gap, not edge drift
        data_source.load_historical_bars.return_value = [bar_100, bar_220]

        auditor = NinjaTraderBarAuditor(
            gateway=gateway,
            data_source=data_source,
            logger=logger,
            interval_minutes=5,
            bars_back=3,
            on_drift=on_drift,
        )

        def mock_send_audit_request(bars_back):
            auditor._on_audit_response({
                "bars": [bar_100, bar_160, bar_220],
            })

        gateway.send_audit_request.side_effect = mock_send_audit_request
        monkeypatch.setattr("time.sleep", lambda _x: None)

        auditor._run_audit()

        drift_errors = [call for call in logger.error.call_args_list if "DRIFT DETECTED" in str(call)]
        assert len(drift_errors) == 1, f"Expected one drift error, got: {drift_errors}"
        on_drift.assert_called_once()

    def test_mismatch_only_drift_downgraded_to_warning(self, monkeypatch):
        """
        OHLCV-only mismatches (no missing/extra bars) are expected NT behavior
        (live stream values vs historical cache values). They should produce a
        warning, not a critical error, and must not fire on_drift.
        """
        gateway = MagicMock()
        data_source = MagicMock()
        logger = MagicMock()
        on_drift = MagicMock()

        bar_100_local = {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100, "pair": "MNQ"}
        bar_100_remote = {"time": 100, "open": 10.0, "high": 11.5, "low": 9.0, "close": 10.5, "volume": 100, "pair": "MNQ"}

        data_source.load_historical_bars.return_value = [bar_100_local]

        auditor = NinjaTraderBarAuditor(
            gateway=gateway,
            data_source=data_source,
            logger=logger,
            interval_minutes=5,
            bars_back=1,
            on_drift=on_drift,
        )

        def mock_send_audit_request(bars_back):
            auditor._on_audit_response({
                "bars": [bar_100_remote],
            })

        gateway.send_audit_request.side_effect = mock_send_audit_request
        monkeypatch.setattr("time.sleep", lambda _x: None)

        auditor._run_audit()

        # Should NOT log a DRIFT error
        drift_errors = [call for call in logger.error.call_args_list if "DRIFT DETECTED" in str(call)]
        assert len(drift_errors) == 0, f"Expected no drift errors, got: {drift_errors}"

        # Should log a warning about OHLCV mismatch
        warning_calls = [call for call in logger.warning.call_args_list if "OHLCV mismatch" in str(call)]
        assert len(warning_calls) == 1, f"Expected one OHLCV warning, got: {warning_calls}"

        # on_drift must NOT fire for mismatch-only drift
        on_drift.assert_not_called()

    def test_structural_drift_with_mismatch_still_logged_as_error(self, monkeypatch):
        """
        A drift that includes missing/extra bars PLUS mismatches is a true structural
        problem and must still produce an error and fire on_drift.
        """
        gateway = MagicMock()
        data_source = MagicMock()
        logger = MagicMock()
        on_drift = MagicMock()

        bar_100_local = {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100, "pair": "MNQ"}
        bar_160_local = {"time": 160, "open": 10.5, "high": 12.0, "low": 10.0, "close": 11.5, "volume": 200, "pair": "MNQ"}
        bar_100_remote = {"time": 100, "open": 10.0, "high": 11.5, "low": 9.0, "close": 10.5, "volume": 100, "pair": "MNQ"}
        bar_220_remote = {"time": 220, "open": 11.5, "high": 13.0, "low": 11.0, "close": 12.5, "volume": 300, "pair": "MNQ"}

        data_source.load_historical_bars.return_value = [bar_100_local, bar_160_local]

        auditor = NinjaTraderBarAuditor(
            gateway=gateway,
            data_source=data_source,
            logger=logger,
            interval_minutes=5,
            bars_back=2,
            on_drift=on_drift,
        )

        def mock_send_audit_request(bars_back):
            auditor._on_audit_response({
                "bars": [bar_100_remote, bar_220_remote],
            })

        gateway.send_audit_request.side_effect = mock_send_audit_request
        monkeypatch.setattr("time.sleep", lambda _x: None)

        auditor._run_audit()

        drift_errors = [call for call in logger.error.call_args_list if "DRIFT DETECTED" in str(call)]
        assert len(drift_errors) == 1, f"Expected one drift error, got: {drift_errors}"
        on_drift.assert_called_once()

    def test_edge_only_case2_downgraded_to_warning(self, monkeypatch):
        """
        Case 2: Python has the newest live bar that NT historical cache hasn't
        picked up yet; NT has the oldest bar that Python dropped. This is a
        transient window-edge mismatch, not data corruption.
        """
        gateway = MagicMock()
        data_source = MagicMock()
        logger = MagicMock()
        on_drift = MagicMock()

        # Local bars: [100, 160] (2 bars, simulating last-2 window)
        bar_100 = {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100, "pair": "MNQ"}
        bar_160 = {"time": 160, "open": 10.5, "high": 12.0, "low": 10.0, "close": 11.5, "volume": 200, "pair": "MNQ"}

        # Remote bars: [40, 100] — oldest 40 is not in local, newest 160 is not in remote
        bar_40 = {"time": 40, "open": 9.0, "high": 10.0, "low": 8.0, "close": 9.5, "volume": 50, "pair": "MNQ"}
        bar_100_remote = {"time": 100, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100, "pair": "MNQ"}

        data_source.load_historical_bars.return_value = [bar_100, bar_160]

        auditor = NinjaTraderBarAuditor(
            gateway=gateway,
            data_source=data_source,
            logger=logger,
            interval_minutes=5,
            bars_back=2,
            on_drift=on_drift,
        )

        def mock_send_audit_request(bars_back):
            auditor._on_audit_response({
                "bars": [bar_40, bar_100_remote],
            })

        gateway.send_audit_request.side_effect = mock_send_audit_request
        monkeypatch.setattr("time.sleep", lambda _x: None)

        auditor._run_audit()

        # Should NOT log a DRIFT error
        drift_errors = [call for call in logger.error.call_args_list if "DRIFT DETECTED" in str(call)]
        assert len(drift_errors) == 0, f"Expected no drift errors, got: {drift_errors}"

        # Should log a warning about window-edge drift
        warning_calls = [call for call in logger.warning.call_args_list if "window-edge drift" in str(call)]
        assert len(warning_calls) == 1, f"Expected one window-edge warning, got: {warning_calls}"

        # on_drift must NOT fire for edge-only drift
        on_drift.assert_not_called()
