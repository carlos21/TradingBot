"""Configuration layer for TradingBot.

Provides a clean, SOLID way to configure the application from any source
(environment variables, CLI arguments, or composite sources).

Example::

    from src.config import CompositeConfigLoader, EnvConfigLoader, CliConfigLoader

    loader = CompositeConfigLoader(EnvConfigLoader(), CliConfigLoader())
    config = loader.load()

    from src.config import AppBuilder
    wiring, ds = AppBuilder(config).build()
"""
from src.config.models import AppConfig
from src.config.loaders import (
    ConfigLoader,
    EnvConfigLoader,
    CliConfigLoader,
    CompositeConfigLoader,
)
from src.config.builder import AppBuilder

__all__ = [
    "AppConfig",
    "ConfigLoader",
    "EnvConfigLoader",
    "CliConfigLoader",
    "CompositeConfigLoader",
    "AppBuilder",
]
