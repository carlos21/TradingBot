from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import TYPE_CHECKING, Any

from src.analytics import AnalyticsReporter, NoOpReporter
from src.application.ports import EventPublisher
from src.application.services.strategy_trade_service import StrategyTradeService
from src.dbexception import DBNotFoundException
from src.domain.events import DomainEvent, EventType
from src.domain.repositories import (
    LineRepository,
    LineTriggerStateRepository,
    TradeRepository,
)
from src.domain.types import Direction
from src.financial_calc import FinancialCalc
from src.infrastructure.repositories.line_trigger_state_repository import (
    InMemoryLineTriggerStateRepository,
)
from src.services.trade_executor import NoOpExecutor
from src.services.trade_manager import TradeManager
from src.strategies.base_strategy import BreakevenConfig, DecisionEventCategory
from src.strategies.entry_context import (
    EntryContext,
    EntryFilter,
    EntryTrigger,
)
from src.utils.app_logger import ILogger

if TYPE_CHECKING:
    from src.domain.readiness.protocols import IExecutionContext



class LineRemovalMode(str, Enum):
    ON_EVALUATE = "on_evaluate"   # remove line after we evaluated it
    ON_ENTER    = "on_enter"      # remove only if we actually opened a trade
    NEVER       = "never"         # never remove (we'll reset state for future triggers)


@dataclass
class StrategyOptions:
    line_removal_mode: LineRemovalMode
    entry_filters: list[EntryFilter] | None
    triggers: list[EntryTrigger] | None
    breakeven: BreakevenConfig | None
    reentry_breakeven: BreakevenConfig | None  # breakeven config applied only to re-entry trades
    reentry_after_sl: bool
    reentry_threshold: float
    reentry_only: bool
    max_reentry_attempts: int


