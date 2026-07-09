"""Tests for src/services/nt_manager_service.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.services.nt_manager_service import NtManagerService


class TestNtManagerServiceCanRunWindowsExe:

    def test_powershell_not_available(self):
        svc = NtManagerService()
        with patch("shutil.which", return_value=None):
            assert svc._can_run_windows_exe() is False

    def test_powershell_available_and_works(self):
        svc = NtManagerService()
        with patch("shutil.which", return_value="/usr/bin/powershell.exe"):
            with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                assert svc._can_run_windows_exe() is True

    def test_powershell_available_but_fails(self):
        svc = NtManagerService()
        with patch("shutil.which", return_value="/usr/bin/powershell.exe"):
            with patch("subprocess.run", side_effect=OSError("Command failed")):
                assert svc._can_run_windows_exe() is False

    def test_powershell_timeout(self):
        svc = NtManagerService()
        with patch("shutil.which", return_value="/usr/bin/powershell.exe"):
            with patch("subprocess.run", side_effect=TimeoutError()):
                assert svc._can_run_windows_exe() is False


class TestNtManagerServiceWslToWindowsPath:

    def test_wslpath_success(self):
        svc = NtManagerService()
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="C:\\Users\\test\n")):
            assert svc._wsl_to_windows_path(Path("/home/user/project")) == "C:\\Users\\test"

    def test_wslpath_failure_mnt_fallback(self):
        svc = NtManagerService()
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            assert svc._wsl_to_windows_path(Path("/mnt/c/Users/test")) == "C:/Users/test"

    def test_wslpath_failure_no_mnt(self):
        svc = NtManagerService()
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            assert svc._wsl_to_windows_path(Path("/home/user")) == "/home/user"


class TestNtManagerServiceFindNtExe:

    def test_cannot_run_windows_exe(self):
        svc = NtManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=False):
            result = svc.find_nt_exe()
            assert result["found"] is False
            assert "WSL cannot run Windows binaries" in result["message"]

    def test_found_in_registry(self):
        svc = NtManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=True):
            with patch.object(svc, "_run_ps", return_value=MagicMock(stdout="C:\\NinjaTrader\\")):
                result = svc.find_nt_exe()
                assert result["found"] is True
                assert result["install_dir"] == "C:\\NinjaTrader\\"
                assert "NinjaTrader.exe" in result["path"]

    def test_not_found_in_registry(self):
        svc = NtManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=True):
            with patch.object(svc, "_run_ps", return_value=MagicMock(stdout="")):
                result = svc.find_nt_exe()
                assert result["found"] is False
                assert "not found in registry" in result["message"]


class TestNtManagerServiceInstallNetmq:

    def test_always_success(self):
        svc = NtManagerService()
        result = svc.install_netmq()
        assert result["success"] is True
        assert "skipped" in result["message"]


class TestNtManagerServiceOpenNtAndLogin:

    def test_missing_credentials(self):
        svc = NtManagerService()
        result = svc.open_nt_and_login("", "")
        assert result["success"] is False
        assert "required" in result["message"]

    def test_cannot_run_windows_exe(self):
        svc = NtManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=False):
            result = svc.open_nt_and_login("user", "pass")
            assert result["success"] is False
            assert "WSL cannot run Windows binaries" in result["message"]

    def test_script_not_found(self):
        svc = NtManagerService(project_dir="/nonexistent")
        with patch.object(svc, "_can_run_windows_exe", return_value=True):
            result = svc.open_nt_and_login("user", "pass")
            assert result["success"] is False
            assert "Auto-login script not found" in result["message"]

    def test_successful_launch(self):
        svc = NtManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=True):
            with patch.object(svc, "find_nt_exe", return_value={"found": False}):
                with patch.object(svc, "_wsl_to_windows_path", return_value="C:\\script.ps1"):
                    with patch("subprocess.Popen", return_value=MagicMock()):
                        result = svc.open_nt_and_login("user", "pass")
                        assert result["success"] is True
                        assert "launching" in result["message"]

    def test_password_escaping(self):
        svc = NtManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=True):
            with patch.object(svc, "find_nt_exe", return_value={"found": False}):
                with patch.object(svc, "_wsl_to_windows_path", return_value="C:\\script.ps1"):
                    with patch("subprocess.Popen") as mock_popen:
                        svc.open_nt_and_login("user", "pass'word")
                        call_args = mock_popen.call_args[0][0]
                        user_idx = call_args.index("-Username")
                        pass_idx = call_args.index("-Password")
                        assert call_args[user_idx + 1] == "user"
                        assert call_args[pass_idx + 1] == "pass'word"

    def test_password_with_at_sign(self):
        svc = NtManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=True):
            with patch.object(svc, "find_nt_exe", return_value={"found": False}):
                with patch.object(svc, "_wsl_to_windows_path", return_value="C:\\script.ps1"):
                    with patch("subprocess.Popen") as mock_popen:
                        svc.open_nt_and_login("user", "MiPuchuxD21@")
                        call_args = mock_popen.call_args[0][0]
                        user_idx = call_args.index("-Username")
                        pass_idx = call_args.index("-Password")
                        assert call_args[user_idx + 1] == "user"
                        assert call_args[pass_idx + 1] == "MiPuchuxD21@"

    def test_launch_exception(self):
        svc = NtManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=True):
            with patch.object(svc, "find_nt_exe", return_value={"found": False}):
                with patch.object(svc, "_wsl_to_windows_path", return_value="C:\\script.ps1"):
                    with patch("subprocess.Popen", side_effect=Exception("Launch failed")):
                        result = svc.open_nt_and_login("user", "pass")
                        assert result["success"] is False
                        assert "Failed to launch" in result["message"]

    def test_with_nt_path_found(self):
        svc = NtManagerService()
        with patch.object(svc, "_can_run_windows_exe", return_value=True):
            with patch.object(svc, "find_nt_exe", return_value={
                "found": True,
                "path": "C:\\NT\\bin64\\NinjaTrader.exe",
                "install_dir": "C:\\NT",
            }):
                with patch.object(svc, "_wsl_to_windows_path", return_value="C:\\NT\\bin64\\NinjaTrader.exe"):
                    with patch("subprocess.Popen") as mock_popen:
                        svc.open_nt_and_login("user", "pass")
                        call_args = mock_popen.call_args[0][0]
                        assert "-NinjaTraderPath" in call_args
                        assert "C:\\NT\\bin64\\NinjaTrader.exe" in call_args
