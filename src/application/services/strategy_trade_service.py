"""Trade persistence and emission service for strategies.

Encapsulates the side effects of opening and closing trades from within
strategy classes, decoupling strategy logic from repository/executor/SocketIO
calls.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from src.application.ports import EventPublisher
from src.application.use_cases.trade_open_use_case import TradeOpenUseCase
from src.domain.repositories import TradeRepository
from src.domain.result_type_classifier import (
    ClassificationContext,
    DefaultResultTypeClassifier,
    ResultTypeClassifier,
)
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
        account_balance: float = 0.0,
        risk_per_trade: float | None = None,
        risk_pct_per_trade: float | None = None,
        use_fractional_lots: bool = False,
        accounts_repo=None,
        instrument: str | None = None,
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
        self._open_use_case = None
        self._account_balance = account_balance
        self._risk_per_trade = risk_per_trade
        self._risk_pct_per_trade = risk_pct_per_trade
        self._use_fractional_lots = use_fractional_lots
        self._accounts_repo = accounts_repo
        self._instrument = instrument

    def _get_open_use_case(self) -> TradeOpenUseCase:
        """Lazy init so account balance can be updated before first use."""
        if self._open_use_case is None:
            self._open_use_case = TradeOpenUseCase(
                trade_repository=self._repo,
                trade_executor=self._executor,
                event_publisher=self._publisher,
                logger=self._logger,
                trade_logger=self._trade_logger,
                point_value=self._point_value,
                account_balance=self._account_balance,
                risk_per_trade=self._risk_per_trade,
                risk_pct_per_trade=self._risk_pct_per_trade,
                use_fractional_lots=self._use_fractional_lots,
                accounts_repo=self._accounts_repo,
                instrument=self._instrument,
            )
        return self._open_use_case

    def update_account_balance(self, account_balance: float) -> None:
        """Update balance used for sizing and propagate to the open use case."""
        self._account_balance = account_balance
        if self._open_use_case is not None:
            self._open_use_case.update_account_balance(account_balance)

    def _make_trade_dict(self, base_trade: dict[str, Any], result: Any) -> dict[str, Any]:
        """Build a strategy-level trade dict from an OpenResult."""
        return {
            "trade_id": result.trade_id,
            "pair": result.pair,
            "type": result.trade_type,
            "entry": result.entry_price,
            "stop_loss": result.stop_loss,
            "take_profit": result.take_profit,
            "risk": result.risk,
            "risk_dollars": result.risk_dollars,
            "risk_pct": result.risk_pct,
            "contracts": result.contracts,
            "entry_time": result.entry_time,
            "rr_ratio": result.rr_ratio if hasattr(result, "rr_ratio") else base_trade.get("rr_ratio", 5.0),
            "account": result.account,
            "line_level": base_trade.get("line_level"),
            "is_reentry": base_trade.get("is_reentry", False),
            "reentry_attempt": base_trade.get("reentry_attempt", 0),
            "status": "open",
        }

    def open_trade(
        self,
        trade: dict[str, Any],
        is_warmup: bool,
        account_configs: list,
        account_balance: float,
    ) -> list[dict[str, Any]]:
        """Open independent trade(s) and return the opened trade dict(s).

        One trade is opened per configured account. With no account configs,
        a single strategy trade is opened. Warmup/phantom modes return empty list
        or the phantom dict without persisting.
        """
        if is_warmup:
            return []

        if trade.get("is_phantom"):
            trade["trade_id"] = f"phantom-{trade['entry_time']}"
            if self._logger:
                self._logger.info(f"[StrategyTradeService] Phantom trade opened @ {trade['entry']:.2f}")
            return [trade]

        opened: list[dict[str, Any]] = []
        use_case = self._get_open_use_case()
        use_case.update_account_balance(account_balance)
        base_rr = trade.get("rr_ratio", 5.0)
        group_signal_id = str(uuid.uuid4()) if len(account_configs or []) > 1 else None

        configs = account_configs or [None]
        for acct in configs:
            rr = base_rr
            account_name = None
            risk_usd_override = None
            risk_pct_override = None
            if acct is not None:
                account_name = acct.name
                rr = acct.rr_ratio if acct.rr_ratio is not None else base_rr
                risk_usd_override = acct.risk_usd
                risk_pct_override = acct.risk_pct

            # Recalculate take-profit for this account's RR ratio
            entry = trade["entry"]
            risk_pts = trade["risk"]
            if trade["type"] == "long":
                account_take_profit = entry + (rr * risk_pts)
            else:
                account_take_profit = entry - (rr * risk_pts)

            params = {
                "line_level": trade.get("line_level"),
                "is_reentry": trade.get("is_reentry", False),
                "reentry_attempt": trade.get("reentry_attempt", 0),
            }

            result = use_case.execute(
                pair=trade["pair"],
                trade_type=trade["type"],
                entry_price=entry,
                stop_loss=trade["stop_loss"],
                take_profit=account_take_profit,
                risk=risk_pts,
                entry_time=trade["entry_time"],
                rr_ratio=rr,
                source=trade.get("source") or "strategy",
                account=account_name,
                signal_id=group_signal_id,
                risk_per_trade_override=risk_usd_override,
                risk_pct_per_trade_override=risk_pct_override,
                params=params,
            )
            opened_trade = self._make_trade_dict(trade, result)
            opened_trade["rr_ratio"] = rr
            opened_trade["signal_id"] = group_signal_id
            opened.append(opened_trade)

        if self._trade_logger:
            for opened_trade in opened:
                self._trade_logger.log(opened_trade["trade_id"], "CMD_SENT", "place_order → NinjaTrader")

        return opened

    def close_trade(self, trade: dict[str, Any]) -> None:
        """Close a trade: calculate metrics, emit event, persist to DB."""
        contracts = trade.get("contracts") or 1
        risk_pts = trade.get("risk", 0) or 1.0
        result_r, t_fees, t_pnl_usd, _ = FinancialCalc.calculate_close_metrics(
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
        trade["result"] = result_r
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
            result=result_r,
            result_type=result_type,
            fees=t_fees,
            pnl_usd=t_pnl_usd,
        )

    @staticmethod
    def _ts_to_dt(ts: float) -> datetime:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
