"""Tests for src.config.builder — application assembly from AppConfig."""
from unittest.mock import MagicMock, patch

import pytest

from src.config.builder import AppBuilder
from src.config.models import AppConfig


class TestAppBuilderBacktest:
    @pytest.fixture(autouse=True)
    def setup_mocks(self):
        """Patch out all SQL / Flask infrastructure so builder tests stay unit-level."""
        self.patches = [
            patch("src.config.builder.database.setup_database"),
            patch("src.config.builder.SQLLineRepository"),
            patch("src.config.builder.SQLTradeRepository"),
            patch("src.config.builder.SQLiteLineTriggerStateRepository"),
            patch("src.config.builder.CSVDataSource"),
            patch("src.config.builder.create_app"),
            patch("src.config.builder._build_notifier"),
            patch("src.config.builder._build_analytics"),
        ]
        for p in self.patches:
            p.start()
        yield
        for p in self.patches:
            p.stop()

    def test_builds_with_default_config(self):
        cfg = AppConfig(mode="backtest")
        builder = AppBuilder(cfg)
        wiring, ds = builder.build()

        assert wiring is not None
        assert ds is None  # backtest doesn't return a data source

    def test_rr_ratio_passed_to_strategy_numbers(self):
        cfg = AppConfig(mode="backtest", rr_ratio=4.0)
        builder = AppBuilder(cfg)
        builder.build()

        mock_create_app = __import__("src.config.builder", fromlist=["create_app"]).create_app
        numbers = mock_create_app.call_args.kwargs["numbers"]
        assert numbers.rr_ratio == 4.0

    def test_risk_per_trade_passed(self):
        cfg = AppConfig(mode="backtest", risk_per_trade=100.0)
        builder = AppBuilder(cfg)
        builder.build()

        mock_create_app = __import__("src.config.builder", fromlist=["create_app"]).create_app
        numbers = mock_create_app.call_args.kwargs["numbers"]
        assert numbers.risk_per_trade == 100.0

    def test_pair_passed_to_csv_source(self):
        cfg = AppConfig(mode="backtest", pair="MNQ", csv_file="csvs/MNQ.csv")
        builder = AppBuilder(cfg)
        builder.build()

        mock_csv = __import__("src.config.builder", fromlist=["CSVDataSource"]).CSVDataSource
        call_kwargs = mock_csv.call_args.kwargs
        assert call_kwargs["pair"] == "MNQ"
        assert call_kwargs["filename"] == "csvs/MNQ.csv"

    def test_timeframes_passed(self):
        cfg = AppConfig(mode="backtest", timeframes=["5m", "15m"])
        builder = AppBuilder(cfg)
        builder.build()

        mock_create_app = __import__("src.config.builder", fromlist=["create_app"]).create_app
        call_kwargs = mock_create_app.call_args.kwargs
        assert call_kwargs["timeframes"] == ["5m", "15m"]


class TestAppBuilderLive:
    """Live-mode builder tests with gateway patched."""

    def test_builds_live_mode(self):
        with patch("src.config.builder.database.setup_database"), \
             patch("src.config.builder.SQLLineRepository"), \
             patch("src.config.builder.SQLTradeRepository"), \
             patch("src.config.builder.SQLiteLineTriggerStateRepository"), \
             patch("src.config.builder.create_app"), \
             patch("src.config.builder._build_notifier"), \
             patch("src.config.builder._build_analytics"), \
             patch("src.config.builder.FileAndConsoleLogger"), \
             patch("src.gateway.create_live_components") as mock_live:

            mock_live.return_value = (MagicMock(), MagicMock())
            cfg = AppConfig(mode="live", pair="MNQ")
            builder = AppBuilder(cfg)
            wiring, ds = builder.build()

            assert wiring is not None
            assert ds is not None

    def test_zmq_ports_passed(self):
        with patch("src.config.builder.database.setup_database"), \
             patch("src.config.builder.SQLLineRepository"), \
             patch("src.config.builder.SQLTradeRepository"), \
             patch("src.config.builder.SQLiteLineTriggerStateRepository"), \
             patch("src.config.builder.create_app"), \
             patch("src.config.builder._build_notifier"), \
             patch("src.config.builder._build_analytics"), \
             patch("src.config.builder.FileAndConsoleLogger"), \
             patch("src.gateway.create_live_components") as mock_live:

            mock_live.return_value = (MagicMock(), MagicMock())
            cfg = AppConfig(
                mode="live",
                zmq_host="192.168.1.1",
                zmq_market_port=6000,
                zmq_command_port=6001,
            )
            builder = AppBuilder(cfg)
            builder.build()

            assert mock_live.call_args.kwargs["host"] == "192.168.1.1"
            assert mock_live.call_args.kwargs["market_port"] == 6000
            assert mock_live.call_args.kwargs["command_port"] == 6001

    def test_nt_accounts_passed(self):
        with patch("src.config.builder.database.setup_database"), \
             patch("src.config.builder.SQLLineRepository"), \
             patch("src.config.builder.SQLTradeRepository"), \
             patch("src.config.builder.SQLiteLineTriggerStateRepository"), \
             patch("src.config.builder.create_app"), \
             patch("src.config.builder._build_notifier"), \
             patch("src.config.builder._build_analytics"), \
             patch("src.config.builder.FileAndConsoleLogger"), \
             patch("src.gateway.create_multi_account_live_components") as mock_multi:

            mock_multi.return_value = (MagicMock(), MagicMock())
            from src.config.models import AccountConfig
            cfg = AppConfig(mode="live", nt_accounts=[AccountConfig(name="TestAccount")])
            builder = AppBuilder(cfg)
            builder.build()

            assert mock_multi.call_args.kwargs["account_configs"][0].name == "TestAccount"
