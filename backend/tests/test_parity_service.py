"""Unit tests for ParityCheckService."""

from unittest.mock import MagicMock

from src.application.parity_service import ParityCheckService
from src.infrastructure.market_closure_filter import MarketClosureFilter
from src.infrastructure.parity_checker import NinjaTraderParityChecker


class TestParityCheckService:
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

    def _make_service(self, local_bars, remote_bars, logger=None):
        """Build a ParityCheckService with mocked gateway that auto-responds."""
        data_source = MagicMock()
        data_source.load_historical_bars.return_value = local_bars

        gateway = MagicMock()

        logger = logger or MagicMock()

        checker = NinjaTraderParityChecker(market_filter=MarketClosureFilter())
        market_filter = MarketClosureFilter()

        service = ParityCheckService(
            data_source=data_source,
            gateway=gateway,
            checker=checker,
            market_filter=market_filter,
            logger=logger,
            pair="MNQ",
            instrument="MNQ",
        )

        # Patch the send to auto-trigger the response handler
        def _auto_send(bars_back, instrument=None):
            service._on_audit_response({
                "bars": remote_bars,
                "count": len(remote_bars),
            })

        gateway.send_audit_request.side_effect = _auto_send

        return service, data_source, gateway

    def test_successful_parity_check(self):
        """Happy path: local and remote match."""
        local = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(160, 10.5, 12.0, 10.0, 11.5, 200),
        ]
        remote = list(local)
        service, ds, _ = self._make_service(local, remote)

        result = service.check_parity(hours_back=5)

        assert result.all_good is True
        assert result.gaps_found == 0
        ds.load_historical_bars.assert_called_once_with("1m", pair="MNQ")

    def test_detects_missing_bar(self):
        """Remote has more bars than local → missing gap detected."""
        local = [self._bar(100, 10.0, 11.0, 9.0, 10.5, 100)]
        remote = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(160, 10.5, 12.0, 10.0, 11.5, 200),
        ]
        service, _, _ = self._make_service(local, remote)

        result = service.check_parity(hours_back=5)

        assert result.all_good is False
        assert result.gaps_found == 1
        assert result.gaps[0].gap_type == "missing"

    def test_empty_local_bars(self):
        """No cached local bars → error result."""
        service, _, _ = self._make_service([], [self._bar(100, 10.0, 11.0, 9.0, 10.5, 100)])

        result = service.check_parity(hours_back=5)

        assert result.all_good is False
        assert "No local bars" in result.summary

    def test_empty_remote_bars(self):
        """NT returns empty audit response → error result."""
        local = [self._bar(100, 10.0, 11.0, 9.0, 10.5, 100)]
        service, _, _ = self._make_service(local, [])
        # Override to send empty
        service._gateway.send_audit_request.side_effect = lambda bars_back, instrument=None: service._on_audit_response({"bars": [], "count": 0})

        result = service.check_parity(hours_back=5)

        assert result.all_good is False
        assert "empty" in result.summary.lower()

    def test_timeout_when_no_audit_response(self, monkeypatch):
        """If NT doesn't respond within timeout, return error result."""
        data_source = MagicMock()
        data_source.load_historical_bars.return_value = [self._bar(100, 10.0, 11.0, 9.0, 10.5, 100)]

        gateway = MagicMock()
        # send_audit_request does NOT trigger response → timeout
        gateway.send_audit_request = MagicMock()

        logger = MagicMock()
        checker = NinjaTraderParityChecker(market_filter=MarketClosureFilter())
        market_filter = MarketClosureFilter()

        service = ParityCheckService(
            data_source=data_source,
            gateway=gateway,
            checker=checker,
            market_filter=market_filter,
            logger=logger,
        )

        # Patch the timeout to be very short for unit tests
        monkeypatch.setattr("threading.Event.wait", lambda self, timeout: False)

        result = service.check_parity(hours_back=5)

        assert result.all_good is False
        assert "timeout" in result.summary.lower()

    def test_service_uses_correct_bars_back(self):
        """5 hours at 1m should request 300 bars."""
        local = [self._bar(i * 60, 10.0, 11.0, 9.0, 10.5, 100) for i in range(400)]
        remote = list(local)
        service, _, gw = self._make_service(local, remote)

        service.check_parity(hours_back=5)

        gw.send_audit_request.assert_called_once()
        call_args = gw.send_audit_request.call_args
        assert call_args.kwargs["bars_back"] == 300
