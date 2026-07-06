"""Tests for src/services/analytics_service.py."""

from datetime import datetime, timezone

from src.services.analytics_service import AnalyticsService
from tests.fakes import FakeTradeRepository


def _make_trade(
    trade_repo,
    pair="MNQ",
    trade_type="long",
    entry_price=100.0,
    stop_loss=90.0,
    take_profit=130.0,
    risk=10.0,
    entry_time=None,
    exit_price=None,
    exit_time=None,
    result=None,
    result_type=None,
    pnl_usd=None,
    risk_dollars=None,
    contracts=None,
    account=None,
):
    trade = trade_repo.insert_trade(
        pair=pair,
        trade_type=trade_type,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk=risk,
        entry_time=entry_time or datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
        risk_dollars=risk_dollars,
        contracts=contracts,
        account=account,
    )
    if exit_price is not None and exit_time is not None:
        trade_repo.close_trade(
            trade_id=trade.trade_id,
            exit_price=exit_price,
            exit_time=exit_time,
            result=result,
            result_type=result_type,
            pnl_usd=pnl_usd,
        )
    return trade


class TestAnalyticsServiceCalculateStatistics:

    def test_no_trades(self):
        repo = FakeTradeRepository()
        svc = AnalyticsService(repo)
        stats = svc.calculate_statistics("MNQ")
        assert stats.total_trades == 0
        assert stats.winning_trades == 0
        assert stats.losing_trades == 0
        assert stats.win_rate == 0.0
        assert stats.total_pnl == 0.0
        assert stats.profit_factor == 0.0

    def test_only_open_trades(self):
        repo = FakeTradeRepository()
        _make_trade(repo, entry_price=100.0, stop_loss=90.0, take_profit=130.0)
        svc = AnalyticsService(repo)
        stats = svc.calculate_statistics("MNQ")
        assert stats.total_trades == 1
        assert stats.open_trades == 1
        assert stats.winning_trades == 0
        assert stats.losing_trades == 0

    def test_one_winner(self):
        repo = FakeTradeRepository()
        _make_trade(
            repo,
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
            result=3.0,
            result_type="TP",
        )
        svc = AnalyticsService(repo)
        stats = svc.calculate_statistics("MNQ")
        assert stats.total_trades == 1
        assert stats.winning_trades == 1
        assert stats.losing_trades == 0
        assert stats.win_rate == 1.0
        assert stats.total_pnl == 3.0
        assert stats.avg_win == 3.0
        assert stats.avg_loss == 0.0
        assert stats.profit_factor == float("inf")

    def test_one_loser(self):
        repo = FakeTradeRepository()
        _make_trade(
            repo,
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            exit_price=90.0,
            exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
            result=-1.0,
            result_type="SL",
        )
        svc = AnalyticsService(repo)
        stats = svc.calculate_statistics("MNQ")
        assert stats.total_trades == 1
        assert stats.winning_trades == 0
        assert stats.losing_trades == 1
        assert stats.win_rate == 0.0
        assert stats.total_pnl == -1.0
        assert stats.avg_win == 0.0
        assert stats.avg_loss == -1.0

    def test_mixed_results(self):
        repo = FakeTradeRepository()
        _make_trade(
            repo,
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
            result=3.0,
            result_type="TP",
        )
        _make_trade(
            repo,
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            exit_price=90.0,
            exit_time=datetime(2024, 1, 2, 11, 0, tzinfo=timezone.utc),
            result=-1.0,
            result_type="SL",
        )
        svc = AnalyticsService(repo)
        stats = svc.calculate_statistics("MNQ")
        assert stats.total_trades == 2
        assert stats.winning_trades == 1
        assert stats.losing_trades == 1
        assert stats.win_rate == 0.5
        assert stats.total_pnl == 2.0
        assert stats.avg_pnl == 1.0
        assert stats.profit_factor == 3.0  # 3 / 1

    def test_pnl_usd_fallback(self):
        repo = FakeTradeRepository()
        _make_trade(
            repo,
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            risk_dollars=200.0,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
            result=3.0,
            pnl_usd=600.0,
        )
        svc = AnalyticsService(repo)
        stats = svc.calculate_statistics("MNQ")
        assert stats.total_pnl_usd == 600.0
        assert stats.avg_pnl_usd == 600.0

    def test_pnl_usd_legacy_fallback(self):
        repo = FakeTradeRepository()
        _make_trade(
            repo,
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            risk_dollars=200.0,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
            result=3.0,
            pnl_usd=None,
        )
        svc = AnalyticsService(repo)
        stats = svc.calculate_statistics("MNQ")
        # Fallback: result * risk_dollars = 3 * 200 = 600
        assert stats.total_pnl_usd == 600.0


