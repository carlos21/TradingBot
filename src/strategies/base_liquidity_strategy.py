from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.dbexception import DBNotFoundException
from src.repositories.lines_repository import LineRepository
from src.repositories.trades_repository import TradeRepository
from src.services.trade_manager import TradeManager
from src.strategies.entry_context import (
    EntryContext,
    EntryFilter,
    EntryTrigger,
)


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
        fixed_stop_loss: Optional[float] = None,
        max_stop_loss: Optional[float] = None,
        strategy_tf: str = "5m",
        options: Optional[StrategyOptions] = None,
        htf_fetcher: Optional[Callable[..., Optional[dict]]] = None,
        sl_levels: Optional[List[float]] = None,
    ):
        self.min_stop_loss = float(min_stop_loss)
        self.max_bounce    = float(max_bounce)
        self.socketio      = socketio
        self.line_repository  = line_repository
        self.trade_repository = trade_repository
        self.trade_manager = trade_manager
        self.extra_sl_space = float(extra_sl_space)
        self.fixed_stop_loss = fixed_stop_loss
        self.max_stop_loss = max_stop_loss
        self.sl_levels = sorted(sl_levels) if sl_levels else None

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

        # Optional dependency for multi-TF checks
        self.htf_fetcher = htf_fetcher

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
            self.strategy_lines[id] = {
                "level":       float(level),
                "direction":   None,
                "extreme":     0.0,
                "creation_ts": float(creation_timestamp),
            }

    def remove_strategy_line(self, id: Any):
        with self.lock:
            self.strategy_lines.pop(id, None)
        try:
            self.line_repository.delete_line(id)
        except DBNotFoundException:
            pass
        self.socketio.emit("line_removed", {"id": id})

    def _reset_line_state(self, line_state: Dict[str, Any]):
        """If we keep the line, reset so it can trigger again in the future."""
        line_state["extreme"] = 0.0

    # ----- Aggregation -----

    def _aggregate_bars(self, bars: List[Dict[str, Any]], window_start: int, window_secs: int) -> Dict[str, Any]:
        high   = max(b["high"] for b in bars)
        low    = min(b["low"] for b in bars)
        open_  = bars[0]["open"]
        close_ = bars[-1]["close"]
        volume = sum(b["volume"] for b in bars)
        pair   = bars[0]["pair"]
        return {
            "time":   window_start + window_secs,
            "open":   open_,
            "high":   high,
            "low":    low,
            "close":  close_,
            "volume": volume,
            "pair":   pair,
        }

    def on_raw_bar(self, bar: Dict[str, Any]):
        """
        Feed raw stream bars (assumed 1s or tick granularity).
        We aggregate to the configured TF and then process strategy logic on each completed bar.
        """

        # Check breakeven conditions on every raw bar before aggregation
        if self.options.breakeven:
            self._check_breakeven(bar)

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
        cfg = self.options.breakeven
        if not cfg:
            return

        for trade in self.open_trades:
            if trade['status'] != 'open':
                continue
            if trade['pair'] != bar['pair']:
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

    def _update_trade_sl(self, trade: Dict[str, Any], new_sl: float):
        print(f"[Strategy] 🛡️ Moving SL for {trade['trade_id']} to {new_sl}")
        
        # 1. Update In-Memory State
        trade['stop_loss'] = new_sl
        
        # 2. Update Database
        try:
            self.trade_repository.update_stop_loss(trade['trade_id'], new_sl)
            self.trade_manager.update_local_trade_sl(trade['trade_id'], new_sl)
        except Exception as e:
            print(f"[Strategy] ⚠️ Failed to update SL in DB: {e}")

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
                allow, _reason = self._filters_allow_entry(proposed_ctx)
                if allow:
                    trade = self._build_trade_from_context(proposed_ctx)
                    self._store_and_emit_open(trade)
                    opened = True

                # line removal policy
                self._maybe_remove_line(sid, opened)

    def _filters_allow_entry(self, ctx: EntryContext) -> Tuple[bool, str]:
        """Run all pluggable entry filters. If any returns (False, reason), block the entry."""
        for f in self.entry_filters:
            ok, reason = f(ctx)
            if not ok:
                return False, reason
        return True, "ok"

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

            if t["type"] == "long":
                if low <= t["stop_loss"]:
                    # Hit SL
                    exit_price = t["stop_loss"]
                    pnl = exit_price - t["entry"]
                    r_result = pnl / risk
                    closed = True
                elif high >= t["take_profit"]:
                    # Hit TP
                    exit_price = t["take_profit"]
                    pnl = exit_price - t["entry"]
                    r_result = pnl / risk
                    closed = True
            else:  # short
                if high >= t["stop_loss"]:
                    # Hit SL
                    exit_price = t["stop_loss"]
                    pnl = t["entry"] - exit_price
                    r_result = pnl / risk
                    closed = True
                elif low <= t["take_profit"]:
                    # Hit TP
                    exit_price = t["take_profit"]
                    pnl = t["entry"] - exit_price
                    r_result = pnl / risk
                    closed = True

            if closed:
                t.update(status="closed", result=r_result, exit_time=bar["time"], exit_price=exit_price)
                
                # FIX: Persist the close to DB immediately
                try:
                    self.trade_repository.close_trade(
                        trade_id=t["trade_id"],
                        exit_price=exit_price,
                        exit_time=self._ts_to_dt(bar["time"]),
                        result=r_result
                    )
                    print(f"[Strategy] 💾 Persisted CLOSE for {t['trade_id']} (Result: {r_result:.2f}R)")
                except Exception as e:
                    print(f"[Strategy] ❌ Failed to persist close for {t['trade_id']}: {e}")

                self.socketio.emit("trade_close", t)
            else:
                remaining.append(t)

        self.open_trades = remaining

    # ----- Trade creation & persistence -----

    def _select_sl_level(self, distance: float) -> float:
        """Pick the smallest SL tier >= distance. Falls back to the largest tier."""
        for level in self.sl_levels:  # already sorted ascending
            if level >= distance:
                return level
        return self.sl_levels[-1]

    def _build_trade_from_context(self, ctx: EntryContext) -> Dict[str, Any]:
        entry = ctx.close

        if self.sl_levels:
            # Tiered SL: compute distance to extreme, then pick smallest tier that covers it
            if ctx.direction == "long":
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
            if ctx.direction == "long":
                raw_risk = max(entry - ctx.extreme, self.min_stop_loss)
            else:
                raw_risk = max(ctx.extreme - entry, self.min_stop_loss)
            eff_risk = raw_risk + extra

        # Apply Max Cap (if configured and not using tiered levels)
        if not self.sl_levels and self.max_stop_loss and self.max_stop_loss > 0:
            if eff_risk > self.max_stop_loss:
                print(f"[Strategy] ⚠️ Risk {eff_risk:.2f} exceeds Max {self.max_stop_loss}. Capping it.")
                eff_risk = self.max_stop_loss

        if ctx.direction == "long":
            sl = entry - eff_risk
            tp = entry + 4 * eff_risk
            return self._make_trade_dict(ctx.bar, "long", entry, sl, tp, eff_risk)
        else:
            sl = entry + eff_risk
            tp = entry - 4 * eff_risk
            return self._make_trade_dict(ctx.bar, "short", entry, sl, tp, eff_risk)

    def _make_trade_dict(
        self,
        bar: Dict[str, Any],
        trade_type: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        risk: float,
    ) -> Dict[str, Any]:
        return {
            "pair":        bar["pair"],
            "type":        trade_type,
            "entry":       entry,
            "stop_loss":   stop_loss,
            "orig_sl":     stop_loss,
            "take_profit": take_profit,
            "risk":        risk,
            "status":      "open",
            "entry_time":  bar["time"],
        }

    def _store_and_emit_open(self, trade: Dict[str, Any]):
        td = self.trade_repository.insert_trade(
            pair=trade["pair"],
            trade_type=trade["type"],
            entry_price=trade["entry"],
            stop_loss=trade["stop_loss"],
            take_profit=trade["take_profit"],
            risk=trade["risk"],
            entry_time=self._ts_to_dt(trade["entry_time"]),
            params={},
        )
        trade["trade_id"] = td.trade_id
        self.open_trades.append(trade)
        self.socketio.emit("trade_open", {**trade})

    def _store_and_emit_close(self, trade: Dict[str, Any]):
        self.socketio.emit("trade_close", trade)
        self.trade_repository.close_trade(
            trade_id=trade["trade_id"],
            exit_price=trade["exit_price"],
            exit_time=self._ts_to_dt(trade["exit_time"]),
            result=trade["result"],
        )

    # ----- utils -----

    @staticmethod
    def _ts_to_dt(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc)