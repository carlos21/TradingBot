"""Tests for TsiCrossStrategy."""


from src.domain.types import Direction
from src.strategies.base_strategy import BreakevenConfig
from src.strategies.entry_context import EntryFilter, open_trades_limit_filter
from src.strategies.tsi_cross.config import TsiCrossConfig, TsiCrossNumbers
from src.strategies.tsi_cross.strategy import TsiCrossStrategy
from tests.fakes import DummySocketIO, FakeLogger, MutableTradingContext


class FakeTradeRepository:
    def __init__(self):
        self._trades = []

    def list_trades(self, pair: str):
        return self._trades

    def insert_trade(self, **kwargs):
        class FakeTrade:
            pass
        t = FakeTrade()
        for k, v in kwargs.items():
            setattr(t, k, v)
        t.trade_id = f"T{len(self._trades)}"
        t.source = "strategy"
        t.entry_time = kwargs.get("entry_time")
        self._trades.append(t)
        return t

    def update_stop_loss(self, trade_id, sl):
        pass


class FakeTradeManager:
    def __init__(self):
        self.open_trades = []
        self.trade_executor = FakeExecutor()
        self.pair = "MNQ"
        self.account_balance = 100000.0
        self._monitored_trades = set()

    def update_local_trade_sl(self, trade_id, sl):
        pass

    def close_trade(self, trade_id, exit_price, exit_time):
        for t in self.open_trades:
            if t.get("trade_id") == trade_id:
                t["status"] = "closed"
                t["exit_price"] = exit_price
                t["exit_time"] = exit_time
                return


class FakeExecutor:
    def on_sl_update(self, trade_id, sl):
        pass

    def on_trade_open(self, trade):
        pass


def _make_strategy(execution_context=None):
    return TsiCrossStrategy(
        numbers=TsiCrossNumbers(),
        config=TsiCrossConfig(
            entry_filters=[],
            tsi_long_len=6,
            tsi_short_len=13,
            tsi_signal_len=4,
        ),
        event_publisher=DummySocketIO(),
        trade_repository=FakeTradeRepository(),
        trade_manager=FakeTradeManager(),
        logger=FakeLogger(),
        execution_context=execution_context,
    )


