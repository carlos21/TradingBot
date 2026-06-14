"""Trade persistence and emission service for strategies.

Encapsulates the side effects of opening and closing trades from within
strategy classes, decoupling strategy logic from repository/executor/SocketIO
calls.
"""

from datetime import datetime, timezone
from typing import Any

from src.application.ports import EventPublisher
from src.domain.repositories import TradeRepository
from src.domain.result_type_classifier import ClassificationContext, DefaultResultTypeClassifier, ResultTypeClassifier
from src.domain.types import Direction
from src.financial_calc import FinancialCalc
from src.services.trade_executor import TradeExecutor
from src.utils.app_logger import ILogger


class StrategyTradeService:
    """Handles trade persistence, executor commands, and event emission.

    This service is used by strategies to open and close trades without
    directly touching repositories, executors, or SocketIO.
    """

    def __init__(
        self,
        trade_repository: TradeRepository,
        trade_executor: TradeExecutor,
        event_publisher: EventPublisher | None,
        logger: ILogger | None,
        trade_logger=None,
        point_value: float = 2.0,
        fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
        broker_spread: float = 0.0,
        result_type_classifier: ResultTypeClassifier | None = None,
    ):
        self._repo = trade_repository
        self._executor = trade_executor
        self._publisher = event_publisher
        self._logger = logger
        self._trade_logger = trade_logger
        self._point_value = point_value
        self._fee_per_rt = fee_per_rt
        self._broker_spread = broker_spread
        self._classifier = result_type_classifier or DefaultResultTypeClassifier()

    def open_trade(
        self,
        trade: dict[str, Any],
        is_warmup: bool,
        account_configs: list,
    ) -> str | None:
        """Persist a new trade and return its generated trade_id.

        If warmup or phantom, no DB persistence occurs.
        """
        if is_warmup:
            return None

        if trade.get("is_phantom"):
            trade["trade_id"] = f"phantom-{trade['entry_time']}"
            if self._logger:
                self._logger.info(f"[StrategyTradeService] Phantom trade opened @ {trade['entry']:.2f}")
            return trade["trade_id"]

        source = "signal" if account_configs else "strategy"
        td = self._repo.insert_trade(
            pair=trade["pair"],
            trade_type=trade["type"],
            entry_price=trade["entry"],
            stop_loss=trade["stop_loss"],
            take_profit=trade["take_profit"],
            risk=trade["risk"],
            entry_time=self._ts_to_dt(trade["entry_time"]),
            params={
                "line_level": trade.get("line_level"),
                "is_reentry": trade.get("is_reentry", False),
            },
            source=source,
            risk_dollars=trade.get("risk_dollars"),
            risk_pct=trade.get("risk_pct"),
            contracts=trade.get("contracts"),
        )
        trade["trade_id"] = td.trade_id
        if account_configs:
            trade["is_signal"] = True

        if self._publisher:
            self._publisher.emit("trade_open", {**trade})

        self._executor.on_trade_open(trade)

        if self._trade_logger:
            log_msg = "expand_signal → MultiAccountExecutor" if account_configs else "place_order → NinjaTrader"
            self._trade_logger.log(trade["trade_id"], "CMD_SENT", log_msg)

        return td.trade_id

    def close_trade(self, trade: dict[str, Any]) -> None:
        """Close a trade: calculate metrics, emit event, persist to DB."""
        contracts = trade.get("contracts") or 1
        risk_pts = trade.get("risk", 0) or 1.0
        _, t_fees, t_pnl_usd, _ = FinancialCalc.calculate_close_metrics(
            direction=Direction.from_string(trade["type"]),
            entry_price=trade["entry"],
            exit_price=trade["exit_price"],
            stop_loss=trade["stop_loss"],
            take_profit=trade["take_profit"],
            risk_points=risk_pts,
            contracts=contracts,
            point_value=self._point_value,
            fee_per_rt=self._fee_per_rt,
        )
        if self._broker_spread > 0:
            spread_cost = contracts * self._broker_spread * self._point_value
            t_pnl_usd -= spread_cost
            t_fees += spread_cost
        trade["fees"] = t_fees
        trade["pnl_usd"] = t_pnl_usd

        result_type = trade.get("result_type")
        if not result_type:
            result_type = self._classifier.classify(
                ClassificationContext(
                    direction=Direction.from_string(trade["type"]),
                    entry_price=trade.get("entry", 0.0),
                    exit_price=trade.get("exit_price", 0.0),
                    stop_loss=trade.get("stop_loss"),
                    take_profit=trade.get("take_profit"),
                )
            ).value
            trade["result_type"] = result_type

        if self._publisher:
            self._publisher.emit("trade_close", trade)

        self._repo.close_trade(
            trade_id=trade["trade_id"],
            exit_price=trade["exit_price"],
            exit_time=self._ts_to_dt(trade["exit_time"]),
            result=trade["result"],
            result_type=result_type,
            fees=t_fees,
            pnl_usd=t_pnl_usd,
        )

    @staticmethod
    def _ts_to_dt(ts: float) -> datetime:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
