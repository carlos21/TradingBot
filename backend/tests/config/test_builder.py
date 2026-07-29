"""Tests for src.config.builder — application assembly."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import OperationalError

from src.config.builder import (
    AppBuilder,
    _build_analytics,
    _build_database,
    _build_notifier,
    _parse_input_to_epoch,
)
from src.config.models import AccountConfig, AppConfig


class TestParseInputToEpoch:
    def test_empty_string_returns_none(self):
        assert _parse_input_to_epoch("", "America/New_York") is None

    def test_isoformat_without_tz_uses_input_tz(self):
        epoch = _parse_input_to_epoch("2026-01-01T00:00:00", "America/New_York")
        expected = int(datetime(2026, 1, 1, tzinfo=ZoneInfo("America/New_York")).timestamp())
        assert epoch == expected

    def test_isoformat_with_tz_preserved(self):
        epoch = _parse_input_to_epoch("2026-01-01T00:00:00+00:00", "America/New_York")
        expected = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
        assert epoch == expected

    def test_strptime_fallback_when_isoformat_fails(self):
        with patch("src.config.builder.datetime") as mock_datetime:
            real_datetime = __import__("datetime").datetime
            mock_datetime.fromisoformat.side_effect = ValueError("bad iso")
            mock_datetime.strptime = real_datetime.strptime
            mock_datetime.now.return_value = real_datetime.now()
            mock_datetime.side_effect = real_datetime
            epoch = _parse_input_to_epoch("2026-01-01 00:00:00", "America/New_York")
        expected = int(real_datetime(2026, 1, 1, tzinfo=ZoneInfo("America/New_York")).timestamp())
        assert epoch == expected
        mock_datetime.fromisoformat.assert_called_once_with("2026-01-01 00:00:00")


class TestBuildAnalytics:
    def test_sentry_enabled(self):
        with patch("src.config.builder.SentryReporter") as mock_reporter:
            cfg = AppConfig(sentry_dsn="https://example.com")
            reporter = _build_analytics(cfg)
            mock_reporter.assert_called_once_with("https://example.com")
            assert reporter == mock_reporter.return_value

    def test_sentry_disabled(self):
        cfg = AppConfig()
        reporter = _build_analytics(cfg)
        assert reporter.__class__.__name__ == "NoOpReporter"


class TestBuildNotifier:
    def test_telegram_enabled(self):
        cfg = AppConfig(telegram_token="token", telegram_chat_id="chat")
        notifier = _build_notifier(cfg)
        assert notifier.__class__.__name__ == "TelegramNotifier"

    def test_telegram_disabled(self):
        cfg = AppConfig()
        notifier = _build_notifier(cfg)
        assert notifier.__class__.__name__ == "NoOpNotifier"


class TestBuildDatabase:
    def _operational_error(self):
        return OperationalError("SELECT 1", {}, Exception("connection refused"))

    def test_uses_database_url_when_reachable(self):
        primary_db = MagicMock()
        with patch("src.config.builder.get_database", return_value=primary_db) as mock_get, \
             patch("src.config.builder.database.setup_database"):
            cfg = AppConfig(database_url="postgresql://u:p@localhost/db")
            db = _build_database(cfg)
        assert db is primary_db
        mock_get.assert_called_once_with(db_url="postgresql://u:p@localhost/db")

    def test_falls_back_to_sqlite_when_database_url_unreachable(self):
        primary_db, fallback_db = MagicMock(), MagicMock()
        with patch("src.config.builder.get_database") as mock_get, \
             patch("src.config.builder.database.setup_database") as mock_setup:
            mock_get.side_effect = [primary_db, fallback_db]
            mock_setup.side_effect = [self._operational_error(), None]
            cfg = AppConfig(
                database_url="postgresql://u:p@localhost/db",
                db_path="sqlite:///./fallback.db",
            )
            db = _build_database(cfg)
        assert db is fallback_db
        assert mock_get.call_args_list[1].kwargs["db_url"] == "sqlite:///./fallback.db"

    def test_no_fallback_without_database_url(self):
        with patch("src.config.builder.get_database", return_value=MagicMock()), \
             patch("src.config.builder.database.setup_database") as mock_setup:
            mock_setup.side_effect = self._operational_error()
            cfg = AppConfig(db_path="sqlite:///./only.db")
            with pytest.raises(OperationalError):
                _build_database(cfg)

    def test_no_fallback_when_database_url_equals_db_path(self):
        with patch("src.config.builder.get_database", return_value=MagicMock()), \
             patch("src.config.builder.database.setup_database") as mock_setup:
            mock_setup.side_effect = self._operational_error()
            cfg = AppConfig(database_url="sqlite:///./same.db", db_path="sqlite:///./same.db")
            with pytest.raises(OperationalError):
                _build_database(cfg)


@pytest.fixture
def base_patches():
    patches = [
        patch("src.config.builder.database.setup_database"),
        patch("src.config.builder.SQLLineRepository"),
        patch("src.config.builder.SQLTradeRepository"),
        patch("src.config.builder.SQLiteLineTriggerStateRepository"),
        patch("src.config.builder.CSVDataSource"),
        patch("app_factory.create_app"),
        patch("src.config.builder._build_notifier"),
        patch("src.config.builder._build_analytics"),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


class TestAppBuilderBacktest:
    def test_init(self):
        cfg = AppConfig(mode="backtest")
        builder = AppBuilder(cfg)
        assert builder.config is cfg

    def test_build_backtest_returns_wiring_and_none_ds(self, base_patches):
        cfg = AppConfig(mode="backtest")
        builder = AppBuilder(cfg)
        wiring, ds = builder.build()
        assert wiring is not None
        assert ds is None

    def test_configure_logging_called(self, base_patches):
        with patch("src.config.builder.configure_logging") as mock_configure:
            cfg = AppConfig(mode="backtest")
            AppBuilder(cfg).build()
            mock_configure.assert_called_once()

    def test_no_breakeven_option(self, base_patches):
        cfg = AppConfig(mode="backtest", no_breakeven=True)
        AppBuilder(cfg).build()
        mock_create_app = __import__("app_factory", fromlist=["create_app"]).create_app
        options = mock_create_app.call_args.kwargs["options"]
        assert options.breakeven is None

    def test_no_reentry_breakeven_option(self, base_patches):
        cfg = AppConfig(mode="backtest", no_reentry_breakeven=True)
        AppBuilder(cfg).build()
        mock_create_app = __import__("app_factory", fromlist=["create_app"]).create_app
        options = mock_create_app.call_args.kwargs["options"]
        assert options.reentry_breakeven is None

    def test_csv_source_args(self, base_patches):
        cfg = AppConfig(mode="backtest", pair="MES", csv_file="csvs/MES.csv")
        AppBuilder(cfg).build()
        mock_csv = __import__("src.config.builder", fromlist=["CSVDataSource"]).CSVDataSource
        kwargs = mock_csv.call_args.kwargs
        assert kwargs["pair"] == "MES"
        assert kwargs["filename"] == "csvs/MES.csv"
        assert kwargs["bars_per_second"] == cfg.bars_per_second

    def test_strategy_numbers_and_options_passed(self, base_patches):
        cfg = AppConfig(mode="backtest", rr_ratio=4.0, risk_per_trade=100.0)
        AppBuilder(cfg).build()
        mock_create_app = __import__("app_factory", fromlist=["create_app"]).create_app
        kwargs = mock_create_app.call_args.kwargs
        assert kwargs["numbers"].rr_ratio == 4.0
        assert kwargs["numbers"].risk_per_trade == 100.0
        assert kwargs["timeframes"] == cfg.timeframes
        assert kwargs["candle_config"] is not None


class TestAppBuilderLive:
    def test_build_live_returns_wiring_and_ds(self):
        with patch("src.config.builder.database.setup_database"), \
             patch("src.config.builder.SQLLineRepository"), \
             patch("src.config.builder.SQLTradeRepository"), \
             patch("src.config.builder.SQLiteLineTriggerStateRepository"), \
             patch("app_factory.create_app"), \
             patch("src.config.builder._build_notifier"), \
             patch("src.config.builder._build_analytics"), \
             patch("src.config.builder.FileAndConsoleLogger"), \
             patch("src.config.builder.create_live_components") as mock_live:

            mock_live.return_value = (MagicMock(), MagicMock())
            cfg = AppConfig(mode="live", pair="MNQ")
            wiring, ds = AppBuilder(cfg).build()
            assert wiring is not None
            assert ds is not None

    def test_risk_per_trade_branch(self):
        with patch("src.config.builder.database.setup_database"), \
             patch("src.config.builder.SQLLineRepository"), \
             patch("src.config.builder.SQLTradeRepository"), \
             patch("src.config.builder.SQLiteLineTriggerStateRepository"), \
             patch("app_factory.create_app"), \
             patch("src.config.builder._build_notifier"), \
             patch("src.config.builder._build_analytics"), \
             patch("src.config.builder.FileAndConsoleLogger"), \
             patch("src.config.builder.create_live_components") as mock_live:

            mock_live.return_value = (MagicMock(), MagicMock())
            cfg = AppConfig(mode="live", risk_per_trade=75.0)
            AppBuilder(cfg).build()
            assert mock_live.call_args.kwargs["risk_usd"] == 75.0

    def test_risk_pct_branch(self):
        with patch("src.config.builder.database.setup_database"), \
             patch("src.config.builder.SQLLineRepository"), \
             patch("src.config.builder.SQLTradeRepository"), \
             patch("src.config.builder.SQLiteLineTriggerStateRepository"), \
             patch("app_factory.create_app"), \
             patch("src.config.builder._build_notifier"), \
             patch("src.config.builder._build_analytics"), \
             patch("src.config.builder.FileAndConsoleLogger"), \
             patch("src.config.builder.create_live_components") as mock_live:

            mock_live.return_value = (MagicMock(), MagicMock())
            cfg = AppConfig(mode="live", risk_pct_per_trade=1.5)
            AppBuilder(cfg).build()
            assert mock_live.call_args.kwargs["risk_pct"] == 1.5

    def test_no_risk_branch(self):
        with patch("src.config.builder.database.setup_database"), \
             patch("src.config.builder.SQLLineRepository"), \
             patch("src.config.builder.SQLTradeRepository"), \
             patch("src.config.builder.SQLiteLineTriggerStateRepository"), \
             patch("app_factory.create_app"), \
             patch("src.config.builder._build_notifier"), \
             patch("src.config.builder._build_analytics"), \
             patch("src.config.builder.FileAndConsoleLogger"), \
             patch("src.config.builder.create_live_components") as mock_live:

            mock_live.return_value = (MagicMock(), MagicMock())
            cfg = AppConfig(mode="live", risk_per_trade=None, risk_pct_per_trade=None)
            AppBuilder(cfg).build()
            assert mock_live.call_args.kwargs["risk_usd"] is None
            assert mock_live.call_args.kwargs["risk_pct"] is None

    def test_multi_account_live_components(self):
        with patch("src.config.builder.database.setup_database"), \
             patch("src.config.builder.SQLLineRepository"), \
             patch("src.config.builder.SQLTradeRepository"), \
             patch("src.config.builder.SQLiteLineTriggerStateRepository"), \
             patch("app_factory.create_app"), \
             patch("src.config.builder._build_notifier"), \
             patch("src.config.builder._build_analytics"), \
             patch("src.config.builder.FileAndConsoleLogger"), \
             patch("src.config.builder.create_multi_account_live_components") as mock_multi:

            mock_multi.return_value = (MagicMock(), MagicMock())
            cfg = AppConfig(
                mode="live",
                nt_accounts=[
                    AccountConfig(name="A1", risk_usd=100.0),
                    AccountConfig(name="A2", risk_usd=200.0, live_enabled=False),
                ],
            )
            AppBuilder(cfg).build()
            accounts = mock_multi.call_args.kwargs["account_configs"]
            assert len(accounts) == 1
            assert accounts[0].name == "A1"

    def test_live_no_breakeven_options(self):
        with patch("src.config.builder.database.setup_database"), \
             patch("src.config.builder.SQLLineRepository"), \
             patch("src.config.builder.SQLTradeRepository"), \
             patch("src.config.builder.SQLiteLineTriggerStateRepository"), \
             patch("app_factory.create_app") as mock_create_app, \
             patch("src.config.builder._build_notifier"), \
             patch("src.config.builder._build_analytics"), \
             patch("src.config.builder.FileAndConsoleLogger"), \
             patch("src.config.builder.create_live_components") as mock_live:

            mock_live.return_value = (MagicMock(), MagicMock())
            cfg = AppConfig(mode="live", no_breakeven=True, no_reentry_breakeven=True)
            AppBuilder(cfg).build()
            options = mock_create_app.call_args.kwargs["options"]
            assert options.breakeven is None
            assert options.reentry_breakeven is None
