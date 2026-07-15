"""Tests for src.strategies.factory."""

from unittest.mock import MagicMock

from src.strategies.factory import create_liquidity_strategy_v2
from src.strategies.liquidity_v2.base_strategy import StrategyOptions
from src.strategies.liquidity_v2.config import CandleConfig, StrategyNumbers
from src.strategies.liquidity_v2.constants import DEFAULT_STRATEGY_OPTIONS
from src.strategies.liquidity_v2.strategy import LiquidityStrategyV2
from tests.fakes import FakeLineRepository, FakeLogger, FakeTradeRepository, FakeTradeExecutor


class MockRepos:
    def __init__(self):
        self.lines = FakeLineRepository()
        self.trades = FakeTradeRepository()
        self.trigger_state = MagicMock()
        self.decision_logs = MagicMock()


def _make_trade_manager(trade_repo, socketio):
    from src.services.trade_manager import TradeManager
    return TradeManager(
        trade_repository=trade_repo,
        socketio=socketio,
        trade_executor=FakeTradeExecutor(),
        point_value=2.0,
        account_balance=100000.0,
        logger=FakeLogger(),
    )


class TestCreateLiquidityStrategyV2:

    def test_returns_liquidity_strategy_v2(self):
        repos = MockRepos()
        tm = _make_trade_manager(repos.trades, MagicMock())
        numbers = StrategyNumbers(min_stop_loss=10.0, max_bounce=5.0, extra_sl_space=0.0)
        strategy = create_liquidity_strategy_v2(
            numbers=numbers,
            event_publisher=MagicMock(),
            repos=repos,
            trade_manager=tm,
            options=DEFAULT_STRATEGY_OPTIONS,
        )
        assert isinstance(strategy, LiquidityStrategyV2)

    def test_forwards_numbers_to_strategy(self):
        repos = MockRepos()
        tm = _make_trade_manager(repos.trades, MagicMock())
        numbers = StrategyNumbers(
            min_stop_loss=12.0,
            max_bounce=8.0,
            extra_sl_space=2.0,
            fixed_stop_loss=25.0,
            max_stop_loss=60.0,
            sl_levels=[15.0, 30.0],
            rr_ratio=3.3,
            point_value=5.0,
            account_balance=75000.0,
            risk_per_trade=150.0,
            risk_pct_per_trade=1.5,
        )
        strategy = create_liquidity_strategy_v2(
            numbers=numbers,
            event_publisher=MagicMock(),
            repos=repos,
            trade_manager=tm,
            options=DEFAULT_STRATEGY_OPTIONS,
            use_fractional_lots=True,
            fee_per_rt=2.88,
            broker_spread=0.25,
            live_mode=True,
        )
        assert strategy.min_stop_loss == 12.0
        assert strategy.max_bounce == 8.0
        assert strategy.extra_sl_space == 2.0
        assert strategy.fixed_stop_loss == 25.0
        assert strategy.max_stop_loss == 60.0
        assert strategy.sl_levels == [15.0, 30.0]
        assert strategy.rr_ratio == 3.3
        assert strategy.point_value == 5.0
        assert strategy.account_balance == 75000.0
        assert strategy.risk_per_trade == 150.0
        assert strategy.risk_pct_per_trade == 1.5
        assert strategy.use_fractional_lots is True
        assert strategy.fee_per_rt == 2.88
        assert strategy.broker_spread == 0.25

    def test_forwards_options_timeframes_candle_config(self):
        repos = MockRepos()
        tm = _make_trade_manager(repos.trades, MagicMock())
        numbers = StrategyNumbers(min_stop_loss=10.0, max_bounce=5.0, extra_sl_space=0.0)
        options = DEFAULT_STRATEGY_OPTIONS
        candle_config = CandleConfig()
        strategy = create_liquidity_strategy_v2(
            numbers=numbers,
            event_publisher=MagicMock(),
            repos=repos,
            trade_manager=tm,
            options=options,
            timeframes=["1m", "5m"],
            candle_config=candle_config,
            trade_logger=MagicMock(),
            analytics=MagicMock(),
            logger=FakeLogger(),
        )
        assert strategy.options is options
        assert strategy.timeframes == ["1m", "5m"]
        assert strategy.candle_config is candle_config

    def test_repositories_are_wired(self):
        repos = MockRepos()
        tm = _make_trade_manager(repos.trades, MagicMock())
        numbers = StrategyNumbers(min_stop_loss=10.0, max_bounce=5.0, extra_sl_space=0.0)
        strategy = create_liquidity_strategy_v2(
            numbers=numbers,
            event_publisher=MagicMock(),
            repos=repos,
            trade_manager=tm,
            options=DEFAULT_STRATEGY_OPTIONS,
        )
        assert strategy.line_repository is repos.lines
        assert strategy.trade_repository is repos.trades
        assert strategy.trigger_state_repo is repos.trigger_state
        assert strategy.decision_log_repository is repos.decision_logs

    def test_accounts_repo_forwarded(self):
        repos = MockRepos()
        tm = _make_trade_manager(repos.trades, MagicMock())
        numbers = StrategyNumbers(min_stop_loss=10.0, max_bounce=5.0, extra_sl_space=0.0)
        accounts_repo = MagicMock()
        strategy = create_liquidity_strategy_v2(
            numbers=numbers,
            event_publisher=MagicMock(),
            repos=repos,
            trade_manager=tm,
            options=DEFAULT_STRATEGY_OPTIONS,
            accounts_repo=accounts_repo,
        )
        assert strategy._accounts_repo is accounts_repo
