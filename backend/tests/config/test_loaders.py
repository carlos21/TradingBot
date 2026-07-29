"""Tests for src.config.loaders."""
import argparse
import os
from unittest.mock import patch

import pytest

from src.config.loaders import (
    CliConfigLoader,
    CompositeConfigLoader,
    ConfigLoader,
    DbConfigLoader,
    EnvConfigLoader,
    _bool_env,
    _csv_to_floats,
    _float_or_none,
    _int_or_none,
)
from src.config.models import AppConfig


class TestHelperFunctions:
    def test_bool_env_true_values(self):
        for val in ("true", "True", "1", "yes", "on"):
            with patch.dict(os.environ, {"TEST": val}):
                assert _bool_env("TEST", False) is True

    def test_bool_env_false_values(self):
        for val in ("false", "False", "0", "no", "off", ""):
            with patch.dict(os.environ, {"TEST": val}):
                assert _bool_env("TEST", True) is False

    def test_bool_env_returns_default_for_unexpected(self):
        with patch.dict(os.environ, {"TEST": "maybe"}):
            assert _bool_env("TEST", True) is True
            assert _bool_env("TEST", False) is False

    def test_float_or_none(self):
        assert _float_or_none("3.5") == 3.5
        assert _float_or_none("") is None
        assert _float_or_none(None) is None

    def test_int_or_none(self):
        assert _int_or_none("42") == 42
        assert _int_or_none("") is None
        assert _int_or_none(None) is None

    def test_csv_to_floats(self):
        assert _csv_to_floats("1.0, 2.0,3.0") == [1.0, 2.0, 3.0]
        assert _csv_to_floats("") is None
        assert _csv_to_floats(None) is None


class TestConfigLoaderProtocol:
    def test_protocol_can_be_implemented(self):
        class Dummy(ConfigLoader):
            def load(self) -> AppConfig:
                return AppConfig()

        assert Dummy().load().mode == "backtest"


class TestEnvConfigLoader:
    def test_load_with_no_env_vars_uses_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = EnvConfigLoader().load()
        assert cfg.mode == "backtest"
        assert cfg.pair == "MNQ"

    def test_load_overrides_from_env(self):
        env = {
            "MODE": "live",
            "CSV_FILE": "csvs/ES_live.csv",
            "ACCOUNT_BALANCE": "50000",
            "DAILY_TRADES_LIMIT": "5",
            "TIMEFRAMES": "5m,15m",
            "REENTRY_ONLY": "true",
            "SKIP_ROLLOVER_DAYS": "false",
            "SENTRY_DSN": "https://sentry.io",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = EnvConfigLoader().load()
        assert cfg.mode == "live"
        assert cfg.csv_file == "csvs/ES_live.csv"
        assert cfg.account_balance == 50000.0
        assert cfg.daily_trades_limit == 5
        assert cfg.timeframes == ["5m", "15m"]
        assert cfg.reentry_only is True
        assert cfg.skip_rollover_days is False
        assert cfg.sentry_dsn == "https://sentry.io"

    def test_empty_string_env_is_ignored(self):
        with patch.dict(os.environ, {"CSV_FILE": ""}, clear=True):
            cfg = EnvConfigLoader().load()
        assert cfg.csv_file == AppConfig().csv_file

    def test_telegram_env_vars(self):
        env = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "chat"}
        with patch.dict(os.environ, env, clear=True):
            cfg = EnvConfigLoader().load()
        assert cfg.telegram_token == "tok"
        assert cfg.telegram_chat_id == "chat"

    def test_parser_returning_none_skips_setattr(self):
        original_mapping = EnvConfigLoader._MAPPING
        mapping = dict(original_mapping)
        mapping["DUMMY"] = ("mode", lambda _v: None)
        with patch.object(EnvConfigLoader, "_MAPPING", mapping):
            with patch.dict(os.environ, {"DUMMY": "anything"}, clear=True):
                cfg = EnvConfigLoader().load()
        assert cfg.mode == "backtest"