class BaseLiquidityStrategy:
    """
    Base class containing all shared plumbing:
      - 5m (or configured TF) aggregation
      - line state & removal policy
      - filter pipeline
      - trade open/close bookkeeping & Socket.IO events
      - persistence via repositories

    Subclasses must provide default triggers via `default_triggers()`.
    They may set additional attributes needed by their triggers.
    """

    def __init__(
        self,
        min_stop_loss: float,
        max_bounce: float,
        event_publisher: EventPublisher | None,
        line_repository: LineRepository,
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
        options: StrategyOptions | None = None,
        htf_fetcher: Callable[..., dict | None] | None = None,
        sl_levels: list[float] | None = None,
        max_entry_distance: float | None = None,
        sl_level_tolerance: float = 5.0,
        min_cross_depth: float = 0.0,
        rr_ratio: float = 5.0,
        use_fractional_lots: bool = False,
        fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
        broker_spread: float = 0.0,
        trade_logger=None,
        analytics: AnalyticsReporter = None,
        trigger_state_repo: LineTriggerStateRepository = None,
        logger: ILogger = None,
        decision_log_repository=None,
        account_configs: list | None = None,
        accounts_repo=None,
        execution_context: IExecutionContext | None = None,
        live_mode: bool = False,
    ):
        self.min_stop_loss = float(min_stop_loss)
        self.logger = logger
        self.max_bounce    = float(max_bounce)
        self._live_mode    = live_mode
        self.min_cross_depth = float(min_cross_depth)
        self.rr_ratio = float(rr_ratio)
        self.use_fractional_lots = use_fractional_lots
        self.fee_per_rt = fee_per_rt
        self.broker_spread = broker_spread
        self.point_value = float(point_value)
        self.account_balance = float(account_balance)
        self.risk_per_trade = risk_per_trade
        self.risk_pct_per_trade = risk_pct_per_trade
        self.event_publisher = event_publisher
        self.line_repository  = line_repository
        self.trade_repository = trade_repository
        self.trade_manager = trade_manager
        self.extra_sl_space = float(extra_sl_space)
        self.fixed_stop_loss = fixed_stop_loss
        self.max_stop_loss = max_stop_loss
        self.sl_levels = sorted(sl_levels) if sl_levels else None
        self.max_entry_distance = max_entry_distance
        self.trade_logger = trade_logger
        self.analytics = analytics or NoOpReporter()
        self.sl_level_tolerance = float(sl_level_tolerance)
        self.trigger_state_repo: LineTriggerStateRepository = trigger_state_repo or InMemoryLineTriggerStateRepository()
        self.decision_log_repository = decision_log_repository
        self._account_configs: list = list(account_configs) if account_configs else []
        self._accounts_repo = accounts_repo

        self._trade_service = StrategyTradeService(
            trade_repository=trade_repository,
            trade_executor=trade_manager.trade_executor if trade_manager else NoOpExecutor(),
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
            live_mode=live_mode,
        )

        self.strategy_lines: dict[Any, dict[str, Any]] = {}   # id -> { level, direction, extreme, creation_ts }
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

        if options is None:
            raise ValueError("options is required — use DEFAULT_STRATEGY_OPTIONS from src.strategies.liquidity_v2.constants if you need defaults")
        self.options = options
        # Let subclasses decide the triggers (they may depend on attrs set after super().__init__)
        self.triggers: list[EntryTrigger] = list(self.options.triggers) if self.options.triggers else []
        # Filters can be taken straight from options
        self.entry_filters: list[EntryFilter] = list(self.options.entry_filters or [])

        # Pending re-entry opportunities created when a SL is hit
        self._reentry_opportunities: list[dict[str, Any]] = []

        # Optional dependency for multi-TF checks
        self.htf_fetcher = htf_fetcher
        if execution_context is not None:
            self._execution_context = execution_context
        else:
            # Local import avoids a circular dependency with the live_readiness package.
            from src.application.live_readiness.trading_context import (
                AlwaysEnabledTradingContext,
            )
            self._execution_context = AlwaysEnabledTradingContext()
        self.warmup_crossed_lines: set[Any] = set()

    @property
    def is_warmup(self) -> bool:
        """Warm-up mode is derived from the injected execution context."""
        return self._execution_context.is_warmup()

    @property
    def execution_context(self):
        """Execution context that controls trading/warmup behavior."""
        return self._execution_context

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

    # ----- Decision log (no-op; subclass LiquidityStrategyV2 overrides this) -----

    def log_decision(self, bar_time: int, tf: str, line_id: str, event: str,
                     details: str = "", *, direction: str = None,
                     trigger_name: str = None, filter_name: str = None,
                     reason: str = None, extra: dict = None,
                     category: DecisionEventCategory = DecisionEventCategory.TRADE_ACTION):
        pass  # overridden by LiquidityStrategyV2 to write to self.decision_logs

    # ----- Public small API for runtime tweaks -----

    def set_entry_filters(self, filters: list[EntryFilter]):
        with self.lock:
            self.entry_filters = list(filters)

    def set_triggers(self, triggers: list[EntryTrigger]):
        with self.lock:
            self.triggers = list(triggers)

    # ----- Line management -----

    def add_strategy_line(self, id: Any, level: float, creation_timestamp: float = 0.0): # <--- CHANGED
        """
        direction: 'long' | 'short'
        creation_timestamp: Epoch seconds when this line became valid
        """
        self.logger.info(f"[Strategy] add_strategy_line id={id} level={level} ts={creation_timestamp}")
        with self.lock:
            state = {
                "level":       float(level),
                "direction":   None,
                "extreme":     0.0,
                "creation_ts": float(creation_timestamp),
            }
            self.strategy_lines[id] = state
            # Only persist fresh state if no persisted state exists for this line.
            # Avoids overwriting saved trigger state during bootstrap/recovery.
            # Ongoing persistence is handled by _persist_all_line_states() on each bar.
            existing = self.trigger_state_repo.load_all(self.trade_manager.pair)
            if str(id) not in existing:
                self.trigger_state_repo.save(str(id), self.trade_manager.pair, state)

    def remove_strategy_line(self, id: Any):
        with self.lock:
            self.strategy_lines.pop(id, None)
            try:
                self.trigger_state_repo.delete(str(id))
            except Exception as e:
                self.logger.warning(f"[RemoveLine] Failed to delete trigger state for {id}: {e}")
        with contextlib.suppress(DBNotFoundException):
            self.line_repository.delete_line(id)
        self.event_publisher.emit("line_removed", {"id": id})

    def update_strategy_line(self, id: Any, level: float):
        """Update an existing strategy line's price level."""
        with self.lock:
            if id in self.strategy_lines:
                self.strategy_lines[id]["level"] = float(level)
                self.logger.info(f"[Strategy] update_strategy_line id={id} new_level={level}")
        self.event_publisher.emit("line_updated", {"id": id, "level": level})

    def _persist_all_line_states(self):
        """Write current trigger state for every active line to the repo."""
        pair = self.trade_manager.pair
        with self.lock:
            items = list(self.strategy_lines.items())
        for line_id, state in items:
            try:
                self.trigger_state_repo.save(str(line_id), pair, state)
            except Exception as e:
                self.logger.warning(
                    f"[PersistState] Failed to save trigger state for {line_id}: {type(e).__name__}: {e}"
                )

    def restore_trigger_states(self, pair: str):
        """Overlay persisted trigger states onto bootstrapped lines.

        Lines that were crossed during warm-up keep their warm-up state; restoring
        stale persisted state would incorrectly reset direction/extreme/trigger stages.
        """
        with self.lock:
            saved = self.trigger_state_repo.load_all(pair)
            restored = 0
            for line_id, line_state in self.strategy_lines.items():
                if line_id in self.warmup_crossed_lines:
                    continue
                persisted = saved.get(str(line_id))
                if persisted:
                    # Discard stale persisted state if creation_ts mismatch —
                    # the line was re-added or its creation time changed.
                    persisted_ts = persisted.get("creation_ts")
                    if persisted_ts is not None and persisted_ts != line_state.get("creation_ts"):
                        continue
                    # Keep level/creation_ts from fresh bootstrap (authoritative for geometry),
                    # restore everything else (direction, extreme, trigger stages, etc.)
                    level = line_state["level"]
                    creation_ts = line_state["creation_ts"]
                    line_state.update(persisted)
                    line_state["level"] = level
                    line_state["creation_ts"] = creation_ts
                    restored += 1
            if restored:
                self.logger.info(f"[Strategy] Restored trigger state for {restored}/{len(self.strategy_lines)} lines")

    def restore_open_trades(self):
        """Sync open trades from TradeManager into strategy's in-memory list."""
        with self.lock:
            if self.open_trades:
                return
            if self.trade_manager.open_trades:
                for t in self.trade_manager.open_trades:
                    if t.get('status') == 'open':
                        self.open_trades.append(dict(t))
                if self.open_trades:
                    self.logger.info(f"[Strategy] Restored {len(self.open_trades)} open trade(s) from DB")

    def restore_reentry_opportunities(self, pair: str, reference_time: datetime = None):
        """Rebuild pending re-entry opportunities from recently SL'd trades in DB."""
        if not (self.options.reentry_after_sl or self.options.reentry_only):
            return

        from datetime import timedelta
        ref = reference_time or datetime.now(timezone.utc)
        cutoff = ref - timedelta(hours=4)

        trades = sorted(
            self.trade_repository.list_trades(pair),
            key=lambda t: t.exit_time or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        restored = 0
        for t in trades:
            if t.result is None or t.result >= 0:
                continue
            if t.exit_time is None or t.exit_time < cutoff:
                continue
            line_level = t.params.get("line_level") if t.params else None
            if line_level is None:
                continue
            attempt = t.params.get("reentry_attempt", 0) if t.params else 0
            if attempt == 0 and t.params and t.params.get("is_reentry"):
                attempt = 1
            next_attempt = attempt + 1
            if next_attempt > self.options.max_reentry_attempts:
                continue
            already_watching = any(
                opp["level"] == line_level and opp["direction"] == t.trade_type
                for opp in self._reentry_opportunities
            )
            if already_watching:
                continue
            self._reentry_opportunities.append({
                "level": line_level,
                "direction": t.trade_type,
                "pair": pair,
                "extreme_excursion": line_level,
                "sl_bar_time": int(t.exit_time.timestamp()) if t.exit_time else 0,
                "reentry_attempt": next_attempt,
            })
            restored += 1
            self.logger.info(f"[ReEntry] Restored re-entry watch: {t.trade_type} @ level={line_level:.2f} (attempt {next_attempt})")
        if restored:
            self.logger.info(f"[Strategy] Restored {restored} re-entry opportunity(ies) from DB")

    def _reset_line_state(self, line_state: dict[str, Any]):
        """If we keep the line, reset so it can trigger again in the future."""
        line_state["extreme"] = 0.0

    # ----- Aggregation -----

    def _aggregate_bars(self, bars: list[dict[str, Any]], window_start: int, window_secs: int) -> dict[str, Any]:
        from src.utils.bar_aggregator import BarAggregator
        return BarAggregator.aggregate_with_window(bars, window_start, window_secs)

    def on_raw_bar(self, bar: dict[str, Any]):
        """
        Feed raw stream bars (assumed 1s or tick granularity).
        We aggregate to the configured TF and then process strategy logic on each completed bar.

        NOTE: Trade exits are handled by TradeManager (backtest) or broker fills (live).
        The strategy reacts to TRADE_CLOSED domain events instead of polling bars.
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

    def check_breakeven(self, bar: dict[str, Any]):
        if self.is_warmup:
            return
        with self.lock:
            open_trades_snapshot = list(self.open_trades)
        for trade in open_trades_snapshot:
            if trade['status'] != 'open':
                continue
            if trade['pair'] != bar['pair']:
                continue

            # Pick the right breakeven config based on trade type
            is_reentry = trade.get('is_reentry', False)
            if is_reentry:
                cfg = self.options.reentry_breakeven
            else:
                cfg = self.options.breakeven

            if not cfg:
                continue

            current_sl = trade['stop_loss']
            entry      = trade['entry']
            risk       = trade['risk']

            new_sl = None
            should_update = False

            if trade['type'] == 'long':
                trigger_price = entry + (risk * cfg.trigger_rr)

                if bar['high'] >= trigger_price:
                    proposed_sl = entry + (risk * cfg.move_to_rr)
                    if proposed_sl > current_sl:
                        new_sl = proposed_sl
                        should_update = True

            elif trade['type'] == 'short':
                trigger_price = entry - (risk * cfg.trigger_rr)

                if bar['low'] <= trigger_price:
                    proposed_sl = entry - (risk * cfg.move_to_rr)
                    if proposed_sl < current_sl:
                        new_sl = proposed_sl
                        should_update = True

            if should_update and new_sl is not None:
                self._update_trade_sl(trade, new_sl)

    def _check_reentry_opportunities(self, bar: dict[str, Any]):
        """
        On every 1m bar: update adverse excursion tracking for pending re-entry
        opportunities and trigger a new trade if price closes back through the line.
        Bypasses all normal entry filters (including daily trade limit).
        """
        if self.is_warmup:
            return
        with self.lock:
            threshold = self.options.reentry_threshold
            remaining = []
            for opp in self._reentry_opportunities:
                if opp["pair"] != bar["pair"]:
                    remaining.append(opp)
                    continue

                # Re-check on every iteration: if a previous opportunity opened a trade,
                # subsequent opportunities must wait for the next bar.
                if any(t["status"] == "open" for t in self.open_trades):
                    remaining.append(opp)
                    continue

                level = opp["level"]
                direction = opp["direction"]

                lid = f"reentry@{level:.2f}"

                # Skip re-entry on the same bar as the SL hit
                if bar["time"] <= opp.get("sl_bar_time", 0):
                    remaining.append(opp)
                    continue

                if direction == "long":
                    opp["extreme_excursion"] = min(opp["extreme_excursion"], bar["low"])
                    adverse = level - opp["extreme_excursion"]
                    if adverse > threshold:
                        self.logger.info(f"[ReEntry] Cancelled LONG re-entry: price went {adverse:.1f} pts below line {level:.2f}")
                        self.log_decision(bar["time"], "1m", lid, "REENTRY_CANCEL",
                                          f"Cancelled — price went {adverse:.1f}pts below line={level:.2f} (threshold={threshold:.0f}pts)",
                                          category=DecisionEventCategory.STATE_CHANGE)
                        continue  # drop opportunity
                    if bar["close"] > level and bar["close"] > bar["open"] and adverse >= 0:
                        ctx = EntryContext(
                            strategy=self, line_id=None, direction=Direction.LONG, level=level,
                            bar=bar, close=bar["close"], low=bar["low"], high=bar["high"],
                            extreme=opp["extreme_excursion"], cross_depth=0.0,
                        )
                        allow, reason, _ = self._filters_allow_reentry(ctx)
                        if not allow:
                            self.logger.info(f"[ReEntry] Filter blocked LONG re-entry at {bar['close']:.2f}: {reason}")
                            self.log_decision(bar["time"], "1m", lid, "REENTRY_FILTER_BLOCK",
                                              f"Filter blocked — {reason}",
                                              category=DecisionEventCategory.TRADE_ACTION)
                            remaining.append(opp)
                            continue  # keep opportunity alive for next bar
                        self.logger.info(f"[ReEntry] Triggering LONG re-entry at {bar['close']:.2f} (line={level:.2f}) — bullish candle")
                        self.log_decision(bar["time"], "1m", lid, "ENTRY",
                                          f"Re-entry LONG @ {bar['close']:.2f} — bullish close above line={level:.2f} (max adverse={adverse:.1f}pts)",
                                          category=DecisionEventCategory.TRADE_ACTION)
                        trade = self._build_trade_from_context(ctx)
                        trade["is_reentry"] = True
                        trade["reentry_attempt"] = opp.get("reentry_attempt", 1)
                        self._store_and_emit_open(trade)
                        continue  # consumed

                else:  # short
                    opp["extreme_excursion"] = max(opp["extreme_excursion"], bar["high"])
                    adverse = opp["extreme_excursion"] - level
                    if adverse > threshold:
                        self.logger.info(f"[ReEntry] Cancelled SHORT re-entry: price went {adverse:.1f} pts above line {level:.2f}")
                        self.log_decision(bar["time"], "1m", lid, "REENTRY_CANCEL",
                                          f"Cancelled — price went {adverse:.1f}pts above line={level:.2f} (threshold={threshold:.0f}pts)",
                                          category=DecisionEventCategory.STATE_CHANGE)
                        continue  # drop opportunity
                    if bar["close"] < level and bar["close"] < bar["open"] and adverse >= 0:
                        ctx = EntryContext(
                            strategy=self, line_id=None, direction=Direction.SHORT, level=level,
                            bar=bar, close=bar["close"], low=bar["low"], high=bar["high"],
                            extreme=opp["extreme_excursion"], cross_depth=0.0,
                        )
                        allow, reason, _ = self._filters_allow_reentry(ctx)
                        if not allow:
                            self.logger.info(f"[ReEntry] Filter blocked SHORT re-entry at {bar['close']:.2f}: {reason}")
                            self.log_decision(bar["time"], "1m", lid, "REENTRY_FILTER_BLOCK",
                                              f"Filter blocked — {reason}",
                                              category=DecisionEventCategory.TRADE_ACTION)
                            remaining.append(opp)
                            continue  # keep opportunity alive for next bar
                        self.logger.info(f"[ReEntry] Triggering SHORT re-entry at {bar['close']:.2f} (line={level:.2f}) — bearish candle")
                        self.log_decision(bar["time"], "1m", lid, "ENTRY",
                                          f"Re-entry SHORT @ {bar['close']:.2f} — bearish close below line={level:.2f} (max adverse={adverse:.1f}pts)",
                                          category=DecisionEventCategory.TRADE_ACTION)
                        trade = self._build_trade_from_context(ctx)
                        trade["is_reentry"] = True
                        trade["reentry_attempt"] = opp.get("reentry_attempt", 1)
                        self._store_and_emit_open(trade)
                        continue  # consumed

                remaining.append(opp)

            self._reentry_opportunities = remaining

    def _update_trade_sl(self, trade: dict[str, Any], new_sl: float):
        old_sl = trade['stop_loss']
        self.logger.info(f"[Strategy] Moving SL for {trade['trade_id']} to {new_sl}")

        # 1. Update In-Memory State
        trade['stop_loss'] = new_sl

        # 2. Update Database (this trade and any sibling trades from the same signal)
        try:
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
            self.logger.error(f"[Strategy] Failed to update SL in DB: {e}")
            self.analytics.capture_exception(e, {"op": "update_sl", "trade_id": trade['trade_id']})

        # 2b. Notify executor (live mode: sends modify_order to NinjaTrader)
        self.trade_manager.trade_executor.on_sl_update(trade['trade_id'], new_sl)

        if self.trade_logger:
            self.trade_logger.log(trade['trade_id'], "SL_UPDATE", f"{old_sl:.2f} → {new_sl:.2f}")
            self.trade_logger.log(trade['trade_id'], "CMD_SENT", f"modify_order SL={new_sl:.2f} → NinjaTrader")

        # 3. Notify Frontend
        # We emit a 'trade_update' event. You might need to handle this in JS.
        self.event_publisher.emit("trade_update", {
            "trade_id": trade['trade_id'],
            "stop_loss": new_sl,
            "pair": trade['pair']
        })

    # ----- Core bar processing -----

    def _on_strategy_bar(self, bar: dict[str, Any]):
        """
        1) for each strategy line, run triggers → may propose an EntryContext
        2) if proposed, run filters; if allowed, open trade and maybe remove the line

        NOTE: Trade exits are handled by TradeManager (backtest) or broker fills (live).
        The strategy reacts to TRADE_CLOSED domain events instead of polling bars.
        """
        with self.lock:
            # 1) evaluate lines via triggers
            for sid, line in list(self.strategy_lines.items()):
                opened = False
                proposed_ctx: EntryContext | None = None

                # run triggers in order until one proposes
                for trig in self.triggers:
                    proposed_ctx = trig(self, sid, line, bar)
                    if proposed_ctx is not None:
                        break

                if proposed_ctx is None:
                    # nothing to evaluate
                    continue

                # 3) run filters
                allow, _reason, hold = self._filters_allow_entry(proposed_ctx)
                if allow:
                    trade = self._build_trade_from_context(proposed_ctx)
                    if self.options.reentry_only:
                        trade["is_phantom"] = True
                    self._store_and_emit_open(trade)
                    opened = True
                elif hold:
                    # depth too shallow — reset trigger state so it can re-fire, keep line alive
                    self._reset_trigger_state(line)
                    continue

                # line removal policy
                self._maybe_remove_line(sid, opened)

    def _filters_allow_entry(self, ctx: EntryContext) -> tuple[bool, str, bool]:
        """Run all pluggable entry filters.
        Returns (allowed, reason, hold) where hold=True means the line should stay alive
        and re-evaluate (used by filters that need more depth/time rather than a hard block)."""
        for f in self.entry_filters:
            ok, reason = f(ctx)
            if not ok:
                hold = getattr(f, '_hold_on_block', False)
                return False, f"{f.__name__}: {reason}", hold
        return True, "ok", False

    def _filters_allow_reentry(self, ctx: EntryContext) -> tuple[bool, str, bool]:
        """Run entry filters for re-entry trades.
        Re-entries are a 'second chance' on the same setup, so they only
        apply the filters that make sense for re-entries:
        - time_range_filter (don't enter outside trading hours)
        - open_trades_limit_filter (don't enter if max open trades reached)
        - rollover_filter (don't enter during rollover)
        Filters like daily_trades_limit, min_cross_depth, and max_bounce
        are bypassed because they don't apply to re-entry context."""
        reentry_filter_names = {"time_range", "open_trades_limit", "rollover"}
        for f in self.entry_filters:
            if f.__name__ not in reentry_filter_names:
                continue
            ok, reason = f(ctx)
            if not ok:
                hold = getattr(f, '_hold_on_block', False)
                return False, f"{f.__name__}: {reason}", hold
        return True, "ok", False

    def _reset_trigger_state(self, line_state: dict[str, Any]):
        """Reset only trigger-specific state, keeping direction and extreme intact.
        Overridden by subclasses that manage additional trigger state (tsi_stage etc.)."""

    def _maybe_remove_line(self, line_id: Any, opened: bool):
        """Remove the evaluated strategy line depending on removal mode."""
        mode = self.options.line_removal_mode
        if mode == LineRemovalMode.NEVER:
            # keep the line and reset state, so it can re-trigger
            if line_id in self.strategy_lines:
                self._reset_line_state(self.strategy_lines[line_id])
            return
        if mode == LineRemovalMode.ON_EVALUATE or (mode == LineRemovalMode.ON_ENTER and opened):
            self.remove_strategy_line(line_id)

    # ------------------------------------------------------------------
    # Event-driven trade close (replaces _check_open_trades / handle_broker_exit_fill)
    # ------------------------------------------------------------------
    def on_event(self, event: DomainEvent) -> None:
        """EventSubscriber protocol: react to domain trade events."""
        if event.event_type == EventType.TRADE_CLOSED:
            self._on_trade_closed(event.payload)
        elif event.event_type in (EventType.TRADE_UPDATED, EventType.TRADE_ENTRY_UPDATED):
            self._on_trade_updated(event.payload)

    def _on_trade_updated(self, payload: dict[str, Any]) -> None:
        """Sync strategy trade dict with broker-reported updates (entry fill, SL move, etc.)."""
        with self.lock:
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

    def _on_trade_closed(self, payload: dict[str, Any]) -> None:
        """Update strategy state when a trade close event is published.

        This is the SINGLE code path for trade closes in both live and backtest:
        - Backtest: TradeManager._check_sl_tp / _check_session_end_close emits event
        - Live: TradeManager.handle_broker_fill emits event after NT fill
        """
        with self.lock:
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

            # Remove from strategy's open list so has_open becomes False
            self.open_trades = [t for t in self.open_trades if t is not trade]

            # Create re-entry opportunity on SL hit (mirrors old _check_open_trades logic)
            level = payload.get("line_level")
            if level is None:
                level = trade.get("line_level")
            # Prefer strategy trade's reentry_attempt (authoritative) over payload,
            # because multi-account account trades lack this field.
            attempt = trade.get("reentry_attempt", 0)
            if attempt == 0 and trade.get("is_reentry"):
                # Legacy trades without reentry_attempt field: treat as attempt 1
                attempt = 1

            if (
                (self.options.reentry_after_sl or self.options.reentry_only)
                and payload.get("result_type") == "SL"
                and attempt < self.options.max_reentry_attempts
            ) and level is not None:
                direction = trade["type"]
                # Guard against duplicate opportunities (duplicate events, retries, etc.)
                already_watching = any(
                    o["level"] == level and o["direction"] == direction
                    for o in self._reentry_opportunities
                )
                if not already_watching:
                    extreme = payload.get("extreme_excursion", payload.get("exit_price"))
                    self._reentry_opportunities.append({
                        "level": level,
                        "direction": direction,
                        "pair": trade["pair"],
                        "extreme_excursion": extreme,
                        "sl_bar_time": payload.get("exit_time", 0),
                        "reentry_attempt": attempt + 1,
                    })
                    self.logger.info(
                        f"[ReEntry] SL hit on {direction} @ {trade['pair']}. "
                        f"Watching level={level} for re-entry (attempt {attempt + 1}/{self.options.max_reentry_attempts})."
                    )

    def _check_phantom_exits(self, bar: dict[str, Any]) -> None:
        """Check SL/TP for phantom trades only.

        Real trades are monitored by TradeManager._check_sl_tp.
        Phantom trades are strategy-only constructs (no DB, no broker).
        """
        if self.is_warmup:
            return
        with self.lock:
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

                if t["type"] == "long":
                    if low <= t["stop_loss"]:
                        exit_price = t["stop_loss"]
                        result_type = "SL"
                        closed = True
                    elif high >= t["take_profit"]:
                        exit_price = t["take_profit"]
                        result_type = "TP"
                        closed = True
                else:
                    if high >= t["stop_loss"]:
                        exit_price = t["stop_loss"]
                        result_type = "SL"
                        closed = True
                    elif low <= t["take_profit"]:
                        exit_price = t["take_profit"]
                        result_type = "TP"
                        closed = True
                # NOTE: If both SL and TP are inside the same bar, SL always wins.
                # This is a conservative assumption since intrabar sequence is unknown.

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
                    # Reentry logic for phantom SL hits
                    attempt = t.get("reentry_attempt", 0)
                    if attempt == 0 and t.get("is_reentry"):
                        attempt = 1
                    if (
                        (self.options.reentry_after_sl or self.options.reentry_only)
                        and result_type == "SL"
                        and attempt < self.options.max_reentry_attempts
                    ):
                        level = t.get("line_level")
                        if level is not None:
                            extreme = bar["low"] if t["type"] == Direction.LONG else bar["high"]
                            self._reentry_opportunities.append({
                                "level": level,
                                "direction": t["type"],
                                "pair": t["pair"],
                                "extreme_excursion": extreme,
                                "sl_bar_time": bar["time"],
                                "reentry_attempt": attempt + 1,
                            })
                else:
                    remaining.append(t)
            self.open_trades = remaining

    # ----- Trade creation & persistence -----

    def _select_sl_level(self, distance: float) -> float:
        """Pick the smallest SL tier that is close enough to cover the distance.

        A level is accepted if ``level + sl_level_tolerance >= distance``,
        so the SL doesn't need to fully cover the extreme — just get close.
        Falls back to the largest tier.
        """
        for level in self.sl_levels:  # already sorted ascending
            if level + self.sl_level_tolerance >= distance:
                return level
        return self.sl_levels[-1]

    def _build_trade_from_context(self, ctx: EntryContext) -> dict[str, Any]:
        entry = ctx.close

        if self.sl_levels:
            # Tiered SL: compute distance to extreme, then pick smallest tier that covers it
            if ctx.is_long:
                distance = max(entry - ctx.extreme, self.min_stop_loss)
            else:
                distance = max(ctx.extreme - entry, self.min_stop_loss)

            eff_risk = self._select_sl_level(distance)
            self.logger.info(
                f"[Strategy] SL Selection | Dir: {ctx.direction} | "
                f"Entry: {entry:.2f} | Extreme: {ctx.extreme:.2f} | "
                f"Distance: {distance:.2f} pts | Selected SL: {eff_risk:.2f} pts "
                f"(levels: {self.sl_levels})"
            )

        elif self.fixed_stop_loss and self.fixed_stop_loss > 0:
            # Use Fixed Risk
            eff_risk = self.fixed_stop_loss
        else:
            # Use Dynamic Risk (Distance to Extreme + Extra Space)
            extra = self.extra_sl_space
            if ctx.is_long:
                raw_risk = max(entry - ctx.extreme, self.min_stop_loss)
            else:
                raw_risk = max(ctx.extreme - entry, self.min_stop_loss)
            eff_risk = raw_risk + extra

        # Apply Max Cap (if configured and not using tiered levels)
        if not self.sl_levels and self.max_stop_loss and self.max_stop_loss > 0 and eff_risk > self.max_stop_loss:
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
        """Calculate number of contracts/lots, matching NinjaTrader's logic."""
        if risk_per_contract <= 0:
            return 1.0 if not self.use_fractional_lots else 0.01
        # Use trade manager's account balance if available (for percentage-based risk compounding)
        account_balance = self.account_balance
        if self.trade_manager is not None:
            account_balance = self.trade_manager.account_balance
        risk_usd, risk_pct = self._get_current_risk()
        risk_budget = FinancialCalc.risk_budget(
            account_balance, risk_usd, risk_pct
        )
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
        # Use trade manager's account balance if available (for percentage-based risk compounding)
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
            "reentry_attempt": 0,
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

            # Also register with trade_manager so it can track SL/TP hits
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

    # ----- utils -----

    @staticmethod
    def _ts_to_dt(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
