"""Tests for src.config.loaders — env, CLI, and composite configuration loading."""
import os
from unittest.mock import patch

from src.config.loaders import CliConfigLoader, CompositeConfigLoader, EnvConfigLoader


class TestEnvConfigLoader:
    def test_defaults_when_no_env_set(self):
        """With no env vars, loader returns default AppConfig."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = EnvConfigLoader().load()
        assert cfg.rr_ratio == 5.0
        assert cfg.pair == "MNQ"
        assert cfg.mode == "backtest"
        assert cfg.risk_per_trade is None

    def test_reads_rr_ratio(self):
        with patch.dict(os.environ, {"RR_RATIO": "3.3"}, clear=False):
            cfg = EnvConfigLoader().load()
        assert cfg.rr_ratio == 3.3

    def test_reads_risk(self):
        with patch.dict(os.environ, {"RISK": "100"}, clear=False):
            cfg = EnvConfigLoader().load()
        assert cfg.risk_per_trade == 100.0

    def test_reads_mode(self):
        with patch.dict(os.environ, {"MODE": "live"}, clear=False):
            cfg = EnvConfigLoader().load()
        assert cfg.mode == "live"

    def test_reads_pair(self):
        with patch.dict(os.environ, {"PAIR": "MNQ"}, clear=False):
            cfg = EnvConfigLoader().load()
        assert cfg.pair == "MNQ"

    def test_reads_timeframes(self):
        with patch.dict(os.environ, {"TIMEFRAMES": "5m,15m,1h"}, clear=False):
            cfg = EnvConfigLoader().load()
        assert cfg.timeframes == ["5m", "15m", "1h"]

    def test_reads_booleans_true(self):
        with patch.dict(os.environ, {"REENTRY_ONLY": "true"}, clear=False):
            cfg = EnvConfigLoader().load()
        assert cfg.reentry_only is True

    def test_reads_booleans_false(self):
        with patch.dict(os.environ, {"REENTRY_AFTER_SL": "false"}, clear=False):
            cfg = EnvConfigLoader().load()
        assert cfg.reentry_after_sl is False

    def test_empty_env_does_not_override(self):
        """An empty string env var should not override the default."""
        with patch.dict(os.environ, {"RISK": ""}, clear=False):
            cfg = EnvConfigLoader().load()
        assert cfg.risk_per_trade is None


class TestCliConfigLoader:
    def test_defaults_when_no_args(self):
        cfg = CliConfigLoader(args=[]).load()
        assert cfg.rr_ratio == 5.0
        assert cfg.pair == "MNQ"

    def test_reads_rr_ratio(self):
        cfg = CliConfigLoader(args=["--rr", "3.3"]).load()
        assert cfg.rr_ratio == 3.3

    def test_reads_risk(self):
        cfg = CliConfigLoader(args=["--risk", "100"]).load()
        assert cfg.risk_per_trade == 100.0

    def test_reads_mode(self):
        cfg = CliConfigLoader(args=["--mode", "live"]).load()
        assert cfg.mode == "live"

    def test_reads_pair(self):
        cfg = CliConfigLoader(args=["--pair", "MNQ"]).load()
        assert cfg.pair == "MNQ"

    def test_reads_timeframes(self):
        cfg = CliConfigLoader(args=["--timeframes", "5m,15m"]).load()
        assert cfg.timeframes == ["5m", "15m"]

    def test_reads_boolean_flags(self):
        cfg = CliConfigLoader(args=["--reentry-only"]).load()
        assert cfg.reentry_only is True

    def test_reads_no_bootstrap_lines(self):
        cfg = CliConfigLoader(args=["--no-bootstrap-lines"]).load()
        assert cfg.bootstrap_existing_lines is False


class TestCompositeConfigLoader:
    def test_env_then_cli_override(self):
        """CLI args should override env vars."""
        env = EnvConfigLoader()
        cli = CliConfigLoader(args=["--rr", "4.0"])
        composite = CompositeConfigLoader(env, cli)

        with patch.dict(os.environ, {"RR_RATIO": "3.3"}, clear=False):
            cfg = composite.load()

        assert cfg.rr_ratio == 4.0

    def test_env_used_when_cli_not_provided(self):
        """When CLI does not specify a value, env var should win."""
        env = EnvConfigLoader()
        cli = CliConfigLoader(args=[])
        composite = CompositeConfigLoader(env, cli)

        with patch.dict(os.environ, {"RR_RATIO": "3.3"}, clear=False):
            cfg = composite.load()

        assert cfg.rr_ratio == 3.3

    def test_default_used_when_neither_provided(self):
        env = EnvConfigLoader()
        cli = CliConfigLoader(args=[])
        composite = CompositeConfigLoader(env, cli)

        with patch.dict(os.environ, {}, clear=True):
            cfg = composite.load()

        assert cfg.rr_ratio == 5.0

    def test_multiple_overrides(self):
        env = EnvConfigLoader()
        cli = CliConfigLoader(args=["--mode", "live", "--pair", "MNQ", "--risk", "75"])
        composite = CompositeConfigLoader(env, cli)

        with patch.dict(os.environ, {"MODE": "backtest", "PAIR": "MNQ", "RISK": "50"}, clear=False):
            cfg = composite.load()

        assert cfg.mode == "live"
        assert cfg.pair == "MNQ"
        assert cfg.risk_per_trade == 75.0
