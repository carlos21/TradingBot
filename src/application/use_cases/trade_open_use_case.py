"""Single source of truth for opening trades.

Extracted from TradeManager.open_trade to separate the orchestration
of contract calculation, DB persistence, event emission, and logging
from the in-memory state tracking that TradeManager still owns.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from src.application.ports import EventPublisher
from src.financial_calc import FinancialCalc
from src.domain.repositories import TradeRepository
from src.services.trade_executor import TradeExecutor
from src.utils.app_logger import ILogger


@dataclass(frozen=True)
class OpenResult:
    """Result of a successful trade open."""
    trade_id: str
    pair: str
    trade_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk: float
    risk_dollars: float | None
    risk_pct: float | None
    contracts: float
    entry_time: float
    account: str | None
    signal_id: str | None


class TradeOpenUseCase:
    """Orchestrates the full trade open lifecycle."""

    def __init__(
        self,
        trade_repository: TradeRepository,
        trade_executor: TradeExecutor,
        event_publisher: EventPublisher | None,
        logger: ILogger | None,
        trade_logger=None,
        point_value: float = 2.0,
        account_balance: float = 0.0,
        risk_per_trade: float | None = None,
        risk_pct_per_trade: float | None = None,
        use_fractional_lots: bool = False,
        accounts_repo=None,
    ):
        self._repo = trade_repository
        self._executor = trade_executor
        self._publisher = event_publisher
        self._logger = logger
        self._trade_logger = trade_logger
        self._point_value = point_value
        self._account_balance = account_balance
        self._risk_per_trade = risk_per_trade
        self._risk_pct_per_trade = risk_pct_per_trade
        self._use_fractional_lots = use_fractional_lots
        self._accounts_repo = accounts_repo

    def _get_current_risk(self) -> tuple[float | None, float | None]:
        """Return (risk_per_trade, risk_pct_per_trade) from DB if available, else fallbacks."""
        if self._accounts_repo is not None:
            try:
                accounts = self._accounts_repo.list_accounts()
                if accounts:
                    first = accounts[0]
                    return first.risk_usd, first.risk_pct
            except Exception:
                pass
        return self._risk_per_trade, self._risk_pct_per_trade

    def _calc_contracts(self, risk_per_contract: float,
                        risk_per_trade_override: float | None = None,
                        risk_pct_per_trade_override: float | None = None) -> float:
        """Calculate number of contracts/lots, matching NinjaTrader's logic."""
        if risk_per_contract <= 0:
            return 1.0 if not self._use_fractional_lots else 0.01

        if risk_per_trade_override is not None or risk_pct_per_trade_override is not None:
            risk_budget = FinancialCalc.risk_budget(
                self._account_balance,
                risk_per_trade_override,
                risk_pct_per_trade_override,
            )
        else:
            risk_usd, risk_pct = self._get_current_risk()
            risk_budget = FinancialCalc.risk_budget(
                self._account_balance,
                risk_usd,
                risk_pct,
            )

        if risk_budget <= 0:
            return 1.0 if not self._use_fractional_lots else 0.01
        if self._use_fractional_lots:
            return FinancialCalc.lots(risk_budget, risk_per_contract)
        return FinancialCalc.contracts(risk_budget, risk_per_contract)

    def execute(
        self,
        pair: str,
        trade_type: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk: float,
        entry_time: float,
        rr_ratio: float = 5.0,
        source: str | None = None,
        account: str | None = None,
        signal_id: str | None = None,
        risk_per_trade_override: float | None = None,
        risk_pct_per_trade_override: float | None = None,
        trade_id: str | None = None,
    ) -> OpenResult:
        """Open a new trade with precomputed parameters."""
        risk_per_contract = risk * self._point_value
        contracts = self._calc_contracts(
            risk_per_contract,
            risk_per_trade_override,
            risk_pct_per_trade_override,
        )
        risk_dollars = risk_per_contract * contracts
        risk_pct = (risk_dollars / self._account_balance * 100) if self._account_balance > 0 else None

        # Persist open trade
        td = self._repo.insert_trade(
            pair=pair,
            trade_type=trade_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk=risk,
            entry_time=datetime.fromtimestamp(entry_time, tz=timezone.utc),
            params={},
            risk_dollars=risk_dollars,
            risk_pct=risk_pct,
            contracts=contracts,
            source=source,
            account=account,
            signal_id=signal_id,
            trade_id=trade_id,
        )

        result = OpenResult(
            trade_id=td.trade_id,
            pair=pair,
            trade_type=trade_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk=risk,
            risk_dollars=risk_dollars,
            risk_pct=risk_pct,
            contracts=contracts,
            entry_time=entry_time,
            account=account,
            signal_id=signal_id,
        )

        # Send to broker/platform
        trade_for_executor = {
            'trade_id': td.trade_id,
            'pair': pair,
            'type': trade_type,
            'entry': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'risk': risk,
            'rr_ratio': rr_ratio,
            'entry_time': entry_time,
            'risk_dollars': risk_dollars,
            'risk_pct': risk_pct,
            'contracts': contracts,
            'account': account,
        }
        try:
            self._executor.on_trade_open(trade_for_executor)
        except Exception as e:
            if self._logger:
                self._logger.error(f"[TradeOpenUseCase] Executor failed for {td.trade_id}: {e}")
            # Rollback DB insert to prevent zombie trade
            try:
                self._repo.close_trade(
                    trade_id=td.trade_id,
                    exit_price=entry_price,
                    exit_time=datetime.fromtimestamp(entry_time, tz=timezone.utc),
                    result=0.0,
                    result_type="CANCELLED",
                )
            except Exception as db_err:
                if self._logger:
                    self._logger.error(f"[TradeOpenUseCase] Failed to rollback DB for {td.trade_id}: {db_err}")
            raise

        if self._logger:
            self._logger.info(f"[TradeOpenUseCase] Registered OPEN trade {result.trade_id} @ {entry_time}")

        if self._publisher:
            self._publisher.emit('trade_open', {
                'trade_id': result.trade_id,
                'pair': pair,
                'type': trade_type,
                'entry': entry_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'risk': risk,
                'risk_dollars': risk_dollars,
                'risk_pct': risk_pct,
                'contracts': contracts,
                'entry_time': entry_time,
                'rr_ratio': rr_ratio,
                'account': account,
                'signal_id': signal_id,
            })

        return result

    def update_account_balance(self, new_balance: float) -> None:
        """Update the balance used for contract calculations."""
        self._account_balance = new_balance
