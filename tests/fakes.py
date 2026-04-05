from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
from src.models import LineData, TradeData
from src.services.trade_executor import TradeExecutor
from src.notifier import Notifier
from src.analytics import AnalyticsReporter
from src.repositories.line_trigger_state_repository import InMemoryLineTriggerStateRepository as FakeLineTriggerStateRepository

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

    def get_line(self, line_id: str) -> Optional[LineData]:
        obj = self._store.get(line_id)
        if not obj:
            return None
        return LineData(
            line_id=obj.line_id,
            pair=obj.pair,
            price=obj.price,
            creation_date=obj.creation_date
        )

    def update_line(self, line_id: str, price: float) -> LineData:
        obj = self._store.get(line_id)
        if not obj:
            raise Exception(f"Line {line_id} not found")
        obj.price = price
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

    def update_take_profit(self, trade_id, new_take_profit):
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                t['take_profit'] = new_take_profit
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

    def get_trade(self, trade_id: str) -> Optional[TradeData]:
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                closed_info = next((c for c in self.closed if c['trade_id'] == trade_id), None)
                return TradeData(
                    trade_id=t['trade_id'],
                    pair=t['pair'],
                    trade_type=t['type'],
                    entry_price=t['entry'],
                    stop_loss=t['stop_loss'],
                    take_profit=t['take_profit'],
                    risk=t['risk'],
                    entry_time=t['entry_time'],
                    exit_price=closed_info['exit_price'] if closed_info else None,
                    exit_time=closed_info['exit_time'] if closed_info else None,
                    result=closed_info['result'] if closed_info else None,
                    result_type=closed_info.get('result_type') if closed_info else None,
                    params=t.get('params'),
                    created_at=datetime.utcnow()
                )
        return None

    def get_all_trades(self, pair: str) -> List[TradeData]:
        return self.list_trades(pair)

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


class FakeNotifier(Notifier):
    def __init__(self):
        self.messages = []

    def send(self, message: str) -> None:
        self.messages.append(message)


class FakeAnalyticsReporter(AnalyticsReporter):
    def __init__(self):
        self.exceptions = []
        self.trade_events = []
        self.signal_events = []
        self.contexts = {}

    def capture_exception(self, exc, context=None):
        self.exceptions.append((exc, context))

    def capture_trade_event(self, event_type, trade_data):
        self.trade_events.append((event_type, trade_data))

    def capture_signal_event(self, event_type, details):
        self.signal_events.append((event_type, details))

    def set_context(self, name, data):
        self.contexts[name] = data


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