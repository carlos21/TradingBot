from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.analytics import AnalyticsReporter, NoOpReporter
from src.dbexception import DBNotFoundException
from src.financial_calc import FinancialCalc
from src.types import Direction
from src.repositories.lines_repository import LineRepository
from src.repositories.trades_repository import TradeRepository
from src.repositories.line_trigger_state_repository import LineTriggerStateRepository, InMemoryLineTriggerStateRepository
from src.services.trade_manager import TradeManager
from zoneinfo import ZoneInfo

from src.strategies.entry_context import (
    EntryContext,
    EntryFilter,
    EntryTrigger,
)

# Re-export for backward compatibility - use FinancialCalc.DEFAULT_BE_THRESHOLD_POINTS
BE_TRESHOLD_POINTS = FinancialCalc.DEFAULT_BE_THRESHOLD_POINTS


class LineRemovalMode(str, Enum):
    ON_EVALUATE = "on_evaluate"   # remove line after we evaluated it
    ON_ENTER    = "on_enter"      # remove only if we actually opened a trade
    NEVER       = "never"         # never remove (we'll reset state for future triggers)


@dataclass
class BreakevenConfig:
    trigger_rr: float       # Risk:Reward ratio to trigger the move (e.g., 2.0)
    move_to_rr: float = 0.0 # Where to move SL in R terms (0.0 = Entry, 0.1 = Entry + small profit)


