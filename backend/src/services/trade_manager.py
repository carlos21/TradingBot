# src/trade_manager.py

import contextlib
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from src.analytics import AnalyticsReporter, NoOpReporter
from src.application.ports import EventPublisher
from src.application.use_cases.broker_fill_handler import BrokerFillHandler
from src.application.use_cases.trade_close_use_case import TradeCloseUseCase
from src.application.use_cases.trade_open_use_case import TradeOpenUseCase
from src.domain.repositories import TradeRepository
from src.domain.types import Direction
from src.financial_calc import FinancialCalc
from src.notifier import NoOpNotifier, Notifier
from src.services.trade_executor import NoOpExecutor, TradeExecutor
from src.utils.app_logger import ILogger


class TradeManager:
    """
    Manages the lifecycle of open trades: SL/TP detection, session close,
    broker fill handling, and stream-end cleanup.

    Core close logic is delegated to TradeCloseUseCase.
    Core open logic is delegated to TradeOpenUseCase.
    Broker fill logic is delegated to BrokerFillHandler.
    """

    # Sources that are user- or broker-controlled and should not be auto-closed
    # by Python's session-end timer (broker/NinjaTrader manages their lifecycle).
    USER_CONTROLLED_SOURCES = ("manual", "test")

    def __init__(self, trade_repository: TradeRepository, socketio: EventPublisher | None = None,
                 point_value: float = 0.0, account_balance: float = 0.0,
                 logger: ILogger | None = None,
                 risk_per_trade: float = None, risk_pct_per_trade: float = None,
                 pair: str = 'MNQ',
                 session_end_time: str = None, session_tz: str = None,
                 broker_mode: str = 'futures', broker_spread: float = 0.0,
                 use_fractional_lots: bool = False,
                 fee_per_rt: float = FinancialCalc.DEFAULT_FEE_PER_RT,
                 trade_executor: TradeExecutor = None,
                 trade_logger=None,
                 notifier: Notifier = None,
                 analytics: AnalyticsReporter = None,
                 accounts_repo=None,
                 instrument: str | None = None,
                 live_mode: bool = False):
        self.open_trades = []
        self.trade_repository = trade_repository
        self.socketio         = socketio
        self.pair             = pair
        self.broker_mode      = broker_mode
        self.broker_spread    = broker_spread
        self.use_fractional_lots = use_fractional_lots
        self.trade_executor   = trade_executor or NoOpExecutor()
        self.trade_logger     = trade_logger
        self.notifier         = notifier or NoOpNotifier()
        self.analytics        = analytics or NoOpReporter()
        self.logger           = logger
        self.point_value      = float(point_value)
        self.account_balance  = float(account_balance)
        self.risk_per_trade   = risk_per_trade
        self.risk_pct_per_trade = risk_pct_per_trade
        self.fee_per_rt       = fee_per_rt
        self._accounts_repo   = accounts_repo
        self._live_mode       = live_mode
        self._lock = RLock()

        # Session end close config
        self._session_end_time = None
        self._session_tz = None
        if session_end_time and session_tz:
            self._session_end_time = datetime.strptime(session_end_time, "%H:%M").time()
            self._session_tz = ZoneInfo(session_tz)

        # Track which trades we have already logged as "Active"
        self._monitored_trades = set()
        # Guard against duplicate broker fills for the same trade (NT retries, network duplicates)
        self._closing_trades = set()

        # Use cases
        self._open_use_case = TradeOpenUseCase(
            trade_repository=trade_repository,
            trade_executor=self.trade_executor,
            event_publisher=socketio,
            logger=logger,
            trade_logger=trade_logger,
            point_value=point_value,
            account_balance=account_balance,
            risk_per_trade=risk_per_trade,
            risk_pct_per_trade=risk_pct_per_trade,
            use_fractional_lots=use_fractional_lots,
            accounts_repo=accounts_repo,
            instrument=instrument,
            live_mode=live_mode,
        )
        self._close_use_case = TradeCloseUseCase(
            trade_repository=trade_repository,
            trade_executor=self.trade_executor,
            event_publisher=socketio,
            logger=logger,
            trade_logger=trade_logger,
            point_value=point_value,
            fee_per_rt=fee_per_rt,
            broker_spread=broker_spread,
        )
        self._broker_handler = BrokerFillHandler(
            trade_repository=trade_repository,
            event_publisher=socketio,
            logger=logger,
            trade_logger=trade_logger,
            analytics=analytics,
            point_value=point_value,
            account_balance=account_balance,
            risk_per_trade=risk_per_trade,
            risk_pct_per_trade=risk_pct_per_trade,
            use_fractional_lots=use_fractional_lots,
            accounts_repo=accounts_repo,
        )

        # RESUME: Load any open trades from the DB
        self._load_open_trades_from_db()

    # ------------------------------------------------------------------
    # Contract / financial helpers
    # ------------------------------------------------------------------
    def _calc_contracts(self, risk_per_contract: float,
                        risk_per_trade_override: float | None = None,
                        risk_pct_per_trade_override: float | None = None) -> float:
        """Calculate number of contracts/lots."""
        return self._open_use_case._calc_contracts(
            risk_per_contract, risk_per_trade_override, risk_pct_per_trade_override
        )

    # ------------------------------------------------------------------
    # DB resume
    # ------------------------------------------------------------------
    def _current_account_names(self) -> set[str]:
        if self._accounts_repo is None:
            return set()
        try:
            return {a.name for a in self._accounts_repo.list_accounts() if a.name}
        except Exception:
            return set()

    def _live_account_names(self) -> set[str]:
        """Return only accounts eligible for live trading."""
        if self._accounts_repo is None:
            return set()
        try:
            return {
                a.name for a in self._accounts_repo.list_accounts()
                if a.name and getattr(a, "live_enabled", True)
            }
        except Exception:
            return set()

    def _load_open_trades_from_db(self):
        try:
            all_trades = self.trade_repository.list_trades(self.pair)
            open_count = 0
            closed_count = 0
            current_accounts = self._current_account_names()

            for t in all_trades:
                if t.exit_time is None:
                    # Skip stale open trades whose account is no longer configured.
                    if current_accounts and t.account and t.account not in current_accounts:
                        self.logger.warning(
                            f"[TradeManager] Stale open trade {t.trade_id} belongs to "
                            f"unconfigured account {t.account}; closing as ORPHAN."
                        )
                        self.trade_repository.close_trade(
                            trade_id=t.trade_id,
                            exit_price=t.entry_price,
                            exit_time=datetime.now(tz=timezone.utc),
                            result=0.0,
                            result_type="ORPHAN",
                            fees=0.0,
                            pnl_usd=0.0,
                        )
                        continue

                    trade_dict = {
                        'trade_id':    t.trade_id,
                        'pair':        t.pair,
                        'type':        t.trade_type,
                        'entry':       t.entry_price,
                        'stop_loss':   t.stop_loss,
                        'take_profit': t.take_profit,
                        'risk':        t.risk,
                        'risk_dollars': t.risk_dollars,
                        'risk_pct':    t.risk_pct,
                        'contracts':   t.contracts,
                        'status':      'open',
                        'entry_time':  t.entry_time.timestamp(),
                        'account':     t.account,
                        'signal_id':   t.signal_id,
                        'source':      t.source,
                    }
                    self.open_trades.append(trade_dict)
                    open_count += 1
                    self.logger.info(f"[TradeManager] LOADED OPEN TRADE: ID={t.trade_id} Entry={t.entry_price} SL={t.stop_loss} TP={t.take_profit} EntryTime={t.entry_time}")
                else:
                    closed_count += 1

            self.logger.info(f"[TradeManager] DB scan complete: {open_count} open, {closed_count} closed, {len(all_trades)} total trades for {self.pair}")

        except Exception as e:
            self.logger.error(f"[TradeManager] Failed to load open trades on init: {e}")
            self.analytics.capture_exception(e, {"op": "load_open_trades"})
            self.notifier.send(f"[TradeManager] Failed to load open trades on init: {e}")

    # ------------------------------------------------------------------
    # Close-path helpers (prevent duplicate close orders / double-counted PnL)
    # ------------------------------------------------------------------
    def _guard_close(self, trade_id: str) -> bool:
        """Atomically mark a trade as closing. Return False if already in flight."""
        with self._lock:
            if trade_id in self._closing_trades:
                self.logger.warning(
                    f"[TradeManager] Close already in flight for {trade_id}; skipping"
                )
                return False
            self._closing_trades.add(trade_id)
            return True

    def _cleanup_after_close_attempt(
        self,
        trade_id: str,
        result: Any,
        remove_on_failure: bool = False,
    ) -> None:
        """
        Clean up monitored/closing state after a close attempt.

        If the close succeeded, the trade is removed from open_trades.
        If it failed and ``remove_on_failure`` is True (e.g. broker fill or
        DB error after executor already sent the close), the trade is still
        removed so we do not retry indefinitely. If ``remove_on_failure`` is
        False (executor failure), the trade stays in open_trades for retry.
        """
        with self._lock:
            self._monitored_trades.discard(trade_id)
            self._closing_trades.discard(trade_id)
            if result is not None or remove_on_failure:
                with contextlib.suppress(StopIteration, ValueError):
                    self.open_trades.remove(next(
                        t for t in self.open_trades if t["trade_id"] == trade_id
                    ))

    def _is_executor_failure(self, exc: Exception) -> bool:
        """Heuristic used to decide whether a close can be retried next bar."""
        err = str(exc).lower()
        return "zmq" in err or "executor" in err or "connection" in err

    # ------------------------------------------------------------------
    # Bar handlers
    # ------------------------------------------------------------------
    def handle_new_1m_bar(self, bar: dict):
        self._check_sl_tp(bar)
        self._check_session_end_close(bar)

    def _check_sl_tp(self, bar: dict):
        for trade in list(self.open_trades):
            if trade['pair'] != bar['pair']:
                continue
            if trade['entry_time'] >= bar['time']:
                continue

            if trade['trade_id'] not in self._monitored_trades:
                self.logger.info(f"[TradeManager] ACTIVATING Trade {trade['trade_id']} at {bar['time']}")
                self._monitored_trades.add(trade['trade_id'])

            ttype = trade['type']
            is_long  = ttype == 'long'
            is_short = ttype == 'short'

            hit_sl = False
            hit_tp = False
            sl = trade.get('stop_loss')
            tp = trade.get('take_profit')

            if is_long:
                if sl is not None and bar['low'] <= sl:
                    hit_sl = True
                elif tp is not None and bar['high'] >= tp:
                    hit_tp = True
            elif is_short:
                if sl is not None and bar['high'] >= sl:
                    hit_sl = True
                elif tp is not None and bar['low'] <= tp:
                    hit_tp = True
            # NOTE: If both SL and TP are inside the same bar, SL always wins.
            # This is a conservative assumption since intrabar sequence is unknown.

            if not hit_sl and not hit_tp:
                continue

            exit_price = sl if hit_sl else tp
            result_type_override = "SL" if hit_sl else "TP"
            log_event = "SL_HIT" if hit_sl else "TP_HIT"
            analytics_event = "SL_HIT" if hit_sl else "TP_HIT"

            # For reentry SL calculation: use the bar's extreme, not just the SL price
            extreme_excursion = None
            if hit_sl:
                extreme_excursion = bar['low'] if is_long else bar['high']

            trade_id = trade['trade_id']
            if not self._guard_close(trade_id):
                continue

            result = None
            remove_on_failure = False
            try:
                result = self._close_use_case.execute(
                    trade=trade,
                    exit_price=exit_price,
                    exit_time=bar['time'],
                    result_type_override=result_type_override,
                    log_event=log_event,
                    analytics_event=analytics_event,
                    extreme_excursion=extreme_excursion,
                )
            except Exception as e:
                if self._is_executor_failure(e):
                    self.logger.warning(
                        f"[TradeManager] Executor failure on SL/TP close for {trade_id}: {e}; will retry"
                    )
                    remove_on_failure = False
                else:
                    self.logger.error(f"[TradeManager] DB error on SL/TP close for {trade_id}: {e}")
                    self.analytics.capture_exception(e, {"op": "close_trade_sl_tp", "trade_id": trade_id})
                    if self.trade_logger:
                        self.trade_logger.log(trade_id, "ERROR", str(e))
                    self.notifier.send(f"[TradeManager] DB error on SL/TP close for {trade_id}: {e}")
                    remove_on_failure = True

            if result is not None:
                self.account_balance += result.pnl_usd
                self._open_use_case.update_account_balance(self.account_balance)

            self._cleanup_after_close_attempt(trade_id, result, remove_on_failure=remove_on_failure)

            if result is not None:
                self.analytics.capture_trade_event(analytics_event, {
                    "trade_id": trade_id, "exit_price": exit_price,
                    "result": result.result, "result_type": result.result_type,
                })

    def _check_session_end_close(self, bar: dict):
        if not self._session_end_time or not self._session_tz:
            return

        bar_dt = datetime.fromtimestamp(bar['time'], tz=self._session_tz)
        if bar_dt.time() < self._session_end_time:
            return

        for trade in list(self.open_trades):
            if trade['pair'] != bar['pair']:
                continue
            if trade['entry_time'] >= bar['time']:
                continue
            if trade.get('source') in self.USER_CONTROLLED_SOURCES:
                continue

            trade_id = trade['trade_id']
            if not self._guard_close(trade_id):
                continue

            result_type_override = FinancialCalc.calculate_session_end_result_type(
                FinancialCalc.calculate_close_metrics(
                    direction=Direction.from_string(trade['type']),
                    entry_price=trade['entry'],
                    exit_price=bar['close'],
                    stop_loss=trade['stop_loss'],
                    take_profit=trade['take_profit'],
                    risk_points=trade.get('risk', 1.0) or 1.0,
                    contracts=trade.get('contracts') or 1,
                    point_value=self.point_value,
                    fee_per_rt=self.fee_per_rt,
                )[0]
            )

            result = None
            remove_on_failure = False
            try:
                result = self._close_use_case.execute(
                    trade=trade,
                    exit_price=bar['close'],
                    exit_time=bar['time'],
                    result_type_override=result_type_override,
                    log_event="SESSION_END",
                    log_message=f"Close @ {bar['close']:.2f}",
                    analytics_event="SESSION_END",
                )
            except Exception as e:
                if self._is_executor_failure(e):
                    self.logger.warning(
                        f"[TradeManager] Executor failure on session end for {trade_id}: {e}; will retry"
                    )
                    remove_on_failure = False
                else:
                    self.logger.error(f"[TradeManager] DB error on session end for {trade_id}: {e}")
                    self.analytics.capture_exception(e, {"op": "session_end_close", "trade_id": trade_id})
                    if self.trade_logger:
                        self.trade_logger.log(trade_id, "ERROR", str(e))
                    self.notifier.send(f"[TradeManager] DB error on session end for {trade_id}: {e}")
                    remove_on_failure = True

            if result is not None:
                self.account_balance += result.pnl_usd
                self._open_use_case.update_account_balance(self.account_balance)

            self._cleanup_after_close_attempt(trade_id, result, remove_on_failure=remove_on_failure)

            if result is not None:
                self.analytics.capture_trade_event("SESSION_END", {
                    "trade_id": trade_id, "exit_price": bar['close'], "result": result.result,
                })

    # ------------------------------------------------------------------
    # Open / Close public API
    # ------------------------------------------------------------------
    def open_trade(self, pair: str, trade_type: str, entry_price: float,
                   stop_loss: float, take_profit: float,
                   risk: float, entry_time: float, rr_ratio: float = 5.0,
                   source: str | None = None,
                   account: str | None = None,
                   signal_id: str | None = None,
                   risk_per_trade_override: float | None = None,
                   risk_pct_per_trade_override: float | None = None,
                   trade_id: str | None = None):
        # Sync use-case balance with manager balance BEFORE calculating contracts
        self._open_use_case.update_account_balance(self.account_balance)
        self._broker_handler.update_balance(self.account_balance)

        # In live multi-account mode, callers like the manual/test controller may
        # not specify an account. Fall back to the first live-enabled account so the
        # executor can route the order correctly.
        if account is None:
            candidate_accounts = self._live_account_names() if self._live_mode else self._current_account_names()
            if candidate_accounts:
                account = next(iter(candidate_accounts))
            else:
                # Some executors (e.g. MultiAccountExecutor) carry their own
                # account list when no accounts repo is wired yet.
                executor_configs = getattr(self.trade_executor, "account_configs", None)
                if executor_configs:
                    account = next(
                        (getattr(c, "name", None) for c in executor_configs
                         if getattr(c, "name", None) and (not self._live_mode or getattr(c, "live_enabled", True))),
                        None,
                    )

        result = self._open_use_case.execute(
            pair=pair, trade_type=trade_type, entry_price=entry_price,
            stop_loss=stop_loss, take_profit=take_profit, risk=risk,
            entry_time=entry_time, rr_ratio=rr_ratio, source=source,
            account=account, signal_id=signal_id,
            risk_per_trade_override=risk_per_trade_override,
            risk_pct_per_trade_override=risk_pct_per_trade_override,
            trade_id=trade_id,
        )

        trade = {
            'trade_id':   result.trade_id,
            'pair':       result.pair,
            'type':       result.trade_type,
            'entry':      result.entry_price,
            'stop_loss':  result.stop_loss,
            'take_profit':result.take_profit,
            'risk':       result.risk,
            'risk_dollars': result.risk_dollars,
            'risk_pct':   result.risk_pct,
            'contracts':  result.contracts,
            'entry_time': result.entry_time,
            'rr_ratio':   rr_ratio,
            'account':    result.account,
            'signal_id':  result.signal_id,
            'instrument': result.instrument,
            'status':     'open',
            'source':     source,
        }
        with self._lock:
            self.open_trades.append(trade)
        self._monitored_trades.add(result.trade_id)

        self.analytics.capture_trade_event("TRADE_OPEN", {
            "trade_id": result.trade_id, "pair": pair, "type": trade_type,
            "entry": entry_price, "sl": stop_loss, "tp": take_profit,
        })

        return trade

    def cancel_trade(self, trade_id: str, reason: str = "CANCELLED") -> None:
        """Close a trade locally without sending a command to the broker.

        Used when the broker has already rejected or timed out the original
        command, so the trade never actually entered the market.
        """
        if not self._guard_close(trade_id):
            return

        trade_data = self.trade_repository.get_trade(trade_id)
        if trade_data is None or trade_data.exit_time is not None:
            self._cleanup_after_close_attempt(trade_id, result=None, remove_on_failure=True)
            return

        try:
            self.trade_repository.close_trade(
                trade_id=trade_id,
                exit_price=trade_data.entry_price,
                exit_time=datetime.now(tz=timezone.utc),
                result=0.0,
                result_type=reason,
                fees=0.0,
                pnl_usd=0.0,
            )
        except Exception as e:
            if self.logger:
                self.logger.error(f"[TradeManager] DB error cancelling trade {trade_id}: {e}")
            self.analytics.capture_exception(e, {"op": "cancel_trade", "trade_id": trade_id})
            self._cleanup_after_close_attempt(trade_id, result=None, remove_on_failure=True)
            return

        with self._lock:
            self._monitored_trades.discard(trade_id)
            self._closing_trades.discard(trade_id)
            with contextlib.suppress(ValueError):
                self.open_trades.remove(next(
                    t for t in self.open_trades if t["trade_id"] == trade_id
                ))
        if self.logger:
            self.logger.warning(f"[TradeManager] Cancelled trade {trade_id} ({reason})")

    def close_trade(self, trade_id: str, exit_price: float, exit_time: float):
        with self._lock:
            trade = next(
                (t for t in self.open_trades if t['trade_id'] == trade_id), None
            )

        if not trade:
            self.logger.warning(f"[TradeManager] Trade {trade_id} not in memory, fetching from DB.")
            trade_data = self.trade_repository.get_trade(trade_id)
            if trade_data:
                if trade_data.exit_time is not None:
                    self.logger.warning(f"[TradeManager] Trade {trade_id} already closed in DB.")
                    return {
                        'trade_id': trade_id,
                        'exit_price': exit_price,
                        'result': trade_data.result,
                        'result_type': trade_data.result_type,
                    }
                trade = {
                    'trade_id': trade_data.trade_id,
                    'pair': trade_data.pair,
                    'type': trade_data.trade_type,
                    'entry': trade_data.entry_price,
                    'stop_loss': trade_data.stop_loss,
                    'take_profit': trade_data.take_profit,
                    'risk': trade_data.risk,
                    'contracts': trade_data.contracts,
                }
            else:
                self.trade_repository.close_trade(
                    trade_id=trade_id,
                    exit_price=exit_price,
                    exit_time=datetime.fromtimestamp(exit_time, tz=timezone.utc),
                    result=0.0,
                    result_type="SP"
                )
                return {'trade_id': trade_id, 'exit_price': exit_price, 'result': 0.0}

        if not self._guard_close(trade_id):
            return None

        result = None
        remove_on_failure = False
        try:
            result = self._close_use_case.execute(
                trade=trade,
                exit_price=exit_price,
                exit_time=exit_time,
                log_event="CLOSE",
                log_message=f"Exit={exit_price:.2f}",
                analytics_event="CLOSE",
            )
        except Exception as e:
            if self._is_executor_failure(e):
                self.logger.warning(
                    f"[TradeManager] Executor failure on close for {trade_id}: {e}; will retry"
                )
                self._cleanup_after_close_attempt(trade_id, result, remove_on_failure=False)
                raise
            else:
                self.logger.error(f"[TradeManager] DB error on close for {trade_id}: {e}")
                self.analytics.capture_exception(e, {"op": "close_trade", "trade_id": trade_id})
                if self.trade_logger:
                    self.trade_logger.log(trade_id, "ERROR", str(e))
                self.notifier.send(f"[TradeManager] DB error on close for {trade_id}: {e}")
                remove_on_failure = True

        if result is not None:
            self.account_balance += result.pnl_usd
            self._open_use_case.update_account_balance(self.account_balance)

        self._cleanup_after_close_attempt(trade_id, result, remove_on_failure=remove_on_failure)

        if result is None:
            return None

        return {
            'trade_id': trade_id,
            'pair': trade['pair'],
            'type': trade['type'],
            'exit_price': exit_price,
            'exit_time': exit_time,
            'result': result.result,
            'result_type': result.result_type,
            'fees': result.fees,
            'pnl_usd': result.pnl_usd,
        }

    def close_remaining_trades_at_stream_end(self, final_close_price: float, final_time: float):
        self.logger.info(f"[TradeManager] STREAM END CALLBACK FIRED! close_price={final_close_price}, time={final_time}")
        for trade in list(self.open_trades):
            if trade['entry_time'] >= final_time:
                continue
            if trade.get('source') in self.USER_CONTROLLED_SOURCES:
                continue

            trade_id = trade['trade_id']
            if not self._guard_close(trade_id):
                continue

            result_type = FinancialCalc.calculate_session_end_result_type(
                FinancialCalc.calculate_close_metrics(
                    direction=Direction.from_string(trade['type']),
                    entry_price=trade['entry'],
                    exit_price=final_close_price,
                    stop_loss=trade['stop_loss'],
                    take_profit=trade['take_profit'],
                    risk_points=trade.get('risk', 1.0) or 1.0,
                    contracts=trade.get('contracts') or 1,
                    point_value=self.point_value,
                    fee_per_rt=self.fee_per_rt,
                )[0]
            )

            result = None
            remove_on_failure = False
            try:
                result = self._close_use_case.execute(
                    trade=trade,
                    exit_price=final_close_price,
                    exit_time=final_time,
                    result_type_override=result_type,
                    log_event="SESSION_END",
                    log_message=f"Stream end @ {final_close_price:.2f}",
                    analytics_event="STREAM_END",
                )
            except Exception as e:
                if self._is_executor_failure(e):
                    self.logger.warning(
                        f"[TradeManager] Executor failure on stream end for {trade_id}: {e}; will retry"
                    )
                    remove_on_failure = False
                else:
                    self.logger.error(f"[TradeManager] DB error on stream end for {trade_id}: {e}")
                    self.analytics.capture_exception(e, {"op": "stream_end_close", "trade_id": trade_id})
                    if self.trade_logger:
                        self.trade_logger.log(trade_id, "ERROR", str(e))
                    self.notifier.send(f"[TradeManager] DB error on stream end for {trade_id}: {e}")
                    remove_on_failure = True

            if result is not None:
                self.account_balance += result.pnl_usd
                self._open_use_case.update_account_balance(self.account_balance)

            self._cleanup_after_close_attempt(trade_id, result, remove_on_failure=remove_on_failure)

            if result is not None:
                self.analytics.capture_trade_event("STREAM_END", {
                    "trade_id": trade_id, "exit_price": final_close_price, "result": result.result,
                })

    def update_local_trade_sl(self, trade_id: str, new_sl: float):
        found = False
        for t in self.open_trades:
            if t['trade_id'] == trade_id:
                old_sl = t['stop_loss']
                t['stop_loss'] = new_sl
                self.logger.info(f"[TradeManager] Synced SL for {t['trade_id']}: {old_sl} -> {new_sl}")
                found = True
        if not found:
            self.logger.warning(f"[TradeManager] Could not find trade {trade_id} to update SL")

    # ------------------------------------------------------------------
    # Broker fills
    # ------------------------------------------------------------------
    def handle_broker_entry_fill(self, trade_id: str, entry_price: float,
                                stop_loss: float = None, take_profit: float = None,
                                quantity: float | None = None,
                                account_balance: float | None = None):
        trade = next((t for t in self.open_trades if t['trade_id'] == trade_id), None)
        if not trade:
            self.logger.warning(f"[TradeManager] Entry fill for {trade_id} but trade not in memory")
            return

        self.logger.info(
            f"[TradeManager] ENTRY FILL received for {trade_id}: entry={entry_price} "
            f"qty={quantity} SL={stop_loss} TP={take_profit} balance={account_balance}"
        )

        # NinjaTrader is the source of truth for account balance in live mode.
        if account_balance is not None and account_balance > 0:
            self.account_balance = account_balance
            self._open_use_case.update_account_balance(self.account_balance)

        self._broker_handler.update_balance(self.account_balance)
        self._broker_handler.handle_entry_fill(
            trade, entry_price, stop_loss, take_profit, quantity,
            account_balance=account_balance,
        )

    def handle_broker_fill(
        self,
        trade_id: str,
        exit_price: float,
        result_type: str = None,
        broker_pnl_usd: float | None = None,
        broker_fees: float | None = None,
        account_balance: float | None = None,
    ):
        trade = next((t for t in self.open_trades if t['trade_id'] == trade_id), None)
        if not trade:
            self.logger.warning(f"[TradeManager] Broker fill for {trade_id} but trade not in memory")
            return

        self.logger.info(
            f"[TradeManager] EXIT FILL received for {trade_id}: exit={exit_price} "
            f"type={result_type} broker_pnl={broker_pnl_usd} broker_fees={broker_fees} "
            f"balance={account_balance}"
        )

        # NinjaTrader is the source of truth for account balance in live mode.
        if account_balance is not None and account_balance > 0:
            self.account_balance = account_balance
            self._open_use_case.update_account_balance(self.account_balance)
            self._broker_handler.update_balance(self.account_balance)

        # Atomic guard: prevent duplicate fills from double-counting PnL
        if not self._guard_close(trade_id):
            return

        result = None
        try:
            result = self._close_use_case.execute(
                trade=trade,
                exit_price=exit_price,
                exit_time=datetime.now(tz=timezone.utc).timestamp(),
                result_type_override=result_type,
                log_event="NT_FILL",
                log_message=f"{result_type or 'FILL'} @ {exit_price:.2f}",
                analytics_event="BROKER_FILL",
                skip_executor=True,
                broker_pnl_usd=broker_pnl_usd,
                broker_fees=broker_fees,
            )
        except Exception as e:
            self.logger.error(f"[TradeManager] DB error on broker fill for {trade_id}: {e}")
            self.analytics.capture_exception(e, {"op": "broker_fill", "trade_id": trade_id})
            if self.trade_logger:
                self.trade_logger.log(trade_id, "ERROR", str(e))
            self.notifier.send(f"[TradeManager] DB error on broker fill for {trade_id}: {e}")

        if result is not None:
            self.account_balance += result.pnl_usd
            self._open_use_case.update_account_balance(self.account_balance)

        # Broker is source of truth — trade is closed even if DB persist failed
        self._cleanup_after_close_attempt(trade_id, result, remove_on_failure=True)

        # Persist the broker-reported exit-time balance on the closed trade record.
        if account_balance is not None and account_balance > 0:
            try:
                self.trade_repository.update_account_balance(trade_id, account_balance)
            except Exception as e:
                self.logger.error(f"[TradeManager] Failed to persist account balance for {trade_id}: {e}")

    def notify_strategy_close(self, trade_id: str, exit_price: float, result: float,
                               pnl_usd: float, fees: float, result_type: str, exit_time: float):
        trade = next((t for t in self.open_trades if t['trade_id'] == trade_id), None)
        if not trade:
            return

        if not self._guard_close(trade_id):
            return

        # Persist the strategy-reported close to the DB so the row is not left open.
        try:
            self.trade_repository.close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_time=datetime.fromtimestamp(exit_time, tz=timezone.utc),
                result=result,
                result_type=result_type,
                fees=fees,
                pnl_usd=pnl_usd,
            )
        except Exception as e:
            self.logger.error(f"[TradeManager] DB error persisting strategy close for {trade_id}: {e}")
            self.analytics.capture_exception(e, {"op": "notify_strategy_close", "trade_id": trade_id})
            if self.trade_logger:
                self.trade_logger.log(trade_id, "ERROR", str(e))
            self.notifier.send(f"[TradeManager] DB error persisting strategy close for {trade_id}: {e}")
            # Strategy is source of truth for this close; remove from memory even on DB failure.
            self._cleanup_after_close_attempt(trade_id, result=None, remove_on_failure=True)
            return

        self.account_balance += pnl_usd
        self._open_use_case.update_account_balance(self.account_balance)
        with self._lock:
            self._monitored_trades.discard(trade_id)
            self._closing_trades.discard(trade_id)
            with contextlib.suppress(ValueError):
                self.open_trades.remove(next(
                    t for t in self.open_trades if t["trade_id"] == trade_id
                ))

        event = "SL_HIT" if result_type == "SL" else "TP_HIT" if result_type == "TP" else "SESSION_END"
        self.logger.info(f"[TradeManager] SYNCED {event} for {trade_id} @ {exit_price} (Result: {result:.2f}R)")

        if self.trade_logger:
            self.trade_logger.log(trade_id, event, f"Exit={exit_price:.2f} Result={result:.2f}R")
            self.trade_logger.log(trade_id, "CLOSE", "Synced from strategy")

        self.analytics.capture_trade_event(event, {
            "trade_id": trade_id, "exit_price": exit_price,
            "result": result, "result_type": result_type,
        })
