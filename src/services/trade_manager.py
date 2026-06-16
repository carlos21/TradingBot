# src/trade_manager.py

import contextlib
from datetime import datetime, timezone
from threading import RLock
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
    USER_CONTROLLED_SOURCES = ("manual", "test", "broker_sync")

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
                 instrument: str | None = None):
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
                # Distinguish executor failure (before DB) from DB failure
                # Executor failure: trade stays open, no analytics
                # DB failure: trade removed, analytics captured
                if "ZMQ" in str(e) or "Executor" in str(e) or "connection" in str(e).lower():
                    continue  # executor failed — retry next bar
                self.logger.error(f"[TradeManager] DB error on SL/TP close for {trade['trade_id']}: {e}")
                self.analytics.capture_exception(e, {"op": "close_trade_sl_tp", "trade_id": trade['trade_id']})
                if self.trade_logger:
                    self.trade_logger.log(trade['trade_id'], "ERROR", str(e))
                self.notifier.send(f"[TradeManager] DB error on SL/TP close for {trade['trade_id']}: {e}")
                result = None

            if result is not None:
                self.account_balance += result.pnl_usd
                self._open_use_case.update_account_balance(self.account_balance)
            self._monitored_trades.discard(trade['trade_id'])
            self._closing_trades.discard(trade['trade_id'])
            with contextlib.suppress(ValueError):
                self.open_trades.remove(trade)
            if result is not None:
                self.analytics.capture_trade_event(analytics_event, {
                    "trade_id": trade['trade_id'], "exit_price": exit_price,
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

            try:
                result = self._close_use_case.execute(
                    trade=trade,
                    exit_price=bar['close'],
                    exit_time=bar['time'],
                    result_type_override=FinancialCalc.calculate_session_end_result_type(
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
                    ),
                    log_event="SESSION_END",
                    log_message=f"Close @ {bar['close']:.2f}",
                    analytics_event="SESSION_END",
                )
            except Exception as e:
                if "ZMQ" in str(e) or "Executor" in str(e) or "connection" in str(e).lower():
                    continue
                self.logger.error(f"[TradeManager] DB error on session end for {trade['trade_id']}: {e}")
                self.analytics.capture_exception(e, {"op": "session_end_close", "trade_id": trade['trade_id']})
                if self.trade_logger:
                    self.trade_logger.log(trade['trade_id'], "ERROR", str(e))
                self.notifier.send(f"[TradeManager] DB error on session end for {trade['trade_id']}: {e}")
                result = None

            if result is not None:
                self.account_balance += result.pnl_usd
                self._open_use_case.update_account_balance(self.account_balance)
            self._monitored_trades.discard(trade['trade_id'])
            self._closing_trades.discard(trade['trade_id'])
            with contextlib.suppress(ValueError):
                self.open_trades.remove(trade)
            if result is not None:
                self.analytics.capture_trade_event("SESSION_END", {
                    "trade_id": trade['trade_id'], "exit_price": bar['close'], "result": result.result,
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
        # not specify an account. Fall back to the first configured account so the
        # executor can route the order correctly.
        if account is None:
            current_accounts = self._current_account_names()
            if current_accounts:
                account = next(iter(current_accounts))
            else:
                # Some executors (e.g. MultiAccountExecutor) carry their own
                # account list when no accounts repo is wired yet.
                executor_configs = getattr(self.trade_executor, "account_configs", None)
                if executor_configs:
                    account = next((getattr(c, "name", None) for c in executor_configs if getattr(c, "name", None)), None)

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
        with self._lock:
            trade = next(
                (t for t in self.open_trades if t['trade_id'] == trade_id), None
            )
            if trade:
                self.open_trades.remove(trade)

        trade_data = self.trade_repository.get_trade(trade_id)
        if trade_data is None or trade_data.exit_time is not None:
            return

        self.trade_repository.close_trade(
            trade_id=trade_id,
            exit_price=trade_data.entry_price,
            exit_time=datetime.now(tz=timezone.utc),
            result=0.0,
            result_type=reason,
            fees=0.0,
            pnl_usd=0.0,
        )
        if self.logger:
            self.logger.warning(f"[TradeManager] Cancelled trade {trade_id} ({reason})")

    def create_synced_trade(
        self,
        trade_id: str,
        pair: str,
        trade_type: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        quantity: float,
        account: str | None,
    ) -> dict:
        """Create a trade locally from a broker position without sending an order.

        Used during POSITION_SYNC when the broker reports a position Python does
        not know about. The position already exists on the broker, so we must not
        send a new order_open command.
        """
        risk = abs(entry_price - stop_loss)
        risk_per_contract = risk * self.point_value
        risk_budget = FinancialCalc.risk_budget(
            self.account_balance,
            self.risk_per_trade,
            self.risk_pct_per_trade,
        )
        if self.use_fractional_lots:
            contracts = FinancialCalc.lots(risk_budget, risk_per_contract) if risk_budget > 0 else 0.01
        else:
            contracts = FinancialCalc.contracts(risk_budget, risk_per_contract) if risk_budget > 0 else 1
        contracts = quantity if quantity > 0 else contracts
        risk_dollars = risk_per_contract * contracts
        risk_pct = (
            (risk_dollars / self.account_balance * 100)
            if self.account_balance > 0 else None
        )
        entry_time = datetime.now(tz=timezone.utc)

        trade_data = self.trade_repository.insert_trade(
            pair=pair,
            trade_type=trade_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk=risk,
            entry_time=entry_time,
            risk_dollars=risk_dollars,
            risk_pct=risk_pct,
            contracts=contracts,
            source="broker_sync",
            account=account,
            trade_id=trade_id,
        )

        trade = {
            'trade_id': trade_data.trade_id,
            'pair': trade_data.pair,
            'type': trade_data.trade_type,
            'entry': trade_data.entry_price,
            'stop_loss': trade_data.stop_loss,
            'take_profit': trade_data.take_profit,
            'risk': trade_data.risk,
            'risk_dollars': trade_data.risk_dollars,
            'risk_pct': trade_data.risk_pct,
            'contracts': trade_data.contracts,
            'entry_time': trade_data.entry_time.timestamp(),
            'account': trade_data.account,
            'status': 'open',
            'source': 'broker_sync',
        }
        with self._lock:
            self.open_trades.append(trade)
        self._monitored_trades.add(trade_id)

        if self.logger:
            self.logger.info(
                f"[PositionSync] Created trade {trade_id} from broker position "
                f"({trade_type} @ {entry_price}, qty={quantity})"
            )
        return trade

    def close_trade(self, trade_id: str, exit_price: float, exit_time: float):
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

        result = self._close_use_case.execute(
            trade=trade,
            exit_price=exit_price,
            exit_time=exit_time,
            log_event="CLOSE",
            log_message=f"Exit={exit_price:.2f}",
            analytics_event="CLOSE",
        )

        self.account_balance += result.pnl_usd
        self._open_use_case.update_account_balance(self.account_balance)

        with contextlib.suppress(ValueError):
            self.open_trades.remove(trade)

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
                if "ZMQ" in str(e) or "Executor" in str(e) or "connection" in str(e).lower():
                    continue
                self.logger.error(f"[TradeManager] DB error on stream end for {trade['trade_id']}: {e}")
                self.analytics.capture_exception(e, {"op": "stream_end_close", "trade_id": trade['trade_id']})
                if self.trade_logger:
                    self.trade_logger.log(trade['trade_id'], "ERROR", str(e))
                self.notifier.send(f"[TradeManager] DB error on stream end for {trade['trade_id']}: {e}")
                result = None

            if result is not None:
                self.account_balance += result.pnl_usd
                self._open_use_case.update_account_balance(self.account_balance)
            self._monitored_trades.discard(trade['trade_id'])
            self._closing_trades.discard(trade['trade_id'])
            with contextlib.suppress(ValueError):
                self.open_trades.remove(trade)
            if result is not None:
                self.analytics.capture_trade_event("STREAM_END", {
                    "trade_id": trade['trade_id'], "exit_price": final_close_price, "result": result.result,
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
                                stop_loss: float = None, take_profit: float = None):
        trade = next((t for t in self.open_trades if t['trade_id'] == trade_id), None)
        if not trade:
            print(f"[TradeManager] ⚠️ Entry fill for {trade_id} but trade not in memory")
            return

        self._broker_handler.update_balance(self.account_balance)
        self._broker_handler.handle_entry_fill(trade, entry_price, stop_loss, take_profit)

    def handle_broker_fill(self, trade_id: str, exit_price: float, result_type: str = None):
        trade = next((t for t in self.open_trades if t['trade_id'] == trade_id), None)
        if not trade:
            print(f"[TradeManager] ⚠️ Broker fill for {trade_id} but trade not in memory")
            return

        # Atomic guard: prevent duplicate fills from double-counting PnL
        with self._lock:
            if trade_id in self._closing_trades:
                self.logger.warning(f"[TradeManager] Duplicate broker fill ignored for {trade_id}")
                return
            self._closing_trades.add(trade_id)

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
            )
        except Exception as e:
            self.logger.error(f"[TradeManager] DB error on broker fill for {trade_id}: {e}")
            self.analytics.capture_exception(e, {"op": "broker_fill", "trade_id": trade_id})
            if self.trade_logger:
                self.trade_logger.log(trade_id, "ERROR", str(e))
            self.notifier.send(f"[TradeManager] DB error on broker fill for {trade_id}: {e}")
            # Broker is source of truth — trade is closed even if DB persist failed
            self._monitored_trades.discard(trade_id)
            self._closing_trades.discard(trade_id)
            with contextlib.suppress(ValueError):
                self.open_trades.remove(trade)
            return

        self.account_balance += result.pnl_usd
        self._open_use_case.update_account_balance(self.account_balance)
        self._monitored_trades.discard(trade_id)
        self._closing_trades.discard(trade_id)
        with contextlib.suppress(ValueError):
            self.open_trades.remove(trade)

    def notify_strategy_close(self, trade_id: str, exit_price: float, result: float,
                               pnl_usd: float, fees: float, result_type: str, exit_time: float):
        trade = next((t for t in self.open_trades if t['trade_id'] == trade_id), None)
        if not trade:
            return

        self.account_balance += pnl_usd
        self._open_use_case.update_account_balance(self.account_balance)
        self._monitored_trades.discard(trade_id)
        self._closing_trades.discard(trade_id)
        with contextlib.suppress(ValueError):
            self.open_trades.remove(trade)

        event = "SL_HIT" if result_type == "SL" else "TP_HIT" if result_type == "TP" else "SESSION_END"
        self.logger.info(f"[TradeManager] SYNCED {event} for {trade_id} @ {exit_price} (Result: {result:.2f}R)")

        if self.trade_logger:
            self.trade_logger.log(trade_id, event, f"Exit={exit_price:.2f} Result={result:.2f}R")
            self.trade_logger.log(trade_id, "CLOSE", "Synced from strategy")

        self.analytics.capture_trade_event(event, {
            "trade_id": trade_id, "exit_price": exit_price,
            "result": result, "result_type": result_type,
        })