class TestAnalyticsServiceEquityCurve:

    def test_empty(self):
        repo = FakeTradeRepository()
        svc = AnalyticsService(repo)
        curve = svc.get_equity_curve("MNQ")
        assert curve.labels == []
        assert curve.values == []

    def test_single_trade(self):
        repo = FakeTradeRepository()
        _make_trade(
            repo,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
            result=3.0,
        )
        svc = AnalyticsService(repo)
        curve = svc.get_equity_curve("MNQ")
        assert len(curve.values) == 1
        assert curve.values[0] == 3.0
        assert curve.labels == ["Jan 2024"]

    def test_multiple_trades_cumulative(self):
        repo = FakeTradeRepository()
        _make_trade(
            repo,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
            result=3.0,
        )
        _make_trade(
            repo,
            exit_price=90.0,
            exit_time=datetime(2024, 1, 2, 11, 0, tzinfo=timezone.utc),
            result=-1.0,
        )
        _make_trade(
            repo,
            exit_price=130.0,
            exit_time=datetime(2024, 2, 1, 11, 0, tzinfo=timezone.utc),
            result=3.0,
        )
        svc = AnalyticsService(repo)
        curve = svc.get_equity_curve("MNQ")
        assert curve.values == [3.0, 2.0, 5.0]
        assert curve.labels[0] == "Jan 2024"
        assert curve.labels[2] == "Feb 2024"


class TestAnalyticsServiceTradesByHour:

    def test_distribution(self):
        repo = FakeTradeRepository()
        _make_trade(repo, entry_time=datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc))
        _make_trade(repo, entry_time=datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc))
        _make_trade(repo, entry_time=datetime(2024, 1, 1, 14, 0, tzinfo=timezone.utc))
        svc = AnalyticsService(repo)
        dist = svc.get_trades_by_hour("MNQ")
        assert "09:00" in dist.labels
        assert "14:00" in dist.labels
        assert dist.values[dist.labels.index("09:00")] == 2
        assert dist.values[dist.labels.index("14:00")] == 1


class TestAnalyticsServiceTradesByDay:

    def test_distribution(self):
        repo = FakeTradeRepository()
        # Monday, Wednesday, Friday
        _make_trade(repo, entry_time=datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc))  # Mon
        _make_trade(repo, entry_time=datetime(2024, 1, 3, 9, 0, tzinfo=timezone.utc))  # Wed
        _make_trade(repo, entry_time=datetime(2024, 1, 5, 9, 0, tzinfo=timezone.utc))  # Fri
        svc = AnalyticsService(repo)
        dist = svc.get_trades_by_day("MNQ")
        assert dist.labels == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        assert dist.values == [1, 0, 1, 0, 1, 0, 0]


class TestAnalyticsServiceResultDistribution:

    def test_distribution(self):
        repo = FakeTradeRepository()
        _make_trade(repo, exit_price=130.0, exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc), result=3.0, result_type="TP")
        _make_trade(repo, exit_price=90.0, exit_time=datetime(2024, 1, 2, 11, 0, tzinfo=timezone.utc), result=-1.0, result_type="SL")
        _make_trade(repo, exit_price=95.0, exit_time=datetime(2024, 1, 3, 11, 0, tzinfo=timezone.utc), result=-0.5, result_type="SL")
        svc = AnalyticsService(repo)
        dist = svc.get_result_distribution("MNQ")
        assert "TP" in dist.labels
        assert "SL" in dist.labels
        assert dist.values[dist.labels.index("TP")] == 1
        assert dist.values[dist.labels.index("SL")] == 2


