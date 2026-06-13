"""Expanded tests for src/config/loaders.py.

Covers all public classes and private helpers with edge-case focus:
- EnvConfigLoader (boolean parsing, defaults, empty strings, None handling)
- CliConfigLoader (empty args, flags, type coercion, choices)
- CompositeConfigLoader (empty list, single loader, merge semantics)
- DbConfigLoader (missing DB, key mapping, attribute parsing, accounts)
- Private helpers (_bool_env, _float_or_none, _int_or_none, _csv_to_floats)
"""

import sys

import pytest

from src.config.loaders import (
    CliConfigLoader,
    CompositeConfigLoader,
    DbConfigLoader,
    EnvConfigLoader,
    _bool_env,
    _csv_to_floats,
    _float_or_none,
    _int_or_none,
)
from src.config.models import AppConfig
from src.infrastructure.database.database import setup_database
from src.infrastructure.repositories.accounts_repository import NtAccountRepository
from src.infrastructure.repositories.settings_repository import SettingsRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class TestBoolEnv:
    """Tests for _bool_env parsing logic."""

    @pytest.mark.parametrize(
        "val,expected",
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", True),
            ("yes", True),
            ("on", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("0", False),
            ("no", False),
            ("off", False),
            ("", False),
        ],
    )
    def test_parses_standard_values(self, monkeypatch, val, expected):
        monkeypatch.setenv("TEST_BOOL", val)
        assert _bool_env("TEST_BOOL", default=False) is expected
        assert _bool_env("TEST_BOOL", default=True) is expected

    def test_unknown_value_returns_default(self, monkeypatch):
        monkeypatch.setenv("TEST_BOOL", "maybe")
        assert _bool_env("TEST_BOOL", default=False) is False
        assert _bool_env("TEST_BOOL", default=True) is True

    def test_missing_key_returns_false(self):
        # When the key is missing _bool_env returns "" which maps to False,
        # so the *default* parameter is only honoured for non-empty unknowns.
        assert _bool_env("TEST_BOOL_DEF_FALSE", default=False) is False
        assert _bool_env("TEST_BOOL_DEF_TRUE", default=True) is False


class TestFloatOrNone:
    def test_none_returns_none(self):
        assert _float_or_none(None) is None

    def test_empty_string_returns_none(self):
        assert _float_or_none("") is None

    def test_valid_string_returns_float(self):
        assert _float_or_none("3.14") == 3.14
        assert _float_or_none("42") == 42.0


class TestIntOrNone:
    def test_none_returns_none(self):
        assert _int_or_none(None) is None

    def test_empty_string_returns_none(self):
        assert _int_or_none("") is None

    def test_valid_string_returns_int(self):
        assert _int_or_none("42") == 42


class TestCsvToFloats:
    def test_none_returns_none(self):
        assert _csv_to_floats(None) is None

    def test_empty_string_returns_none(self):
        assert _csv_to_floats("") is None

    def test_single_value(self):
        assert _csv_to_floats("1.5") == [1.5]

    def test_multiple_values_with_whitespace(self):
        assert _csv_to_floats(" 1.0 , 2.5 , 3.0 ") == [1.0, 2.5, 3.0]


