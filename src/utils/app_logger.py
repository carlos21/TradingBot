"""Unified application logging with strategy pattern.

Two implementations:
- ConsoleLogger: prints to terminal only (backtest mode)
- FileAndConsoleLogger: prints to terminal AND writes to file (live mode)
"""

import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional


class ILogger(ABC):
    """Logger interface - all loggers must implement this."""

    @abstractmethod
    def debug(self, message: str) -> None:
        """Log a debug message."""
        pass

    @abstractmethod
    def info(self, message: str) -> None:
        """Log an info message."""
        pass

    @abstractmethod
    def warning(self, message: str) -> None:
        """Log a warning message."""
        pass

    @abstractmethod
    def error(self, message: str) -> None:
        """Log an error message."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close any open resources."""
        pass


class ConsoleLogger(ILogger):
    """Logger that only prints to console (for backtest mode)."""

    def debug(self, message: str) -> None:
        print(f"[DEBUG] {message}", flush=True)

    def info(self, message: str) -> None:
        print(f"[INFO] {message}", flush=True)

    def warning(self, message: str) -> None:
        print(f"[WARN] {message}", flush=True)

    def error(self, message: str) -> None:
        print(f"[ERROR] {message}", flush=True)

    def close(self) -> None:
        pass  # Nothing to close


class FileAndConsoleLogger(ILogger):
    """Logger that writes to both file and console (for live mode)."""

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self._file_handle: Optional[object] = None
        self._lock = threading.Lock()
        self._setup_file_logging()

    def _setup_file_logging(self) -> None:
        """Create daily log file in logs/ directory."""
        self.log_dir.mkdir(exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"app_{date_str}.log"
        self._file_handle = open(log_file, "a", buffering=1)  # line-buffered

    def _write(self, level: str, message: str) -> None:
        """Write to both console and file."""
        formatted = f"[{level}] {message}"
        print(formatted, flush=True)

        with self._lock:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            self._file_handle.write(f"{timestamp} {formatted}\n")

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
