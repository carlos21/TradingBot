"""Service for deploying vendor files from the repo to platform directories."""
from __future__ import annotations

import json
import shutil
from pathlib import Path


class PlatformDeployService:
    """Deploys third-party dependencies and config files to MetaTrader and NinjaTrader.

    All vendor files live in the repo under zmq_connectors/<platform>/vendor/.
    This service copies them to the active platform installation directories.
    """

    def __init__(self, project_dir: str | None = None):
        self._project_dir = Path(project_dir) if project_dir else Path(__file__).resolve().parents[2]
        self._mt_vendor = self._project_dir / "zmq_connectors" / "metatrader" / "vendor"
        self._nt_vendor = self._project_dir / "zmq_connectors" / "ninjatrader" / "vendor"

    # ═══════════════════════════════════════════════════════════════════
    # MetaTrader
    # ═══════════════════════════════════════════════════════════════════

    def find_metatrader_data_dir(self) -> Path | None:
        """Find the active MetaTrader data directory (the one with MQL5/)."""
        # 1. Resolve existing EA symlink (fastest)
        symlink_path = Path.home() / (
            "AppData/Roaming/MetaQuotes/Terminal/"
            "73B7A2420D6397DFF9014A20F1201F97/MQL5/Experts/TradingBotZmq"
        )
        if symlink_path.is_symlink():
            resolved = symlink_path.resolve()
            return resolved.parent  # up from TradingBotZmq to MQL5/Experts

        # 2. Scan all Terminal directories for the symlink
        terminals_base = Path.home() / "AppData/Roaming/MetaQuotes/Terminal"
        if terminals_base.exists():
            for terminal_dir in terminals_base.iterdir():
                if terminal_dir.is_dir():
                    symlink = terminal_dir / "MQL5/Experts/TradingBotZmq"
                    if symlink.is_symlink():
                        return symlink.resolve().parent

        # 3. Fallback: scan for any MQL5 directory
        if terminals_base.exists():
            for terminal_dir in terminals_base.iterdir():
                mql5 = terminal_dir / "MQL5"
                if mql5.is_dir():
                    return mql5 / "Experts"

        return None

    def deploy_metatrader(
        self,
        target_dir: str | None = None,
        pair: str = "NAS100",
        ports: dict | None = None,
    ) -> dict:
        """Copy vendor files and generate config for MetaTrader.

        Args:
            target_dir: Optional override MQL5 directory. If None, auto-detect.
            pair: Trading pair for the generated config.
            ports: Dict with market_port, command_port, query_port, heartbeat_port.
        """
        if ports is None:
            # Defaults must match the application's live ZMQ ports so the
            # deployed MetaTrader connector can actually connect.
            ports = {
                "market_port": 5565,
                "command_port": 5566,
                "query_port": 5567,
                "heartbeat_port": 5568,
            }

        mql5_dir = Path(target_dir) if target_dir else self.find_metatrader_data_dir()
        if mql5_dir is None:
            return {"success": False, "message": "MetaTrader data directory not found"}

        mql5_dir = Path(mql5_dir)
        if not mql5_dir.exists():
            return {"success": False, "message": f"MetaTrader directory does not exist: {mql5_dir}"}

        copied: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []

        # 1. Copy Zmq includes
        src_zmq = self._mt_vendor / "include" / "Zmq"
        dst_zmq = mql5_dir / "Include" / "Zmq"
        if src_zmq.exists():
            dst_zmq.mkdir(parents=True, exist_ok=True)
            for f in src_zmq.iterdir():
                if f.is_file():
                    dst_file = dst_zmq / f.name
                    try:
                        shutil.copy2(f, dst_file)
                        copied.append(f"Include/Zmq/{f.name}")
                    except Exception as e:
                        errors.append(f"Include/Zmq/{f.name}: {e}")
        else:
            errors.append("Vendor Zmq includes not found in repo")

        # 2. Copy JSON includes
        src_json = self._mt_vendor / "include" / "JSON"
        dst_json = mql5_dir / "Include" / "JSON"
        if src_json.exists():
            dst_json.mkdir(parents=True, exist_ok=True)
            for f in src_json.iterdir():
                if f.is_file():
                    dst_file = dst_json / f.name
                    try:
                        shutil.copy2(f, dst_file)
                        copied.append(f"Include/JSON/{f.name}")
                    except Exception as e:
                        errors.append(f"Include/JSON/{f.name}: {e}")
        else:
            errors.append("Vendor JSON includes not found in repo")

        # 3. Copy libzmq.dll
        src_dll = self._mt_vendor / "libraries" / "libzmq.dll"
        dst_dll = mql5_dir / "Libraries" / "libzmq.dll"
        if src_dll.exists():
            dst_dll.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src_dll, dst_dll)
                copied.append("Libraries/libzmq.dll")
            except Exception as e:
                errors.append(f"Libraries/libzmq.dll: {e}")
        else:
            errors.append("Vendor libzmq.dll not found in repo")

        # 4. Generate config JSON
        config = {
            "host": "127.0.0.1",
            "marketPort": ports.get("market_port", 5565),
            "commandPort": ports.get("command_port", 5566),
            "queryPort": ports.get("query_port", 5567),
            "heartbeatPort": ports.get("heartbeat_port", 5568),
            "pair": pair,
            "historyDays": 30,
            "autoConnectOnStartup": True,
            "autoShowPanel": True,
            "platformVersion": "2.0.0",
        }
        files_dir = mql5_dir / "Files"
        files_dir.mkdir(parents=True, exist_ok=True)
        config_path = files_dir / "TradingBotZmqConfig.json"
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, separators=(",", ":"))
            copied.append("Files/TradingBotZmqConfig.json")
        except Exception as e:
            errors.append(f"Files/TradingBotZmqConfig.json: {e}")

        success = len(errors) == 0
        return {
            "success": success,
            "message": f"Deployed {len(copied)} file(s) to {mql5_dir}",
            "target_dir": str(mql5_dir),
            "copied": copied,
            "skipped": skipped,
            "errors": errors,
        }

    # ═══════════════════════════════════════════════════════════════════
    # NinjaTrader
    # ═══════════════════════════════════════════════════════════════════

    def find_ninjatrader_custom_dir(self) -> Path | None:
        """Find the NinjaTrader bin/Custom directory."""
        # 1. Resolve existing AddOn symlink
        symlink_path = Path.home() / "Documents/NinjaTrader 8/bin/Custom/AddOns/TradingBotZMQ"
        if symlink_path.is_symlink():
            resolved = symlink_path.resolve()
            # resolved points to repo/zmq_connectors/ninjatrader
            # We need the parent of AddOns, which is bin/Custom
            return resolved.parent.parent

        # 2. Check common path
        common = Path.home() / "Documents/NinjaTrader 8/bin/Custom"
        if common.exists():
            return common

        # 3. Fallback: scan Documents for NinjaTrader
        docs = Path.home() / "Documents"
        if docs.exists():
            for sub in docs.iterdir():
                if sub.is_dir() and sub.name.startswith("NinjaTrader"):
                    custom = sub / "bin/Custom"
                    if custom.exists():
                        return custom

        return None

    def deploy_ninjatrader(self, target_dir: str | None = None) -> dict:
        """Copy NetMQ DLLs and verify AddOn symlink for NinjaTrader.

        Args:
            target_dir: Optional override bin/Custom directory. If None, auto-detect.
        """
        custom_dir = Path(target_dir) if target_dir else self.find_ninjatrader_custom_dir()
        if custom_dir is None:
            return {"success": False, "message": "NinjaTrader Custom directory not found"}

        custom_dir = Path(custom_dir)
        if not custom_dir.exists():
            return {"success": False, "message": f"NinjaTrader directory does not exist: {custom_dir}"}

        copied: list[str] = []
        skipped: list[str] = []
        errors: list[str] = []

        # 1. Copy NetMQ DLLs
        src_libs = self._nt_vendor / "libraries"
        dst_libs = custom_dir
        if src_libs.exists():
            for f in src_libs.iterdir():
                if f.is_file() and f.suffix.lower() == ".dll":
                    dst_file = dst_libs / f.name
                    try:
                        shutil.copy2(f, dst_file)
                        copied.append(f"Custom/{f.name}")
                    except Exception as e:
                        errors.append(f"Custom/{f.name}: {e}")
        else:
            errors.append("Vendor NetMQ libraries not found in repo")

        # 2. Verify / create AddOn symlink
        addon_dir = custom_dir / "AddOns"
        addon_dir.mkdir(parents=True, exist_ok=True)
        symlink = addon_dir / "TradingBotZMQ"
        repo_addon = self._project_dir / "zmq_connectors" / "ninjatrader"

        if symlink.is_symlink():
            try:
                current_target = symlink.resolve()
                if current_target == repo_addon.resolve():
                    skipped.append("AddOns/TradingBotZMQ (symlink already correct)")
                else:
                    # Wrong target, recreate
                    symlink.unlink()
                    symlink.symlink_to(repo_addon, target_is_directory=True)
                    copied.append("AddOns/TradingBotZMQ (symlink recreated)")
            except Exception as e:
                errors.append(f"AddOns/TradingBotZMQ: {e}")
        elif symlink.exists():
            # It's a regular file/dir, not a symlink
            errors.append(
                "AddOns/TradingBotZMQ exists but is not a symlink. "
                "Please remove it manually and retry."
            )
        else:
            try:
                symlink.symlink_to(repo_addon, target_is_directory=True)
                copied.append("AddOns/TradingBotZMQ (symlink created)")
            except Exception as e:
                errors.append(f"AddOns/TradingBotZMQ: {e}")

        # 3. Also ensure the old "TradingBot" symlink is updated if it exists
        old_symlink = addon_dir / "TradingBot"
        if old_symlink.is_symlink():
            try:
                current_target = old_symlink.resolve()
                if current_target != repo_addon.resolve():
                    old_symlink.unlink()
                    old_symlink.symlink_to(repo_addon, target_is_directory=True)
                    copied.append("AddOns/TradingBot (symlink updated)")
                else:
                    skipped.append("AddOns/TradingBot (symlink already correct)")
            except Exception as e:
                errors.append(f"AddOns/TradingBot: {e}")

        # 4. Write a fresh connector config file. The instrument is intentionally
        # omitted because the Python app now sends it via the subscribe command.
        config = {
            "host": "127.0.0.1",
            "marketPort": 5555,
            "commandPort": 5556,
            "queryPort": 5557,
            "heartbeatPort": 5558,
            "historyDays": 30,
            "batchSize": 500,
            "maxTicksPerSecond": 10,
            "platformVersion": "2.0.0-refactored",
            "autoConnectOnStartup": False,
            "autoShowWindow": True,
        }
        config_path = custom_dir / "TradingBotZmqConfig.json"
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, separators=(",", ":"))
            copied.append("TradingBotZmqConfig.json")
        except Exception as e:
            errors.append(f"TradingBotZmqConfig.json: {e}")

        success = len(errors) == 0
        return {
            "success": success,
            "message": f"Deployed {len(copied)} item(s) to {custom_dir}",
            "target_dir": str(custom_dir),
            "copied": copied,
            "skipped": skipped,
            "errors": errors,
        }