class TestCliConfigLoader:
    def test_load_with_no_args_uses_defaults(self):
        cfg = CliConfigLoader(args=[]).load()
        assert cfg.mode == "backtest"
        assert cfg.pair == "MNQ"
        assert cfg._cli_provided == set()

    def test_load_overrides_from_args(self):
        args = [
            "--mode", "live",
            "--csv-file", "csvs/ES_live.csv",
            "--rr", "4.0",
            "--daily-trades-limit", "3",
            "--timeframes", "5m,15m",
            "--reentry-only",
            "--skip-rollover-days",
        ]
        cfg = CliConfigLoader(args=args).load()
        assert cfg.mode == "live"
        assert cfg.csv_file == "csvs/ES_live.csv"
        assert cfg.rr_ratio == 4.0
        assert cfg.daily_trades_limit == 3
        assert cfg.timeframes == ["5m", "15m"]
        assert cfg.reentry_only is True
        assert cfg.skip_rollover_days is True
        assert "mode" in cfg._cli_provided

    def test_store_false_flag(self):
        cfg = CliConfigLoader(args=["--no-bootstrap-lines"]).load()
        assert cfg.bootstrap_existing_lines is False

    def test_boolean_flag(self):
        cfg = CliConfigLoader(args=["--reentry-after-sl", "true"]).load()
        assert cfg.reentry_after_sl is True
        cfg = CliConfigLoader(args=["--reentry-after-sl", "false"]).load()
        assert cfg.reentry_after_sl is False

    def test_unsupported_mode_raises(self):
        with pytest.raises(SystemExit):
            CliConfigLoader(args=["--mode", "invalid"]).load()


class TestCompositeConfigLoader:
    def test_empty_loaders_returns_defaults(self):
        cfg = CompositeConfigLoader().load()
        assert cfg.mode == "backtest"

    def test_single_loader(self):
        class OnlyPair:
            def load(self) -> AppConfig:
                cfg = AppConfig()
                cfg.pair = "ES"
                return cfg

        cfg = CompositeConfigLoader(OnlyPair()).load()
        assert cfg.pair == "ES"

    def test_later_loader_overrides_earlier(self):
        class EnvLike:
            def load(self) -> AppConfig:
                cfg = AppConfig()
                cfg.pair = "ES"
                cfg.zmq_host = "0.0.0.0"
                return cfg

        class CliLike:
            def load(self) -> AppConfig:
                cfg = AppConfig()
                cfg.pair = "YM"
                cfg._cli_provided = {"pair"}
                return cfg

        cfg = CompositeConfigLoader(EnvLike(), CliLike()).load()
        assert cfg.pair == "YM"
        assert cfg.zmq_host == "0.0.0.0"

    def test_non_cli_loader_skips_default_values(self):
        class Override(ConfigLoader):
            def load(self) -> AppConfig:
                cfg = AppConfig()
                cfg.pair = "MNQ"  # same as default
                cfg.zmq_host = "1.2.3.4"
                return cfg

        cfg = CompositeConfigLoader(Override()).load()
        assert cfg.pair == "MNQ"
        assert cfg.zmq_host == "1.2.3.4"

    def test_second_non_cli_loader_skips_default_value(self):
        class Base(ConfigLoader):
            def load(self) -> AppConfig:
                cfg = AppConfig()
                cfg.pair = "ES"
                return cfg

        class SkipDefault(ConfigLoader):
            def load(self) -> AppConfig:
                cfg = AppConfig()
                cfg.pair = "MNQ"  # default, should be skipped
                cfg.zmq_host = "1.2.3.4"
                return cfg

        cfg = CompositeConfigLoader(Base(), SkipDefault()).load()
        assert cfg.pair == "ES"
        assert cfg.zmq_host == "1.2.3.4"


