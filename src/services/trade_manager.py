# src/trade_manager.py

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from src.repositories.trades_repository import TradeRepository
from src.services.trade_executor import TradeExecutor, NoOpExecutor
from src.notifier import Notifier, NoOpNotifier
from src.analytics import AnalyticsReporter, NoOpReporter


class TradeManager:
    """
    Handles closing open trades when a raw 1m bar touches SL or TP.
    """

    def __init__(self, trade_repository: TradeRepository, socketio,
                 pair: str = 'NQ',
                 session_end_time: str = None, session_tz: str = None,
                 broker_mode: str = 'futures', broker_spread: float = 0.0,
                 trade_executor: TradeExecutor = None,
                 trade_logger=None,
                 notifier: Notifier = None,
                 analytics: AnalyticsReporter = None):
        """
        :param trade_repository: SQLTradeRepository instance (must have close_trade)
        :param socketio:         flask_socketio.SocketIO instance
        :param pair:             Instrument pair name (e.g. "NQ", "MNQ")
        :param session_end_time: "HH:MM" — close open trades at this time (e.g. "15:00")
        :param session_tz:       Timezone for session_end_time (e.g. "America/New_York")
        :param broker_mode:      'futures' or 'cfd' - affects TP/SL hit logic
        :param broker_spread:    Spread in points for CFD mode (e.g., 0.5)
        :param trade_logger:     TradeLogger instance for per-trade lifecycle logging
        """
        self.open_trades = []
        self.trade_repository = trade_repository
        self.socketio         = socketio
        self.pair             = pair
        self.broker_mode      = broker_mode
        self.broker_spread    = broker_spread
        self.trade_executor   = trade_executor or NoOpExecutor()
        self.trade_logger     = trade_logger
        self.notifier         = notifier or NoOpNotifier()
        self.analytics        = analytics or NoOpReporter()

        # Session end close config
        self._session_end_time = None
        self._session_tz = None
        if session_end_time and session_tz:
            self._session_end_time = datetime.strptime(session_end_time, "%H:%M").time()
            self._session_tz = ZoneInfo(session_tz)

        # Track which trades we have already logged as "Active" to avoid spamming logs
        self._monitored_trades = set()

        # RESUME: Load any open trades from the DB so we can manage them
        self._load_open_trades_from_db()

    def _load_open_trades_from_db(self):
        """
        Fetch all trades, filter for open ones, and populate self.open_trades.
        This ensures orphaned trades are picked up and closed properly during replay.
        """
        try:
            all_trades = self.trade_repository.list_trades(self.pair)
            
            count = 0
            for t in all_trades:
                if t.exit_time is None:
                    # Map TradeData back to the dict structure TradeManager expects
                    trade_dict = {
                        'trade_id':    t.trade_id,
                        'pair':        t.pair,
                        'type':        t.trade_type,
                        'entry':       t.entry_price,
                        'stop_loss':   t.stop_loss,
                        'take_profit': t.take_profit,
                        'risk':        t.risk,
                        'status':      'open',
                        'entry_time':  t.entry_time.timestamp()
                    }
                    self.open_trades.append(trade_dict)
                    count += 1
                    print(f"[TradeManager] 📥 LOADED OPEN TRADE: ID={t.trade_id} Entry={t.entry_price} SL={t.stop_loss} TP={t.take_profit} EntryTime={t.entry_time}")
            
            if count > 0:
                print(f"[TradeManager] ♻️ Resumed {count} open trades from DB.")
            else:
                print("[TradeManager] No open trades found in DB to resume.")
                
        except Exception as e:
            print(f"[TradeManager] ⚠️ Failed to load open trades on init: {e}")
            self.analytics.capture_exception(e, {"op": "load_open_trades"})
            self.notifier.send(f"[TradeManager] Failed to load open trades on init: {e}")

    def handle_new_1m_bar(self, bar: dict):
        """
        Call this on every 1m bar:
        - scans open_trades for SL/TP hits, closes & emits
        - checks session end close
        """
        self._check_sl_tp(bar)
        self._check_session_end_close(bar)

    def _check_sl_tp(self, bar: dict):
        """SL/TP detection on 1m bars."""
        for trade in list(self.open_trades):
            if trade['pair'] != bar['pair']:
                continue

            if trade['entry_time'] > bar['time']:
                continue

            if trade['trade_id'] not in self._monitored_trades:
                print(f"[TradeManager] 🟢 ACTIVATING Trade {trade['trade_id']} at {bar['time']} (Replay caught up to Entry)")
                self._monitored_trades.add(trade['trade_id'])

            ttype = trade['type']
            is_buy  = ttype in ('buy', 'long')
            is_sell = ttype in ('sell', 'short')

            spread_adj = self.broker_spread / 2.0 if self.broker_mode == 'cfd' else 0.0

            adjusted_sl = trade['stop_loss']
            adjusted_tp = trade['take_profit']
            if self.broker_mode == 'cfd':
                if is_buy:
                    adjusted_sl = trade['stop_loss'] - spread_adj
                    adjusted_tp = trade['take_profit'] - spread_adj
                elif is_sell:
                    adjusted_sl = trade['stop_loss'] + spread_adj
                    adjusted_tp = trade['take_profit'] + spread_adj

            hit_sl = False
            hit_tp = False

            if is_buy:
                if bar['low'] <= adjusted_sl:
                    hit_sl = True
                    print(f"[TradeManager] 🛑 BUY SL HIT! Trade {trade['trade_id']} | Low {bar['low']} <= Adj.SL {adjusted_sl:.2f} (spread: {self.broker_spread}pt)")
                elif bar['high'] >= adjusted_tp:
                    hit_tp = True
                    print(f"[TradeManager] 💰 BUY TP HIT! Trade {trade['trade_id']} | High {bar['high']} >= Adj.TP {adjusted_tp:.2f} (spread: {self.broker_spread}pt)")
            elif is_sell:
                if bar['high'] >= adjusted_sl:
                    hit_sl = True
                    print(f"[TradeManager] 🛑 SELL SL HIT! Trade {trade['trade_id']} | High {bar['high']} >= Adj.SL {adjusted_sl:.2f} (spread: {self.broker_spread}pt)")
                elif bar['low'] <= adjusted_tp:
                    hit_tp = True
                    print(f"[TradeManager] 💰 SELL TP HIT! Trade {trade['trade_id']} | Low {bar['low']} <= Adj.TP {adjusted_tp:.2f} (spread: {self.broker_spread}pt)")

            if not hit_sl and not hit_tp:
                continue

            exit_price = trade['stop_loss'] if hit_sl else trade['take_profit']
            exit_time  = datetime.fromtimestamp(bar['time'], tz=ZoneInfo('UTC'))
            result_type = "SL" if hit_sl else "TP"

            risk = trade.get('risk', 0)
            if risk <= 0: risk = 1.0

            if is_buy:
                pnl_points = exit_price - trade['entry']
            else:
                pnl_points = trade['entry'] - exit_price

            result = pnl_points / risk

            print(f"[TradeManager] 📉 Closing trade {trade['trade_id']} (Result: {result:.2f}R, Type: {result_type}) at {bar['time']}")

            try:
                self.trade_repository.close_trade(
                    trade_id   = trade['trade_id'],
                    exit_price = exit_price,
                    exit_time  = exit_time,
                    result     = result,
                    result_type = result_type
                )
                print(f"[TradeManager] 💾 DB Updated for Trade {trade['trade_id']} (Closed)")
            except Exception as e:
                print(f"[TradeManager] ❌ DB ERROR closing trade {trade['trade_id']}: {e}")
                self.analytics.capture_exception(e, {"op": "close_trade_sl_tp", "trade_id": trade['trade_id']})
                if self.trade_logger:
                    self.trade_logger.log(trade['trade_id'], "ERROR", str(e))
                self.notifier.send(f"[TradeManager] DB ERROR closing trade {trade['trade_id']}: {e}")

            if self.trade_logger:
                event = "SL_HIT" if hit_sl else "TP_HIT"
                self.trade_logger.log(trade['trade_id'], event, f"Exit={exit_price:.2f} Result={result:.2f}R")
                self.trade_logger.log(trade['trade_id'], "CLOSE", "Persisted to DB")

            self.open_trades.remove(trade)
            self.trade_executor.on_trade_close(trade['trade_id'], exit_price)

            self.analytics.capture_trade_event("SL_HIT" if hit_sl else "TP_HIT", {
                "trade_id": trade['trade_id'], "exit_price": exit_price,
                "result": result, "result_type": result_type,
            })

            self.socketio.emit('trade_close', {
                'trade_id':   trade['trade_id'],
                'pair':       trade['pair'],
                'type':       trade['type'],
                'exit_price': exit_price,
                'exit_time':  bar['time'],
                'result':     result,
                'result_type': result_type
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
            if trade['entry_time'] > bar['time']:
                continue

            exit_price = bar['close']
            exit_time = datetime.fromtimestamp(bar['time'], tz=ZoneInfo('UTC'))

            risk = trade.get('risk', 0)
            if risk <= 0:
                risk = 1.0

            is_buy = trade['type'] in ('buy', 'long')
            if is_buy:
                pnl_points = exit_price - trade['entry']
            else:
                pnl_points = trade['entry'] - exit_price

            result = pnl_points / risk

            result_type = "BE" if result <= 0.001 else "SP"

            print(f"[TradeManager] 🕐 SESSION END closing trade {trade['trade_id']} @ {exit_price} (Result: {result:.2f}R, Type: {result_type})")

            try:
                self.trade_repository.close_trade(
                    trade_id=trade['trade_id'],
                    exit_price=exit_price,
                    exit_time=exit_time,
                    result=result,
                    result_type=result_type
                )
            except Exception as e:
                print(f"[TradeManager] ❌ DB ERROR closing trade {trade['trade_id']}: {e}")
                self.analytics.capture_exception(e, {"op": "session_end_close", "trade_id": trade['trade_id']})
                if self.trade_logger:
                    self.trade_logger.log(trade['trade_id'], "ERROR", str(e))
                self.notifier.send(f"[TradeManager] DB ERROR closing trade {trade['trade_id']}: {e}")

            if self.trade_logger:
                self.trade_logger.log(trade['trade_id'], "SESSION_END", f"Close @ {exit_price:.2f} Result={result:.2f}R")
                self.trade_logger.log(trade['trade_id'], "CLOSE", "Persisted to DB")

            self.open_trades.remove(trade)

            self.analytics.capture_trade_event("SESSION_END", {
                "trade_id": trade['trade_id'], "exit_price": exit_price, "result": result,
            })

            self.socketio.emit('trade_close', {
                'trade_id':   trade['trade_id'],
                'pair':       trade['pair'],
                'type':       trade['type'],
                'exit_price': exit_price,
                'exit_time':  bar['time'],
                'result':     result,
                'result_type': result_type
            })

    def open_trade(self, pair: str, trade_type: str, entry_price: float,
                   stop_loss: float, take_profit: float,
                   risk: float, entry_time: float):
        """
        Open a new trade with precomputed parameters.
        """
        # persist open trade
        td = self.trade_repository.insert_trade(
            pair=pair,
            trade_type=trade_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk=risk,
            entry_time=datetime.fromtimestamp(entry_time, tz=timezone.utc),
            params={}
        )
        trade = {
            'trade_id':   td.trade_id,
            'pair':       pair,
            'type':       trade_type,
            'entry':      entry_price,
            'stop_loss':  stop_loss,
            'take_profit':take_profit,
            'risk':       risk,
            'entry_time': entry_time
        }
        # track in-memory
        self.open_trades.append(trade)
        self._monitored_trades.add(td.trade_id) # Mark as monitored since we just opened it

        print(f"[TradeManager] ✅ Registered OPEN trade {trade['trade_id']} @ {entry_time}")

        self.analytics.capture_trade_event("TRADE_OPEN", {
            "trade_id": trade['trade_id'], "pair": pair, "type": trade_type,
            "entry": entry_price, "sl": stop_loss, "tp": take_profit,
        })

        # notify clients
        self.socketio.emit('trade_open', trade)

        return trade

    def close_trade(self, trade_id: str, exit_price: float, exit_time: float):
        """
        Close an existing trade by ID with provided exit parameters.
        """
        # find the trade
        trade = next(
            (t for t in self.open_trades if t['trade_id'] == trade_id),
            None
        )

        # If not in memory, try to fetch from DB to calculate PnL
        if not trade:
            print(f"[TradeManager] ⚠️ Trade {trade_id} not in memory, fetching from DB.")
            trade_data = self.trade_repository.get_trade(trade_id)
            if trade_data:
                trade = {
                    'trade_id': trade_data.trade_id,
                    'type': trade_data.trade_type,
                    'entry': trade_data.entry_price,
                    'stop_loss': trade_data.stop_loss,
                    'take_profit': trade_data.take_profit,
                    'risk': trade_data.risk,
                }
            else:
                # Fallback: just close in DB with unknown result_type
                self.trade_repository.close_trade(
                    trade_id=trade_id,
                    exit_price=exit_price,
                    exit_time=datetime.fromtimestamp(exit_time, tz=timezone.utc),
                    result=0.0,
                    result_type=None
                )
                return {
                    'trade_id': trade_id,
                    'exit_price': exit_price,
                    'result': 0.0
                }


        # calculate P&L (R-Multiple)
        risk = trade.get('risk', 0)
        if risk <= 0: risk = 1.0

        if trade['type'] in ('buy', 'long'):
            pnl_points = exit_price - trade['entry']
        else:
            pnl_points = trade['entry'] - exit_price

        result = pnl_points / risk

        # Determine result type based on exit price
        sl = trade.get('stop_loss', trade.get('sl', trade.get('orig_sl')))
        tp = trade.get('take_profit', trade.get('tp'))
        entry = trade.get('entry', trade.get('entry_price'))
        
        if sl and abs(exit_price - sl) < 0.5:
            result_type = "SL"
        elif tp and abs(exit_price - tp) < 0.5:
            result_type = "TP"
        elif entry and abs(exit_price - entry) < 0.5:
            result_type = "BE"
        else:
            result_type = "SP"

        # persist close
        self.trade_repository.close_trade(
            trade_id=trade_id,
            exit_price=exit_price,
            exit_time=datetime.fromtimestamp(exit_time, tz=timezone.utc),
            result=result,
            result_type=result_type
        )

        if self.trade_logger:
            self.trade_logger.log(trade_id, "CLOSE", f"Exit={exit_price:.2f} Result={result:.2f}R")
            self.trade_logger.log(trade_id, "CMD_SENT", "close_order → NinjaTrader")

        # remove from in-memory
        if trade in self.open_trades:
            self.open_trades.remove(trade)
        self.trade_executor.on_trade_close(trade_id, exit_price)

        # emit close event
        payload = {
            'trade_id':  trade_id,
            'pair':      trade['pair'],
            'type':      trade['type'],
            'exit_price':exit_price,
            'exit_time': exit_time,
            'result':    result,
            'result_type': None
        }
        self.socketio.emit('trade_close', payload)

        return payload
    
    def close_remaining_trades_at_stream_end(self, final_close_price: float, final_time: float):
        """
        Close any remaining open trades at stream end (end of day/replay).
        Uses the final bar's close price and time. Marks with result_type="SP".
        """
        print(f"[TradeManager] 🎬 STREAM END CALLBACK FIRED! close_price={final_close_price}, time={final_time}")
        print(f"[TradeManager] Open trades count: {len(self.open_trades)}")
        for trade in list(self.open_trades):
            exit_price = final_close_price
            exit_time = datetime.fromtimestamp(final_time, tz=ZoneInfo('UTC'))

            risk = trade.get('risk', 0)
            if risk <= 0:
                risk = 1.0

            is_buy = trade['type'] in ('buy', 'long')
            if is_buy:
                pnl_points = exit_price - trade['entry']
            else:
                pnl_points = trade['entry'] - exit_price

            result = pnl_points / risk

            result_type = "BE" if result <= 0.001 else "SP"

            print(f"[TradeManager] 🎬 STREAM END closing trade {trade['trade_id']} @ {exit_price} (Result: {result:.2f}R, Type: {result_type})")

            try:
                self.trade_repository.close_trade(
                    trade_id=trade['trade_id'],
                    exit_price=exit_price,
                    exit_time=exit_time,
                    result=result,
                    result_type=result_type
                )
            except Exception as e:
                print(f"[TradeManager] ❌ DB ERROR closing trade {trade['trade_id']}: {e}")
                self.analytics.capture_exception(e, {"op": "stream_end_close", "trade_id": trade['trade_id']})
                if self.trade_logger:
                    self.trade_logger.log(trade['trade_id'], "ERROR", str(e))
                self.notifier.send(f"[TradeManager] DB ERROR closing trade {trade['trade_id']}: {e}")

            if self.trade_logger:
                self.trade_logger.log(trade['trade_id'], "SESSION_END", f"Stream end @ {exit_price:.2f} Result={result:.2f}R")
                self.trade_logger.log(trade['trade_id'], "CLOSE", "Persisted to DB")

            self.open_trades.remove(trade)

            self.analytics.capture_trade_event("STREAM_END", {
                "trade_id": trade['trade_id'], "exit_price": exit_price, "result": result,
            })

            self.socketio.emit('trade_close', {
                'trade_id':   trade['trade_id'],
                'pair':       trade['pair'],
                'type':       trade['type'],
                'exit_price': exit_price,
                'exit_time':  final_time,
                'result':     result,
                'result_type': result_type
            })

    def update_local_trade_sl(self, trade_id: str, new_sl: float):
        """
        Update the SL of an in-memory trade so the exit logic respects the new level.
        """
        for t in self.open_trades:
            if t['trade_id'] == trade_id:
                old_sl = t['stop_loss']
                t['stop_loss'] = new_sl
                print(f"[TradeManager] 🔄 Synced SL for {trade_id}: {old_sl} -> {new_sl}")
                return
        print(f"[TradeManager] ⚠️ Could not find trade {trade_id} to update SL")

    def handle_broker_entry_fill(self, trade_id: str, entry_price: float,
                                stop_loss: float = None, take_profit: float = None):
        """
        Called when NinjaTrader reports the actual entry fill price and
        the real SL/TP calculated from the fill price.
        Updates DB, in-memory trade, and emits to UI so chart shows real broker levels.
        """
        # Update in-memory trade
        trade = next(
            (t for t in self.open_trades if t['trade_id'] == trade_id),
            None
        )

        if not trade:
            print(f"[TradeManager] ⚠️ Entry fill for {trade_id} but trade not in memory")
            return

        old_entry = trade['entry']
        trade['entry'] = entry_price

        # Update SL/TP if provided by broker (calculated from real fill price)
        if stop_loss is not None:
            trade['stop_loss'] = stop_loss
        if take_profit is not None:
            trade['take_profit'] = take_profit

        # Recalculate risk based on actual entry and SL
        is_buy = trade['type'] in ('buy', 'long')
        if is_buy:
            trade['risk'] = abs(entry_price - trade['stop_loss'])
        else:
            trade['risk'] = abs(trade['stop_loss'] - entry_price)

        print(f"[TradeManager] 📡 ENTRY FILL: {trade_id} @ {entry_price} "
              f"(was {old_entry}, slippage={entry_price - old_entry:+.2f}) "
              f"SL={trade['stop_loss']} TP={trade['take_profit']}")

        if self.trade_logger:
            self.trade_logger.log(trade_id, "NT_ENTRY_FILL",
                f"Filled @ {entry_price:.2f} (slippage: {entry_price - old_entry:+.2f}) "
                f"SL={trade['stop_loss']:.2f} TP={trade['take_profit']:.2f}")

        # Persist to DB
        try:
            self.trade_repository.update_entry_price(trade_id, entry_price)
            if stop_loss is not None:
                self.trade_repository.update_stop_loss(trade_id, stop_loss)
            if take_profit is not None:
                self.trade_repository.update_take_profit(trade_id, take_profit)
        except Exception as e:
            print(f"[TradeManager] ❌ DB ERROR on entry fill for {trade_id}: {e}")
            self.analytics.capture_exception(e, {"op": "broker_entry_fill", "trade_id": trade_id})
            if self.trade_logger:
                self.trade_logger.log(trade_id, "ERROR", str(e))
            self.notifier.send(f"[TradeManager] DB ERROR on entry fill for {trade_id}: {e}")

        # Emit to UI so chart updates
        self.socketio.emit('trade_entry_update', {
            'trade_id':    trade_id,
            'entry_price': entry_price,
            'stop_loss':   trade['stop_loss'],
            'take_profit': trade['take_profit'],
            'risk':        trade['risk'],
        })

    def handle_broker_fill(self, trade_id: str, exit_price: float, result_type: str = None):
        """
        Called when NinjaTrader reports a fill (SL, TP, or manual close).
        This is the ONLY path that closes trades in live mode.
        Updates DB, removes from open_trades, emits to UI.
        """
        # Find trade in memory
        trade = next(
            (t for t in self.open_trades if t['trade_id'] == trade_id),
            None
        )

        if not trade:
            print(f"[TradeManager] ⚠️ Broker fill for {trade_id} but trade not in memory (already closed?)")
            return

        # Calculate P&L
        risk = trade.get('risk', 0)
        if risk <= 0:
            risk = 1.0

        is_buy = trade['type'] in ('buy', 'long')
        if is_buy:
            pnl_points = exit_price - trade['entry']
        else:
            pnl_points = trade['entry'] - exit_price

        result = pnl_points / risk

        # Auto-detect result_type if not provided
        if not result_type:
            if abs(exit_price - trade['stop_loss']) < 0.5:
                result_type = "SL"
            elif abs(exit_price - trade['take_profit']) < 0.5:
                result_type = "TP"
            else:
                result_type = "MANUAL"

        exit_time = datetime.now(tz=timezone.utc)

        print(f"[TradeManager] 📡 BROKER FILL: {trade_id} @ {exit_price} "
              f"(Result: {result:.2f}R, Type: {result_type})")

        if self.trade_logger:
            self.trade_logger.log(trade_id, "NT_FILL", f"{result_type} @ {exit_price:.2f} (Result: {result:.2f}R)")

        # Persist close
        try:
            self.trade_repository.close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_time=exit_time,
                result=result,
                result_type=result_type
            )
        except Exception as e:
            print(f"[TradeManager] ❌ DB ERROR on broker fill for {trade_id}: {e}")
            self.analytics.capture_exception(e, {"op": "broker_fill", "trade_id": trade_id})
            if self.trade_logger:
                self.trade_logger.log(trade_id, "ERROR", str(e))
            self.notifier.send(f"[TradeManager] DB ERROR on broker fill for {trade_id}: {e}")

        if self.trade_logger:
            self.trade_logger.log(trade_id, "CLOSE", "Persisted to DB")

        # Remove from in-memory lists
        self.open_trades.remove(trade)

        # Emit to UI
        self.socketio.emit('trade_close', {
            'trade_id':    trade_id,
            'pair':        trade['pair'],
            'type':        trade['type'],
            'exit_price':  exit_price,
            'exit_time':   exit_time.timestamp(),
            'result':      result,
            'result_type': result_type
        })