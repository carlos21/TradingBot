from dataclasses import dataclass
from typing import Dict

@dataclass
class StrategyConfig:
    stop_loss:      Dict[str, float]
    max_bounce:     Dict[str, float]
    extra_sl_space: Dict[str, float]