# ---------------------------------------------------------------------------
# EnvConfigLoader
# ---------------------------------------------------------------------------
class TestEnvConfigLoader:
    def test_load_returns_defaults_when_no_env_set(self, monkeypatch):
        # Clear every mapped env var so defaults shine through.
        loader = EnvConfigLoader()
        for key in loader._MAPPING:
            monkeypatch.delenv(key, raising=False)
        cfg = loader.load()
        assert cfg.pair == "MNQ"
        assert cfg.mode == "backtest"
        assert cfg.flask_port == 5001

    def test_load_overrides_string_fields(self, monkeypatch):
        monkeypatch.setenv("PAIR", "ES")
        monkeypatch.setenv("MODE", "live")
        monkeypatch.setenv("ZMQ_HOST", "0.0.0.0")
        cfg = EnvConfigLoader().load()
        assert cfg.pair == "ES"
        assert cfg.mode == "live"
        assert cfg.zmq_host == "0.0.0.0"

    def test_load_parses_numeric_fields(self, monkeypatch):
        monkeypatch.setenv("FLASK_PORT", "5002")
        monkeypatch.setenv("BARS_PER_SECOND", "20.5")
        monkeypatch.setenv("DAILY_TRADES_LIMIT", "5")
        cfg = EnvConfigLoader().load()
        assert cfg.flask_port == 5002
        assert cfg.bars_per_second == 20.5
        assert cfg.daily_trades_limit == 5

    def test_load_parses_timeframes(self, monkeypatch):
        monkeypatch.setenv("TIMEFRAMES", "1m, 5m, 15m")
        cfg = EnvConfigLoader().load()
        assert cfg.timeframes == ["1m", "5m", "15m"]

    def test_load_timeframes_empty_string_ignored(self, monkeypatch):
        monkeypatch.setenv("TIMEFRAMES", "")
        cfg = EnvConfigLoader().load()
        assert cfg.timeframes == AppConfig().timeframes

    def test_boolean_env_vars(self, monkeypatch):
        # True variants
        for val in ("true", "1", "yes", "on"):
            monkeypatch.setenv("REENTRY_AFTER_SL", val)
            assert EnvConfigLoader().load().reentry_after_sl is True

        # False variants (non-empty)
        for val in ("false", "0", "no", "off"):
            monkeypatch.setenv("REENTRY_AFTER_SL", val)
            assert EnvConfigLoader().load().reentry_after_sl is False

    def test_boolean_empty_env_var_uses_default(self, monkeypatch):
        """Empty string env vars are skipped by EnvConfigLoader, so default remains."""
        monkeypatch.setenv("REENTRY_AFTER_SL", "")
        assert EnvConfigLoader().load().reentry_after_sl is True

    def test_boolean_unknown_returns_default(self, monkeypatch):
        # Default for reentry_after_sl in AppConfig is True
        monkeypatch.setenv("REENTRY_AFTER_SL", "maybe")
        assert EnvConfigLoader().load().reentry_after_sl is True

    def test_optional_strings_become_none_when_empty(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
        cfg = EnvConfigLoader().load()
        assert cfg.sentry_dsn is None
        assert cfg.telegram_token is None
        assert cfg.telegram_chat_id is None

    def test_empty_env_var_ignored(self, monkeypatch):
        """An empty string env var must not overwrite the hard-coded default."""
        monkeypatch.setenv("PAIR", "")
        cfg = EnvConfigLoader().load()
        assert cfg.pair == "MNQ"

    def test_broker_spread_parsed_as_float(self, monkeypatch):
        monkeypatch.setenv("BROKER_SPREAD", "1.5")
        cfg = EnvConfigLoader().load()
        assert cfg.broker_spread == 1.5


# ---------------------------------------------------------------------------
# CliConfigLoader
# ---------------------------------------------------------------------------
class TestCliConfigLoader:
    def test_empty_args_returns_defaults(self):
        loader = CliConfigLoader(args=[])
        cfg = loader.load()
        assert cfg.pair == "MNQ"
        assert cfg.mode == "backtest"
        assert cfg.flask_port == 5001

    def test_overrides_string_fields(self):
        loader = CliConfigLoader(args=["--pair", "ES", "--mode", "live"])
        cfg = loader.load()
        assert cfg.pair == "ES"
        assert cfg.mode == "live"

    def test_overrides_numeric_fields(self):
        loader = CliConfigLoader(args=[
            "--flask-port", "5002",
            "--bars-per-second", "5.5",
            "--daily-trades-limit", "10",
        ])
        cfg = loader.load()
        assert cfg.flask_port == 5002
        assert cfg.bars_per_second == 5.5
        assert cfg.daily_trades_limit == 10

    def test_parses_timeframes(self):
        loader = CliConfigLoader(args=["--timeframes", "1m, 5m, 15m"])
        cfg = loader.load()
        assert cfg.timeframes == ["1m", "5m", "15m"]

    def test_store_true_flags(self):
        loader = CliConfigLoader(args=["--reentry-only", "--skip-rollover-days", "--no-breakeven", "--no-reentry-breakeven"])
        cfg = loader.load()
        assert cfg.reentry_only is True
        assert cfg.skip_rollover_days is True
        assert cfg.no_breakeven is True
        assert cfg.no_reentry_breakeven is True

    def test_store_false_flag(self):
        loader = CliConfigLoader(args=["--no-bootstrap-lines"])
        cfg = loader.load()
        assert cfg.bootstrap_existing_lines is False

    def test_invalid_choice_exits(self):
        loader = CliConfigLoader(args=["--mode", "invalid"])
        with pytest.raises(SystemExit):
            loader.load()

    def test_reentry_after_sl_bool_string(self):
        loader = CliConfigLoader(args=["--reentry-after-sl", "false"])
        cfg = loader.load()
        assert cfg.reentry_after_sl is False

    def test_uses_sys_argv_by_default(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["prog", "--pair", "YM"])
        loader = CliConfigLoader()
        cfg = loader.load()
        assert cfg.pair == "YM"


# ---------------------------------------------------------------------------
# CompositeConfigLoader
# ---------------------------------------------------------------------------
class TestCompositeConfigLoader:
    def test_empty_loaders_returns_defaults(self):
        loader = CompositeConfigLoader()
        cfg = loader.load()
        assert cfg.pair == "MNQ"
        assert cfg.mode == "backtest"

    def test_single_loader(self, monkeypatch):
        monkeypatch.setenv("PAIR", "ES")
        loader = CompositeConfigLoader(EnvConfigLoader())
        cfg = loader.load()
        assert cfg.pair == "ES"

    def test_later_overrides_earlier(self, monkeypatch):
        monkeypatch.setenv("PAIR", "ES")
        # CLI comes after env and should override.
        loader = CompositeConfigLoader(
            EnvConfigLoader(),
            CliConfigLoader(args=["--pair", "YM"]),
        )
        cfg = loader.load()
        assert cfg.pair == "YM"

    def test_empty_cli_does_not_wipe_env_vars(self, monkeypatch):
        """Key Composite behaviour: an empty CLI must not reset env overrides."""
        monkeypatch.setenv("PAIR", "ES")
        loader = CompositeConfigLoader(
            EnvConfigLoader(),
            CliConfigLoader(args=[]),
        )
        cfg = loader.load()
        assert cfg.pair == "ES"

    def test_merge_is_field_by_field(self, monkeypatch):
        monkeypatch.setenv("PAIR", "ES")
        monkeypatch.setenv("ZMQ_HOST", "0.0.0.0")
        loader = CompositeConfigLoader(
            EnvConfigLoader(),
            CliConfigLoader(args=["--mode", "live"]),
        )
        cfg = loader.load()
        assert cfg.pair == "ES"          # from env
        assert cfg.zmq_host == "0.0.0.0" # from env
        assert cfg.mode == "live"        # from CLI
        assert cfg.flask_port == 5001    # default untouched

    def test_three_loaders(self, monkeypatch):
        monkeypatch.setenv("PAIR", "ES")
        loader = CompositeConfigLoader(
            EnvConfigLoader(),
            CliConfigLoader(args=["--pair", "YM"]),
            CliConfigLoader(args=["--pair", "NQ"]),
        )
        cfg = loader.load()
        assert cfg.pair == "NQ"


# ---------------------------------------------------------------------------
# DbConfigLoader
# ---------------------------------------------------------------------------
class TestDbConfigLoader:
    @pytest.fixture
    def db_loader(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'loader_test.db'}"
        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        session = get_db_session().__enter__()

        settings_repo = SettingsRepository()
        accounts_repo = NtAccountRepository()
        settings_repo.set("pair", "ES")
        settings_repo.set("zmq_host", "0.0.0.0")
        settings_repo.set("flask_port", "5002")
        accounts_repo.upsert("A1", risk_usd=100.0)
        session.close()
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

    def test_fallback_on_missing_db(self):
        loader = DbConfigLoader(db_path="sqlite:///./nonexistent.db")
        cfg = loader.load()
        assert cfg.pair == "MNQ"

    def test_key_to_attr_returns_none_for_unknown_key(self):
        assert DbConfigLoader._key_to_attr("unknown_key") is None

    def test_key_to_attr_maps_known_keys(self):
        assert DbConfigLoader._key_to_attr("pair") == "pair"
        assert DbConfigLoader._key_to_attr("flask_port") == "flask_port"
        assert DbConfigLoader._key_to_attr("broker_spread") == "broker_spread"

    def test_parse_attr_floats(self):
        assert DbConfigLoader._parse_attr("risk_per_trade", "50.5") == 50.5
        assert DbConfigLoader._parse_attr("broker_spread", "1.2") == 1.2
        assert DbConfigLoader._parse_attr("rr_ratio", "3") == 3.0

    def test_parse_attr_ints(self):
        assert DbConfigLoader._parse_attr("flask_port", "5002") == 5002
        assert DbConfigLoader._parse_attr("daily_trades_limit", "5") == 5
        assert DbConfigLoader._parse_attr("zmq_market_port", "5555") == 5555

    def test_parse_attr_strings(self):
        assert DbConfigLoader._parse_attr("pair", "ES") == "ES"
        assert DbConfigLoader._parse_attr("line_removal_mode", "NEVER") == "NEVER"

    def test_empty_setting_value_ignored(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'empty_setting.db'}"
        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        session = get_db_session().__enter__()
        settings_repo = SettingsRepository()
        settings_repo.set("pair", "ES")
        settings_repo.set("zmq_host", "")
        session.close()

        loader = DbConfigLoader(db_path=db_path)
        cfg = loader.load()
        assert cfg.pair == "ES"
        assert cfg.zmq_host == AppConfig().zmq_host

    def test_none_setting_value_ignored(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'none_setting.db'}"
        setup_database(db_url=db_path)
        from src.infrastructure.database.database import AppSetting, get_db_session
        session = get_db_session().__enter__()
        session.add(AppSetting(key="pair", value="ES"))
        session.add(AppSetting(key="zmq_host", value=None))
        session.commit()
        session.close()

        loader = DbConfigLoader(db_path=db_path)
        cfg = loader.load()
        assert cfg.pair == "ES"
        assert cfg.zmq_host == AppConfig().zmq_host

    def test_derives_globals_from_first_account(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'derive.db'}"
        setup_database(db_url=db_path)
        from src.infrastructure.database.database import get_db_session
        session = get_db_session().__enter__()
        accounts_repo = NtAccountRepository()
        accounts_repo.upsert("A1", risk_usd=75.0, risk_pct=1.5, rr_ratio=4.0)
        session.close()

        loader = DbConfigLoader(db_path=db_path)
        cfg = loader.load()
        assert cfg.risk_per_trade == 75.0
        assert cfg.risk_pct_per_trade == 1.5
        assert cfg.rr_ratio == 4.0

    def test_missing_db_tables_falls_back_gracefully(self, tmp_path):
        """A DB file with no AppSetting/NtAccount tables should not crash."""
        db_file = tmp_path / "no_tables.db"
        # Create an empty SQLite file (no tables)
        import sqlite3
        conn = sqlite3.connect(str(db_file))
        conn.close()

        loader = DbConfigLoader(db_path=f"sqlite:///{db_file}")
        cfg = loader.load()
        assert cfg.pair == "MNQ"
