"""Tests for backend/app.py entry point."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.config.models import AccountConfig, AppConfig


class TestAppMainLiveMode:
    """Verify live-mode boot behavior without running the real Flask server."""

    @pytest.fixture
    def base_patches(self):
        patches = [
            patch("app.load_dotenv"),
            patch("app.configure_logging"),
            patch("app.CompositeConfigLoader"),
            patch("app.AppBuilder"),
            patch("app.signal.signal"),
        ]
        mocks = {}
        for p in patches:
            mock = p.start()
            mocks[p.attribute.replace("app.", "")] = mock
        yield mocks
        for p in patches:
            p.stop()

    def _make_wiring(self):
        wiring = MagicMock()
        wiring.logger = MagicMock()
        wiring.app = MagicMock()
        wiring.socketio = MagicMock()
        wiring.socketio.run.side_effect = KeyboardInterrupt
        return wiring

    def test_live_mode_does_not_auto_start_gateway(self, base_patches):
        config = AppConfig(mode="live", platform_type="ninjatrader")
        base_patches["CompositeConfigLoader"].return_value.load.return_value = config

        ds = MagicMock()
        wiring = self._make_wiring()
        builder = MagicMock()
        builder.build.return_value = (wiring, ds)
        base_patches["AppBuilder"].return_value = builder

        import app as app_module

        with pytest.raises(KeyboardInterrupt):
            app_module.main()

        ds.start.assert_not_called()
        ds.stop.assert_called_once()

    def test_live_mode_warns_when_no_nt_accounts(self, base_patches):
        config = AppConfig(mode="live", platform_type="ninjatrader", nt_accounts=[])
        base_patches["CompositeConfigLoader"].return_value.load.return_value = config

        ds = MagicMock()
        wiring = self._make_wiring()
        builder = MagicMock()
        builder.build.return_value = (wiring, ds)
        base_patches["AppBuilder"].return_value = builder

        import app as app_module

        with pytest.raises(KeyboardInterrupt):
            app_module.main()

        warning_calls = [c for c in wiring.logger.warning.call_args_list if "No NT accounts" in str(c)]
        assert len(warning_calls) == 1
        ds.start.assert_not_called()

    def test_live_mode_with_accounts_still_waits_for_user_start(self, base_patches):
        config = AppConfig(
            mode="live",
            platform_type="ninjatrader",
            nt_accounts=[AccountConfig(name="Sim101")],
        )
        base_patches["CompositeConfigLoader"].return_value.load.return_value = config

        ds = MagicMock()
        wiring = self._make_wiring()
        builder = MagicMock()
        builder.build.return_value = (wiring, ds)
        base_patches["AppBuilder"].return_value = builder

        import app as app_module

        with pytest.raises(KeyboardInterrupt):
            app_module.main()

        ds.start.assert_not_called()
