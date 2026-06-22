"""Tests for src.config.models — AppConfig validation edge cases."""

import pytest

from src.config.models import AccountConfig, AppConfig


class TestAppConfigValidation:
    def test_default_config_is_valid(self):
        cfg = AppConfig()
        assert cfg.validate() is None

    def test_negative_rr_ratio_raises(self):
        with pytest.raises(ValueError, match="rr_ratio must be positive"):
            AppConfig(rr_ratio=-1.0)

    def test_zero_rr_ratio_raises(self):
        with pytest.raises(ValueError, match="rr_ratio must be positive"):
            AppConfig(rr_ratio=0.0)

    def test_negative_risk_per_trade_raises(self):
        with pytest.raises(ValueError, match="risk_per_trade cannot be negative"):
            AppConfig(risk_per_trade=-10.0)

    def test_negative_risk_pct_per_trade_raises(self):
        with pytest.raises(ValueError, match="risk_pct_per_trade cannot be negative"):
            AppConfig(risk_pct_per_trade=-1.0)

    def test_zero_account_balance_raises(self):
        with pytest.raises(ValueError, match="account_balance must be positive"):
            AppConfig(account_balance=0.0)

    def test_zero_point_value_raises(self):
        with pytest.raises(ValueError, match="point_value must be positive"):
            AppConfig(point_value=0.0)

    def test_negative_daily_trades_limit_raises(self):
        with pytest.raises(ValueError, match="daily_trades_limit cannot be negative"):
            AppConfig(daily_trades_limit=-1)

    def test_negative_max_open_trades_raises(self):
        with pytest.raises(ValueError, match="max_open_trades cannot be negative"):
            AppConfig(max_open_trades=-1)

    def test_negative_broker_spread_raises(self):
        with pytest.raises(ValueError, match="broker_spread cannot be negative"):
            AppConfig(broker_spread=-0.5)

    def test_zero_history_days_raises(self):
        with pytest.raises(ValueError, match="history_days must be positive"):
            AppConfig(history_days=0)

    def test_zero_bars_per_second_raises(self):
        with pytest.raises(ValueError, match="bars_per_second must be positive"):
            AppConfig(bars_per_second=0.0)

    def test_invalid_flask_port_raises(self):
        with pytest.raises(ValueError, match="flask_port must be a valid TCP port"):
            AppConfig(flask_port=0)
        with pytest.raises(ValueError, match="flask_port must be a valid TCP port"):
            AppConfig(flask_port=70000)

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="mode must be 'live' or 'backtest'"):
            AppConfig(mode="paper")

    def test_invalid_broker_mode_raises(self):
        with pytest.raises(ValueError, match="broker_mode must be 'futures' or 'cfd'"):
            AppConfig(broker_mode="spread")

    def test_invalid_platform_type_raises(self):
        with pytest.raises(ValueError, match="platform_type must be 'ninjatrader' or 'metatrader'"):
            AppConfig(platform_type="ib")

    def test_invalid_line_removal_mode_raises(self):
        with pytest.raises(ValueError, match="line_removal_mode must be 'ON_EVALUATE' or 'NEVER'"):
            AppConfig(line_removal_mode="SOMETIMES")

    def test_empty_timeframes_raises(self):
        with pytest.raises(ValueError, match="timeframes cannot be empty"):
            AppConfig(timeframes=[])

    def test_whitespace_timeframe_raises(self):
        with pytest.raises(ValueError, match="each timeframe must be a non-empty string"):
            AppConfig(timeframes=["1m", "   "])


class TestAccountConfigValidation:
    def test_empty_account_name_raises(self):
        with pytest.raises(ValueError, match="account name cannot be empty"):
            AppConfig(nt_accounts=[AccountConfig(name="")])

    def test_negative_account_risk_usd_raises(self):
        with pytest.raises(ValueError, match="account risk_usd cannot be negative"):
            AppConfig(nt_accounts=[AccountConfig(name="A1", risk_usd=-1.0)])

    def test_negative_account_risk_pct_raises(self):
        with pytest.raises(ValueError, match="account risk_pct cannot be negative"):
            AppConfig(nt_accounts=[AccountConfig(name="A1", risk_pct=-1.0)])

    def test_zero_account_rr_ratio_raises(self):
        with pytest.raises(ValueError, match="account rr_ratio must be positive"):
            AppConfig(nt_accounts=[AccountConfig(name="A1", rr_ratio=0.0)])

    def test_account_live_enabled_defaults_to_true(self):
        cfg = AppConfig(nt_accounts=[AccountConfig(name="A1")])
        assert cfg.nt_accounts[0].live_enabled is True

    def test_account_live_enabled_can_be_false(self):
        cfg = AppConfig(nt_accounts=[AccountConfig(name="A1", live_enabled=False)])
        assert cfg.nt_accounts[0].live_enabled is False
