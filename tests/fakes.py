from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
from src.models import LineData, TradeData
from src.services.trade_executor import TradeExecutor

class DummySocketIO:
    def __init__(self):
        self.events = []

    def emit(self, event, payload):
        self.events.append((event, payload))

    def start_background_task(self, target, *args, **kwargs):
        return target(*args, **kwargs)

    def run(self, app, **kwargs):
        pass

@dataclass
class _Line:
    line_id: str
    pair: str
    price: float
    creation_date: datetime

class FakeLineRepository:
    def __init__(self):
        self._seq = 0
        self._store = {}

    def list_lines(self, pair: str) -> List[LineData]:
        results = []
        for obj in self._store.values():
            if obj.pair == pair:
                results.append(LineData(
                    line_id=obj.line_id,
                    pair=obj.pair,
                    price=obj.price,
                    creation_date=obj.creation_date
                ))
        return results

    def insert_line(self, pair, price, creation_date=None) -> LineData:
        self._seq += 1
        lid = f"L{self._seq}"
        c_date = creation_date if creation_date else datetime.utcnow()
        obj = _Line(lid, pair, price, c_date)
        self._store[lid] = obj
        
        return LineData(
            line_id=obj.line_id,
            pair=obj.pair,
            price=obj.price,
            creation_date=obj.creation_date
        )

    def delete_line(self, line_id):
        if line_id in self._store:
            del self._store[line_id]

class FakeTradeRepository:
    def __init__(self):
        self._seq = 0
        self.inserted = [] 
        self.closed   = [] 

    def clear(self):
        """Wipe all data for a fresh scenario."""
        self._seq = 0
        self.inserted = []
        self.closed = []

    def insert_trade(self, pair, trade_type, entry_price, stop_loss, take_profit, risk, entry_time, params=None):
        self._seq += 1
        trade_id = f"T{self._seq}"

        self.inserted.append({
            "trade_id": trade_id,
            "pair": pair,
            "type": trade_type,
            "entry": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk,
            "entry_time": entry_time,
            "params": params
        })
        return TradeData(
            trade_id=trade_id,
            pair=pair,
            trade_type=trade_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk=risk,
            entry_time=entry_time,
            exit_price=None,
            exit_time=None,
            result=None,
            result_type=None,
            params=params,
        )

    def update_stop_loss(self, trade_id, new_stop_loss):
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                t['stop_loss'] = new_stop_loss
                return

    def update_entry_price(self, trade_id, new_entry_price):
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                t['entry'] = new_entry_price
                return
    
    def close_trade(self, trade_id, exit_price, exit_time, result, result_type=None):
        self.closed.append({
            "trade_id": trade_id,
            "exit_price": exit_price,
            "exit_time": exit_time,
            "result": result,
            "result_type": result_type
        })

    def append_trade_log(self, trade_id: str, event: str, message: str) -> None:
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                logs = t.setdefault('logs', [])
                logs.append({"ts": "", "event": event, "msg": message})
                return

    def get_trade_logs(self, trade_id: str) -> list:
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                return t.get('logs', [])
        return []

    def list_trades(self, pair: str) -> List[TradeData]:
        results = []
        for t in self.inserted:
            if t['pair'] != pair:
                continue
            
            closed_info = next((c for c in self.closed if c['trade_id'] == t['trade_id']), None)

            exit_price = closed_info['exit_price'] if closed_info else None
            exit_time  = closed_info['exit_time']  if closed_info else None
            result     = closed_info['result']     if closed_info else None
            result_type = closed_info.get('result_type') if closed_info else None

            results.append(TradeData(
                trade_id=t['trade_id'],
                pair=t['pair'],
                trade_type=t['type'],
                entry_price=t['entry'],
                stop_loss=t['stop_loss'],
                take_profit=t['take_profit'],
                risk=t['risk'],
                entry_time=t['entry_time'],
                exit_price=exit_price,
                exit_time=exit_time,
                result=result,
                result_type=result_type,
                params=t.get('params'),
                created_at=datetime.utcnow()
            ))
        return results


class FakeTradeExecutor(TradeExecutor):
    def __init__(self):
        self.opens = []
        self.closes = []
        self.sl_updates = []

    def on_trade_open(self, trade):
        self.opens.append(trade)

    def on_trade_close(self, trade_id, exit_price):
        self.closes.append((trade_id, exit_price))

    def on_sl_update(self, trade_id, new_sl):
        self.sl_updates.append((trade_id, new_sl))