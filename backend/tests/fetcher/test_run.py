"""Tests for fetcher.run CLI."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestFetcherRunCLI:
    def test_negative_lookback_exits(self, monkeypatch):
        from fetcher import run as fetcher_run
        monkeypatch.setattr("sys.argv", ["fetcher.run", "--lookback", "-1"])

        with pytest.raises(SystemExit) as exc_info:
            fetcher_run.main()
        assert exc_info.value.code == 2

    def test_sync_failure_exits_non_zero(self, monkeypatch, tmp_path: Path):
        from fetcher import run as fetcher_run
        output = tmp_path / "out" / "bars.csv"
        monkeypatch.setattr(
            "sys.argv",
            [
                "fetcher.run",
                "--provider", "polygon",
                "--symbol", "NQ:XCME",
                "--output", str(output),
                "--polygon-api-key", "test_key",
            ],
        )

        fake_provider = MagicMock()
        fake_provider.name = "FakePolygon"
        fake_provider.fetch_bars.side_effect = RuntimeError("api failure")

        with patch.object(fetcher_run, "_build_provider", return_value=fake_provider):
            with pytest.raises(SystemExit) as exc_info:
                fetcher_run.main()

        assert exc_info.value.code == 1
        assert output.parent.exists()
