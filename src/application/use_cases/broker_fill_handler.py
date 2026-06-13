"""Handle broker entry and exit fills.

Extracted from TradeManager.handle_broker_entry_fill and
TradeManager.handle_broker_fill to decouple broker-specific logic
from the main trade manager.
"""

from src.analytics import AnalyticsReporter
from src.application.ports import EventPublisher
from src.domain.repositories import TradeRepository
from src.financial_calc import FinancialCalc
from src.utils.app_logger import ILogger


class BrokerFillHandler:
    """Handles entry and exit fill notifications from the broker."""

    def __init__(
        self,
        trade_repository: TradeRepository,
        event_publisher: EventPublisher | None,
        logger: ILogger | None,
        trade_logger=None,
        analytics: AnalyticsReporter | None = None,
        point_value: float = 2.0,
        account_balance: float = 0.0,
        risk_per_trade: float | None = None,
        risk_pct_per_trade: float | None = None,
        use_fractional_lots: bool = False,
        accounts_repo=None,
    ):
        self._repo = trade_repository
        self._publisher = event_publisher
        self._logger = logger
        self._trade_logger = trade_logger
        self._analytics = analytics
        self._point_value = point_value
        self._account_balance = account_balance
        self._risk_per_trade = risk_per_trade
        self._risk_pct_per_trade = risk_pct_per_trade
        self._use_fractional_lots = use_fractional_lots
        self._accounts_repo = accounts_repo

    def update_balance(self, new_balance: float) -> None:
        self._account_balance = new_balance

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

    def handle_entry_fill(
        self,
        trade: dict,
        entry_price: float,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> dict:
        """Update trade state when broker reports an entry fill.

        Returns the updated trade dict.
        """
        old_entry = trade['entry']
        trade['entry'] = entry_price

        if stop_loss is not None:
            trade['stop_loss'] = stop_loss
        if take_profit is not None:
            trade['take_profit'] = take_profit

        # Recalculate risk
        is_long = trade['type'] == 'long'
        sl = trade.get('stop_loss')
        if sl is not None:
            if is_long:
                trade['risk'] = abs(entry_price - sl)
            else:
                trade['risk'] = abs(sl - entry_price)
        else:
            trade['risk'] = trade.get('risk') or 0

        # Recalculate contracts
        risk_per_contract = trade['risk'] * self._point_value
        risk_usd, risk_pct = self._get_current_risk()
        risk_budget = FinancialCalc.risk_budget(
            self._account_balance, risk_usd, risk_pct
        )
        if self._use_fractional_lots:
            contracts = FinancialCalc.lots(risk_budget, risk_per_contract) if risk_budget > 0 else 0.01
        else:
            contracts = FinancialCalc.contracts(risk_budget, risk_per_contract) if risk_budget > 0 else 1
        trade['contracts'] = contracts
        trade['risk_dollars'] = risk_per_contract * contracts
        trade['risk_pct'] = (
            (trade['risk_dollars'] / self._account_balance * 100)
            if self._account_balance > 0 else None
        )

        if self._logger:
            self._logger.info(
                f"[BrokerFillHandler] ENTRY FILL: {trade['trade_id']} @ {entry_price} "
                f"(was {old_entry}, slippage={entry_price - old_entry:+.2f}) "
                f"SL={trade['stop_loss']} TP={trade['take_profit']}"
            )

        if self._trade_logger:
            self._trade_logger.log(
                trade['trade_id'], "NT_ENTRY_FILL",
                f"Filled @ {entry_price:.2f} (slippage: {entry_price - old_entry:+.2f}) "
                f"SL={trade['stop_loss']:.2f} TP={trade['take_profit']:.2f}"
            )

        # Persist to DB
        try:
            self._repo.update_entry_price(trade['trade_id'], entry_price)
            if stop_loss is not None:
                self._repo.update_stop_loss(trade['trade_id'], stop_loss)
            if take_profit is not None:
                self._repo.update_take_profit(trade['trade_id'], take_profit)
            self._repo.update_risk_fields(
                trade['trade_id'], trade['risk'], trade['risk_dollars'], trade.get('risk_pct')
            )
            if trade.get('contracts') is not None:
                self._repo.update_contracts(trade['trade_id'], trade['contracts'])
        except Exception as e:
            if self._logger:
                self._logger.error(f"[BrokerFillHandler] DB error on entry fill for {trade['trade_id']}: {e}")
            if self._analytics:
                self._analytics.capture_exception(e, {"op": "broker_entry_fill", "trade_id": trade['trade_id']})
            # Don't raise — broker fill is informational, trade stays open

        # Emit to UI
        if self._publisher:
            self._publisher.emit('trade_entry_update', {
                'trade_id': trade['trade_id'],
                'signal_id': trade.get('signal_id'),
                'entry_price': entry_price,
                'stop_loss': trade['stop_loss'],
                'take_profit': trade['take_profit'],
                'risk': trade['risk'],
                'risk_dollars': trade.get('risk_dollars'),
                'risk_pct': trade.get('risk_pct'),
            })

        return trade
