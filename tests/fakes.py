from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
from src.models import LineData  # Import strict model

class DummySocketIO:
    def __init__(self):
        self.events = []

    def emit(self, event, payload):
        self.events.append((event, payload))

    def start_background_task(self, target, *args, **kwargs):
        return target(*args, **kwargs)

    def run(self, app, **kwargs):
        # Allow calling run() in tests, though usually we mock it out
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
        # Must return LineData objects, not internal _Line dicts
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
        # Use passed date or fallback
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
        # Silent ignore if missing, matching some DB behaviors or strict if needed

class FakeTradeRepository:
    def __init__(self):
        self._seq = 0
        self.inserted = [] 
        self.closed   = [] 

    def insert_trade(self, pair, trade_type, entry_price, stop_loss, take_profit, risk, entry_time, params=None):
        self._seq += 1
        trade_id = f"T{self._seq}"
        
        # Create a mock object that mimics the SQL Alchemy model return
        class MockTradeData:
            def __init__(self, tid): self.trade_id = tid

        self.inserted.append({
            "trade_id": trade_id,
            "pair": pair,
            "type": trade_type,
            "entry": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": risk
        })
        return MockTradeData(trade_id)

    def update_stop_loss(self, trade_id, new_stop_loss):
        for t in self.inserted:
            if t['trade_id'] == trade_id:
                t['stop_loss'] = new_stop_loss
                return
    
    def close_trade(self, trade_id, exit_price, exit_time, result):
        self.closed.append({
            "trade_id": trade_id,
            "exit_price": exit_price,
            "result": result
        })