"""Configuration layer for TradingBot.

Provides a clean, SOLID way to configure the application from any source
(environment variables, CLI arguments, or composite sources).

Example::

    from src.config import CompositeConfigLoader, EnvConfigLoader, CliConfigLoader

    loader = CompositeConfigLoader(EnvConfigLoader(), CliConfigLoader())
    config = loader.load()
"""
from src.config.builder import AppBuilder
from src.config.loaders import (
    CliConfigLoader,
    CompositeConfigLoader,
    ConfigLoader,
    DbConfigLoader,
    EnvConfigLoader,
)
from src.config.models import AppConfig

__all__ = [
    "AppBuilder",
    "AppConfig",
    "ConfigLoader",
    "EnvConfigLoader",
    "CliConfigLoader",
    "CompositeConfigLoader",
    "DbConfigLoader",
]
