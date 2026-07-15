"""Tests for src.config.builders compatibility shim."""
import src.config.builders as builders
from src.config.builder import AppBuilder
from src.config.loaders import CliConfigLoader, CompositeConfigLoader, EnvConfigLoader
from src.config.models import AppConfig


class TestConfigBuildersShim:
    def test_exports_app_config(self):
        assert builders.AppConfig is AppConfig

    def test_exports_config_loaders(self):
        assert builders.ConfigLoader
        assert builders.EnvConfigLoader is EnvConfigLoader
        assert builders.CliConfigLoader is CliConfigLoader
        assert builders.CompositeConfigLoader is CompositeConfigLoader

    def test_exports_app_builder(self):
        assert builders.AppBuilder is AppBuilder

    def test_all_matches_public_api(self):
        assert builders.__all__ == [
            "AppConfig",
            "ConfigLoader",
            "EnvConfigLoader",
            "CliConfigLoader",
            "CompositeConfigLoader",
            "AppBuilder",
        ]
