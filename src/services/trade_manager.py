# src/trade_manager.py

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from src.repositories.trades_repository import TradeRepository


class TradeManager:
    """
    Handles closing open trades when a raw 1m bar touches SL or TP.
    """

    def __init__(self, trade_repository: TradeRepository, socketio,
                 session_end_time: str = None, session_tz: str = None):
        """
        :param trade_repository: SQLTradeRepository instance (must have close_trade)
        :param socketio:         flask_socketio.SocketIO instance
        :param session_end_time: "HH:MM" — close open trades at this time (e.g. "15:00")
        :param session_tz:       Timezone for session_end_time (e.g. "America/New_York")
        """
        self.open_trades = []
        self.trade_repository = trade_repository
        self.socketio         = socketio

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
            all_trades = self.trade_repository.list_trades('NQ')
            
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

    def handle_new_1m_bar(self, bar: dict):
        """
        Call this on every 1m bar:
        - scans strategy.open_trades
        - when SL/TP is hit, closes & emits a 'trade_close'
        """
        for trade in list(self.open_trades):
            if trade['pair'] != bar['pair']:
                continue
            
            # CRITICAL FIX: Do not evaluate trades that haven't happened yet in this replay timeline
            if trade['entry_time'] > bar['time']:
                # Optional: Log once that we are waiting for this trade
                # if trade['trade_id'] not in self._monitored_trades:
                #     print(f"[TradeManager] ⏳ Waiting for Trade {trade['trade_id']} (Entry: {trade['entry_time']} > Current: {bar['time']})")
                continue
            
            # Log activation once
            if trade['trade_id'] not in self._monitored_trades:
                print(f"[TradeManager] 🟢 ACTIVATING Trade {trade['trade_id']} at {bar['time']} (Replay caught up to Entry)")
                self._monitored_trades.add(trade['trade_id'])

            ttype = trade['type']
            is_buy  = ttype in ('buy', 'long')
            is_sell = ttype in ('sell', 'short')

            hit_sl = False
            hit_tp = False

            if is_buy:
                if bar['low'] <= trade['stop_loss']:
                    hit_sl = True
                    print(f"[TradeManager] 🛑 BUY SL HIT! Trade {trade['trade_id']} | Low {bar['low']} <= SL {trade['stop_loss']}")
                elif bar['high'] >= trade['take_profit']:
                    hit_tp = True
                    print(f"[TradeManager] 💰 BUY TP HIT! Trade {trade['trade_id']} | High {bar['high']} >= TP {trade['take_profit']}")
            elif is_sell: 
                if bar['high'] >= trade['stop_loss']:
                    hit_sl = True
                    print(f"[TradeManager] 🛑 SELL SL HIT! Trade {trade['trade_id']} | High {bar['high']} >= SL {trade['stop_loss']}")
                elif bar['low'] <= trade['take_profit']:
                    hit_tp = True
                    print(f"[TradeManager] 💰 SELL TP HIT! Trade {trade['trade_id']} | Low {bar['low']} <= TP {trade['take_profit']}")
            
            if not hit_sl and not hit_tp:
                continue

            # determine exit
            exit_price = trade['stop_loss'] if hit_sl else trade['take_profit']
            exit_time  = datetime.fromtimestamp(bar['time'], tz=ZoneInfo('UTC'))

            # calculate P&L (R-Multiple)
            risk = trade.get('risk', 0)
            if risk <= 0: risk = 1.0 # avoid div/0

            if is_buy:
                pnl_points = exit_price - trade['entry']
            else:
                pnl_points = trade['entry'] - exit_price
            
            result = pnl_points / risk

            print(f"[TradeManager] 📉 Closing trade {trade['trade_id']} (Result: {result:.2f}R) at {bar['time']}")

            try:
                # persist the close
                self.trade_repository.close_trade(
                    trade_id   = trade['trade_id'],
                    exit_price = exit_price,
                    exit_time  = exit_time,
                    result     = result
                )
                print(f"[TradeManager] 💾 DB Updated for Trade {trade['trade_id']} (Closed)")
            except Exception as e:
                print(f"[TradeManager] ❌ DB ERROR closing trade {trade['trade_id']}: {e}")

            # remove from live list
            self.open_trades.remove(trade)

            # emit to clients
            self.socketio.emit('trade_close', {
                'trade_id':   trade['trade_id'],
                'pair':       trade['pair'],
                'type':       trade['type'],
                'exit_price': exit_price,
                'exit_time':  bar['time'],
                'result':     result
            })

        # After SL/TP checks, close any remaining open trades if session has ended
        self._check_session_end_close(bar)

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

            print(f"[TradeManager] 🕐 SESSION END closing trade {trade['trade_id']} @ {exit_price} (Result: {result:.2f}R)")

            try:
                self.trade_repository.close_trade(
                    trade_id=trade['trade_id'],
                    exit_price=exit_price,
                    exit_time=exit_time,
                    result=result
                )
            except Exception as e:
                print(f"[TradeManager] ❌ DB ERROR closing trade {trade['trade_id']}: {e}")

            self.open_trades.remove(trade)

            self.socketio.emit('trade_close', {
                'trade_id':   trade['trade_id'],
                'pair':       trade['pair'],
                'type':       trade['type'],
                'exit_price': exit_price,
                'exit_time':  bar['time'],
                'result':     result
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
        
        # If not in memory, try to fetch from DB to calculate PnL, or just close it blindly
        if not trade:
            print(f"[TradeManager] ⚠️ Trade {trade_id} not in memory, closing in DB directly.")
            # Fallback: just close in DB
            self.trade_repository.close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_time=datetime.fromtimestamp(exit_time, tz=timezone.utc),
                result=0.0 
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

        # persist close
        self.trade_repository.close_trade(
            trade_id=trade_id,
            exit_price=exit_price,
            exit_time=datetime.fromtimestamp(exit_time, tz=timezone.utc),
            result=result
        )
        # remove from in-memory
        if trade in self.open_trades:
            self.open_trades.remove(trade)
            
        # emit close event
        payload = {
            'trade_id':  trade_id,
            'pair':      trade['pair'],
            'type':      trade['type'],
            'exit_price':exit_price,
            'exit_time': exit_time,
            'result':    result
        }
        self.socketio.emit('trade_close', payload)

        return payload
    
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