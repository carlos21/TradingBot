
from dataclasses import dataclass
from datetime import datetime


class DummySocketIO:
    def __init__(self):
        self.events = []  # list[(event, payload)]

    def emit(self, event, payload):
        self.events.append((event, payload))

    # In tests, call the target immediately (no threads).
    def start_background_task(self, target, *args, **kwargs):
        return target(*args, **kwargs)
    
@dataclass
class _Line:
    line_id: str
    pair: str
    price: float
    direction: str
    creation_date: datetime

class FakeLineRepository:
    def __init__(self):
        self._seq = 0
        self._store = {}

    def list_lines(self, pair: str):
        return [obj for obj in self._store.values() if obj.pair == pair]

    def insert_line(self, pair, price, direction):
        self._seq += 1
        lid = f"L{self._seq}"
        obj = _Line(lid, pair, price, direction, datetime.utcnow())
        self._store[lid] = obj
        return obj

    def delete_line(self, line_id):
        if line_id not in self._store:
            from src.dbexception import DBNotFoundException
            raise DBNotFoundException()
        del self._store[line_id]

@dataclass
class _Trade:
    trade_id: str

class FakeTradeRepository:
    def __init__(self):
        self._seq = 0
        self.inserted = []  # dicts of fields
        self.closed   = []  # dicts of fields

    def insert_trade(self, **kwargs):
        self._seq += 1
        self.inserted.append(kwargs | {"trade_id": f"T{self._seq}"})
        return _Trade(trade_id=f"T{self._seq}")

    def close_trade(self, **kwargs):
        self.closed.append(kwargs)