class TestAnalyticsServiceMonthlyPnL:

    def test_monthly_grouping(self):
        repo = FakeTradeRepository()
        _make_trade(repo, exit_price=130.0, exit_time=datetime(2024, 1, 15, 11, 0, tzinfo=timezone.utc), result=3.0)
        _make_trade(repo, exit_price=90.0, exit_time=datetime(2024, 1, 20, 11, 0, tzinfo=timezone.utc), result=-1.0)
        _make_trade(repo, exit_price=130.0, exit_time=datetime(2024, 2, 10, 11, 0, tzinfo=timezone.utc), result=3.0)
        svc = AnalyticsService(repo)
        monthly = svc.get_monthly_pnl("MNQ")
        assert "2024-01" in monthly.labels
        assert "2024-02" in monthly.labels
        assert monthly.values[monthly.labels.index("2024-01")] == 2.0
        assert monthly.values[monthly.labels.index("2024-02")] == 3.0


class TestAnalyticsServicePnLDistribution:

    def test_empty(self):
        repo = FakeTradeRepository()
        svc = AnalyticsService(repo)
        dist = svc.get_pnl_distribution("MNQ")
        assert dist.labels == []
        assert dist.values == []

    def test_single_value(self):
        repo = FakeTradeRepository()
        _make_trade(repo, exit_price=130.0, exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc), result=3.0)
        svc = AnalyticsService(repo)
        dist = svc.get_pnl_distribution("MNQ")
        assert len(dist.labels) == 1
        assert dist.values[0] == 1

    def test_binned_distribution(self):
        repo = FakeTradeRepository()
        for i in range(5):
            _make_trade(repo, exit_price=130.0, exit_time=datetime(2024, 1, i + 1, 11, 0, tzinfo=timezone.utc), result=float(i))
        svc = AnalyticsService(repo)
        dist = svc.get_pnl_distribution("MNQ", bins=2)
        assert len(dist.labels) == 2
        assert sum(dist.values) == 5


class TestAnalyticsServiceTradeDetail:

    def test_get_trade_detail(self):
        repo = FakeTradeRepository()
        trade = _make_trade(
            repo,
            entry_price=100.0,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
            result=3.0,
            result_type="TP",
        )
        svc = AnalyticsService(repo)
        detail = svc.get_trade_detail(trade.trade_id)
        assert detail is not None
        assert detail.trade_id == trade.trade_id
        assert detail.status == "closed"
        assert detail.result == 3.0
        assert detail.result_type == "TP"

    def test_get_trade_detail_open_trade(self):
        repo = FakeTradeRepository()
        trade = _make_trade(repo)
        svc = AnalyticsService(repo)
        detail = svc.get_trade_detail(trade.trade_id)
        assert detail is not None
        assert detail.status == "open"
        assert detail.exit_price is None
        assert detail.exit_time is None

    def test_get_trade_detail_not_found(self):
        repo = FakeTradeRepository()
        svc = AnalyticsService(repo)
        assert svc.get_trade_detail("NONEXISTENT") is None


class TestAnalyticsServicePaginatedTrades:

    def test_pagination(self):
        repo = FakeTradeRepository()
        for i in range(5):
            _make_trade(repo, entry_time=datetime(2024, 1, i + 1, 10, 0, tzinfo=timezone.utc))
        svc = AnalyticsService(repo)
        page = svc.get_paginated_trades("MNQ", limit=2, offset=0)
        assert len(page["trades"]) == 2
        assert page["total"] == 5
        assert page["limit"] == 2
        assert page["offset"] == 0

    def test_pagination_offset(self):
        repo = FakeTradeRepository()
        for i in range(5):
            _make_trade(repo, entry_time=datetime(2024, 1, i + 1, 10, 0, tzinfo=timezone.utc))
        svc = AnalyticsService(repo)
        page = svc.get_paginated_trades("MNQ", limit=2, offset=4)
        assert len(page["trades"]) == 1
        assert page["total"] == 5

    def test_pagination_empty(self):
        repo = FakeTradeRepository()
        svc = AnalyticsService(repo)
        page = svc.get_paginated_trades("MNQ")
        assert page["trades"] == []
        assert page["total"] == 0

    def test_shows_all_independent_trades_and_account_column(self):
        repo = FakeTradeRepository()
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            trade_id="T1",
            account="Sim101",
        )
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime(2024, 1, 1, 10, 5, tzinfo=timezone.utc),
            trade_id="T2",
            account="Sim102",
        )
        svc = AnalyticsService(repo)
        page = svc.get_paginated_trades("MNQ")

        assert page["total"] == 2
        assert len(page["trades"]) == 2
        accounts = {t["account"] for t in page["trades"]}
        assert accounts == {"Sim101", "Sim102"}

    def test_shows_single_account_trade_when_no_children(self):
        repo = FakeTradeRepository()
        repo.insert_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk=10.0,
            entry_time=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            trade_id="single_1",
        )
        svc = AnalyticsService(repo)
        page = svc.get_paginated_trades("MNQ")

        assert page["total"] == 1
        assert page["trades"][0]["trade_id"] == "single_1"
        assert page["trades"][0]["account"] is None

    def test_negative_limit_is_clamped(self):
        repo = FakeTradeRepository()
        _make_trade(repo, entry_price=100.0, stop_loss=90.0, take_profit=130.0)
        svc = AnalyticsService(repo)
        page = svc.get_paginated_trades("MNQ", limit=-5, offset=-10)
        assert page["limit"] == 1
        assert page["offset"] == 0
        assert len(page["trades"]) == 1


