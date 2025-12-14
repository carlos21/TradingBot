# src/trade_manager.py

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from src.repositories.trades_repository import TradeRepository


class TradeManager:
    """
    Handles closing open trades when a raw 1m bar touches SL or TP.
    """

    def __init__(self, trade_repository: TradeRepository, socketio):
        """
        :param strategy:        your LiquidityStrategy instance (must have `open_trades` list)
        :param trade_repository:SQLTradeRepository instance (must have close_trade)
        :param socketio:        flask_socketio.SocketIO instance
        """
        self.open_trades = []
        self.trade_repository = trade_repository
        self.socketio         = socketio

    def handle_new_1m_bar(self, bar: dict):
        """
        Call this on every 1m bar:
        - scans strategy.open_trades
        - when SL/TP is hit, closes & emits a 'trade_close'
        """
        for trade in list(self.open_trades):
            if trade['pair'] != bar['pair']:
                continue
            
            ttype = trade['type']
            is_buy  = ttype in ('buy', 'long')
            is_sell = ttype in ('sell', 'short')

            if is_buy:
                hit_sl = bar['low']  <= trade['stop_loss']
                hit_tp = bar['high'] >= trade['take_profit']
            elif is_sell: 
                # for sells, SL is above entry and TP is below entry
                hit_sl = bar['high'] >= trade['stop_loss']
                hit_tp = bar['low']  <= trade['take_profit']
            else:
                continue

            # determine exit
            exit_price = trade['stop_loss'] if hit_sl else trade['take_profit']
            exit_time  = datetime.fromtimestamp(bar['time'], tz=ZoneInfo('UTC'))

            # calculate P&L
            if is_buy:
                result = exit_price - trade['entry']
            else:
                result = trade['entry'] - exit_price

            # persist the close
            self.trade_repository.close_trade(
                trade_id   = trade['trade_id'],
                exit_price = exit_price,
                exit_time  = exit_time,
                result     = result
            )

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
        # notify clients
        self.socketio.emit('trade_open', trade)

        return trade

    def close_trade(self, trade_id: str, exit_price: float, exit_time: float):
        """
        Close an existing trade by ID with provided exit parameters.
        """
        # find the trade
        trade = next(
            t for t in self.open_trades
            if t['trade_id'] == trade_id
        )
        # calculate P&L
        result = (
            exit_price - trade['entry']
            if trade['type'] == 'buy'
            else trade['entry'] - exit_price
        )
        # persist close
        self.trade_repository.close_trade(
            trade_id=trade_id,
            exit_price=exit_price,
            exit_time=datetime.fromtimestamp(exit_time, tz=timezone.utc),
            result=result
        )
        # remove from in-memory
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