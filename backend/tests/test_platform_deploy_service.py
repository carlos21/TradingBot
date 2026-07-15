"""Tests for src/services/platform_deploy_service.py."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.platform_deploy_service import PlatformDeployService


@pytest.fixture
def service():
    return PlatformDeployService()


def _service_with_project(tmp_path):
    """Return a PlatformDeployService rooted in tmp_path."""
    return PlatformDeployService(project_dir=str(tmp_path))


def _setup_mt_vendor(project_dir, with_zmq=True, with_json=True, with_dll=True):
    """Create a fake MetaTrader vendor tree under project_dir."""
    vendor = project_dir / "zmq_connectors" / "metatrader" / "vendor"
    if with_zmq:
        zmq_dir = vendor / "include" / "Zmq"
        zmq_dir.mkdir(parents=True)
        (zmq_dir / "zmq.mqh").write_text("zmq")
    if with_json:
        json_dir = vendor / "include" / "JSON"
        json_dir.mkdir(parents=True)
        (json_dir / "json.mqh").write_text("json")
    if with_dll:
        libs = vendor / "libraries"
        libs.mkdir(parents=True)
        (libs / "libzmq.dll").write_text("dll")


def _setup_nt_vendor(project_dir, dlls=None):
    """Create a fake NinjaTrader vendor library tree under project_dir."""
    vendor = project_dir / "zmq_connectors" / "ninjatrader" / "vendor" / "libraries"
    vendor.mkdir(parents=True)
    for name in dlls or ["NetMQ.dll"]:
        (vendor / name).write_text("dll")


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


class TestDeployMetaTraderBranches:
    def test_auto_detect_missing_data_dir(self, tmp_path):
        svc = _service_with_project(tmp_path)
        result = svc.deploy_metatrader()
        assert result["success"] is False
        assert "not found" in result["message"].lower()

    def test_target_dir_does_not_exist(self, tmp_path):
        svc = _service_with_project(tmp_path)
        result = svc.deploy_metatrader(target_dir=str(tmp_path / "missing"))
        assert result["success"] is False
        assert "does not exist" in result["message"]

    def test_copies_all_vendor_files(self, tmp_path):
        svc = _service_with_project(tmp_path)
        _setup_mt_vendor(tmp_path, with_zmq=True, with_json=True, with_dll=True)
        target = tmp_path / "MQL5"
        target.mkdir()

        result = svc.deploy_metatrader(target_dir=str(target), pair="NAS100")

        assert result["success"] is True
        assert "Include/Zmq/zmq.mqh" in result["copied"]
        assert "Include/JSON/json.mqh" in result["copied"]
        assert "Libraries/libzmq.dll" in result["copied"]
        assert "Files/TradingBotZmqConfig.json" in result["copied"]
        assert (target / "Files" / "TradingBotZmqConfig.json").exists()

    def test_missing_vendor_includes_reported(self, tmp_path):
        svc = _service_with_project(tmp_path)
        _setup_mt_vendor(tmp_path, with_zmq=False, with_json=False, with_dll=False)
        target = tmp_path / "MQL5"
        target.mkdir()

        result = svc.deploy_metatrader(target_dir=str(target))

        assert result["success"] is False
        assert any("Zmq includes not found" in e for e in result["errors"])
        assert any("JSON includes not found" in e for e in result["errors"])
        assert any("libzmq.dll not found" in e for e in result["errors"])

    def test_copy_error_caught(self, tmp_path):
        svc = _service_with_project(tmp_path)
        _setup_mt_vendor(tmp_path)
        target = tmp_path / "MQL5"
        target.mkdir()

        with patch("src.services.platform_deploy_service.shutil.copy2", side_effect=Exception("copy failed")):
            result = svc.deploy_metatrader(target_dir=str(target))

        assert result["success"] is False
        assert result["errors"]
        assert any("copy failed" in e for e in result["errors"])

    def test_config_write_error_caught(self, tmp_path):
        svc = _service_with_project(tmp_path)
        target = tmp_path / "MQL5"
        target.mkdir()

        with patch("src.services.platform_deploy_service.open", side_effect=OSError("write failed")):
            result = svc.deploy_metatrader(target_dir=str(target))

        assert result["success"] is False
        assert any("write failed" in e for e in result["errors"])


class TestFindMetaTraderDataDir:
    def test_resolve_existing_symlink(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr("src.services.platform_deploy_service.Path.home", lambda: home)
        svc = _service_with_project(tmp_path)

        target = tmp_path / "MQL5" / "Experts" / "TradingBotZmq"
        target.mkdir(parents=True)
        symlink = home / "AppData/Roaming/MetaQuotes/Terminal/73B7A2420D6397DFF9014A20F1201F97/MQL5/Experts/TradingBotZmq"
        symlink.parent.mkdir(parents=True)
        symlink.symlink_to(target, target_is_directory=True)

        assert svc.find_metatrader_data_dir() == target.parent

    def test_scan_terminal_dirs_for_symlink(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr("src.services.platform_deploy_service.Path.home", lambda: home)
        svc = _service_with_project(tmp_path)

        target = tmp_path / "MQL5" / "Experts" / "TradingBotZmq"
        target.mkdir(parents=True)
        terminal = home / "AppData/Roaming/MetaQuotes/Terminal/OtherTerminal"
        symlink = terminal / "MQL5/Experts/TradingBotZmq"
        symlink.parent.mkdir(parents=True)
        symlink.symlink_to(target, target_is_directory=True)

        assert svc.find_metatrader_data_dir() == target.parent

    def test_fallback_mql5_dir(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr("src.services.platform_deploy_service.Path.home", lambda: home)
        svc = _service_with_project(tmp_path)

        mql5 = home / "AppData/Roaming/MetaQuotes/Terminal/SomeTerminal/MQL5"
        mql5.mkdir(parents=True)

        assert svc.find_metatrader_data_dir() == mql5 / "Experts"

    def test_no_data_dir_found(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr("src.services.platform_deploy_service.Path.home", lambda: home)
        svc = _service_with_project(tmp_path)
        home.mkdir()

        assert svc.find_metatrader_data_dir() is None


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


class TestDeployNinjaTraderBranches:
    def test_auto_detect_missing_custom_dir(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr("src.services.platform_deploy_service.Path.home", lambda: home)
        svc = _service_with_project(tmp_path)
        home.mkdir()

        result = svc.deploy_ninjatrader()
        assert result["success"] is False
        assert "not found" in result["message"].lower()

    def test_target_dir_does_not_exist(self, tmp_path):
        svc = _service_with_project(tmp_path)
        result = svc.deploy_ninjatrader(target_dir=str(tmp_path / "missing"))
        assert result["success"] is False
        assert "does not exist" in result["message"]

    def test_copies_dlls_and_creates_symlink(self, tmp_path):
        svc = _service_with_project(tmp_path)
        _setup_nt_vendor(tmp_path, dlls=["NetMQ.dll", "AsyncIO.dll"])
        target = tmp_path / "Custom"
        target.mkdir()

        result = svc.deploy_ninjatrader(target_dir=str(target))

        assert result["success"] is True
        assert any("NetMQ.dll" in c for c in result["copied"])
        assert any("AsyncIO.dll" in c for c in result["copied"])
        assert any("symlink created" in c for c in result["copied"])
        assert (target / "AddOns" / "TradingBotZMQ").is_symlink()

    def test_missing_vendor_libraries_reported(self, tmp_path):
        svc = _service_with_project(tmp_path)
        target = tmp_path / "Custom"
        target.mkdir()

        result = svc.deploy_ninjatrader(target_dir=str(target))

        assert result["success"] is False
        assert any("NetMQ libraries not found" in e for e in result["errors"])

    def test_copy_dll_error_caught(self, tmp_path):
        svc = _service_with_project(tmp_path)
        _setup_nt_vendor(tmp_path)
        target = tmp_path / "Custom"
        target.mkdir()

        with patch("src.services.platform_deploy_service.shutil.copy2", side_effect=Exception("copy failed")):
            result = svc.deploy_ninjatrader(target_dir=str(target))

        assert result["success"] is False
        assert any("copy failed" in e for e in result["errors"])

    def test_symlink_already_correct(self, tmp_path):
        svc = _service_with_project(tmp_path)
        _setup_nt_vendor(tmp_path)
        target = tmp_path / "Custom"
        addon_dir = target / "AddOns"
        addon_dir.mkdir(parents=True)
        repo_addon = tmp_path / "zmq_connectors" / "ninjatrader"
        repo_addon.mkdir(parents=True, exist_ok=True)
        symlink = addon_dir / "TradingBotZMQ"
        symlink.symlink_to(repo_addon, target_is_directory=True)

        result = svc.deploy_ninjatrader(target_dir=str(target))

        assert result["success"] is True
        assert any("symlink already correct" in s for s in result["skipped"])

    def test_symlink_wrong_target_recreated(self, tmp_path):
        svc = _service_with_project(tmp_path)
        _setup_nt_vendor(tmp_path)
        target = tmp_path / "Custom"
        addon_dir = target / "AddOns"
        addon_dir.mkdir(parents=True)
        repo_addon = tmp_path / "zmq_connectors" / "ninjatrader"
        repo_addon.mkdir(parents=True, exist_ok=True)
        other = tmp_path / "other"
        other.mkdir()
        symlink = addon_dir / "TradingBotZMQ"
        symlink.symlink_to(other, target_is_directory=True)

        result = svc.deploy_ninjatrader(target_dir=str(target))

        assert result["success"] is True
        assert any("symlink recreated" in c for c in result["copied"])
        assert symlink.resolve() == repo_addon.resolve()

    def test_symlink_exists_but_is_regular_dir(self, tmp_path):
        svc = _service_with_project(tmp_path)
        _setup_nt_vendor(tmp_path)
        target = tmp_path / "Custom"
        addon_dir = target / "AddOns"
        addon_dir.mkdir(parents=True)
        (addon_dir / "TradingBotZMQ").mkdir()

        result = svc.deploy_ninjatrader(target_dir=str(target))

        assert result["success"] is False
        assert any("not a symlink" in e for e in result["errors"])

    def test_old_symlink_updated(self, tmp_path):
        svc = _service_with_project(tmp_path)
        _setup_nt_vendor(tmp_path)
        target = tmp_path / "Custom"
        addon_dir = target / "AddOns"
        addon_dir.mkdir(parents=True)
        repo_addon = tmp_path / "zmq_connectors" / "ninjatrader"
        repo_addon.mkdir(parents=True, exist_ok=True)
        old = addon_dir / "TradingBot"
        other = tmp_path / "other"
        other.mkdir()
        old.symlink_to(other, target_is_directory=True)

        result = svc.deploy_ninjatrader(target_dir=str(target))

        assert result["success"] is True
        assert any("AddOns/TradingBot" in c for c in result["copied"])
        assert old.resolve() == repo_addon.resolve()

    def test_config_write_error_caught(self, tmp_path):
        svc = _service_with_project(tmp_path)
        target = tmp_path / "Custom"
        target.mkdir()

        with patch("src.services.platform_deploy_service.open", side_effect=OSError("write failed")):
            result = svc.deploy_ninjatrader(target_dir=str(target))

        assert result["success"] is False
        assert any("write failed" in e for e in result["errors"])


class TestFindNinjaTraderCustomDir:
    def test_resolve_addon_symlink(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr("src.services.platform_deploy_service.Path.home", lambda: home)
        svc = _service_with_project(tmp_path)
        repo_addon = tmp_path / "zmq_connectors" / "ninjatrader"
        repo_addon.mkdir(parents=True, exist_ok=True)

        symlink = home / "Documents/NinjaTrader 8/bin/Custom/AddOns/TradingBotZMQ"
        symlink.parent.mkdir(parents=True)
        symlink.symlink_to(repo_addon, target_is_directory=True)

        assert svc.find_ninjatrader_custom_dir() == repo_addon.resolve().parent.parent

    def test_common_path(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr("src.services.platform_deploy_service.Path.home", lambda: home)
        svc = _service_with_project(tmp_path)
        common = home / "Documents/NinjaTrader 8/bin/Custom"
        common.mkdir(parents=True)

        assert svc.find_ninjatrader_custom_dir() == common

    def test_scan_documents_fallback(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr("src.services.platform_deploy_service.Path.home", lambda: home)
        svc = _service_with_project(tmp_path)
        custom = home / "Documents/NinjaTrader 9/bin/Custom"
        custom.mkdir(parents=True)

        assert svc.find_ninjatrader_custom_dir() == custom

    def test_no_custom_dir_found(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setattr("src.services.platform_deploy_service.Path.home", lambda: home)
        svc = _service_with_project(tmp_path)
        home.mkdir()

        assert svc.find_ninjatrader_custom_dir() is None
