"""Service for Windows-side NinjaTrader management from WSL."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class NtManagerService:
    """Manages NinjaTrader auto-login and NetMQ from the WSL backend."""

    def __init__(self, project_dir: str | None = None, logger=None):
        self._project_dir = Path(project_dir) if project_dir else Path(__file__).resolve().parents[3]
        self._logger = logger

    def _can_run_windows_exe(self) -> bool:
        """Check if WSL can execute Windows binaries (e.g. powershell.exe)."""
        if not shutil.which("powershell.exe"):
            return False
        try:
            # Quick test: run a no-op PowerShell command
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", "exit 0"],
                capture_output=True,
                timeout=5,
            )
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _run_ps(self, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
        )

    def _wsl_to_windows_path(self, wsl_path: Path) -> str:
        """Convert a WSL path to a Windows path using wslpath."""
        result = subprocess.run(
            ["wslpath", "-w", str(wsl_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        # Fallback: manual conversion for /mnt/c/... paths
        p = str(wsl_path)
        if p.startswith("/mnt/"):
            drive = p[5].upper()
            return f"{drive}:{p[6:]}"
        return p

    def find_nt_exe(self) -> dict:
        """Try to locate NinjaTrader executable via Windows registry."""
        if not self._can_run_windows_exe():
            return {"found": False, "path": None, "message": "WSL cannot run Windows binaries (powershell.exe unavailable). Start NinjaTrader manually."}

        ps = (
            'try { '
            '$key = Get-ItemProperty "HKLM:\\SOFTWARE\\NinjaTrader, LLC\\NinjaTrader 8" -ErrorAction Stop; '
            'Write-Output $key.InstallDir '
            '} catch { '
            'try { '
            '$key = Get-ItemProperty "HKCU:\\SOFTWARE\\NinjaTrader, LLC\\NinjaTrader 8" -ErrorAction Stop; '
            'Write-Output $key.InstallDir '
            '} catch { Write-Output "" } }'
        )
        result = self._run_ps(ps)
        install_dir = result.stdout.strip()
        if install_dir:
            exe_path = Path(install_dir) / "bin64" / "NinjaTrader.exe"
            return {"found": True, "path": str(exe_path), "install_dir": install_dir}
        return {"found": False, "path": None, "message": "NinjaTrader not found in registry"}

    def install_netmq(self) -> dict:
        """Placeholder for NetMQ DLL installation."""
        # In practice the DLLs are either already present or copied with the AddOns.
        return {"success": True, "message": "NetMQ installation skipped — DLLs are bundled with the ZMQ connector"}

    def open_nt_and_login(self, username: str, password: str) -> dict:
        """Launch NinjaTrader and auto-login with the given credentials."""
        if not username or not password:
            return {"success": False, "message": "Username and password are required"}

        if not self._can_run_windows_exe():
            return {
                "success": False,
                "message": "WSL cannot run Windows binaries (powershell.exe unavailable). Start NinjaTrader manually.",
            }

        ps_script = self._project_dir / "bin" / "Start-NinjaTraderAutoLogin.ps1"
        if not ps_script.exists():
            return {"success": False, "message": f"Auto-login script not found: {ps_script}"}

        win_script = self._wsl_to_windows_path(ps_script)

        # Escape special PowerShell characters in password
        safe_password = password.replace("'", "''")

        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", win_script,
            "-Username", username,
            "-Password", safe_password,
        ]

        # Try to locate NinjaTrader; if not found in registry, let PowerShell auto-detect
        nt = self.find_nt_exe()
        if nt["found"]:
            win_nt_path = self._wsl_to_windows_path(Path(nt["path"]))
            cmd.extend(["-NinjaTraderPath", win_nt_path])

        try:
            if self._logger:
                self._logger.info(f"[NT Launch] Spawning PowerShell: {' '.join(cmd)}")

            # Let PS inherit our stdout/stderr so the user sees all output directly
            # in the console (avoids WSL pipe-buffer deadlocks too).
            proc = subprocess.Popen(
                cmd,
                start_new_session=True,
            )

            if self._logger:
                self._logger.info(f"[NT Launch] Popen returned — PID={proc.pid}")
            return {
                "success": True,
                "message": f"NinjaTrader launching with auto-login for {username}...",
            }
        except Exception as e:
            if self._logger:
                self._logger.error(f"[NT Launch] Failed to spawn PowerShell: {e}")
            return {"success": False, "message": f"Failed to launch: {e}"}
