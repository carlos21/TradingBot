"""Unified application logging with strategy pattern.

Two implementations:
- ConsoleLogger: prints to terminal only (backtest mode)
- FileAndConsoleLogger: prints to terminal AND writes to file (live mode)
"""

import logging
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path


def log_timestamp() -> str:
    """Return the current local time as ``YYYY-MM-DD HH:MM:SS.mmm``."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the root Python logger with a datetime-aware formatter.

    This is idempotent: if handlers are already attached it does nothing,
    so it is safe to call multiple times (e.g. from entry points and tests).
    """
    if logging.root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logging.root.addHandler(handler)
    logging.root.setLevel(level)


class ILogger(ABC):
    """Logger interface - all loggers must implement this."""

    @abstractmethod
    def debug(self, message: str) -> None:
        """Log a debug message."""

    @abstractmethod
    def info(self, message: str) -> None:
        """Log an info message."""

    @abstractmethod
    def warning(self, message: str) -> None:
        """Log a warning message."""

    @abstractmethod
    def error(self, message: str) -> None:
        """Log an error message."""

    @abstractmethod
    def close(self) -> None:
        """Close any open resources."""


class ConsoleLogger(ILogger):
    """Logger that only prints to console (for backtest mode)."""

    def debug(self, message: str) -> None:
        print(f"{log_timestamp()} [DEBUG] {message}", flush=True)

    def info(self, message: str) -> None:
        print(f"{log_timestamp()} [INFO] {message}", flush=True)

    def warning(self, message: str) -> None:
        print(f"{log_timestamp()} [WARN] {message}", flush=True)

    def error(self, message: str) -> None:
        print(f"{log_timestamp()} [ERROR] {message}", flush=True)

    def close(self) -> None:
        pass  # Nothing to close


class FileAndConsoleLogger(ILogger):
    """Logger that writes to both file and console (for live mode)."""

    def __init__(self, log_dir: str = "logs", instance_name: str = ""):
        self.log_dir = Path(log_dir)
        self.instance_name = instance_name
        self._file_handle: object | None = None
        self._lock = threading.Lock()
        self._setup_file_logging()

    def _setup_file_logging(self) -> None:
        """Create daily log file in logs/ directory."""
        self.log_dir.mkdir(exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"app_{date_str}.log"
        self._file_handle = open(log_file, "a", buffering=1)  # line-buffered  # noqa: SIM115

    def _write(self, level: str, message: str) -> None:
        """Write to both console and file."""
        prefix = f"[{self.instance_name}] " if self.instance_name else ""
        formatted = f"{log_timestamp()} {prefix}[{level}] {message}"
        print(formatted, flush=True)

        with self._lock:
            self._file_handle.write(f"{formatted}\n")

    def debug(self, message: str) -> None:
        self._write("DEBUG", message)

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def warning(self, message: str) -> None:
        self._write("WARN", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)

    def close(self) -> None:
        """Close the log file handle if open."""
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None
