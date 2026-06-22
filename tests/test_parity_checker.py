"""Unit tests for NinjaTraderParityChecker."""


from src.infrastructure.market_closure_filter import MarketClosureFilter
from src.infrastructure.parity_checker import NinjaTraderParityChecker


class TestNinjaTraderParityChecker:
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

    def test_identical_bars_all_good(self):
        """No differences → all_good=True, empty gaps list."""
        local = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(160, 10.5, 12.0, 10.0, 11.5, 200),
        ]
        remote = list(local)
        checker = NinjaTraderParityChecker(market_filter=MarketClosureFilter())
        result = checker.check(local, remote)

        assert result.all_good is True
        assert result.gaps_found == 0
        assert len(result.gaps) == 0
        assert "match perfectly" in result.summary

    def test_missing_bar_detected(self):
        """Remote has a bar that local doesn't → missing gap."""
        local = [self._bar(100, 10.0, 11.0, 9.0, 10.5, 100)]
        remote = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(160, 10.5, 12.0, 10.0, 11.5, 200),
        ]
        checker = NinjaTraderParityChecker(market_filter=MarketClosureFilter())
        result = checker.check(local, remote)

        assert result.all_good is False
        assert result.gaps_found == 1
        assert len(result.gaps) == 1
        gap = result.gaps[0]
        assert gap.gap_type == "missing"
        assert gap.is_market_closed is False

    def test_extra_bar_detected(self):
        """Local has a bar that remote doesn't → extra gap."""
        local = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(160, 10.5, 12.0, 10.0, 11.5, 200),
        ]
        remote = [self._bar(100, 10.0, 11.0, 9.0, 10.5, 100)]
        checker = NinjaTraderParityChecker(market_filter=MarketClosureFilter())
        result = checker.check(local, remote)

        assert result.all_good is False
        assert result.gaps_found == 1
        gap = result.gaps[0]
        assert gap.gap_type == "extra"

    def test_mismatch_detected(self):
        """Same timestamp but different OHLC → mismatch gap with field details."""
        local = [self._bar(100, 10.0, 11.0, 9.0, 10.5, 100)]
        remote = [self._bar(100, 10.0, 11.5, 9.0, 10.5, 105)]
        checker = NinjaTraderParityChecker(market_filter=MarketClosureFilter())
        result = checker.check(local, remote)

        assert result.all_good is False
        assert result.gaps_found == 1
        gap = result.gaps[0]
        assert gap.gap_type == "mismatch"
        assert "high" in gap.details
        assert "volume" not in gap.details

    def test_market_closed_gap_excluded_from_alert(self):
        """A gap during CME maintenance should be in gaps list but all_good stays True."""
        from datetime import datetime, timezone

        # CME maintenance: 16:00-17:01 CDT on 2024-06-10
        # Use a bar clearly inside the window: 16:30 CDT = 21:30 UTC
        maint_time = int(datetime(2024, 6, 10, 21, 30, 0, tzinfo=timezone.utc).timestamp())

        local = [self._bar(100, 10.0, 11.0, 9.0, 10.5, 100)]
        remote = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(maint_time, 10.5, 12.0, 10.0, 11.5, 200),
        ]
        checker = NinjaTraderParityChecker(market_filter=MarketClosureFilter())
        result = checker.check(local, remote)

        # The missing bar during maintenance should be flagged as market-closed
        assert result.all_good is True
        assert len(result.gaps) == 1
        assert result.gaps[0].is_market_closed is True
        assert "market-closed" in result.summary

    def test_mixed_gaps_some_closed(self):
        """One market-closed gap + one real gap → all_good=False."""
        from datetime import datetime, timezone

        # Bar inside maintenance window: 16:30 CDT = 21:30 UTC
        maint_time = int(datetime(2024, 6, 10, 21, 30, 0, tzinfo=timezone.utc).timestamp())
        normal_time = int(datetime(2024, 6, 10, 15, 0, 0, tzinfo=timezone.utc).timestamp())  # 10:00 CDT

        local = [self._bar(100, 10.0, 11.0, 9.0, 10.5, 100)]
        remote = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(maint_time, 10.5, 12.0, 10.0, 11.5, 200),     # market-closed gap
            self._bar(normal_time, 11.0, 12.0, 10.5, 11.5, 300),    # real gap
        ]
        checker = NinjaTraderParityChecker(market_filter=MarketClosureFilter())
        result = checker.check(local, remote)

        assert result.all_good is False
        assert result.gaps_found == 1  # Only the non-market-closed gap counts
        assert len(result.gaps) == 2
        closed_gaps = [g for g in result.gaps if g.is_market_closed]
        open_gaps = [g for g in result.gaps if not g.is_market_closed]
        assert len(closed_gaps) == 1
        assert len(open_gaps) == 1

    def test_parity_result_to_dict(self):
        """ParityResult.to_dict() must be JSON-serializable."""
        local = [self._bar(100, 10.0, 11.0, 9.0, 10.5, 100)]
        remote = [
            self._bar(100, 10.0, 11.0, 9.0, 10.5, 100),
            self._bar(160, 10.5, 12.0, 10.0, 11.5, 200),
        ]
        checker = NinjaTraderParityChecker(market_filter=MarketClosureFilter())
        result = checker.check(local, remote)

        d = result.to_dict()
        assert "checked_at" in d
        assert "bars_checked" in d
        assert "gaps_found" in d
        assert "gaps" in d
        assert "all_good" in d
        assert "summary" in d
        assert isinstance(d["gaps"], list)
        assert isinstance(d["all_good"], bool)

        if d["gaps"]:
            gap = d["gaps"][0]
            assert "start_time" in gap
            assert "end_time" in gap
            assert "duration_seconds" in gap
            assert "gap_type" in gap
            assert "details" in gap
            assert "is_market_closed" in gap
