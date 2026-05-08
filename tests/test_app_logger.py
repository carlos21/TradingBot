"""Tests for src/utils/app_logger.py."""

import os
import tempfile
from pathlib import Path

import pytest

from src.utils.app_logger import ConsoleLogger, FileAndConsoleLogger, ILogger


class TestILogger:

    def test_is_abstract(self):
        assert hasattr(ILogger, "__abstractmethods__")


class TestConsoleLogger:

    def test_debug(self, capsys):
        logger = ConsoleLogger()
        logger.debug("test debug")
        captured = capsys.readouterr()
        assert "[DEBUG] test debug" in captured.out

    def test_info(self, capsys):
        logger = ConsoleLogger()
        logger.info("test info")
        captured = capsys.readouterr()
        assert "[INFO] test info" in captured.out

    def test_warning(self, capsys):
        logger = ConsoleLogger()
        logger.warning("test warning")
        captured = capsys.readouterr()
        assert "[WARN] test warning" in captured.out

    def test_error(self, capsys):
        logger = ConsoleLogger()
        logger.error("test error")
        captured = capsys.readouterr()
        assert "[ERROR] test error" in captured.out

    def test_close(self):
        logger = ConsoleLogger()
        logger.close()  # Should not raise


class TestFileAndConsoleLogger:

    def test_creates_log_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"
            logger = FileAndConsoleLogger(log_dir=str(log_dir))
            assert log_dir.exists()
            logger.close()

    def test_writes_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = FileAndConsoleLogger(log_dir=tmpdir)
            logger.info("test file write")
            logger.close()

            log_files = list(Path(tmpdir).glob("app_*.log"))
            assert len(log_files) == 1
            content = log_files[0].read_text()
            assert "test file write" in content

    def test_all_levels_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = FileAndConsoleLogger(log_dir=tmpdir)
            logger.debug("debug msg")
            logger.info("info msg")
            logger.warning("warning msg")
            logger.error("error msg")
            logger.close()

            log_files = list(Path(tmpdir).glob("app_*.log"))
            content = log_files[0].read_text()
            assert "[DEBUG] debug msg" in content
            assert "[INFO] info msg" in content
            assert "[WARN] warning msg" in content
            assert "[ERROR] error msg" in content

    def test_instance_name_prefix(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = FileAndConsoleLogger(log_dir=tmpdir, instance_name="TEST")
            logger.info("prefixed")
            captured = capsys.readouterr()
            assert "[TEST] [INFO] prefixed" in captured.out
            logger.close()

    def test_file_has_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = FileAndConsoleLogger(log_dir=tmpdir)
            logger.info("timestamp test")
            logger.close()

            log_files = list(Path(tmpdir).glob("app_*.log"))
            content = log_files[0].read_text()
            # Should have timestamp prefix like "2024-01-01 12:00:00.000"
            import re
            assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}", content)

    def test_close_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = FileAndConsoleLogger(log_dir=tmpdir)
            logger.close()
            logger.close()  # Should not raise

    def test_concurrent_writes(self):
        import threading
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = FileAndConsoleLogger(log_dir=tmpdir)
            errors = []

            def worker(n):
                try:
                    for i in range(10):
                        logger.info(f"thread-{n}-msg-{i}")
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            logger.close()
            assert not errors

            log_files = list(Path(tmpdir).glob("app_*.log"))
            content = log_files[0].read_text()
            assert content.count("thread-") == 50