@dataclass
class StrategyOptions:
    line_removal_mode: LineRemovalMode = LineRemovalMode.ON_EVALUATE
    entry_filters: Optional[List[EntryFilter]] = None
    triggers: Optional[List[EntryTrigger]] = None
    breakeven: Optional[BreakevenConfig] = None
    reentry_breakeven: Optional[BreakevenConfig] = None  # breakeven config applied only to re-entry trades
    reentry_after_sl: bool = False        # re-enter if price comes back after a SL hit
    reentry_threshold: float = 60.0       # cancel re-entry if price goes this many pts past the line
    reentry_only: bool = False            # skip initial trade, only take re-entry trades


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
        socketio,
        line_repository: LineRepository,
        trade_repository: TradeRepository,
        trade_manager: TradeManager,
        extra_sl_space: float,
        point_value: float,
        account_balance: float,
        risk_per_trade: Optional[float] = None,
        risk_pct_per_trade: Optional[float] = None,
        fixed_stop_loss: Optional[float] = None,
        max_stop_loss: Optional[float] = None,
        strategy_tf: str = "5m",
        options: Optional[StrategyOptions] = None,
        htf_fetcher: Optional[Callable[..., Optional[dict]]] = None,
        sl_levels: Optional[List[float]] = None,
        sl_level_tolerance: float = 5.0,
        min_cross_depth: float = 0.0,
        rr_ratio: float = 5.0,
        trade_logger=None,
        analytics: AnalyticsReporter = None,
        trigger_state_repo: LineTriggerStateRepository = None,
    ):
        self.min_stop_loss = float(min_stop_loss)
        self.max_bounce    = float(max_bounce)
        self.min_cross_depth = float(min_cross_depth)
        self.rr_ratio = float(rr_ratio)
        self.point_value = float(point_value)
        self.account_balance = float(account_balance)
        self.risk_per_trade = risk_per_trade
        self.risk_pct_per_trade = risk_pct_per_trade
        self.socketio      = socketio
        self.line_repository  = line_repository
        self.trade_repository = trade_repository
        self.trade_manager = trade_manager
        self.extra_sl_space = float(extra_sl_space)
        self.fixed_stop_loss = fixed_stop_loss
        self.max_stop_loss = max_stop_loss
        self.sl_levels = sorted(sl_levels) if sl_levels else None
        self.trade_logger = trade_logger
        self.analytics = analytics or NoOpReporter()
        self.sl_level_tolerance = float(sl_level_tolerance)
        self.trigger_state_repo: LineTriggerStateRepository = trigger_state_repo or InMemoryLineTriggerStateRepository()

        self.strategy_lines: Dict[Any, Dict[str, Any]] = {}   # id -> { level, direction, extreme, creation_ts }
        self.open_trades: List[Dict[str, Any]] = []
        self.total_pnl = 0.0
        self.lock = RLock()

        # parse TF like "5m" or "1h"
        num, unit = int(strategy_tf[:-1]), strategy_tf[-1]
        self.strategy_window = num * (60 if unit == "m" else 3600)
        self._buf: List[Dict[str, Any]] = []
        self._group_start: Optional[int] = None

        self.options = options or StrategyOptions()
        # Let subclasses decide the triggers (they may depend on attrs set after super().__init__)
        self.triggers: List[EntryTrigger] = list(self.options.triggers) if self.options.triggers else []
        # Filters can be taken straight from options
        self.entry_filters: List[EntryFilter] = list(self.options.entry_filters or [])

        # Pending re-entry opportunities created when a SL is hit
        self._reentry_opportunities: List[Dict[str, Any]] = []

        # Optional dependency for multi-TF checks
        self.htf_fetcher = htf_fetcher

    # ----- Decision log (no-op; subclass LiquidityStrategyV2 overrides this) -----

    def log_decision(self, bar_time: int, tf: str, line_id: str, event: str, details: str):
        pass  # overridden by LiquidityStrategyV2 to write to self.decision_logs

    # ----- Public small API for runtime tweaks -----

    def set_entry_filters(self, filters: List[EntryFilter]):
        with self.lock:
            self.entry_filters = list(filters)

    def set_triggers(self, triggers: List[EntryTrigger]):
        with self.lock:
            self.triggers = list(triggers)

    # ----- Line management -----

    def add_strategy_line(self, id: Any, level: float, creation_timestamp: float = 0.0): # <--- CHANGED
        """
        direction: 'long' | 'short'
        creation_timestamp: Epoch seconds when this line became valid
        """
        print(f"[Strategy] ➕ add_strategy_line id={id} level={level} ts={creation_timestamp}")
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
        self.trigger_state_repo.delete(str(id))
        try:
            self.line_repository.delete_line(id)
        except DBNotFoundException:
            pass
        self.socketio.emit("line_removed", {"id": id})

    def update_strategy_line(self, id: Any, level: float):
        """Update an existing strategy line's price level."""
        with self.lock:
            if id in self.strategy_lines:
                self.strategy_lines[id]["level"] = float(level)
                print(f"[Strategy] ✏️ update_strategy_line id={id} new_level={level}")
        self.socketio.emit("line_updated", {"id": id, "level": level})

    def _persist_all_line_states(self):
        """Write current trigger state for every active line to the repo."""
        pair = self.trade_manager.pair
        for line_id, state in self.strategy_lines.items():
            self.trigger_state_repo.save(str(line_id), pair, state)

    def restore_trigger_states(self, pair: str):
        """Overlay persisted trigger states onto bootstrapped lines."""
        with self.lock:
            saved = self.trigger_state_repo.load_all(pair)
            restored = 0
            for line_id, line_state in self.strategy_lines.items():
                persisted = saved.get(str(line_id))
                if persisted:
                    # Keep level/creation_ts from fresh bootstrap (authoritative for geometry),
                    # restore everything else (direction, extreme, trigger stages, etc.)
                    level = line_state["level"]
                    creation_ts = line_state["creation_ts"]
                    line_state.update(persisted)
                    line_state["level"] = level
                    line_state["creation_ts"] = creation_ts
                    restored += 1
            if restored:
                print(f"[Strategy] Restored trigger state for {restored}/{len(self.strategy_lines)} lines")

    def restore_open_trades(self):
        """Sync open trades from TradeManager into strategy's in-memory list."""
        with self.lock:
            if not self.open_trades and self.trade_manager.open_trades:
                for t in self.trade_manager.open_trades:
                    if t.get('status') == 'open':
                        self.open_trades.append(dict(t))
                if self.open_trades:
                    print(f"[Strategy] Restored {len(self.open_trades)} open trade(s) from DB")

    def restore_reentry_opportunities(self, pair: str, reference_time: datetime = None):
        """Rebuild pending re-entry opportunities from recently SL'd trades in DB."""
        if not (self.options.reentry_after_sl or self.options.reentry_only):
            return

        from datetime import timedelta
        ref = reference_time or datetime.now(timezone.utc)
        cutoff = ref - timedelta(hours=4)

        trades = self.trade_repository.list_trades(pair)
        restored = 0
        for t in trades:
            if t.result is None or t.result >= 0:
                continue
            if t.exit_time is None or t.exit_time < cutoff:
                continue
            if t.params and t.params.get("is_reentry"):
                continue
            line_level = t.params.get("line_level") if t.params else None
            if line_level is None:
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
            })
            restored += 1
            print(f"[ReEntry] Restored re-entry watch: {t.trade_type} @ level={line_level:.2f}")
        if restored:
            print(f"[Strategy] Restored {restored} re-entry opportunity(ies) from DB")

    def _reset_line_state(self, line_state: Dict[str, Any]):
        """If we keep the line, reset so it can trigger again in the future."""
        line_state["extreme"] = 0.0

    # ----- Aggregation -----

    def _aggregate_bars(self, bars: List[Dict[str, Any]], window_start: int, window_secs: int) -> Dict[str, Any]:
        from src.utils.bar_aggregator import BarAggregator
        return BarAggregator.aggregate_with_window(bars, window_start, window_secs)

    def on_raw_bar(self, bar: Dict[str, Any]):
        """
        Feed raw stream bars (assumed 1s or tick granularity).
        We aggregate to the configured TF and then process strategy logic on each completed bar.
        """

        # Check breakeven conditions on every raw bar before aggregation
        if self.options.breakeven or self.options.reentry_breakeven:
            self._check_breakeven(bar)

        # Close any open trades at session end
        self._check_session_end_close(bar)

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

    def _check_breakeven(self, bar: Dict[str, Any]):
        for trade in self.open_trades:
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

            elif trade['type'] in ('short', 'sell'):
                trigger_price = entry - (risk * cfg.trigger_rr)

                if bar['low'] <= trigger_price:
                    proposed_sl = entry - (risk * cfg.move_to_rr)
                    if proposed_sl < current_sl:
                        new_sl = proposed_sl
                        should_update = True

            if should_update and new_sl is not None:
                self._update_trade_sl(trade, new_sl)

    def _check_session_end_close(self, bar: Dict[str, Any]):
        """Close any open trades if the bar is at or past the NY session end (15:00 NY)."""
        session_end = self.trade_manager._session_end_time
        session_tz = self.trade_manager._session_tz
        if not session_end or not session_tz:
            return

        bar_dt = datetime.fromtimestamp(bar["time"], tz=session_tz)
        if bar_dt.time() < session_end:
            return

        remaining = []
        for t in self.open_trades:
            if t["status"] != "open" or t["pair"] != bar["pair"]:
                remaining.append(t)
                continue

            exit_price = bar["close"]
            risk = t.get("risk", 1.0)
            if risk <= 0:
                risk = 1.0
            contracts = t.get("contracts") or 1

            # Use unified FinancialCalc for ALL close metrics (single source of truth)
            r_result, t_fees, t_pnl_usd, _ = FinancialCalc.calculate_close_metrics(
                direction=Direction.from_string(t["type"]),
                entry_price=t["entry"],
                exit_price=exit_price,
                stop_loss=t["stop_loss"],
                take_profit=t["take_profit"],
                risk_points=risk,
                contracts=contracts,
                point_value=self.point_value,
            )
            result_type = FinancialCalc.calculate_session_end_result_type(r_result)
            t.update(status="closed", result=r_result, exit_time=bar["time"], exit_price=exit_price, fees=t_fees, pnl_usd=t_pnl_usd, result_type=result_type)
            try:
                self.trade_repository.close_trade(
                    trade_id=t["trade_id"],
                    exit_price=exit_price,
                    exit_time=self._ts_to_dt(bar["time"]),
                    result=r_result,
                    result_type=result_type,
                    fees=t_fees,
                    pnl_usd=t_pnl_usd,
                )
                print(f"[Strategy] 🕐 SESSION END closed {t['trade_id']} @ {exit_price} (Result: {r_result:.2f}R)")
            except Exception as e:
                print(f"[Strategy] ❌ Failed to persist session-end close for {t['trade_id']}: {e}")
                self.analytics.capture_exception(e, {"op": "strategy_session_close", "trade_id": t["trade_id"]})
                if self.trade_logger:
                    self.trade_logger.log(t["trade_id"], "ERROR", str(e))

            # Note: Logging is handled by TradeManager to avoid duplicates

            self.socketio.emit("trade_close", t)

        self.open_trades = remaining

    def _check_reentry_opportunities(self, bar: Dict[str, Any]):
        """
        On every 1m bar: update adverse excursion tracking for pending re-entry
        opportunities and trigger a new trade if price closes back through the line.
        Bypasses all normal entry filters (including daily trade limit).
        """
        threshold = self.options.reentry_threshold
        has_open = any(t["status"] == "open" for t in self.open_trades)

        remaining = []
        for opp in self._reentry_opportunities:
            if opp["pair"] != bar["pair"]:
                remaining.append(opp)
                continue

            if has_open:
                # Keep opportunity alive but don't enter while a trade is open
                remaining.append(opp)
                continue

            level = opp["level"]
            direction = opp["direction"]

            lid = f"reentry@{level:.2f}"

            if direction == "long":
                opp["extreme_excursion"] = min(opp["extreme_excursion"], bar["low"])
                adverse = level - opp["extreme_excursion"]
                if adverse > threshold:
                    print(f"[ReEntry] ❌ Cancelled LONG re-entry: price went {adverse:.1f} pts below line {level:.2f}")
                    self.log_decision(bar["time"], "1m", lid, "REENTRY_CANCEL",
                                      f"Cancelled — price went {adverse:.1f}pts below line={level:.2f} (threshold={threshold:.0f}pts)")
                    continue  # drop opportunity
                if bar["close"] > level:
                    print(f"[ReEntry] ✅ Triggering LONG re-entry at {bar['close']:.2f} (line={level:.2f})")
                    self.log_decision(bar["time"], "1m", lid, "ENTRY",
                                      f"Re-entry LONG @ {bar['close']:.2f} — close above line={level:.2f} (max adverse={adverse:.1f}pts)")
                    ctx = EntryContext(
                        strategy=self, line_id=None, direction=Direction.LONG, level=level,
                        bar=bar, close=bar["close"], low=bar["low"], high=bar["high"],
                        extreme=level, cross_depth=0.0,
                    )
                    trade = self._build_trade_from_context(ctx)
                    trade["is_reentry"] = True
                    self._store_and_emit_open(trade)
                    continue  # consumed

            else:  # short
                opp["extreme_excursion"] = max(opp["extreme_excursion"], bar["high"])
                adverse = opp["extreme_excursion"] - level
                if adverse > threshold:
                    print(f"[ReEntry] ❌ Cancelled SHORT re-entry: price went {adverse:.1f} pts above line {level:.2f}")
                    self.log_decision(bar["time"], "1m", lid, "REENTRY_CANCEL",
                                      f"Cancelled — price went {adverse:.1f}pts above line={level:.2f} (threshold={threshold:.0f}pts)")
                    continue  # drop opportunity
                if bar["close"] < level:
                    print(f"[ReEntry] ✅ Triggering SHORT re-entry at {bar['close']:.2f} (line={level:.2f})")
                    self.log_decision(bar["time"], "1m", lid, "ENTRY",
                                      f"Re-entry SHORT @ {bar['close']:.2f} — close below line={level:.2f} (max adverse={adverse:.1f}pts)")
                    ctx = EntryContext(
                        strategy=self, line_id=None, direction=Direction.SHORT, level=level,
                        bar=bar, close=bar["close"], low=bar["low"], high=bar["high"],
                        extreme=level, cross_depth=0.0,
                    )
                    trade = self._build_trade_from_context(ctx)
                    trade["is_reentry"] = True
                    self._store_and_emit_open(trade)
                    continue  # consumed

            remaining.append(opp)

        self._reentry_opportunities = remaining

    def _update_trade_sl(self, trade: Dict[str, Any], new_sl: float):
        old_sl = trade['stop_loss']
        print(f"[Strategy] 🛡️ Moving SL for {trade['trade_id']} to {new_sl}")

        # 1. Update In-Memory State
        trade['stop_loss'] = new_sl

        # 2. Update Database
        try:
            self.trade_repository.update_stop_loss(trade['trade_id'], new_sl)
            self.trade_manager.update_local_trade_sl(trade['trade_id'], new_sl)
        except Exception as e:
            print(f"[Strategy] ⚠️ Failed to update SL in DB: {e}")
            self.analytics.capture_exception(e, {"op": "update_sl", "trade_id": trade['trade_id']})

        # 2b. Notify executor (live mode: sends modify_order to NinjaTrader)
        self.trade_manager.trade_executor.on_sl_update(trade['trade_id'], new_sl)

        if self.trade_logger:
            self.trade_logger.log(trade['trade_id'], "SL_UPDATE", f"{old_sl:.2f} → {new_sl:.2f}")
            self.trade_logger.log(trade['trade_id'], "CMD_SENT", f"modify_order SL={new_sl:.2f} → NinjaTrader")

        # 3. Notify Frontend
        # We emit a 'trade_update' event. You might need to handle this in JS.
        self.socketio.emit("trade_update", {
            "trade_id": trade['trade_id'],
            "stop_loss": new_sl,
            "pair": trade['pair']
        })

    # ----- Core bar processing -----

    def _on_strategy_bar(self, bar: Dict[str, Any]):
        """
        1) check exits on open trades
        2) for each strategy line, run triggers → may propose an EntryContext
        3) if proposed, run filters; if allowed, open trade and maybe remove the line
        """
        with self.lock:
            # 1) exits first
            self._check_open_trades(bar)

            # 2) evaluate lines via triggers
            for sid, line in list(self.strategy_lines.items()):
                opened = False
                proposed_ctx: Optional[EntryContext] = None

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

    def _filters_allow_entry(self, ctx: EntryContext) -> Tuple[bool, str, bool]:
        """Run all pluggable entry filters.
        Returns (allowed, reason, hold) where hold=True means the line should stay alive
        and re-evaluate (used by filters that need more depth/time rather than a hard block)."""
        for f in self.entry_filters:
            ok, reason = f(ctx)
            if not ok:
                hold = getattr(f, '_hold_on_block', False)
                return False, f"{f.__name__}: {reason}", hold
        return True, "ok", False

    def _reset_trigger_state(self, line_state: Dict[str, Any]):
        """Reset only trigger-specific state, keeping direction and extreme intact.
        Overridden by subclasses that manage additional trigger state (tsi_stage etc.)."""
        pass

    def _maybe_remove_line(self, line_id: Any, opened: bool):
        """Remove the evaluated strategy line depending on removal mode."""
        mode = self.options.line_removal_mode
        if mode == LineRemovalMode.NEVER:
            # keep the line and reset state, so it can re-trigger
            if line_id in self.strategy_lines:
                self._reset_line_state(self.strategy_lines[line_id])
            return
        if mode == LineRemovalMode.ON_EVALUATE:
            self.remove_strategy_line(line_id)
        elif mode == LineRemovalMode.ON_ENTER and opened:
            self.remove_strategy_line(line_id)

    # ----- Exits -----

    def _check_open_trades(self, bar: Dict[str, Any]):
        remaining: List[Dict[str, Any]] = []
        for t in self.open_trades:
            if t["status"] != "open":
                continue
            low, high = bar["low"], bar["high"]
            
            # Calculate R-multiple dynamically
            risk = t.get("risk", 1.0)
            if risk == 0: risk = 1.0

            closed = False
            exit_price = 0.0
            r_result = 0.0
            result_type = None

            exit_price = None
            hit_sl = False
            hit_tp = False

            if t["type"] == "long":
                if low <= t["stop_loss"]:
                    exit_price = t["stop_loss"]
                    hit_sl = True
                    closed = True
                elif high >= t["take_profit"]:
                    exit_price = t["take_profit"]
                    hit_tp = True
                    closed = True
            else:  # short
                if high >= t["stop_loss"]:
                    exit_price = t["stop_loss"]
                    hit_sl = True
                    closed = True
                elif low <= t["take_profit"]:
                    exit_price = t["take_profit"]
                    hit_tp = True
                    closed = True

            if closed and exit_price is not None:
                # Use unified FinancialCalc for ALL close metrics (single source of truth)
                contracts = t.get("contracts") or 1
                risk_pts = t.get("risk", 0) or 1.0
                
                r_result, t_fees, t_pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
                    direction=Direction.from_string(t["type"]),
                    entry_price=t["entry"],
                    exit_price=exit_price,
                    stop_loss=t["stop_loss"],
                    take_profit=t["take_profit"],
                    risk_points=risk_pts,
                    contracts=contracts,
                    point_value=self.point_value,
                )
                t.update(status="closed", result=r_result, exit_time=bar["time"], exit_price=exit_price, fees=t_fees, pnl_usd=t_pnl_usd, result_type=result_type)
                is_phantom = t.get("is_phantom", False)

                # Register re-entry opportunity when SL is hit (not on TP, not on re-entry trades)
                # For reentry_only mode, phantom trades always create reentry opportunities
                if (
                    (self.options.reentry_after_sl or is_phantom)
                    and r_result < 0
                    and not t.get("is_reentry", False)
                ):
                    level = t.get("line_level")
                    if level is not None:
                        direction = t["type"]
                        self._reentry_opportunities.append({
                            "level": level,
                            "direction": direction,
                            "pair": t["pair"],
                            # track the most adverse price seen since SL hit
                            "extreme_excursion": bar["low"] if direction == Direction.LONG else bar["high"],
                        })
                        print(f"[ReEntry] 🎯 SL hit on {direction} @ {t['pair']}. Watching level={level} for re-entry.")
                        self.log_decision(
                            bar["time"], "1m", f"reentry@{level:.2f}",
                            "REENTRY_WATCH",
                            f"SL hit on {direction} trade — watching level={level:.2f} for re-entry (threshold={self.options.reentry_threshold:.0f}pts)"
                        )

                if is_phantom:
                    print(f"[Strategy] 👻 Phantom trade closed (Result: {r_result:.2f}R) — not persisted")
                else:
                    try:
                        self.trade_repository.close_trade(
                            trade_id=t["trade_id"],
                            exit_price=exit_price,
                            exit_time=self._ts_to_dt(bar["time"]),
                            result=r_result,
                            result_type=result_type,
                            fees=t_fees,
                            pnl_usd=t_pnl_usd,
                        )
                        print(f"[Strategy] 💾 Persisted CLOSE for {t['trade_id']} (Result: {r_result:.2f}R, Type: {result_type})")
                    except Exception as e:
                        print(f"[Strategy] ❌ Failed to persist close for {t['trade_id']}: {e}")
                        self.analytics.capture_exception(e, {"op": "strategy_persist_close", "trade_id": t["trade_id"]})
                        if self.trade_logger:
                            self.trade_logger.log(t["trade_id"], "ERROR", str(e))

                    # Note: SL_HIT/TP_HIT/CLOSE logging is handled by TradeManager to avoid duplicates

                    self.trade_manager.trade_executor.on_trade_close(t['trade_id'], exit_price)
                    self.socketio.emit("trade_close", t)
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

    def _build_trade_from_context(self, ctx: EntryContext) -> Dict[str, Any]:
        entry = ctx.close

        if self.sl_levels:
            # Tiered SL: compute distance to extreme, then pick smallest tier that covers it
            if ctx.is_long:
                distance = max(entry - ctx.extreme, self.min_stop_loss)
            else:
                distance = max(ctx.extreme - entry, self.min_stop_loss)

            eff_risk = self._select_sl_level(distance)
            print(
                f"[Strategy] 📏 SL Selection | Dir: {ctx.direction} | "
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
        if not self.sl_levels and self.max_stop_loss and self.max_stop_loss > 0:
            if eff_risk > self.max_stop_loss:
                print(f"[Strategy] ⚠️ Risk {eff_risk:.2f} exceeds Max {self.max_stop_loss}. Capping it.")
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

    def _calc_contracts(self, risk_per_contract: float) -> int:
        """Calculate number of contracts, matching NinjaTrader's logic."""
        if risk_per_contract <= 0:
            return 1
        # Use trade manager's account balance if available (for percentage-based risk compounding)
        account_balance = self.account_balance
        if self.trade_manager is not None:
            account_balance = self.trade_manager.account_balance
        risk_budget = FinancialCalc.risk_budget(
            account_balance, self.risk_per_trade, self.risk_pct_per_trade
        )
        if risk_budget <= 0:
            return 1
        return FinancialCalc.contracts(risk_budget, risk_per_contract)

    def _make_trade_dict(
        self,
        bar: Dict[str, Any],
        trade_type: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        risk: float,
    ) -> Dict[str, Any]:
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
        }

    def _store_and_emit_open(self, trade: Dict[str, Any]):
        if trade.get("is_phantom"):
            trade["trade_id"] = f"phantom-{trade['entry_time']}"
            self.open_trades.append(trade)
            print(f"[Strategy] 👻 Phantom trade opened @ {trade['entry']:.2f} (reentry_only mode)")
            return

        td = self.trade_repository.insert_trade(
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
            risk_dollars=trade.get("risk_dollars"),
            risk_pct=trade.get("risk_pct"),
            contracts=trade.get("contracts"),
        )
        trade["trade_id"] = td.trade_id
        self.open_trades.append(trade)

        # Also register with trade_manager so it can track SL/TP hits
        if self.trade_manager:
            tm_trade = {
                'trade_id':    trade["trade_id"],
                'pair':        trade["pair"],
                'type':        trade["type"],
                'entry':       trade["entry"],
                'stop_loss':   trade["stop_loss"],
                'take_profit': trade["take_profit"],
                'risk':        trade["risk"],
                'risk_dollars': trade.get("risk_dollars"),
                'risk_pct':    trade.get("risk_pct"),
                'contracts':   trade.get("contracts"),
                'entry_time':  trade["entry_time"],
                'status':      'open'
            }
            self.trade_manager.open_trades.append(tm_trade)
            self.trade_manager._monitored_trades.add(trade["trade_id"])

        self.socketio.emit("trade_open", {**trade})
        self.trade_manager.trade_executor.on_trade_open(trade)
        if self.trade_logger:
            self.trade_logger.log(trade["trade_id"], "CMD_SENT", "place_order → NinjaTrader")

    def _store_and_emit_close(self, trade: Dict[str, Any]):
        contracts = trade.get("contracts") or 1
        risk_pts = trade.get("risk", 0) or 1.0
        t_fees = FinancialCalc.fees(contracts)
        t_pnl_usd = FinancialCalc.pnl_usd(contracts, trade["result"], risk_pts, self.point_value, t_fees)
        trade["fees"] = t_fees
        trade["pnl_usd"] = t_pnl_usd
        # Determine result_type if not already set (use unified FinancialCalc)
        result_type = trade.get("result_type")
        if not result_type:
            entry = trade.get("entry")
            sl = trade.get("stop_loss")
            tp = trade.get("take_profit")
            exit_px = trade.get("exit_price")
            result_type = FinancialCalc.determine_result_type(
                exit_price=exit_px,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
            )
            trade["result_type"] = result_type
        self.socketio.emit("trade_close", trade)
        self.trade_repository.close_trade(
            trade_id=trade["trade_id"],
            exit_price=trade["exit_price"],
            exit_time=self._ts_to_dt(trade["exit_time"]),
            result=trade["result"],
            result_type=result_type,
            fees=t_fees,
            pnl_usd=t_pnl_usd,
        )

    # ----- utils -----

    @staticmethod
    def _ts_to_dt(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc)