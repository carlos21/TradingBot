"""Single source of truth for opening trades.

Extracted from TradeManager.open_trade to separate the orchestration
of contract calculation, DB persistence, event emission, and logging
from the in-memory state tracking that TradeManager still owns.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from src.application.ports import EventPublisher
from src.domain.repositories import TradeRepository
from src.financial_calc import FinancialCalc
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
    instrument: str | None


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
        instrument: str | None = None,
        live_mode: bool = False,
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
        self._instrument = instrument
        self._live_mode = live_mode

    def _get_current_risk(self, account_name: str | None = None) -> tuple[float | None, float | None]:
        """Return (risk_usd, risk_pct) for the named account, else constructor defaults.

        When ``account_name`` is provided, look up that exact account's configured
        risk. If the account is unknown or no name is given, fall back to the
        constructor-level defaults only — never silently use another account's
        risk setting.
        """
        if self._accounts_repo is not None and account_name is not None:
            try:
                account = self._accounts_repo.get_account(account_name)
                if account is not None:
                    return account.risk_usd, account.risk_pct
            except Exception:
                pass
        return self._risk_per_trade, self._risk_pct_per_trade

    def _effective_risk_config(
        self,
        risk_per_trade_override: float | None = None,
        risk_pct_per_trade_override: float | None = None,
        account_name: str | None = None,
    ) -> tuple[float | None, float | None]:
        """Return the configured risk inputs with override > account > default precedence.

        This is the source of truth for what NinjaTrader receives. In live mode
        the broker sizes from these values, not from Python's dollar estimate.
        """
        if risk_per_trade_override is not None or risk_pct_per_trade_override is not None:
            return risk_per_trade_override, risk_pct_per_trade_override
        return self._get_current_risk(account_name)

    def _calc_contracts(
        self,
        risk_per_contract: float,
        risk_per_trade_override: float | None = None,
        risk_pct_per_trade_override: float | None = None,
        account_name: str | None = None,
        risk_usd: float | None = None,
        risk_pct: float | None = None,
    ) -> float:
        """Calculate number of contracts/lots, matching NinjaTrader's logic."""
        if risk_usd is None and risk_pct is None:
            risk_usd, risk_pct = self._effective_risk_config(
                risk_per_trade_override, risk_pct_per_trade_override, account_name
            )
        risk_budget = FinancialCalc.risk_budget(
            self._account_balance,
            risk_usd,
            risk_pct,
        )

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
        params: dict | None = None,
    ) -> OpenResult:
        """Open a new trade with precomputed parameters."""
        # Preserve the configured risk inputs so the executor can forward the
        # same source of truth to the broker (e.g., NinjaTrader sizes from the
        # configured risk_usd or risk_pct, not from Python's dollar estimate).
        risk_usd, risk_pct = self._effective_risk_config(
            risk_per_trade_override, risk_pct_per_trade_override, account_name=account
        )

        # In backtest/simulation mode Python is the source of truth for sizing,
        # so an explicit account balance is required when using %-based risk.
        if not self._live_mode and risk_pct is not None and self._account_balance <= 0:
            raise ValueError(
                "Backtest/simulation mode with risk_pct requires a positive account_balance. "
                "Set it before opening trades."
            )

        risk_per_contract = risk * self._point_value
        contracts = self._calc_contracts(
            risk_per_contract,
            risk_usd=risk_usd,
            risk_pct=risk_pct,
        )
        risk_dollars, calculated_risk_pct = FinancialCalc.risk_fields(
            risk, contracts, self._point_value, self._account_balance
        )

        # Persist open trade
        td = self._repo.insert_trade(
            pair=pair,
            trade_type=trade_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk=risk,
            entry_time=datetime.fromtimestamp(entry_time, tz=timezone.utc),
            params=params or {},
            risk_dollars=risk_dollars,
            risk_pct=calculated_risk_pct,
            account_balance=self._account_balance if self._account_balance > 0 else None,
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
            risk_pct=calculated_risk_pct,
            contracts=contracts,
            entry_time=entry_time,
            account=account,
            signal_id=signal_id,
            instrument=self._instrument,
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
            # In live mode the broker is the source of truth for account balance
            # and point value, so forward the *configured* risk inputs rather than
            # Python's dollar estimate.
            'risk_usd': risk_usd,
            'risk_pct': risk_pct,
            'contracts': contracts,
            'account': account,
            'source': source,
            'instrument': self._instrument,
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
            params = params or {}
            self._publisher.emit('trade_open', {
                'trade_id': result.trade_id,
                'pair': pair,
                'type': trade_type,
                'entry': entry_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'risk': risk,
                'risk_dollars': risk_dollars,
                'risk_pct': calculated_risk_pct,
                'contracts': contracts,
                'entry_time': entry_time,
                'rr_ratio': rr_ratio,
                'account': account,
                'signal_id': signal_id,
                'line_level': params.get('line_level'),
                'is_reentry': params.get('is_reentry', False),
                'reentry_attempt': params.get('reentry_attempt', 0),
            })

        return result

    def update_account_balance(self, new_balance: float) -> None:
        """Update the balance used for contract calculations."""
        self._account_balance = new_balance
