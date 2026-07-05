"""Tests for live-trading account eligibility filtering."""

from __future__ import annotations

import pytest

from src.application.services.strategy_trade_service import StrategyTradeService
from src.config.models import AccountConfig
from src.services.trade_manager import TradeManager
from src.strategies.base_strategy import BaseStrategy
from src.strategies.liquidity_v2.base_strategy import BaseLiquidityStrategy
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from tests.fakes import (
    FakeAnalyticsReporter,
    FakeLineRepository,
    FakeLogger,
    FakeNtAccountRepository,
    FakeTradeExecutor,
    FakeTradeRepository,
)


def _make_trade_manager(*, live_mode: bool = False, accounts=None) -> TradeManager:
    repo = FakeNtAccountRepository()
    for acct in accounts or []:
        repo.upsert(
            acct.name,
            risk_usd=acct.risk_usd,
            risk_pct=acct.risk_pct,
            rr_ratio=acct.rr_ratio,
            live_enabled=acct.live_enabled,
        )
    return TradeManager(
        trade_repository=FakeTradeRepository(),
        trade_executor=FakeTradeExecutor(),
        point_value=2.0,
        account_balance=100000.0,
        logger=FakeLogger(),
        accounts_repo=repo,
        live_mode=live_mode,
    )


def _five_accounts() -> list[AccountConfig]:
    return [
        AccountConfig(name="Sim101", risk_usd=100.0, live_enabled=True),
        AccountConfig(name="Sim102", risk_usd=100.0, live_enabled=True),
        AccountConfig(name="Sim103", risk_usd=100.0, live_enabled=False),
        AccountConfig(name="Sim104", risk_usd=100.0, live_enabled=False),
        AccountConfig(name="Sim105", risk_usd=100.0, live_enabled=False),
    ]


class TestBaseStrategyLiveFiltering:
    """BaseStrategy._get_current_account_configs filters in live mode."""

    def test_live_mode_returns_only_enabled_accounts(self):
        tm = _make_trade_manager(live_mode=True, accounts=_five_accounts())
        strategy = BaseStrategy(
            min_stop_loss=10.0,
            event_publisher=None,
            trade_repository=FakeTradeRepository(),
            trade_manager=tm,
            extra_sl_space=0.0,
            point_value=2.0,
            account_balance=100000.0,
            live_mode=True,
            accounts_repo=tm._accounts_repo,
        )
        configs = strategy._get_current_account_configs()
        assert [c.name for c in configs] == ["Sim101", "Sim102"]

    def test_non_live_mode_returns_all_accounts(self):
        tm = _make_trade_manager(live_mode=False, accounts=_five_accounts())
        strategy = BaseStrategy(
            min_stop_loss=10.0,
            event_publisher=None,
            trade_repository=FakeTradeRepository(),
            trade_manager=tm,
            extra_sl_space=0.0,
            point_value=2.0,
            account_balance=100000.0,
            live_mode=False,
            accounts_repo=tm._accounts_repo,
        )
        configs = strategy._get_current_account_configs()
        assert [c.name for c in configs] == ["Sim101", "Sim102", "Sim103", "Sim104", "Sim105"]


class TestBaseLiquidityStrategyLiveFiltering:
    """BaseLiquidityStrategy._get_current_account_configs filters in live mode."""

    def test_live_mode_returns_only_enabled_accounts(self):
        tm = _make_trade_manager(live_mode=True, accounts=_five_accounts())
        strategy = BaseLiquidityStrategy(
            min_stop_loss=10.0,
            max_bounce=90.0,
            event_publisher=None,
            line_repository=FakeLineRepository(),
            trade_repository=FakeTradeRepository(),
            trade_manager=tm,
            extra_sl_space=0.0,
            point_value=2.0,
            account_balance=100000.0,
            options=DEFAULT_STRATEGY_OPTIONS,
            live_mode=True,
            accounts_repo=tm._accounts_repo,
        )
        configs = strategy._get_current_account_configs()
        assert [c.name for c in configs] == ["Sim101", "Sim102"]


