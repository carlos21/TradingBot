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

    def test_found_in_wine_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.services.mt_manager_service.Path.home",
            lambda: tmp_path,
        )
        wine_exe = tmp_path / ".wine" / "drive_c" / "Program Files" / "MetaTrader 5" / "terminal64.exe"
        wine_exe.parent.mkdir(parents=True)
        wine_exe.write_text("exe")

        svc = MetaTraderManagerService()
        result = svc.find_mt_terminal()
        assert result["found"] is True
        assert "terminal64.exe" in result["path"]
        assert ".wine" in result["path"]


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


class TestWslPathConversions:

    def test_wsl_to_windows_path_via_wslpath(self):
        svc = MetaTraderManagerService()
        with patch(
            "src.services.mt_manager_service.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="C:\\foo\\bar\n"),
        ) as mock_run:
            result = svc._wsl_to_windows_path(Path("/mnt/c/foo/bar"))
            assert result == "C:\\foo\\bar"
            mock_run.assert_called_once_with(
                ["wslpath", "-w", "/mnt/c/foo/bar"],
                capture_output=True,
                text=True,
            )

    def test_wsl_to_windows_path_manual_mnt_fallback(self):
        svc = MetaTraderManagerService()
        with patch(
            "src.services.mt_manager_service.subprocess.run",
            return_value=MagicMock(returncode=1, stdout=""),
        ):
            result = svc._wsl_to_windows_path(Path("/mnt/c/foo/bar"))
            assert result == "C:/foo/bar"

    def test_wsl_to_windows_path_manual_non_mnt_fallback(self):
        svc = MetaTraderManagerService()
        with patch(
            "src.services.mt_manager_service.subprocess.run",
            return_value=MagicMock(returncode=1, stdout=""),
        ):
            result = svc._wsl_to_windows_path(Path("/home/user/mt5"))
            assert result == "/home/user/mt5"

    def test_windows_path_to_wsl_via_wslpath(self):
        svc = MetaTraderManagerService()
        with patch(
            "src.services.mt_manager_service.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="/mnt/c/foo/bar\n"),
        ) as mock_run:
            result = svc._windows_path_to_wsl("C:\\foo\\bar")
            assert result == "/mnt/c/foo/bar"
            mock_run.assert_called_once_with(
                ["wslpath", "-u", "C:\\foo\\bar"],
                capture_output=True,
                text=True,
                timeout=5,
            )

    def test_windows_path_to_wsl_timeout_fallback(self):
        svc = MetaTraderManagerService()
        with patch(
            "src.services.mt_manager_service.subprocess.run",
            side_effect=OSError("timed out"),
        ):
            result = svc._windows_path_to_wsl("C:\\foo\\bar")
            assert result == "/mnt/c/foo/bar"

    def test_windows_path_to_wsl_unc_path_fallback(self):
        svc = MetaTraderManagerService()
        assert svc._windows_path_to_wsl("\\\\server\\share") == "\\\\server\\share"


class TestLaunchTerminalProvidedPath:

    def test_provided_windows_path_converted_and_launched(self, tmp_path):
        svc = MetaTraderManagerService()
        exe = tmp_path / "terminal64.exe"
        exe.write_text("exe")

        with patch.object(svc, "is_terminal_running", return_value=False):
            with patch.object(svc, "_can_run_windows_exe", return_value=True):
                with patch.object(svc, "_windows_path_to_wsl", return_value=str(exe)):
                    with patch("subprocess.Popen", return_value=MagicMock()) as mock_popen:
                        result = svc.launch_terminal(exe_path="C:\\MetaTrader 5\\terminal64.exe")
                        assert result["success"] is True
                        assert "launching" in result["message"]
                        mock_popen.assert_called_once()
                        assert mock_popen.call_args[0][0][0] == str(exe)

    def test_provided_path_does_not_exist(self):
        svc = MetaTraderManagerService()
        with patch.object(svc, "is_terminal_running", return_value=False):
            with patch.object(svc, "_can_run_windows_exe", return_value=True):
                with patch.object(svc, "_windows_path_to_wsl", return_value="/nonexistent/terminal64.exe"):
                    result = svc.launch_terminal(exe_path="C:\\MetaTrader 5\\terminal64.exe")
                    assert result["success"] is False
                    assert "does not exist" in result["message"]
