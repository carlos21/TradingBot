"""Tests for src/services/mt_manager_service.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.mt_manager_service import MetaTraderManagerService


class TestMetaTraderManagerServiceCanRunWindowsExe:

    def test_powershell_not_available(self):
        svc = MetaTraderManagerService()
        with patch("shutil.which", return_value=None):
            assert svc._can_run_windows_exe() is False

    def test_powershell_available_and_works(self):
        svc = MetaTraderManagerService()
        with patch("shutil.which", return_value="/usr/bin/powershell.exe"):
            with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                assert svc._can_run_windows_exe() is True

    def test_powershell_available_but_fails(self):
        svc = MetaTraderManagerService()
        with patch("shutil.which", return_value="/usr/bin/powershell.exe"):
            with patch("subprocess.run", side_effect=OSError("Command failed")):
                assert svc._can_run_windows_exe() is False


class TestMetaTraderManagerServiceFindMtTerminal:

    def test_cannot_run_windows_exe(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=False):
            result = svc.find_mt_terminal()
            assert result["found"] is False
            assert "not found in registry or common paths" in result["message"]

    def test_found_in_registry(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=True):
            with patch.object(svc, "_run_ps", return_value=MagicMock(stdout="C:\\MetaTrader 5\\")):
                result = svc.find_mt_terminal()
                assert result["found"] is True
                assert result["install_dir"] == "C:\\MetaTrader 5\\"
                assert "terminal64.exe" in result["path"]

    def test_not_found_in_registry(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=True):
            with patch.object(svc, "_run_ps", return_value=MagicMock(stdout="")):
                result = svc.find_mt_terminal()
                assert result["found"] is False
                assert "not found in registry or common paths" in result["message"]

    def test_found_in_common_path(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=False):
            with patch.object(Path, "exists", return_value=True):
                result = svc.find_mt_terminal()
                assert result["found"] is True
                assert "terminal64.exe" in result["path"]


class TestMetaTraderManagerServiceIsTerminalRunning:

    def test_running_via_powershell(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=True):
            with patch("subprocess.run", return_value=MagicMock(stdout="terminal64\n", strip=lambda: "terminal64")):
                assert svc.is_terminal_running() is True

    def test_not_running_via_powershell(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=True):
            with patch("subprocess.run", return_value=MagicMock(stdout="", strip=lambda: "")):
                assert svc.is_terminal_running() is False

    def test_running_via_ps(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=False):
            with patch("subprocess.run", return_value=MagicMock(stdout="terminal64.exe\n")):
                assert svc.is_terminal_running() is True

    def test_not_running_via_ps(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=False):
            with patch("subprocess.run", return_value=MagicMock(stdout="bash\n")):
                assert svc.is_terminal_running() is False


class TestMetaTraderManagerServiceLaunchTerminal:

    def test_already_running(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "is_terminal_running", return_value=True):
            result = svc.launch_terminal()
            assert result["success"] is True
            assert "already running" in result["message"]

    def test_not_found(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "is_terminal_running", return_value=False):
            with patch.object(svc, "find_mt_terminal", return_value={"found": False, "message": "Not found"}):
                result = svc.launch_terminal()
                assert result["success"] is False
                assert "Not found" in result["message"]

    def test_successful_launch_windows(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "is_terminal_running", return_value=False):
            with patch.object(svc, "find_mt_terminal", return_value={
                "found": True,
                "path": "C:\\MetaTrader 5\\terminal64.exe",
                "install_dir": "C:\\MetaTrader 5",
            }):
                with patch.object(svc, "_can_run_windows_exe", return_value=True):
                    with patch("subprocess.Popen", return_value=MagicMock()) as mock_popen:
                        result = svc.launch_terminal()
                        assert result["success"] is True
                        assert "launching" in result["message"]
                        mock_popen.assert_called_once()

    def test_successful_launch_wine(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "is_terminal_running", return_value=False):
            with patch.object(svc, "find_mt_terminal", return_value={
                "found": True,
                "path": "/home/user/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe",
                "install_dir": "/home/user/.wine/drive_c/Program Files/MetaTrader 5",
            }):
                with patch.object(svc, "_can_run_windows_exe", return_value=False):
                    with patch("subprocess.Popen", return_value=MagicMock()) as mock_popen:
                        result = svc.launch_terminal()
                        assert result["success"] is True
                        cmd = mock_popen.call_args[0][0]
                        assert cmd[0] == "wine"

    def test_launch_exception(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "is_terminal_running", return_value=False):
            with patch.object(svc, "find_mt_terminal", return_value={
                "found": True,
                "path": "C:\\MetaTrader 5\\terminal64.exe",
                "install_dir": "C:\\MetaTrader 5",
            }):
                with patch.object(svc, "_can_run_windows_exe", return_value=True):
                    with patch("subprocess.Popen", side_effect=Exception("Launch failed")):
                        result = svc.launch_terminal()
                        assert result["success"] is False
                        assert "Failed to launch" in result["message"]
