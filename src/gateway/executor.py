"""
ZeroMQ Trade Executor - Integrates with TradeManager.

This module provides a TradeExecutor implementation that uses the
ZeroMQ gateway to send trade commands to the platform.
"""

import time

from src.services.trade_executor import TradeExecutor
from src.utils.app_logger import ILogger

from .gateway import TradingGateway


class ZMQTradeExecutor(TradeExecutor):
    """
    TradeExecutor that sends commands via ZeroMQ.

    This integrates with the existing TradeManager and sends
    trade commands to NinjaTrader, MetaTrader, or other platforms
    through the ZeroMQ gateway.

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
            # Calculate risk points
            entry = trade.get("entry", trade.get("entry_price", 0))
            sl = trade.get("stop_loss", trade.get("sl", 0))
            tp = trade.get("take_profit", trade.get("tp", 0))
            risk_points = trade.get("risk", abs(entry - sl))

            # Get RR ratio (default 5.0 for your system)
            rr_ratio = trade.get("rr_ratio", 5.0)

            # Use per-account risk values if present in trade dict, otherwise fall back to global defaults
            risk_usd = trade.get("risk_dollars") if trade.get("risk_dollars") is not None else self._risk_usd
            risk_pct = trade.get("risk_pct") if trade.get("risk_pct") is not None else self._risk_pct

            self._gateway.send_open_order(
                trade_id=trade["trade_id"],
                direction=trade["type"],
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                risk_points=risk_points,
                rr_ratio=rr_ratio,
                pair=trade.get("pair"),
                risk_usd=risk_usd,
                risk_pct=risk_pct,
                account=trade.get("account"),
            )
            self.logger.info(f"Sent open order for trade {trade['trade_id']}")

        except Exception as e:
            self.logger.error(f"Error sending open order: {e}")
            raise

    def on_trade_close(self, trade_id: str, _exit_price: float) -> None:
        """
        Called when a trade should be closed.
        Sends close order command to the platform.

        Args:
            trade_id: The trade ID to close
            exit_price: The exit price (for logging, platform determines actual fill)
        """
        try:
            self._gateway.send_close_order(trade_id=trade_id, reason="strategy")
            self.logger.info(f"Sent close order for trade {trade_id}")

        except Exception as e:
            self.logger.error(f"Error sending close order: {e}")
            raise

    def on_sl_update(self, trade_id: str, new_sl: float) -> None:
        """
        Called when stop loss should be updated.
        Sends modify order command to the platform.

        Args:
            trade_id: The trade ID to modify
            new_sl: The new stop loss price
        """
        try:
            self._gateway.send_modify_order(trade_id=trade_id, stop_loss=new_sl)
            self.logger.info(f"Sent SL update for trade {trade_id}: new_sl={new_sl}")

        except Exception as e:
            self.logger.error(f"Error sending modify order: {e}")
            raise


class MultiAccountExecutor(TradeExecutor):
    """
    Expands a single Signal into N per-account trades and broadcasts
    commands through a single ZMQTradeExecutor.

    Each account trade gets its own DB record, trade_id, and risk settings.
    The strategy works with Signals (one per entry decision); this executor
    handles the expansion to account trades and routes fills back.
    """

    def __init__(
        self,
        trade_manager,
        account_configs,
        gateway_executor: ZMQTradeExecutor,
        logger: ILogger,
    ):
        self.trade_manager = trade_manager
        self.account_configs = account_configs
        self.gateway_executor = gateway_executor
        self.logger = logger
        self.signal_to_accounts: dict[str, list[str]] = {}
        self.account_to_signal: dict[str, str] = {}

    def on_trade_open(self, signal_trade: dict) -> None:
        signal_id = signal_trade["trade_id"]
        account_trade_ids: list[str] = []

        for acct in self.account_configs:
            try:
                # Recalculate take_profit per account using account-specific rr_ratio
                entry = signal_trade["entry"]
                sl = signal_trade["stop_loss"]
                risk = signal_trade["risk"]
                rr = acct.rr_ratio if acct.rr_ratio is not None else signal_trade.get("rr_ratio", 5.0)
                if signal_trade["type"] == "long":
                    tp = entry + (rr * risk)
                else:
                    tp = entry - (rr * risk)

                account_trade = self.trade_manager.open_trade(
                    pair=signal_trade.get("pair", signal_trade.get("pair")),
                    trade_type=signal_trade["type"],
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    risk=risk,
                    entry_time=signal_trade["entry_time"],
                    rr_ratio=rr,
                    source=signal_trade.get("source"),
                    account=acct.name,
                    signal_id=signal_id,
                    risk_per_trade_override=acct.risk_usd,
                    risk_pct_per_trade_override=acct.risk_pct,
                )
                account_trade_ids.append(account_trade["trade_id"])
                self.gateway_executor.on_trade_open(account_trade)
            except Exception as e:
                self.logger.error(f"MultiAccount: failed to open trade for account {acct.name} (signal {signal_id}): {e}")

        self.signal_to_accounts[signal_id] = account_trade_ids
        for aid in account_trade_ids:
            self.account_to_signal[aid] = signal_id

        self.logger.info(f"MultiAccount: expanded signal {signal_id} into {len(account_trade_ids)} account trades")

    def on_trade_close(self, trade_id: str, exit_price: float) -> None:
        for aid in self._resolve_ids(trade_id):
            acct_name = self._account_for_trade(aid)
            # Send close command to NT (tagged with account)
            self.gateway_executor._gateway.send_close_order(
                trade_id=aid, reason="strategy", account=acct_name
            )
            self.logger.info(f"MultiAccount: sent close order for account trade {aid} (signal {trade_id})")

    def on_sl_update(self, trade_id: str, new_sl: float) -> None:
        for aid in self._resolve_ids(trade_id):
            acct_name = self._account_for_trade(aid)
            self.gateway_executor._gateway.send_modify_order(
                trade_id=aid, stop_loss=new_sl, account=acct_name
            )
            self.logger.info(f"MultiAccount: updated SL for account trade {aid} account={acct_name}")

    def get_signal_id(self, account_trade_id: str) -> str | None:
        return self.account_to_signal.get(account_trade_id)

    def all_account_trades_closed(self, signal_id: str) -> bool:
        account_ids = self.signal_to_accounts.get(signal_id, [])
        open_ids = {t["trade_id"] for t in self.trade_manager.open_trades}
        return all(aid not in open_ids for aid in account_ids)

    def _resolve_ids(self, trade_id: str) -> list[str]:
        """Return account trade IDs. If trade_id is a signal, expand it."""
        if trade_id in self.signal_to_accounts:
            return list(self.signal_to_accounts[trade_id])
        return [trade_id]

    def _account_for_trade(self, account_trade_id: str) -> str:
        """Look up the account name for an account trade by checking DB."""
        trade = self.trade_manager.trade_repository.get_trade(account_trade_id)
        if trade and trade.account:
            return trade.account
        # Fallback: try to infer from config names
        for acct in self.account_configs:
            if acct.name in account_trade_id:
                return acct.name
        return self.account_configs[0].name if self.account_configs else ""


def create_zmq_executor(
    gateway: TradingGateway,
    *,
    risk_usd: float | None = None,
    risk_pct: float | None = None,
    logger: ILogger | None = None,
) -> ZMQTradeExecutor:
    """
    Convenience factory function to create a ZMQTradeExecutor.

    Usage:
        from src.gateway import TradingGateway, create_zmq_executor

        gateway = TradingGateway()
        gateway.start()

        executor = create_zmq_executor(gateway, risk_usd=500)

        trade_manager = TradeManager(
            ...,
            trade_executor=executor,
        )

    Args:
        gateway: The TradingGateway instance
        risk_usd: Fixed dollar risk per trade
        risk_pct: Percentage of account to risk per trade

    Returns:
        Configured ZMQTradeExecutor
    """
    return ZMQTradeExecutor(
        gateway=gateway,
        risk_usd=risk_usd,
        risk_pct=risk_pct,
        logger=logger,
    )
