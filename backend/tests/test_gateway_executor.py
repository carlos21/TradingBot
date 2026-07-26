"""Tests for src/infrastructure/gateway/executor.py."""

from unittest.mock import MagicMock

import pytest

from src.infrastructure.gateway.executor import MultiAccountExecutor, ZMQTradeExecutor
from tests.fakes import FakeLogger


class TestZMQTradeExecutor:
    def _make(self):
        gateway = MagicMock()
        executor = ZMQTradeExecutor(gateway, logger=FakeLogger())
        return executor, gateway

    def test_on_trade_open_sends_long_order(self):
        executor, gateway = self._make()
        executor.on_trade_open({
            "trade_id": "T1",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "risk": 5.0,
            "rr_ratio": 2.0,
        })
        gateway.send_open_order.assert_called_once()
        call_kwargs = gateway.send_open_order.call_args.kwargs
        assert call_kwargs["trade_id"] == "T1"
        assert call_kwargs["direction"] == "long"
        assert call_kwargs["entry_price"] == 100.0
        assert call_kwargs["stop_loss"] == 95.0
        assert call_kwargs["take_profit"] == 110.0
        assert call_kwargs["risk_points"] == 5.0
        assert call_kwargs["rr_ratio"] == 2.0

    def test_on_trade_open_sends_short_order(self):
        executor, gateway = self._make()
        executor.on_trade_open({
            "trade_id": "T2",
            "type": "short",
            "entry": 100.0,
            "stop_loss": 105.0,
            "take_profit": 90.0,
        })
        assert gateway.send_open_order.call_args.kwargs["direction"] == "short"

    def test_on_trade_open_rejects_invalid_direction(self):
        executor, gateway = self._make()
        with pytest.raises(ValueError, match="Invalid trade direction"):
            executor.on_trade_open({
                "trade_id": "T3",
                "type": "sideways",
                "entry": 100.0,
                "stop_loss": 95.0,
                "take_profit": 110.0,
            })
        gateway.send_open_order.assert_not_called()

    def test_on_trade_open_requires_trade_id(self):
        executor, gateway = self._make()
        with pytest.raises(ValueError, match="trade_id is required"):
            executor.on_trade_open({
                "type": "long",
                "entry": 100.0,
                "stop_loss": 95.0,
                "take_profit": 110.0,
            })
        gateway.send_open_order.assert_not_called()

    def test_on_trade_open_rejects_missing_entry(self):
        executor, gateway = self._make()
        with pytest.raises(ValueError, match="entry_price must be a positive number"):
            executor.on_trade_open({
                "trade_id": "T1",
                "type": "long",
                "stop_loss": 95.0,
                "take_profit": 110.0,
            })
        gateway.send_open_order.assert_not_called()

    def test_on_trade_open_rejects_zero_stop_loss(self):
        executor, gateway = self._make()
        with pytest.raises(ValueError, match="stop_loss must be a positive number"):
            executor.on_trade_open({
                "trade_id": "T1",
                "type": "long",
                "entry": 100.0,
                "stop_loss": 0.0,
                "take_profit": 110.0,
            })
        gateway.send_open_order.assert_not_called()

    def test_on_trade_open_rejects_negative_risk_points(self):
        executor, gateway = self._make()
        with pytest.raises(ValueError, match="risk_points must be a positive number"):
            executor.on_trade_open({
                "trade_id": "T1",
                "type": "long",
                "entry": 100.0,
                "stop_loss": 95.0,
                "take_profit": 110.0,
                "risk": -5.0,
            })
        gateway.send_open_order.assert_not_called()

    def test_on_trade_open_rejects_zero_rr_ratio(self):
        executor, gateway = self._make()
        with pytest.raises(ValueError, match="rr_ratio must be a positive number"):
            executor.on_trade_open({
                "trade_id": "T1",
                "type": "long",
                "entry": 100.0,
                "stop_loss": 95.0,
                "take_profit": 110.0,
                "rr_ratio": 0.0,
            })
        gateway.send_open_order.assert_not_called()

    def test_on_trade_open_uses_entry_price_alias(self):
        executor, gateway = self._make()
        executor.on_trade_open({
            "trade_id": "T1",
            "type": "long",
            "entry_price": 105.0,
            "stop_loss": 95.0,
            "take_profit": 115.0,
        })
        assert gateway.send_open_order.call_args.kwargs["entry_price"] == 105.0

    def test_on_trade_open_forwards_configured_risk_usd(self):
        executor, gateway = self._make()
        executor.on_trade_open({
            "trade_id": "T1",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "risk": 5.0,
            "risk_usd": 250.0,
            "risk_pct": 1.5,
        })
        call_kwargs = gateway.send_open_order.call_args.kwargs
        assert call_kwargs["risk_usd"] == 250.0
        assert call_kwargs["risk_pct"] is None

    def test_on_trade_open_forwards_configured_risk_pct_when_no_risk_usd(self):
        executor, gateway = self._make()
        executor.on_trade_open({
            "trade_id": "T1",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "risk": 5.0,
            "risk_pct": 1.6,
        })
        call_kwargs = gateway.send_open_order.call_args.kwargs
        assert call_kwargs["risk_usd"] is None
        assert call_kwargs["risk_pct"] == 1.6

    def test_on_trade_open_falls_back_to_executor_defaults(self):
        gateway = MagicMock()
        executor = ZMQTradeExecutor(gateway, logger=FakeLogger(), risk_pct=2.0)
        executor.on_trade_open({
            "trade_id": "T1",
            "type": "long",
            "entry": 100.0,
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "risk": 5.0,
        })
        call_kwargs = gateway.send_open_order.call_args.kwargs
        assert call_kwargs["risk_usd"] is None
        assert call_kwargs["risk_pct"] == 2.0

    def test_on_trade_close_passes_account(self):
        executor, gateway = self._make()
        executor.on_trade_close("T1", 99.0, account="Sim101")
        gateway.send_close_order.assert_called_once_with(
            trade_id="T1", reason="strategy", account="Sim101", instrument=None
        )

    def test_on_sl_update_passes_account(self):
        executor, gateway = self._make()
        executor.on_sl_update("T1", 96.0, account="Sim101")
        gateway.send_modify_order.assert_called_once_with(
            trade_id="T1", stop_loss=96.0, account="Sim101", instrument=None
        )

    def test_on_trade_close_sends_close_order(self):
        executor, gateway = self._make()
        executor.on_trade_close("T1", 99.0)
        gateway.send_close_order.assert_called_once_with(
            trade_id="T1", reason="strategy", account=None, instrument=None
        )

    def test_on_sl_update_sends_modify_order(self):
        executor, gateway = self._make()
        executor.on_sl_update("T1", 96.0)
        gateway.send_modify_order.assert_called_once_with(
            trade_id="T1", stop_loss=96.0, account=None, instrument=None
        )

    def test_on_trade_close_resolves_instrument_via_trade_resolver(self):
        """Close carries the trade's own instrument — no gateway default."""
        executor, gateway = self._make()
        executor.trade_resolver = lambda trade_id: {
            "trade_id": trade_id,
            "instrument": "MNQ SEP25",
            "account": "Sim101",
        }
        executor.on_trade_close("T1", 99.0)
        gateway.send_close_order.assert_called_once_with(
            trade_id="T1", reason="strategy", account="Sim101", instrument="MNQ SEP25"
        )

    def test_on_sl_update_resolves_instrument_via_trade_resolver(self):
        """Modify-stop carries the trade's own instrument — no gateway default."""
        executor, gateway = self._make()
        executor.trade_resolver = lambda trade_id: {
            "trade_id": trade_id,
            "instrument": "MNQ SEP25",
            "account": "Sim101",
        }
        executor.on_sl_update("T1", 96.0)
        gateway.send_modify_order.assert_called_once_with(
            trade_id="T1", stop_loss=96.0, account="Sim101", instrument="MNQ SEP25"
        )


