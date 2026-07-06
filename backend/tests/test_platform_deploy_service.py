"""Tests for src/services/platform_deploy_service.py."""

import json

import pytest

from src.services.platform_deploy_service import PlatformDeployService


@pytest.fixture
def service():
    return PlatformDeployService()


class TestDeployMetaTrader:
    def test_default_ports_match_app_defaults(self, service, tmp_path):
        """The generated MT config must use the same ports as the Python app."""
        target = tmp_path / "MQL5"
        target.mkdir()
        result = service.deploy_metatrader(target_dir=str(target), pair="NAS100")

        # Vendor includes/libs may be missing in a minimal tmp tree; we only care
        # that the config JSON is written with the correct default ports.
        config_path = target / "Files" / "TradingBotZmqConfig.json"
        assert config_path.exists(), result
        config = json.loads(config_path.read_text())
        assert config["marketPort"] == 5555
        assert config["commandPort"] == 5556
        assert config["queryPort"] == 5557
        assert config["heartbeatPort"] == 5558
        assert config["pair"] == "NAS100"

    def test_custom_ports_override_defaults(self, service, tmp_path):
        target = tmp_path / "MQL5"
        target.mkdir()
        custom_ports = {
            "market_port": 6001,
            "command_port": 6002,
            "query_port": 6003,
            "heartbeat_port": 6004,
        }
        service.deploy_metatrader(target_dir=str(target), ports=custom_ports)
        config = json.loads((target / "Files" / "TradingBotZmqConfig.json").read_text())
        assert config["marketPort"] == 6001
        assert config["commandPort"] == 6002
        assert config["queryPort"] == 6003
        assert config["heartbeatPort"] == 6004


class TestDeployNinjaTrader:
    def test_default_ports_match_app_defaults(self, service, tmp_path):
        target = tmp_path / "Custom"
        target.mkdir()
        service.deploy_ninjatrader(target_dir=str(target))

        config_path = target / "TradingBotZmqConfig.json"
        assert config_path.exists()
        config = json.loads(config_path.read_text())
        assert config["marketPort"] == 5555
        assert config["commandPort"] == 5556
        assert config["queryPort"] == 5557
        assert config["heartbeatPort"] == 5558
