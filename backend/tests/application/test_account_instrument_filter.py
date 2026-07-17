"""Integration tests: account-instrument filtering through the strategy open path."""

from src.config.models import AccountConfig
from src.services.trade_manager import TradeManager
from src.strategies.base_strategy import BaseStrategy
from tests.fakes import (
    DummySocketIO,
    FakeAnalyticsReporter,
    FakeLogger,
    FakeTradeExecutor,
    FakeTradeRepository,
)


def _make_strategy(account_configs, live_mode=False, pair="MNQ"):
    repo = FakeTradeRepository()
    sio = DummySocketIO()
    tm = TradeManager(
        trade_repository=repo,
        socketio=sio,
        pair=pair,
        trade_executor=FakeTradeExecutor(),
        analytics=FakeAnalyticsReporter(),
        point_value=2.0,
        account_balance=100000.0,
        logger=FakeLogger(),
    )
    strategy = BaseStrategy(
        min_stop_loss=10.0,
        event_publisher=sio,
        trade_repository=repo,
        trade_manager=tm,
        extra_sl_space=0.0,
        point_value=2.0,
        account_balance=100000.0,
        live_mode=live_mode,
        account_configs=account_configs,
    )
    return strategy, tm


def _trade(pair: str):
    return {
        "pair": pair,
        "type": "long",
        "entry": 30000.0,
        "stop_loss": 29980.0,
        "take_profit": 30100.0,
        "risk": 20.0,
        "entry_time": 1000.0,
        "rr_ratio": 5.0,
    }


class TestAccountInstrumentFilter:
    """A strategy only opens trades for accounts assigned to the trade's instrument."""

    def test_mnq_trade_only_hits_mnq_assigned_accounts(self):
        accounts = [
            AccountConfig(name="Sim101", risk_usd=500.0, instrument_symbols=["MNQ"]),
            AccountConfig(name="Sim102", risk_usd=500.0, instrument_symbols=["ES"]),
            AccountConfig(name="Sim103", risk_usd=500.0, instrument_symbols=["MNQ", "ES"]),
            AccountConfig(name="Sim104", risk_usd=500.0, instrument_symbols=[]),
        ]
        strategy, _ = _make_strategy(accounts)

        strategy._store_and_emit_open(_trade("MNQ"))

        accounts_opened = {t.get("account") for t in strategy.open_trades}
        assert accounts_opened == {"Sim101", "Sim103"}
        assert "Sim102" not in accounts_opened
        assert "Sim104" not in accounts_opened

    def test_es_trade_only_hits_es_assigned_accounts(self):
        accounts = [
            AccountConfig(name="Sim101", risk_usd=500.0, instrument_symbols=["MNQ"]),
            AccountConfig(name="Sim102", risk_usd=500.0, instrument_symbols=["ES"]),
            AccountConfig(name="Sim103", risk_usd=500.0, instrument_symbols=["MNQ", "ES"]),
        ]
        strategy, _ = _make_strategy(accounts, pair="ES")

        strategy._store_and_emit_open(_trade("ES"))

        accounts_opened = {t.get("account") for t in strategy.open_trades}
        assert accounts_opened == {"Sim102", "Sim103"}

    def test_unassigned_account_does_not_get_named_trade(self):
        accounts = [
            AccountConfig(name="Sim101", risk_usd=500.0),
        ]
        strategy, _ = _make_strategy(accounts)

        strategy._store_and_emit_open(_trade("MNQ"))

        # The configured account is filtered out; the strategy falls back to a
        # single unnamed strategy trade rather than opening a trade for Sim101.
        assert len(strategy.open_trades) == 1
        assert strategy.open_trades[0].get("account") is None

    def test_live_mode_also_requires_live_enabled(self):
        accounts = [
            AccountConfig(name="Sim101", live_enabled=True, instrument_symbols=["MNQ"]),
            AccountConfig(name="Sim102", live_enabled=False, instrument_symbols=["MNQ"]),
        ]
        strategy, _ = _make_strategy(accounts, live_mode=True)

        strategy._store_and_emit_open(_trade("MNQ"))

        accounts_opened = {t.get("account") for t in strategy.open_trades}
        assert accounts_opened == {"Sim101"}
