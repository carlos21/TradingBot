"""Tests for TsiCrossStrategy."""

import pytest

from src.domain.types import Direction
from src.strategies.tsi_cross.config import TsiCrossConfig, TsiCrossNumbers
from src.strategies.tsi_cross.strategy import TsiCrossStrategy
from tests.fakes import FakeLogger, DummySocketIO


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


class FakeExecutor:
    def on_sl_update(self, trade_id, sl):
        pass

    def on_trade_open(self, trade):
        pass


def _make_strategy():
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
    )


class TestTsiCrossStrategy:
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