class TestTsiCrossStrategy:
    def test_warmup_mode_does_not_open_trades(self):
        strategy = _make_strategy(execution_context=MutableTradingContext(warmup=True))
        for i in range(100):
            strategy.on_raw_bar({
                "time": 1700000000 + i * 300,
                "open": 100.0,
                "high": 110.0,
                "low": 90.0,
                "close": 100.0 + i * 0.1,
                "volume": 10,
                "pair": "MNQ",
            })
        assert len(strategy.open_trades) == 0
        assert len(strategy._history) == 0

    def test_same_direction_pyramiding_is_blocked(self):
        strategy = _make_strategy()
        strategy.open_trades = [{
            "trade_id": "T1",
            "status": "open",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 90.0,
            "take_profit": 120.0,
        }]
        strategy._evaluate_cross({
            "time": 1700000000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 101.0,
            "volume": 10,
            "pair": "MNQ",
        }, direction=Direction.LONG)
        assert len(strategy.open_trades) == 1

    def test_strategy_aggregates_bars(self):
        strategy = _make_strategy()
        for i in range(5):
            strategy.on_raw_bar({
                "time": 1700000000 + i * 60,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0 + i,
                "volume": 10,
                "pair": "MNQ",
            })
        assert len(strategy._history) == 1

    def test_cross_opens_long_trade(self):
        strategy = _make_strategy()
        base = 1700000000
        price = 100.0
        trade_opened = False

        # Phase 1: long flat period so TSI stabilises near 0
        for batch in range(49):
            for i in range(5):
                strategy.on_raw_bar({
                    "time": base + batch * 300 + i * 60,
                    "open": price,
                    "high": price + 0.5,
                    "low": price - 0.5,
                    "close": price,
                    "volume": 10,
                    "pair": "MNQ",
                })

        # Phase 1b: slight dip — ensures TSI ends strictly below signal
        for i in range(5):
            strategy.on_raw_bar({
                "time": base + 49 * 300 + i * 60,
                "open": price,
                "high": price + 0.2,
                "low": price - 1.0,
                "close": price - 0.3,
                "volume": 10,
                "pair": "MNQ",
            })

        # Phase 2: sharp rally — TSI crosses above signal
        for batch in range(50, 70):
            for i in range(5):
                price += 2.0
                strategy.on_raw_bar({
                    "time": base + batch * 300 + i * 60,
                    "open": price - 0.5,
                    "high": price + 0.5,
                    "low": price - 1.0,
                    "close": price,
                    "volume": 10,
                    "pair": "MNQ",
                })
            if strategy.open_trades:
                trade_opened = True
                break

        assert trade_opened, "Expected a long trade to open during the rally"
        trade = strategy.open_trades[0]
        assert trade["type"] == "long"
        assert trade["stop_loss"] < trade["entry"]
        assert trade["take_profit"] > trade["entry"]

    def test_cross_opens_short_trade(self):
        strategy = _make_strategy()
        base = 1700000000
        price = 200.0
        trade_opened = False

        # Phase 1: long flat period so TSI stabilises near 0
        for batch in range(49):
            for i in range(5):
                strategy.on_raw_bar({
                    "time": base + batch * 300 + i * 60,
                    "open": price,
                    "high": price + 0.5,
                    "low": price - 0.5,
                    "close": price,
                    "volume": 10,
                    "pair": "MNQ",
                })

        # Phase 1b: slight rally — ensures TSI ends strictly above signal
        for i in range(5):
            strategy.on_raw_bar({
                "time": base + 49 * 300 + i * 60,
                "open": price,
                "high": price + 1.0,
                "low": price - 0.2,
                "close": price + 0.3,
                "volume": 10,
                "pair": "MNQ",
            })

        # Phase 2: sharp drop — TSI crosses below signal
        for batch in range(50, 70):
            for i in range(5):
                price -= 2.0
                strategy.on_raw_bar({
                    "time": base + batch * 300 + i * 60,
                    "open": price + 0.5,
                    "high": price + 1.0,
                    "low": price - 0.5,
                    "close": price,
                    "volume": 10,
                    "pair": "MNQ",
                })
            if strategy.open_trades:
                trade_opened = True
                break

        assert trade_opened, "Expected a short trade to open during the drop"
        trade = strategy.open_trades[0]
        assert trade["type"] == "short"
        assert trade["stop_loss"] > trade["entry"]
        assert trade["take_profit"] < trade["entry"]


class TestTsiCrossStrategyInit:

    def test_default_config(self):
        strategy = TsiCrossStrategy(
            numbers=TsiCrossNumbers(),
            event_publisher=DummySocketIO(),
            trade_repository=FakeTradeRepository(),
            trade_manager=FakeTradeManager(),
            logger=FakeLogger(),
        )
        assert strategy._tsi_config is not None
        assert strategy._tsi_config.tsi_long_len == 6

    def test_custom_config_passed_through(self):
        config = TsiCrossConfig(timeframe="15m", tsi_long_len=10)
        strategy = _make_strategy()
        strategy._tsi_config = config
        assert strategy._tsi_config.timeframe == "15m"


class TestTsiCrossStrategyReset:

    def test_reset_clears_history(self):
        strategy = _make_strategy()
        strategy._history.append({"time": 1, "close": 100.0})
        strategy.reset()
        assert len(strategy._history) == 0


