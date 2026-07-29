"""
ZeroMQ Trade Executor - Integrates with TradeManager.

This module provides a TradeExecutor implementation that uses the
ZeroMQ gateway to send trade commands to the platform.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from src.services.trade_executor import TradeExecutor
from src.utils.app_logger import ILogger

from .gateway import TradingGateway

if TYPE_CHECKING:
    from src.services.trade_manager import TradeManager


class ZMQTradeExecutor(TradeExecutor):
    """
    TradeExecutor that sends commands via ZeroMQ.

    This integrates with the existing TradeManager and sends
    trade commands to NinjaTrader, MetaTrader, or other platforms
    through the ZeroMQ gateway.

    One executor is shared by all instrument sessions, so it holds no
    per-session TradeManager reference.  Instead a ``trade_resolver``
    (wired by app_factory) looks a trade up across every active session's
    trade repository so close/modify commands carry the trade's own
    instrument (and account) — there is no gateway-level default.

    Usage:
        gateway = TradingGateway()
        gateway.start()

        executor = ZMQTradeExecutor(gateway, risk_usd=500)
        trade_manager = TradeManager(..., trade_executor=executor)
    """

    def __init__(
        self,
        gateway: TradingGateway,
        logger: ILogger,
        *,
        risk_usd: float | None = None,
        risk_pct: float | None = None,
    ):
        """
        Initialize the ZMQ trade executor.

        Args:
            gateway: The TradingGateway instance
            logger: Logger instance (required)
            risk_usd: Fixed dollar risk per trade (optional)
            risk_pct: Percentage of account risk per trade (optional)
        """
        self._gateway = gateway
        self.logger = logger
        self._risk_usd = risk_usd
        self._risk_pct = risk_pct
        # Looks up a trade dict (with at least ``instrument`` / ``account``
        # keys) by trade_id across all active sessions.  Wired by app_factory.
        self.trade_resolver: Callable[[str], dict | None] | None = None
        # Cancels a trade on the session that owns it (wired by app_factory).
        self.trade_canceler: Callable[[str, str], None] | None = None
        # Legacy single-session fallback (set directly in tests).
        self.trade_manager: TradeManager | None = None

    def _resolve_trade(self, trade_id: str) -> dict | None:
        """Find the trade dict for ``trade_id`` across all sessions."""
        if self.trade_resolver is not None:
            return self.trade_resolver(trade_id)
        if self.trade_manager is not None:
            for trade in self.trade_manager.open_trades:
                if trade.get("trade_id") == trade_id:
                    return trade
        return None

    def on_command_failed(self, command_type: str, trade_id: str, seq_num: int, reason: str) -> None:
        """Called by the gateway when a command is NACK'd or times out.

        Cleans up trades that never actually entered the market and logs/alarms
        on close or modify failures.
        """
        if command_type == "order_open":
            if self.trade_canceler is not None:
                self.trade_canceler(trade_id, f"ORDER_OPEN_FAILED:{reason}")
            elif self.trade_manager is not None:
                self.trade_manager.cancel_trade(trade_id, reason=f"ORDER_OPEN_FAILED:{reason}")
            else:
                self.logger.error(
                    f"order_open failed for {trade_id} (seq={seq_num}, reason={reason}) "
                    "but no trade_canceler is set; cannot roll back"
                )
        elif command_type == "order_close":
            self.logger.error(
                f"order_close failed for {trade_id} (seq={seq_num}, reason={reason}). "
                "Broker position may still be open."
            )
        elif command_type == "order_modify":
            self.logger.error(
                f"order_modify failed for {trade_id} (seq={seq_num}, reason={reason}). "
                "Stop loss/take profit may be out of sync with broker."
            )
        else:
            self.logger.warning(
                f"Command {command_type} failed for {trade_id} (seq={seq_num}, reason={reason})"
            )

    def _require_positive(self, value: float, name: str) -> float:
        """Validate that a numeric trade parameter is a positive finite number."""
        if value is None or not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"{name} must be a positive number, got {value!r}")
        return float(value)

    def on_trade_open(self, trade: dict) -> None:
        """
        Called when a new trade is opened.
        Sends open order command to the platform.

        Args:
            trade: Trade dict with keys:
                - trade_id: str
                - pair: str
                - type: "long" or "short"
                - entry: float
                - stop_loss: float
                - take_profit: float
                - risk: float (distance in points)
                - rr_ratio: float (optional)
        """
        try:
            trade_id = trade.get("trade_id")
            if not trade_id:
                raise ValueError("trade_id is required")

            direction = trade.get("type")
            if direction not in ("long", "short"):
                raise ValueError(f"Invalid trade direction: {direction!r}. Expected 'long' or 'short'.")

            entry = self._require_positive(
                trade.get("entry", trade.get("entry_price")),
                "entry_price",
            )
            sl = self._require_positive(
                trade.get("stop_loss", trade.get("sl")),
                "stop_loss",
            )
            tp = self._require_positive(
                trade.get("take_profit", trade.get("tp")),
                "take_profit",
            )

            # Calculate risk points from explicit risk or entry-to-stop distance.
            risk_points = trade.get("risk", abs(entry - sl))
            risk_points = self._require_positive(risk_points, "risk_points")

            rr_ratio = trade.get("rr_ratio", 5.0)
            rr_ratio = self._require_positive(rr_ratio, "rr_ratio")

            # Use the configured risk values from the account/strategy, not the
            # Python-estimated risk_dollars. In live mode NinjaTrader is the
            # source of truth for account balance and point value, so it should
            # size from the same configured input the user chose.
            risk_usd = trade.get("risk_usd")
            risk_pct = trade.get("risk_pct")

            if risk_usd is None and risk_pct is None:
                # Fall back to executor-level defaults if no per-trade config.
                risk_usd = self._risk_usd
                risk_pct = self._risk_pct

            # Prefer fixed-dollar risk when both are provided, matching
            # FinancialCalc.risk_budget precedence.
            if risk_usd is not None and risk_usd > 0:
                risk_pct = None
            elif risk_pct is not None and risk_pct > 0:
                risk_usd = None
            else:
                risk_usd = None
                risk_pct = None

            self._gateway.send_open_order(
                trade_id=trade_id,
                direction=direction,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                risk_points=risk_points,
                rr_ratio=rr_ratio,
                pair=trade.get("pair"),
                risk_usd=risk_usd,
                risk_pct=risk_pct,
                account=trade.get("account"),
                instrument=trade.get("instrument"),
            )
            self.logger.info(f"Sent open order for trade {trade_id}")

        except Exception as e:
            self.logger.error(f"Error sending open order: {e}")
            raise

    def on_trade_close(self, trade_id: str, _exit_price: float, account: str | None = None) -> None:
        """
        Called when a trade should be closed.
        Sends close order command to the platform.

        The instrument (and, unless given, the account) are resolved from the
        trade itself via ``trade_resolver`` — never from a gateway default.

        Args:
            trade_id: The trade ID to close
            exit_price: The exit price (for logging, platform determines actual fill)
            account: Optional target account name for multi-account routing.
        """
        try:
            trade = self._resolve_trade(trade_id)
            instrument = trade.get("instrument") if trade else None
            if account is None and trade:
                account = trade.get("account")
            self._gateway.send_close_order(
                trade_id=trade_id, reason="strategy", account=account, instrument=instrument,
            )
            self.logger.info(f"Sent close order for trade {trade_id}")

        except Exception as e:
            self.logger.error(f"Error sending close order: {e}")
            raise

    def on_sl_update(self, trade_id: str, new_sl: float, account: str | None = None) -> None:
        """
        Called when stop loss should be updated.
        Sends modify order command to the platform.

        The instrument (and, unless given, the account) are resolved from the
        trade itself via ``trade_resolver`` — never from a gateway default.

        Args:
            trade_id: The trade ID to modify
            new_sl: The new stop loss price
            account: Optional target account name for multi-account routing.
        """
        try:
            trade = self._resolve_trade(trade_id)
            instrument = trade.get("instrument") if trade else None
            if account is None and trade:
                account = trade.get("account")
            self._gateway.send_modify_order(
                trade_id=trade_id, stop_loss=new_sl, account=account, instrument=instrument,
            )
            self.logger.info(f"Sent SL update for trade {trade_id}: new_sl={new_sl}")

        except Exception as e:
            self.logger.error(f"Error sending modify order: {e}")
            raise


class MultiAccountExecutor(TradeExecutor):
    """
    Passthrough executor that routes commands to the underlying gateway executor.

    Every trade is now opened as an independent DB row with its own account.
    This executor only ensures close/modify commands include the correct account
    name by looking it up from the DB trade record.
    """

    def __init__(
        self,
        trade_manager,
        account_configs,
        gateway_executor: ZMQTradeExecutor,
        logger: ILogger,
        accounts_repo=None,
    ):
        self.trade_manager = trade_manager
        self.account_configs = account_configs
        self.gateway_executor = gateway_executor
        self.logger = logger
        self._accounts_repo = accounts_repo
        # Dedup recent close commands (same trade closed twice within window)
        self._last_close_time: dict[str, float] = {}
        self._close_dedup_seconds = 5.0

    def _account_for_trade(self, trade_id: str) -> str | None:
        """Look up the account name for a trade.

        The shared gateway executor resolves the trade across all active
        sessions (``trade_resolver``); the legacy per-executor trade_manager
        is only a fallback for tests that wire it directly.
        """
        try:
            trade = self.gateway_executor._resolve_trade(trade_id)
            if trade and trade.get("account"):
                return trade["account"]
        except Exception as e:
            self.logger.warning(f"MultiAccount: failed to resolve account for {trade_id}: {e}")
        if self.trade_manager is None:
            return None
        try:
            trade = self.trade_manager.trade_repository.get_trade(trade_id)
            if trade and trade.account:
                return trade.account
        except Exception as e:
            self.logger.warning(f"MultiAccount: failed to look up account for {trade_id}: {e}")
        return None

    def on_trade_open(self, trade: dict) -> None:
        if trade.get("account") is None:
            raise RuntimeError(
                "MultiAccountExecutor.on_trade_open received a trade with no account. "
                "Every trade must have an account assigned before reaching the executor."
            )
        self.gateway_executor.on_trade_open(trade)

    def on_trade_close(self, trade_id: str, exit_price: float) -> None:
        now = time.time()
        last_close = self._last_close_time.get(trade_id, 0)
        if now - last_close < self._close_dedup_seconds:
            self.logger.info(f"MultiAccount: skipping duplicate close for {trade_id} (last close {now - last_close:.2f}s ago)")
            return
        self._last_close_time[trade_id] = now

        account = self._account_for_trade(trade_id)
        self.gateway_executor.on_trade_close(trade_id, exit_price, account=account)
        self.logger.info(f"MultiAccount: sent close order for {trade_id} account={account}")

    def on_sl_update(self, trade_id: str, new_sl: float) -> None:
        account = self._account_for_trade(trade_id)
        self.gateway_executor.on_sl_update(trade_id, new_sl, account=account)
        self.logger.info(f"MultiAccount: updated SL for {trade_id} account={account}")


def create_zmq_executor(
    gateway: TradingGateway,
    *,
    logger: ILogger,
    risk_usd: float | None = None,
    risk_pct: float | None = None,
) -> ZMQTradeExecutor:
    """
    Convenience factory function to create a ZMQTradeExecutor.

    Usage:
        from src.infrastructure.gateway import TradingGateway, create_zmq_executor

        gateway = TradingGateway()
        gateway.start()

        executor = create_zmq_executor(gateway, logger=logger, risk_usd=500)

        trade_manager = TradeManager(
            ...,
            trade_executor=executor,
        )

    Args:
        gateway: The TradingGateway instance
        logger: Logger instance (required)
        risk_usd: Fixed dollar risk per trade
        risk_pct: Percentage of account to risk per trade

    Returns:
        Configured ZMQTradeExecutor
    """
    return ZMQTradeExecutor(
        gateway=gateway,
        logger=logger,
        risk_usd=risk_usd,
        risk_pct=risk_pct,
    )