class TestMultiAccountExecutor:
    def _make(self, trade_manager=None):
        gateway = MagicMock()
        gateway_executor = MagicMock()
        gateway_executor._gateway = gateway
        # Trade not found in any session → falls back to the trade_manager repo.
        gateway_executor._resolve_trade.return_value = None
        executor = MultiAccountExecutor(
            trade_manager=trade_manager,
            account_configs=[],
            gateway_executor=gateway_executor,
            logger=FakeLogger(),
        )
        return executor, gateway_executor

    def test_on_trade_open_requires_account(self):
        executor, gateway_executor = self._make()
        with pytest.raises(RuntimeError, match="no account"):
            executor.on_trade_open({"trade_id": "T1", "type": "long"})

    def test_on_trade_close_looks_up_account(self):
        trade = MagicMock()
        trade.account = "Sim101"
        repo = MagicMock()
        repo.get_trade.return_value = trade
        tm = MagicMock()
        tm.trade_repository = repo

        executor, gateway_executor = self._make(trade_manager=tm)
        executor.on_trade_close("T1", 99.0)
        gateway_executor.on_trade_close.assert_called_once_with(
            "T1", 99.0, account="Sim101"
        )
        # The underlying gateway call happens inside the executor under test.
        gateway_executor._gateway.send_close_order.assert_not_called()

    def test_on_sl_update_looks_up_account(self):
        trade = MagicMock()
        trade.account = "Sim101"
        repo = MagicMock()
        repo.get_trade.return_value = trade
        tm = MagicMock()
        tm.trade_repository = repo

        executor, gateway_executor = self._make(trade_manager=tm)
        executor.on_sl_update("T1", 96.0)
        gateway_executor.on_sl_update.assert_called_once_with(
            "T1", 96.0, account="Sim101"
        )
        gateway_executor._gateway.send_modify_order.assert_not_called()