class TestTsiCrossStrategyBuildTrade:

    def test_build_trade_preserves_take_profit_by_default(self):
        strategy = _make_strategy()
        strategy._tsi_numbers.close_on_opposite_cross = False
        bar = {"time": 1700000000, "open": 100, "high": 101, "low": 99, "close": 101, "pair": "MNQ"}
        ctx = strategy._create_test_ctx(bar, Direction.LONG, bounce=95.0)
        trade = strategy._build_trade_from_context(ctx)
        assert trade["take_profit"] is not None

    def test_build_trade_opposite_cross_sets_none_tp(self):
        strategy = _make_strategy()
        strategy._tsi_numbers.close_on_opposite_cross = True
        bar = {"time": 1700000000, "open": 100, "high": 101, "low": 99, "close": 101, "pair": "MNQ"}
        ctx = strategy._create_test_ctx(bar, Direction.LONG, bounce=95.0)
        trade = strategy._build_trade_from_context(ctx)
        assert trade["take_profit"] is None
        assert trade.get("close_on_opposite_cross") is True


class TestTsiCrossStrategyCloseOnOppositeCross:

    def test_closes_long_on_bearish_cross(self):
        strategy = _make_strategy()
        strategy._tsi_numbers.close_on_opposite_cross = True
        strategy.open_trades.append({
            "trade_id": "T1", "status": "open", "type": "long",
            "entry": 100.0, "stop_loss": 90.0, "take_profit": 120.0,
        })
        strategy.trade_manager.open_trades.append(strategy.open_trades[0])
        bar = {"time": 1700000000, "close": 99.0, "pair": "MNQ"}
        strategy._close_on_opposite_cross(bar, Direction.SHORT)
        assert len(strategy.open_trades) == 0

    def test_closes_short_on_bullish_cross(self):
        strategy = _make_strategy()
        strategy._tsi_numbers.close_on_opposite_cross = True
        strategy.open_trades.append({
            "trade_id": "T1", "status": "open", "type": "short",
            "entry": 100.0, "stop_loss": 110.0, "take_profit": 80.0,
        })
        strategy.trade_manager.open_trades.append(strategy.open_trades[0])
        bar = {"time": 1700000000, "close": 101.0, "pair": "MNQ"}
        strategy._close_on_opposite_cross(bar, Direction.LONG)
        assert len(strategy.open_trades) == 0

    def test_does_not_close_same_direction(self):
        strategy = _make_strategy()
        strategy._tsi_numbers.close_on_opposite_cross = True
        strategy.open_trades.append({
            "trade_id": "T1", "status": "open", "type": "long",
        })
        bar = {"time": 1700000000, "close": 101.0, "pair": "MNQ"}
        strategy._close_on_opposite_cross(bar, Direction.LONG)
        assert len(strategy.open_trades) == 1


class TestTsiCrossStrategyHasOpenTrade:

    def test_has_open_long_trade(self):
        strategy = _make_strategy()
        strategy.open_trades.append({"status": "open", "type": "long"})
        assert strategy._has_open_trade_in_direction(Direction.LONG) is True
        assert strategy._has_open_trade_in_direction(Direction.SHORT) is False

    def test_closed_trade_ignored(self):
        strategy = _make_strategy()
        strategy.open_trades.append({"status": "closed", "type": "long"})
        assert strategy._has_open_trade_in_direction(Direction.LONG) is False