class TestDbConfigLoader:
    @pytest.fixture
    def db_loader(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'loader_test.db'}"
        from src.infrastructure.database.database import setup_database
        from src.infrastructure.repositories.accounts_repository import NtAccountRepository
        from src.infrastructure.repositories.settings_repository import SettingsRepository

        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        get_db_session().__enter__()

        settings_repo = SettingsRepository()
        accounts_repo = NtAccountRepository()
        settings_repo.set("pair", "ES")
        settings_repo.set("zmq_host", "0.0.0.0")
        settings_repo.set("flask_port", "5002")
        accounts_repo.upsert("A1", risk_usd=100.0)
        return DbConfigLoader(db_path=db_path)

    def test_loads_settings(self, db_loader):
        cfg = db_loader.load()
        assert cfg.pair == "ES"
        assert cfg.zmq_host == "0.0.0.0"
        assert cfg.flask_port == 5002

    def test_loads_accounts(self, db_loader):
        cfg = db_loader.load()
        assert len(cfg.nt_accounts) == 1
        assert cfg.nt_accounts[0].name == "A1"
        assert cfg.nt_accounts[0].risk_usd == 100.0

    def test_history_days_migration(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'migration.db'}"
        from src.infrastructure.database.database import setup_database
        from src.infrastructure.repositories.settings_repository import SettingsRepository

        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        get_db_session().__enter__()

        settings_repo = SettingsRepository()
        settings_repo.set("history_days", "7")
        loader = DbConfigLoader(db_path=db_path)
        cfg = loader.load()
        assert cfg.history_hours == 168

    def test_unknown_setting_key_is_skipped(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'unknown.db'}"
        from src.infrastructure.database.database import setup_database
        from src.infrastructure.repositories.settings_repository import SettingsRepository

        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        get_db_session().__enter__()

        settings_repo = SettingsRepository()
        settings_repo.set("unknown_key", "value")
        loader = DbConfigLoader(db_path=db_path)
        cfg = loader.load()
        assert cfg.pair == "MNQ"

    def test_empty_or_none_values_are_skipped(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'empty.db'}"
        from src.infrastructure.database.database import setup_database
        from src.infrastructure.repositories.settings_repository import SettingsRepository

        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        get_db_session().__enter__()

        settings_repo = SettingsRepository()
        settings_repo.set("pair", "")
        settings_repo.set("zmq_host", None)
        loader = DbConfigLoader(db_path=db_path)
        cfg = loader.load()
        assert cfg.pair == "MNQ"
        assert cfg.zmq_host == "127.0.0.1"

    def test_parse_attr_unknown_returns_string(self):
        assert DbConfigLoader._parse_attr("unknown", "foo") == "foo"

    def test_loads_float_setting(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'float.db'}"
        from src.infrastructure.database.database import setup_database
        from src.infrastructure.repositories.settings_repository import SettingsRepository

        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        get_db_session().__enter__()

        settings_repo = SettingsRepository()
        settings_repo.set("risk_per_trade", "150")
        loader = DbConfigLoader(db_path=db_path)
        cfg = loader.load()
        assert cfg.risk_per_trade == 150.0

    def test_account_with_all_risk_fields(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'acct_full.db'}"
        from src.infrastructure.database.database import setup_database
        from src.infrastructure.repositories.accounts_repository import NtAccountRepository

        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        get_db_session().__enter__()

        accounts_repo = NtAccountRepository()
        accounts_repo.upsert("A1", risk_usd=100.0, risk_pct=1.0, rr_ratio=3.0)
        loader = DbConfigLoader(db_path=db_path)
        cfg = loader.load()
        assert cfg.rr_ratio == 3.0
        assert cfg.risk_per_trade == 100.0
        assert cfg.risk_pct_per_trade == 1.0

    def test_account_with_no_risk_fields(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'acct_empty.db'}"
        from src.infrastructure.database.database import setup_database
        from src.infrastructure.repositories.accounts_repository import NtAccountRepository

        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        get_db_session().__enter__()

        accounts_repo = NtAccountRepository()
        accounts_repo.upsert("A1")
        loader = DbConfigLoader(db_path=db_path)
        cfg = loader.load()
        assert cfg.rr_ratio == 5.0  # AppConfig default
        assert cfg.risk_per_trade is None
        assert cfg.risk_pct_per_trade is None

    def test_parse_attr_returning_none_skips_setattr(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'parse_none.db'}"
        from src.infrastructure.database.database import setup_database
        from src.infrastructure.repositories.settings_repository import SettingsRepository

        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        get_db_session().__enter__()

        settings_repo = SettingsRepository()
        settings_repo.set("pair", "ES")
        loader = DbConfigLoader(db_path=db_path)
        with patch.object(loader, "_parse_attr", return_value=None):
            cfg = loader.load()
        assert cfg.pair == "MNQ"  # unchanged because parsed was None

    def test_fallback_on_db_error(self):
        loader = DbConfigLoader(db_path="not-a-valid-sqlalchemy-url")
        cfg = loader.load()
        assert cfg.mode == "backtest"

    def test_fallback_on_missing_db(self):
        loader = DbConfigLoader(db_path="sqlite:///./this_db_does_not_exist_12345.db")
        cfg = loader.load()
        assert cfg.mode == "backtest"
        assert cfg.pair == "MNQ"
