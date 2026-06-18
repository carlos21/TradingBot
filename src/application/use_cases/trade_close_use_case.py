"""Single source of truth for ALL trade closing operations.

This use case eliminates the ~200 lines of close-logic duplication that
previously existed across TradeManager._check_sl_tp,
TradeManager._check_session_end_close, TradeManager.close_trade,
TradeManager.close_remaining_trades_at_stream_end, and
TradeManager.handle_broker_fill.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.application.ports import EventPublisher
from src.domain.repositories import TradeRepository
from src.domain.result_type_classifier import ClassificationContext, DefaultResultTypeClassifier, ResultTypeClassifier
from src.domain.types import Direction
from src.financial_calc import FinancialCalc
from src.services.trade_executor import TradeExecutor
from src.utils.app_logger import ILogger


class ExecutorCloseError(Exception):
    """Raised when the trade executor fails to close a trade."""


@dataclass(frozen=True)
class CloseResult:
    """Result of a successful trade close."""
    trade_id: str
    pair: str
    trade_type: str
    exit_price: float
    exit_time: float
    result: float
    result_type: Literal["BE", "SL", "TP", "SP", "CLOSE"]
    fees: float
    pnl_usd: float


class TradeCloseUseCase:
    """Orchestrates the full trade close lifecycle.

    Responsibilities:
    1. Calculate close metrics (R, fees, PnL) via FinancialCalc
    2. Call trade executor (e.g., ZMQ close command)
    3. Persist close to database
    4. Log lifecycle events
    5. Emit domain events
    6. Capture analytics

    NOT responsible for:
    - Updating in-memory trade lists (caller does that)
    - Updating account balance (caller does that)
    - Session timing decisions (caller does that)
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

    def execute(
        self,
        trade: dict,
        exit_price: float,
        exit_time: float,
        result_type_override: str | None = None,
        log_event: str = "CLOSE",
        log_message: str | None = None,
        analytics_event: str = "CLOSE",
        raise_on_db_error: bool = True,
        extreme_excursion: float | None = None,
        skip_executor: bool = False,
        broker_pnl_usd: float | None = None,
        broker_fees: float | None = None,
    ) -> CloseResult | None:
        """Execute the full close flow for a single trade.

        Returns CloseResult on success, None if executor fails (trade stays open).
        If raise_on_db_error is False, DB errors are caught and logged (trade stays open).
        If raise_on_db_error is True, DB errors are propagated.
        """
        trade_id = trade['trade_id']

        # 1. Calculate close metrics
        risk = trade.get('risk') or 0
        if risk <= 0:
            risk = 1.0

        sl = trade.get('stop_loss', trade.get('sl', trade.get('orig_sl')))
        tp = trade.get('take_profit', trade.get('tp'))
        entry = trade.get('entry', trade.get('entry_price'))
        contracts = trade.get('contracts') or 1

        # Use broker-reported PnL/fees when available (source of truth from fills).
        if broker_pnl_usd is not None:
            pnl_usd = broker_pnl_usd
            fees = broker_fees if broker_fees is not None else 0.0
            # Recompute R-multiple from the broker PnL so analytics stay consistent.
            result = FinancialCalc.r_multiple_from_pnl(
                pnl_usd=pnl_usd,
                fees=fees,
                risk_points=risk,
                contracts=contracts,
                point_value=self._point_value,
            )
        else:
            result, fees, pnl_usd, _ = FinancialCalc.calculate_close_metrics(
                direction=Direction.from_string(trade['type']),
                entry_price=entry,
                exit_price=exit_price,
                stop_loss=sl,
                take_profit=tp,
                risk_points=risk,
                contracts=contracts,
                point_value=self._point_value,
                fee_per_rt=self._fee_per_rt,
            )

        # Spread adjustment (only when Python is calculating; broker value already includes it)
        if self._broker_spread > 0 and broker_pnl_usd is None:
            spread_cost = contracts * self._broker_spread * self._point_value
            pnl_usd -= spread_cost
            fees += spread_cost

        result_type = self._classifier.classify(
            ClassificationContext(
                direction=Direction.from_string(trade['type']),
                entry_price=entry,
                exit_price=exit_price,
                stop_loss=sl,
                take_profit=tp,
                broker_result_type=result_type_override,
            )
        ).value

        # 2. Call executor FIRST (safety: don't persist if ZMQ fails)
        # Skip when broker already closed the position (broker fill) to avoid
        # sending a redundant close command back to the platform.
        if not skip_executor:
            try:
                self._executor.on_trade_close(trade_id, exit_price)
            except Exception as e:
                if self._logger:
                    self._logger.error(f"[TradeCloseUseCase] Executor failed for {trade_id}: {e}")
                if self._trade_logger:
                    self._trade_logger.log(trade_id, "ERROR", f"Executor close failed: {e}")
                raise

        # 3. Persist to DB
        close_db_time = (
            datetime.fromtimestamp(exit_time, tz=__import__('zoneinfo').ZoneInfo('UTC'))
            if isinstance(exit_time, (int, float))
            else exit_time
        )
        try:
            self._repo.close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_time=close_db_time,
                result=result,
                result_type=result_type,
                fees=fees,
                pnl_usd=pnl_usd,
            )
        except Exception as e:
            if self._logger:
                self._logger.error(f"[TradeCloseUseCase] DB error closing trade {trade_id}: {e}")
            if self._trade_logger:
                self._trade_logger.log(trade_id, "ERROR", f"DB close failed: {e}")
            if raise_on_db_error:
                raise
            return None

        # 4. Log
        if self._trade_logger:
            msg = log_message or f"Exit={exit_price:.2f} Result={result:.2f}R"
            self._trade_logger.log(trade_id, log_event, msg)
            self._trade_logger.log(trade_id, "CLOSE", "Persisted to DB")

        if self._logger:
            self._logger.info(
                f"[TradeCloseUseCase] Closed {trade_id} @ {exit_price} "
                f"(Result: {result:.2f}R, Type: {result_type})"
            )

        # 5. Emit event (enriched with strategy state fields)
        payload = {
            'trade_id': trade_id,
            'pair': trade.get('pair', ''),
            'type': trade['type'],
            'exit_price': exit_price,
            'exit_time': exit_time if isinstance(exit_time, (int, float)) else close_db_time.timestamp(),
            'result': result,
            'result_type': result_type,
            'fees': fees,
            'pnl_usd': pnl_usd,
            'line_level': trade.get('line_level'),
            'is_reentry': trade.get('is_reentry', False),
            'is_phantom': trade.get('is_phantom', False),
            'extreme_excursion': extreme_excursion if extreme_excursion is not None else exit_price,
            'signal_id': trade.get('signal_id'),
        }
        if self._publisher:
            self._publisher.emit('trade_close', payload)

        return CloseResult(
            trade_id=trade_id,
            pair=trade.get('pair', ''),
            trade_type=trade['type'],
            exit_price=exit_price,
            exit_time=exit_time if isinstance(exit_time, (int, float)) else close_db_time.timestamp(),
            result=result,
            result_type=result_type,
            fees=fees,
            pnl_usd=pnl_usd,
        )