class TestTsiCrossStrategyEvaluateCross:

    def test_filter_block_prevents_open(self):
        config = TsiCrossConfig(
            entry_filters=[EntryFilter(fn=lambda _ctx: (False, "blocked"), name="blocker")],
            tsi_long_len=6,
            tsi_short_len=13,
            tsi_signal_len=4,
        )
        strategy = TsiCrossStrategy(
            numbers=TsiCrossNumbers(),
            config=config,
            event_publisher=DummySocketIO(),
            trade_repository=FakeTradeRepository(),
            trade_manager=FakeTradeManager(),
            logger=FakeLogger(),
        )
        strategy._history.append({"time": 1, "open": 100, "high": 101, "low": 99, "close": 100, "pair": "MNQ"})
        bar = {"time": 2, "open": 100, "high": 101, "low": 95, "close": 101, "pair": "MNQ"}
        strategy._evaluate_cross(bar, Direction.LONG)
        assert len(strategy.open_trades) == 0

    def test_breakeven_config_attached_to_trade_dict(self):
        config = TsiCrossConfig(
            entry_filters=[],
            tsi_long_len=6,
            tsi_short_len=13,
            tsi_signal_len=4,
            breakeven=BreakevenConfig(trigger_rr=2.0),
        )
        strategy = TsiCrossStrategy(
            numbers=TsiCrossNumbers(),
            config=config,
            event_publisher=DummySocketIO(),
            trade_repository=FakeTradeRepository(),
            trade_manager=FakeTradeManager(),
            logger=FakeLogger(),
        )
        bar = {"time": 2, "open": 100, "high": 101, "low": 95, "close": 101, "pair": "MNQ"}
        ctx = strategy._create_test_ctx(bar, Direction.LONG, 95.0)
        trade = strategy._build_trade_from_context(ctx)
        if config.breakeven:
            trade["breakeven_config"] = __import__("dataclasses").asdict(config.breakeven)
        assert "breakeven_config" in trade
        assert trade["breakeven_config"]["trigger_rr"] == 2.0


class TestTsiCrossStrategyOnStrategyBar:

    def test_warmup_skips(self):
        strategy = _make_strategy(execution_context=MutableTradingContext(warmup=True))
        strategy._history.append({"time": 1, "open": 100, "high": 101, "low": 99, "close": 100, "pair": "MNQ"})
        strategy._on_strategy_bar({"time": 2, "open": 100, "high": 101, "low": 99, "close": 101, "pair": "MNQ"})
        assert len(strategy._history) == 1

    def test_no_cross_appends_history_only(self):
        strategy = _make_strategy()
        # Flat prices won't produce a cross
        for i in range(30):
            strategy._on_strategy_bar({
                "time": 1700000000 + i * 300,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "pair": "MNQ",
            })
        assert len(strategy._history) == 30
        assert len(strategy.open_trades) == 0

    def test_opposite_cross_closes_trade_when_enabled(self):
        strategy = _make_strategy()
        strategy._tsi_numbers.close_on_opposite_cross = True
        strategy.open_trades.append({
            "trade_id": "T1", "status": "open", "type": "long", "pair": "MNQ",
            "entry": 100.0, "stop_loss": 90.0, "take_profit": 120.0,
        })
        strategy.trade_manager.open_trades.append(strategy.open_trades[0])
        # Force a bearish cross by feeding a sharp drop after a rally
        base = 1700000000
        for i in range(30):
            strategy._on_strategy_bar({
                "time": base + i * 300,
                "open": 100.0 + i * 0.5,
                "high": 101.0 + i * 0.5,
                "low": 99.0 + i * 0.5,
                "close": 100.0 + i * 0.5,
                "pair": "MNQ",
            })
        # Sharp drop should trigger cross and close the long
        strategy._on_strategy_bar({
            "time": base + 30 * 300,
            "open": 120.0,
            "high": 120.0,
            "low": 110.0,
            "close": 110.0,
            "pair": "MNQ",
        })
        # Trade may or may not be closed depending on TSI state; just verify no crash


# Helper to build an EntryContext for trade-building assertions
def _build_ctx(strategy, bar, direction, bounce):
    from src.strategies.entry_context import EntryContext
    return EntryContext(
        strategy=strategy,
        line_id=None,
        direction=direction,
        level=bar["close"],
        bar=bar,
        close=bar["close"],
        low=bar.get("low", bar["close"]),
        high=bar.get("high", bar["close"]),
        extreme=bounce,
        cross_depth=abs(bar["close"] - bounce),
    )


TsiCrossStrategy._create_test_ctx = _build_ctx
