"""Service for MetaTrader terminal management from WSL/Linux."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class MetaTraderManagerService:
    """Manages MetaTrader 5 terminal launching from the WSL/Linux backend."""

    def __init__(self, project_dir: str | None = None):
        self._project_dir = Path(project_dir) if project_dir else Path(__file__).resolve().parents[2]

    def _can_run_windows_exe(self) -> bool:
        """Check if WSL can execute Windows binaries (e.g. powershell.exe)."""
        if not shutil.which("powershell.exe"):
            return False
        try:
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
        p = str(wsl_path)
        if p.startswith("/mnt/"):
            drive = p[5].upper()
            return f"{drive}:{p[6:]}"
        return p

    def find_mt_terminal(self) -> dict:
        """Try to locate MetaTrader 5 terminal executable."""
        # 1. WSL -> Windows registry check
        if self._can_run_windows_exe():
            ps = (
                'try { '
                '$key = Get-ItemProperty "HKLM:\\SOFTWARE\\MetaQuotes Software\\MetaTrader 5" -ErrorAction Stop; '
                'Write-Output $key.InstallDir '
                '} catch { '
                'try { '
                '$key = Get-ItemProperty "HKCU:\\SOFTWARE\\MetaQuotes Software\\MetaTrader 5" -ErrorAction Stop; '
                'Write-Output $key.InstallDir '
                '} catch { Write-Output "" } }'
            )
            result = self._run_ps(ps)
            install_dir = result.stdout.strip()
            if install_dir:
                exe_path = Path(install_dir) / "terminal64.exe"
                return {"found": True, "path": str(exe_path), "install_dir": install_dir}

        # 2. Common Windows fallback paths (WSL)
        fallbacks = [
            Path("/mnt/c/Program Files/MetaTrader 5/terminal64.exe"),
            Path("/mnt/c/Program Files (x86)/MetaTrader 5/terminal64.exe"),
        ]
        for p in fallbacks:
            if p.exists():
                return {"found": True, "path": str(p), "install_dir": str(p.parent)}

        # 3. Linux Wine fallback
        wine_paths = [
            Path.home() / ".wine" / "drive_c" / "Program Files" / "MetaTrader 5" / "terminal64.exe",
            Path.home() / ".wine" / "drive_c" / "Program Files (x86)" / "MetaTrader 5" / "terminal64.exe",
        ]
        for p in wine_paths:
            if p.exists():
                return {"found": True, "path": str(p), "install_dir": str(p.parent)}

        return {
            "found": False,
            "path": None,
            "message": "MetaTrader 5 not found in registry or common paths. Start MetaTrader manually.",
        }

    def is_terminal_running(self) -> bool:
        """Check if terminal64.exe is currently running."""
        if self._can_run_windows_exe():
            try:
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-Command", "Get-Process terminal64 -ErrorAction SilentlyContinue | Select-Object -First 1"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return bool(result.stdout.strip())
            except (OSError, subprocess.TimeoutExpired):
                pass

        # Fallback: check via ps
        try:
            result = subprocess.run(
                ["ps", "-eo", "comm"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.splitlines():
                if "terminal64" in line.lower() or "metatrader" in line.lower():
                    return True
        except (OSError, subprocess.TimeoutExpired):
            pass

        return False

    def _windows_path_to_wsl(self, windows_path: str) -> str:
        """Convert a Windows path (e.g. C:\\foo) to a WSL path (/mnt/c/foo)."""
        try:
            result = subprocess.run(
                ["wslpath", "-u", windows_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        # Fallback: simple manual conversion
        if len(windows_path) > 1 and windows_path[1] == ":":
            drive = windows_path[0].lower()
            rest = windows_path[2:].replace("\\", "/")
            return f"/mnt/{drive}{rest}"
        return windows_path

    def launch_terminal(self, exe_path: str | None = None) -> dict:
        """Launch MetaTrader 5 terminal if found.

        Args:
            exe_path: Optional override path to terminal64.exe. If not provided,
                     auto-detection is used.
        """
        if self.is_terminal_running():
            return {"success": True, "message": "MetaTrader 5 is already running"}

        if exe_path:
            resolved = Path(exe_path)
            # In WSL, a Windows path like C:\\foo won't resolve correctly with PosixPath.
            # Try converting it first.
            if not resolved.exists() and self._can_run_windows_exe():
                wsl_path = self._windows_path_to_wsl(exe_path)
                resolved = Path(wsl_path)
            if not resolved.exists():
                return {"success": False, "message": f"Provided path does not exist: {exe_path}"}
        else:
            mt = self.find_mt_terminal()
            if not mt["found"]:
                return {"success": False, "message": mt.get("message", "MetaTrader 5 not found")}
            resolved = Path(mt["path"])

        launch_path = str(resolved)
        try:
            # On WSL we can launch the Windows exe directly; on Linux Wine we use wine
            if self._can_run_windows_exe():
                subprocess.Popen(
                    [launch_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
            else:
                # Assume Wine
                subprocess.Popen(
                    ["wine", launch_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
            return {"success": True, "message": "MetaTrader 5 launching..."}
        except Exception as e:
            return {"success": False, "message": f"Failed to launch MetaTrader 5: {e}"}
