"""Integration tests for StrategyTradeService.

These tests guard against regressions where contract sizing stops following
changes in account balance (the root cause of the post-readiness PnL collapse).
"""

from src.application.services.strategy_trade_service import StrategyTradeService
from tests.fakes import (
    DummySocketIO,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
)


def _make_service(account_balance: float = 100000.0, risk_pct: float = 1.0):
    return StrategyTradeService(
        trade_repository=FakeTradeRepository(),
        trade_executor=FakeTradeExecutor(),
        event_publisher=DummySocketIO(),
        logger=FakeLogger(),
        point_value=2.0,
        account_balance=account_balance,
        risk_pct_per_trade=risk_pct,
    )


def _sample_trade():
    return {
        "pair": "MNQ",
        "type": "long",
        "entry": 30000.0,
        "stop_loss": 29980.0,
        "take_profit": 30100.0,
        "risk": 20.0,
        "risk_dollars": None,
        "risk_pct": None,
        "contracts": None,
        "entry_time": 1000.0,
        "rr_ratio": 5.0,
    }


class TestStrategyTradeServiceClose:
    def test_close_trade_persists_computed_result(self):
        """close_trade must persist the R-multiple returned by calculate_close_metrics."""
        service = _make_service(account_balance=100000.0)
        trade = {
            "trade_id": "T1",
            "pair": "MNQ",
            "type": "long",
            "entry": 30000.0,
            "stop_loss": 29980.0,
            "take_profit": 30100.0,
            "risk": 20.0,
            "contracts": 25,
            "exit_price": 30100.0,
            "exit_time": 2000.0,
        }
        service.close_trade(trade)
        closed = service._repo.closed[0]
        assert closed["result"] == 5.0
        assert closed["result_type"] == "TP"
        assert closed["pnl_usd"] > 0


class TestStrategyTradeServiceBalanceSizing:
    def test_open_trade_sizes_from_provided_balance(self):
        service = _make_service(account_balance=100000.0, risk_pct=1.0)
        opened = service.open_trade(
            trade=_sample_trade(),
            is_warmup=False,
            account_configs=[],
            account_balance=100000.0,
        )
        assert len(opened) == 1
        first = opened[0]
        # 1% of 100k = 1000 risk budget / (20 pts * $2/pt) = 25 contracts
        assert first["contracts"] == 25
        assert first["risk_dollars"] == 1000.0

    def test_open_trade_uses_doubled_account_balance(self):
        service = _make_service(account_balance=100000.0, risk_pct=1.0)
        opened = service.open_trade(
            trade=_sample_trade(),
            is_warmup=False,
            account_configs=[],
            account_balance=200000.0,
        )
        assert len(opened) == 1
        trade = opened[0]
        # 1% of 200k = 2000 risk budget / (20 pts * $2/pt) = 50 contracts
        assert trade["contracts"] == 50
        assert trade["risk_dollars"] == 2000.0

    def test_subsequent_opens_follow_balance_changes(self):
        service = _make_service(account_balance=100000.0, risk_pct=1.0)

        first = service.open_trade(
            trade=_sample_trade(),
            is_warmup=False,
            account_configs=[],
            account_balance=100000.0,
        )[0]

        second = service.open_trade(
            trade=_sample_trade(),
            is_warmup=False,
            account_configs=[],
            account_balance=200000.0,
        )[0]

        assert second["contracts"] == first["contracts"] * 2
        assert second["risk_dollars"] == first["risk_dollars"] * 2


class TestStrategyTradeServiceTakeProfit:
    def test_open_trade_preserves_strategy_take_profit(self):
        """A strategy-computed TP decoupled from the SL distance (e.g. via
        sl_buffer_pts) must survive open_trade — it must NOT be recomputed
        as rr * risk."""
        service = _make_service(account_balance=100000.0)
        trade = _sample_trade()
        # risk=20 but TP distance is only 60 (3R), not 5*20=100
        trade["take_profit"] = 30060.0
        opened = service.open_trade(
            trade=trade,
            is_warmup=False,
            account_configs=[],
            account_balance=100000.0,
        )
        assert opened[0]["take_profit"] == 30060.0

    def test_account_rr_override_recomputes_take_profit_from_risk(self):
        """A per-account RR override yields TP = entry ± rr_override x SL distance."""
        from src.config.models import AccountConfig
        service = _make_service(account_balance=100000.0)
        acct = AccountConfig(name="A1", rr_ratio=2.5)
        opened = service.open_trade(
            trade=_sample_trade(),  # risk 20 pts
            is_warmup=False,
            account_configs=[acct],
            account_balance=100000.0,
        )
        # 2.5 * 20 = 50 -> 30000 + 50
        assert opened[0]["take_profit"] == 30050.0
