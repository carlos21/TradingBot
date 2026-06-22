"""Line-agnostic base strategy.

Extracts the shared plumbing (bar aggregation, trade creation, filter pipeline,
event handling) from ``BaseLiquidityStrategy`` so that line-less strategies can
reuse it without inheriting line-specific state and methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto
from threading import RLock
from typing import Any

from src.analytics import AnalyticsReporter, NoOpReporter
from src.application.ports import EventPublisher
from src.application.services.strategy_trade_service import StrategyTradeService
from src.domain.events import DomainEvent, EventType
from src.domain.repositories import TradeRepository
from src.domain.types import Direction
from src.financial_calc import FinancialCalc
from src.services.trade_executor import NoOpExecutor
from src.services.trade_manager import TradeManager
from src.strategies.entry_context import EntryContext, EntryFilter
from src.utils.app_logger import ILogger


class DecisionEventCategory(Enum):
    """Classifies decision events so strategies can apply logging policies."""

    ROUTINE_POLL = auto()      # Routine polling checks — typically dropped
    STATE_CHANGE = auto()      # Line state transitions (latch, remove, regime lock)
    TRIGGER_MILESTONE = auto() # Trigger reached a significant milestone (cross, rescue)
    EVAL_FAILURE = auto()      # Real evaluation that failed (3C fail, wick fail, etc.)
    TRADE_ACTION = auto()      # Entry, filter block, breakeven, close


@dataclass
class BreakevenConfig:
    trigger_rr: float       # Risk:Reward ratio to trigger the move (e.g., 2.0)
    move_to_rr: float = 0.0 # Where to move SL in R terms (0.0 = Entry, 0.1 = Entry + small profit)


class BaseStrategy:
    """Line-agnostic strategy base.

    Provides:
      - Bar aggregation to strategy TF
      - Trade creation, persistence & position sizing
      - Entry filter pipeline
      - Event-driven trade close handling
      - Phantom trade SL/TP checks
      - Breakeven logic
      - No-op line API (so LinesController doesn't crash)

    Subclasses override ``_on_strategy_bar()`` with their entry logic.
    """

    def __init__(
        self,
        min_stop_loss: float,
        event_publisher: EventPublisher | None,
        trade_repository: TradeRepository,
        trade_manager: TradeManager,
        extra_sl_space: float,
        point_value: float,
        account_balance: float,
        risk_per_trade: float | None = None,
        risk_pct_per_trade: float | None = None,
        fixed_stop_loss: float | None = None,
        max_stop_loss: float | None = None,
        strategy_tf: str = "5m",
        sl_levels: list[float] | None = None,
        rr_ratio: float = 5.0,
        use_fractional_lots: bool = False,
        fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
        broker_spread: float = 0.0,
        trade_logger=None,
        analytics: AnalyticsReporter | None = None,
        logger: ILogger | None = None,
        account_configs: list | None = None,
        accounts_repo=None,
        live_mode: bool = False,
    ):
        self.min_stop_loss = float(min_stop_loss)
        self.logger = logger
        self.rr_ratio = float(rr_ratio)
        self._live_mode = live_mode
        self.use_fractional_lots = use_fractional_lots
        self.fee_per_rt = fee_per_rt
        self.broker_spread = broker_spread
        self.point_value = float(point_value)
        self.account_balance = float(account_balance)
        self.risk_per_trade = risk_per_trade
        self.risk_pct_per_trade = risk_pct_per_trade
        self.event_publisher = event_publisher
        self.trade_repository = trade_repository
        self.trade_manager = trade_manager
        self.extra_sl_space = float(extra_sl_space)
        self.fixed_stop_loss = fixed_stop_loss
        self.max_stop_loss = max_stop_loss
        self.sl_levels = sorted(sl_levels) if sl_levels else None
        self.trade_logger = trade_logger
        self.analytics = analytics or NoOpReporter()
        self._account_configs: list = list(account_configs) if account_configs else []
        self._accounts_repo = accounts_repo

        executor = trade_manager.trade_executor if trade_manager else NoOpExecutor()
        self._trade_service = StrategyTradeService(
            trade_repository=trade_repository,
            trade_executor=executor,
            event_publisher=event_publisher,
            logger=logger,
            trade_logger=trade_logger,
            point_value=point_value,
            fee_per_rt=fee_per_rt,
            broker_spread=broker_spread,
            account_balance=account_balance,
            risk_per_trade=risk_per_trade,
            risk_pct_per_trade=risk_pct_per_trade,
            use_fractional_lots=use_fractional_lots,
            accounts_repo=accounts_repo,
            instrument=None,
        )

        self.open_trades: list[dict[str, Any]] = []
        self.total_pnl = 0.0
        self.lock = RLock()

        # parse TF like "5m" or "1h"
        if not strategy_tf or len(strategy_tf) < 2 or strategy_tf[-1] not in ('m', 'h'):
            raise ValueError(f"Invalid strategy_tf: {strategy_tf!r}")
        num, unit = int(strategy_tf[:-1]), strategy_tf[-1]
        self.strategy_window = num * (60 if unit == "m" else 3600)
        self._buf: list[dict[str, Any]] = []
        self._group_start: int | None = None

        self.entry_filters: list[EntryFilter] = []
        self.is_warmup = False

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self, preserve_trigger_state: bool = False):
        """Clear strategy state for a fresh run."""
        with self.lock:
            self.open_trades.clear()
            self.total_pnl = 0.0
            self._buf.clear()
            self._group_start = None
            self.is_warmup = False

    # ------------------------------------------------------------------
    # Account / Risk helpers
    # ------------------------------------------------------------------

    def _get_current_account_configs(self) -> list:
        """Return fresh account configs from DB if available, else cached fallback.

        In live mode only accounts explicitly marked ``live_enabled`` are used
        for new trades so the admin can control which accounts actually trade.
        """
        if self._accounts_repo is not None:
            try:
                accounts = self._accounts_repo.list_accounts()
                if accounts:
                    return self._filter_live_accounts(accounts)
            except Exception:
                pass
        return self._filter_live_accounts(self._account_configs)

    def _filter_live_accounts(self, accounts: list | None) -> list:
        """Keep all accounts in non-live mode; only live-enabled ones in live mode."""
        if not accounts:
            return []
        if not self._live_mode:
            return list(accounts)
        return [a for a in accounts if getattr(a, "live_enabled", True)]

    def _get_current_risk(self) -> tuple[float | None, float | None]:
        """Return (risk_per_trade, risk_pct_per_trade) from DB if available, else fallbacks."""
        accounts = self._get_current_account_configs()
        if accounts:
            first = accounts[0]
            return first.risk_usd, first.risk_pct
        return self.risk_per_trade, self.risk_pct_per_trade

    # ------------------------------------------------------------------
    # Decision logging (no-op; subclasses may override)
    # ------------------------------------------------------------------

    def log_decision(self, bar_time: int, tf: str | None, line_id: str | None, event: str,
                     details: str = "", *, direction: str | None = None,
                     trigger_name: str | None = None, filter_name: str | None = None,
                     reason: str | None = None, extra: dict | None = None,
                     category: DecisionEventCategory = DecisionEventCategory.TRADE_ACTION):
        pass

    # ------------------------------------------------------------------
    # Line API (no-op by default — line-based strategies override)
    # ------------------------------------------------------------------

    def add_strategy_line(self, id: Any, level: float, creation_timestamp: float = 0.0):
        if self.logger:
            self.logger.warning("[BaseStrategy] add_strategy_line called but this strategy does not support lines.")

    def remove_strategy_line(self, id: Any):
        pass

    def update_strategy_line(self, id: Any, level: float):
        pass

    # ------------------------------------------------------------------
    # Bar aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate_bars(bars: list[dict[str, Any]], window_start: int, window_secs: int) -> dict[str, Any]:
        from src.utils.bar_aggregator import BarAggregator
        return BarAggregator.aggregate_with_window(bars, window_start, window_secs)

    def on_raw_bar(self, bar: dict[str, Any]):
        """Feed raw stream bars (assumed 1m granularity).

        Aggregates to the configured strategy TF and calls ``_on_strategy_bar``
        on each completed bar.
        """
        ts = bar["time"]
        win = (ts // self.strategy_window) * self.strategy_window
        if self._group_start is None:
            self._group_start = win
        if win == self._group_start:
            self._buf.append(bar)
        else:
            agg = self._aggregate_bars(self._buf, self._group_start, self.strategy_window)
            self._buf, self._group_start = [bar], win
            self._on_strategy_bar(agg)

    def _on_strategy_bar(self, bar: dict[str, Any]):
        """Override this in subclasses to implement entry logic."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------

    def _filters_allow_entry(self, ctx: EntryContext) -> tuple[bool, str, bool]:
        """Run all pluggable entry filters.

        Returns (allowed, reason, hold) where hold=True means the setup should
        stay alive and re-evaluate.
        """
        for f in self.entry_filters:
            ok, reason = f(ctx)
            if not ok:
                hold = getattr(f, '_hold_on_block', False)
                return False, f"{f.__name__}: {reason}", hold
        return True, "ok", False

    # ------------------------------------------------------------------
    # Breakeven
    # ------------------------------------------------------------------

    def _check_breakeven(self, bar: dict[str, Any]):
        if self.is_warmup:
            return
        for trade in self.open_trades:
            if trade['status'] != 'open':
                continue
            if trade['pair'] != bar['pair']:
                continue

            cfg = trade.get('breakeven_config')
            if not cfg:
                continue

            # Support both BreakevenConfig dataclass and plain dict
            def _be_val(obj, key):
                return getattr(obj, key, None) if hasattr(obj, key) else obj.get(key)

            trigger_rr = _be_val(cfg, 'trigger_rr')
            move_to_rr = _be_val(cfg, 'move_to_rr')
            if trigger_rr is None or move_to_rr is None:
                continue

            current_sl = trade['stop_loss']
            entry = trade['entry']
            risk = trade['risk']

            new_sl = None
            should_update = False

            if trade['type'] == 'long':
                trigger_price = entry + (risk * trigger_rr)
                if bar['high'] >= trigger_price:
                    proposed_sl = entry + (risk * move_to_rr)
                    if proposed_sl > current_sl:
                        new_sl = proposed_sl
                        should_update = True
            elif trade['type'] == 'short':
                trigger_price = entry - (risk * trigger_rr)
                if bar['low'] <= trigger_price:
                    proposed_sl = entry - (risk * move_to_rr)
                    if proposed_sl < current_sl:
                        new_sl = proposed_sl
                        should_update = True

            if should_update and new_sl is not None:
                self._update_trade_sl(trade, new_sl)

    def _on_trade_updated(self, payload: dict[str, Any]) -> None:
        """Sync strategy trade dict with broker-reported updates (entry fill, SL move, etc.)."""
        trade_id = payload.get("trade_id")
        trade = next(
            (t for t in self.open_trades if t.get("trade_id") == trade_id),
            None,
        )
        if not trade or trade.get("status") != "open":
            return
        if "entry_price" in payload:
            trade["entry"] = payload["entry_price"]
        if "stop_loss" in payload:
            trade["stop_loss"] = payload["stop_loss"]
        if "take_profit" in payload:
            trade["take_profit"] = payload["take_profit"]
        if "risk" in payload:
            trade["risk"] = payload["risk"]
        if "contracts" in payload:
            trade["contracts"] = payload["contracts"]

    def _update_trade_sl(self, trade: dict[str, Any], new_sl: float):
        old_sl = trade['stop_loss']
        if self.logger:
            self.logger.info(f"[Strategy] Moving SL for {trade['trade_id']} to {new_sl}")

        trade['stop_loss'] = new_sl

        try:
            # Update this trade and any sibling trades from the same signal
            trades_to_update = [trade]
            signal_id = trade.get('signal_id')
            if signal_id and self.trade_manager:
                for t in self.trade_manager.open_trades:
                    if t.get('signal_id') == signal_id and t['trade_id'] != trade['trade_id']:
                        trades_to_update.append(t)

            for t in trades_to_update:
                self.trade_repository.update_stop_loss(t['trade_id'], new_sl)
                self.trade_manager.update_local_trade_sl(t['trade_id'], new_sl)
                t['stop_loss'] = new_sl
        except Exception as e:
            if self.logger:
                self.logger.error(f"[Strategy] Failed to update SL in DB: {e}")
            self.analytics.capture_exception(e, {"op": "update_sl", "trade_id": trade['trade_id']})

        self.trade_manager.trade_executor.on_sl_update(trade['trade_id'], new_sl)

        if self.trade_logger:
            self.trade_logger.log(trade['trade_id'], "SL_UPDATE", f"{old_sl:.2f} → {new_sl:.2f}")
            self.trade_logger.log(trade['trade_id'], "CMD_SENT", f"modify_order SL={new_sl:.2f} → NinjaTrader")

        self.event_publisher.emit("trade_update", {
            "trade_id": trade['trade_id'],
            "stop_loss": new_sl,
            "pair": trade['pair']
        })

    # ------------------------------------------------------------------
    # Event-driven trade close
    # ------------------------------------------------------------------

    def on_event(self, event: DomainEvent) -> None:
        """EventSubscriber protocol: react to domain trade events."""
        if event.event_type == EventType.TRADE_CLOSED:
            self._on_trade_closed(event.payload)
        elif event.event_type in (EventType.TRADE_UPDATED, EventType.TRADE_ENTRY_UPDATED):
            self._on_trade_updated(event.payload)

    def _on_trade_closed(self, payload: dict[str, Any]) -> None:
        trade_id = payload.get("trade_id")
        trade = next(
            (t for t in self.open_trades if t.get("trade_id") == trade_id),
            None,
        )
        if not trade or trade.get("status") != "open":
            return

        trade["status"] = "closed"
        trade["exit_price"] = payload.get("exit_price")
        trade["result_type"] = payload.get("result_type")

        self.open_trades = [t for t in self.open_trades if t is not trade]

    # ------------------------------------------------------------------
    # Phantom trade checks
    # ------------------------------------------------------------------

    def _check_phantom_exits(self, bar: dict[str, Any]) -> None:
        if self.is_warmup:
            return
        remaining = []
        for t in self.open_trades:
            if t.get("status") != "open" or not t.get("is_phantom"):
                remaining.append(t)
                continue
            if t.get('entry_time', 0) >= bar['time']:
                remaining.append(t)
                continue

            low, high = bar["low"], bar["high"]
            closed = False
            exit_price = 0.0
            result_type = None

            sl = t.get("stop_loss")
            tp = t.get("take_profit")
            if t["type"] == "long":
                if sl is not None and low <= sl:
                    exit_price = sl
                    result_type = "SL"
                    closed = True
                elif tp is not None and high >= tp:
                    exit_price = tp
                    result_type = "TP"
                    closed = True
            else:
                if sl is not None and high >= sl:
                    exit_price = sl
                    result_type = "SL"
                    closed = True
                elif tp is not None and low <= tp:
                    exit_price = tp
                    result_type = "TP"
                    closed = True

            if closed:
                contracts = t.get("contracts") or 1
                risk_pts = t.get("risk", 0) or 1.0
                r_result, t_fees, t_pnl_usd, _ = FinancialCalc.calculate_close_metrics(
                    direction=Direction.from_string(t["type"]),
                    entry_price=t["entry"],
                    exit_price=exit_price,
                    stop_loss=t["stop_loss"],
                    take_profit=t["take_profit"],
                    risk_points=risk_pts,
                    contracts=contracts,
                    point_value=self.point_value,
                    fee_per_rt=self.fee_per_rt,
                )
                if self.broker_spread > 0:
                    spread_cost = contracts * self.broker_spread * self.point_value
                    t_pnl_usd -= spread_cost
                    t_fees += spread_cost
                t.update(
                    status="closed", result=r_result, exit_time=bar["time"],
                    exit_price=exit_price, fees=t_fees, pnl_usd=t_pnl_usd,
                    result_type=result_type,
                )
                self.event_publisher.emit("trade_close", t)
            else:
                remaining.append(t)
        self.open_trades = remaining

    # ------------------------------------------------------------------
    # Trade creation & persistence
    # ------------------------------------------------------------------

    def _select_sl_level(self, distance: float) -> float:
        """Pick the smallest SL tier that covers the distance."""
        for level in self.sl_levels:  # already sorted ascending
            if level >= distance:
                return level
        return self.sl_levels[-1]

    def _build_trade_from_context(self, ctx: EntryContext) -> dict[str, Any]:
        entry = ctx.close

        if self.sl_levels:
            if ctx.is_long:
                distance = max(entry - ctx.extreme, self.min_stop_loss)
            else:
                distance = max(ctx.extreme - entry, self.min_stop_loss)
            eff_risk = self._select_sl_level(distance)
        elif self.fixed_stop_loss and self.fixed_stop_loss > 0:
            eff_risk = self.fixed_stop_loss
        else:
            extra = self.extra_sl_space
            if ctx.is_long:
                raw_risk = max(entry - ctx.extreme, self.min_stop_loss)
            else:
                raw_risk = max(ctx.extreme - entry, self.min_stop_loss)
            eff_risk = raw_risk + extra

        if not self.sl_levels and self.max_stop_loss and self.max_stop_loss > 0 and eff_risk > self.max_stop_loss:
            if self.logger:
                self.logger.warning(f"[Strategy] Risk {eff_risk:.2f} exceeds Max {self.max_stop_loss}. Capping it.")
            eff_risk = self.max_stop_loss

        if ctx.is_long:
            sl = entry - eff_risk
            tp = entry + self.rr_ratio * eff_risk
            trade = self._make_trade_dict(ctx.bar, "long", entry, sl, tp, eff_risk)
        else:
            sl = entry + eff_risk
            tp = entry - self.rr_ratio * eff_risk
            trade = self._make_trade_dict(ctx.bar, "short", entry, sl, tp, eff_risk)
        trade["line_level"] = ctx.level
        return trade

    def _calc_contracts(self, risk_per_contract: float) -> float:
        if risk_per_contract <= 0:
            return 1.0 if not self.use_fractional_lots else 0.01
        account_balance = self.account_balance
        if self.trade_manager is not None:
            account_balance = self.trade_manager.account_balance
        risk_usd, risk_pct = self._get_current_risk()
        risk_budget = FinancialCalc.risk_budget(account_balance, risk_usd, risk_pct)
        if risk_budget <= 0:
            return 1.0 if not self.use_fractional_lots else 0.01
        if self.use_fractional_lots:
            return FinancialCalc.lots(risk_budget, risk_per_contract)
        return FinancialCalc.contracts(risk_budget, risk_per_contract)

    def _make_trade_dict(
        self,
        bar: dict[str, Any],
        trade_type: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        risk: float,
    ) -> dict[str, Any]:
        risk_per_contract = risk * self.point_value
        contracts = self._calc_contracts(risk_per_contract)
        risk_dollars = risk_per_contract * contracts
        account_balance = self.account_balance
        if self.trade_manager is not None:
            account_balance = self.trade_manager.account_balance
        risk_pct = (risk_dollars / account_balance * 100) if account_balance > 0 else None
        return {
            "pair":         bar["pair"],
            "type":         trade_type,
            "entry":        entry,
            "stop_loss":    stop_loss,
            "orig_sl":      stop_loss,
            "take_profit":  take_profit,
            "risk":         risk,
            "risk_dollars": risk_dollars,
            "risk_pct":     risk_pct,
            "contracts":    contracts,
            "rr_ratio":     self.rr_ratio,
            "status":       "open",
            "entry_time":   bar["time"],
        }

    def _store_and_emit_open(self, trade: dict[str, Any]):
        account_balance = self.account_balance
        if self.trade_manager is not None:
            account_balance = self.trade_manager.account_balance
        opened_trades = self._trade_service.open_trade(
            trade=trade,
            is_warmup=self.is_warmup,
            account_configs=self._get_current_account_configs(),
            account_balance=account_balance,
        )
        if not opened_trades:
            return

        # Copy generated fields (trade_id, account, etc.) back to the original
        # trade dict so callers can reference the opened trade immediately.
        trade.update(opened_trades[0])

        for opened_trade in opened_trades:
            self.open_trades.append(opened_trade)

            if self.trade_manager and not opened_trade.get("is_phantom"):
                tm_trade = {
                    'trade_id':    opened_trade["trade_id"],
                    'pair':        opened_trade["pair"],
                    'type':        opened_trade["type"],
                    'entry':       opened_trade["entry"],
                    'stop_loss':   opened_trade["stop_loss"],
                    'take_profit': opened_trade["take_profit"],
                    'risk':        opened_trade["risk"],
                    'risk_dollars': opened_trade.get("risk_dollars"),
                    'risk_pct':    opened_trade.get("risk_pct"),
                    'contracts':   opened_trade.get("contracts"),
                    'entry_time':  opened_trade["entry_time"],
                    'account':     opened_trade.get("account"),
                    'status':      'open',
                    'line_level':  opened_trade.get("line_level"),
                    'is_reentry':  opened_trade.get("is_reentry", False),
                }
                self.trade_manager.open_trades.append(tm_trade)
                self.trade_manager._monitored_trades.add(opened_trade["trade_id"])

    def _store_and_emit_close(self, trade: dict[str, Any]):
        self._trade_service.close_trade(trade)

    # ------------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------------

    @staticmethod
    def _ts_to_dt(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