class TestAnalyticsDrawdownAndStreaks:
    def test_all_losing_curve_reports_non_zero_drawdown_pct(self):
        repo = FakeTradeRepository()
        for i in range(3):
            _make_trade(
                repo,
                entry_price=100.0,
                stop_loss=90.0,
                take_profit=130.0,
                risk=10.0,
                exit_price=90.0,
                exit_time=datetime(2024, 1, 1, 11 + i, 0, tzinfo=timezone.utc),
                result=-1.0,
                result_type="SL",
            )
        svc = AnalyticsService(repo)
        stats = svc.calculate_statistics("MNQ")
        assert stats.max_drawdown == 3.0
        assert stats.max_drawdown_pct == 100.0

    def test_breakeven_trade_breaks_streak(self):
        repo = FakeTradeRepository()
        # win, BE, win -> max win streak 1, current streak 1 win
        for i, (result, rt) in enumerate([(1.0, "TP"), (0.0, "BE"), (1.5, "TP")]):
            _make_trade(
                repo,
                entry_price=100.0,
                stop_loss=90.0,
                take_profit=130.0,
                risk=10.0,
                exit_price=100.0 if result == 0 else 130.0,
                exit_time=datetime(2024, 1, 1, 11 + i, 0, tzinfo=timezone.utc),
                result=result,
                result_type=rt,
            )
        svc = AnalyticsService(repo)
        stats = svc.calculate_statistics("MNQ")
        assert stats.max_consecutive_wins == 1
        assert stats.current_streak == 1
        assert stats.current_streak_type == "win"


class TestAnalyticsResultDistribution:
    def test_result_distribution_labels_are_deterministic(self):
        repo = FakeTradeRepository()
        _make_trade(
            repo,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
            result=1.0,
            result_type="TP",
        )
        _make_trade(
            repo,
            exit_price=90.0,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            result=-1.0,
            result_type="SL",
        )
        svc = AnalyticsService(repo)
        dist = svc.get_result_distribution("MNQ")
        assert dist.labels == ["TP", "SL"]


class TestAnalyticsNaNAndInf:
    def test_trade_statistics_sanitizes_nan_and_inf(self):
        from src.analytics import TradeStatistics

        stats = TradeStatistics(
            total_trades=1,
            open_trades=0,
            winning_trades=1,
            losing_trades=0,
            win_rate=float("nan"),
            total_pnl=float("inf"),
            total_pnl_usd=0.0,
            avg_pnl=float("-inf"),
            avg_pnl_usd=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            profit_factor=float("inf"),
            avg_r_multiple=0.0,
        )
        d = stats.to_dict()
        assert d["win_rate"] is None
        assert d["total_pnl"] is None
        assert d["avg_pnl"] is None
        assert d["profit_factor"] is None


class TestAccountAnalytics:
    def test_account_analytics_returns_stats_per_account(self):
        repo = FakeTradeRepository()
        _make_trade(
            repo,
            exit_price=130.0,
            exit_time=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
            result=1.0,
            result_type="TP",
            pnl_usd=100.0,
            account="A1",
        )
        _make_trade(
            repo,
            exit_price=90.0,
            exit_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            result=-1.0,
            result_type="SL",
            pnl_usd=-50.0,
            account="A2",
        )
        svc = AnalyticsService(repo)
        accounts = svc.get_account_analytics("MNQ")
        assert len(accounts) == 2
        totals = {a["account"]: a["total_pnl_usd"] for a in accounts}
        assert totals["A1"] == 100.0
        assert totals["A2"] == -50.0
