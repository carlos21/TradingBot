from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class LineData:
    line_id: str
    pair: str
    price: float
    direction: str
    creation_date: datetime

    def __hash__(self):
        return hash((self.line_id, self.pair, self.price, self.direction, self.creation_date))

    def __eq__(self, other):
        if not isinstance(other, LineData):
            return NotImplemented
        return (
            self.line_id == other.line_id and
            self.pair == other.pair and
            self.price == other.price and
            self.direction == other.direction and
            self.creation_date == other.creation_date
        )