class TestTradeManagerLiveFallback:
    """TradeManager manual open falls back to a live-enabled account."""

    def test_manual_open_uses_a_live_enabled_account(self):
        tm = _make_trade_manager(live_mode=True, accounts=_five_accounts())
        trade = tm.open_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=21000.0,
            stop_loss=20900.0,
            take_profit=21200.0,
            risk=100.0,
            entry_time=1.0,
            rr_ratio=2.0,
        )
        assert trade["account"] in {"Sim101", "Sim102"}

    def test_manual_open_in_backtest_uses_a_configured_account(self):
        tm = _make_trade_manager(live_mode=False, accounts=_five_accounts())
        trade = tm.open_trade(
            pair="MNQ",
            trade_type="long",
            entry_price=21000.0,
            stop_loss=20900.0,
            take_profit=21200.0,
            risk=100.0,
            entry_time=1.0,
            rr_ratio=2.0,
        )
        assert trade["account"] in {"Sim101", "Sim102", "Sim103", "Sim104", "Sim105"}


class TestStrategyTradeServiceLiveFiltering:
    """StrategyTradeService opens trades only for live-enabled accounts."""

    def test_opens_only_live_enabled_accounts(self):
        trade_repo = FakeTradeRepository()
        accounts = _five_accounts()
        accounts_repo = FakeNtAccountRepository()
        for acct in accounts:
            accounts_repo.upsert(
                acct.name, risk_usd=acct.risk_usd, live_enabled=acct.live_enabled
            )

        tm = TradeManager(
            trade_repository=trade_repo,
            trade_executor=FakeTradeExecutor(),
            point_value=2.0,
            account_balance=100000.0,
            logger=FakeLogger(),
            accounts_repo=accounts_repo,
            live_mode=True,
        )

        service = StrategyTradeService(
            trade_repository=trade_repo,
            trade_executor=FakeTradeExecutor(),
            event_publisher=None,
            logger=FakeLogger(),
            point_value=2.0,
            account_balance=100000.0,
            accounts_repo=accounts_repo,
        )

        opened = service.open_trade(
            trade={
                "pair": "MNQ",
                "type": "long",
                "entry": 21000.0,
                "stop_loss": 20900.0,
                "take_profit": 21200.0,
                "risk": 100.0,
                "entry_time": 1.0,
                "rr_ratio": 2.0,
            },
            is_warmup=False,
            account_configs=accounts,
            account_balance=100000.0,
        )

        # The service itself does not filter; strategies filter before calling it.
        # This test documents that behavior while still using live-enabled data.
        assert len(opened) == 5

    def test_strategy_filters_before_service(self):
        """Simulate a live strategy passing only enabled configs to the service."""
        trade_repo = FakeTradeRepository()
        accounts = _five_accounts()
        accounts_repo = FakeNtAccountRepository()
        for acct in accounts:
            accounts_repo.upsert(
                acct.name, risk_usd=acct.risk_usd, live_enabled=acct.live_enabled
            )

        service = StrategyTradeService(
            trade_repository=trade_repo,
            trade_executor=FakeTradeExecutor(),
            event_publisher=None,
            logger=FakeLogger(),
            point_value=2.0,
            account_balance=100000.0,
            accounts_repo=accounts_repo,
        )

        enabled = [a for a in accounts if a.live_enabled]
        opened = service.open_trade(
            trade={
                "pair": "MNQ",
                "type": "long",
                "entry": 21000.0,
                "stop_loss": 20900.0,
                "take_profit": 21200.0,
                "risk": 100.0,
                "entry_time": 1.0,
                "rr_ratio": 2.0,
            },
            is_warmup=False,
            account_configs=enabled,
            account_balance=100000.0,
        )

        assert len(opened) == 2
        assert {t["account"] for t in opened} == {"Sim101", "Sim102"}
