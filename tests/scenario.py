# tests/scenario_model.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Tuple, Dict, Any, Optional
from zoneinfo import ZoneInfo

from tests.fakes import DummySocketIO, FakeLineRepository, FakeTradeRepository
from src.bars_loader import BarsLoader
from src.data_sources.csv_datasource import CSVDataSource
from src.services.trade_manager import TradeManager
from src.strategies.liquidity_strategy import LiquidityStrategy, StrategyOptions, LineRemovalMode
from src.strategies.entry_context import (
    EntryFilter, retest_cross_trigger, open_trades_limit_filter, max_bounce_filter
)
from dateutil import parser as dtparser

import re

Direction = Literal["long", "short", "buy", "sell"]

@dataclass
class Scenario:
    name: str
    csv_path: Path
    pair: str = "NQ"
    strategy_tf: str = "1m"
    min_sl: float = 10.0
    max_bounce: float = 40.0
    extra_sl: float = 0.0
    line_removal: LineRemovalMode = LineRemovalMode.NEVER
    lines: List[List[Any]] = field(default_factory=list)
    extra_filters: Optional[List[EntryFilter]] = None

    # optional: override CSV parsing format/TZ if needed
    time_fmt: Optional[str] = None
    tz: Optional[str] = None  # if None, CSVDataSource default is used

    def _parse_line_row(self, row: List[Any], idx: int) -> Dict[str, Any]:
        """
        Accept any of:
          ["short", 19560.0]
          ["L1", "short", 19560.0]
          ["short", 19560.0, "2024-07-31T11:12:00-04:00"]
          ["L2", "short", 19560.0, "row:3"]
          ["short", 19560.0, "L3", "row:3"]

        Returns a dict: {id, level, direction, at_time (int|None), at_row (int|None)}
        """
        lid, direction, level, at = None, None, None, None

        # Flatten flexible shapes
        flat = list(row)

        # try to extract an ISO/epoch/row:* token if present
        row_match_idx = next((i for i, x in enumerate(flat)
                              if isinstance(x, str) and (x.startswith("row:") or re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", x))), None)
        if row_match_idx is not None:
            at = flat.pop(row_match_idx)

        # pull out id if present (L…)
        id_idx = next((i for i, x in enumerate(flat)
                       if isinstance(x, str) and x.upper().startswith("L")), None)
        if id_idx is not None:
            lid = flat.pop(id_idx)

        # remaining must be [direction, level] (any order was already handled)
        if len(flat) != 2:
            raise ValueError(f"Invalid line spec {row!r}. Expected 2 of (direction, level) after id/at extraction.")
        a, b = flat
        if isinstance(a, str):
            direction, level = a, float(b)
        else:
            direction, level = b, float(a)

        direction = direction.lower()
        if direction == "buy":  direction = "long"
        if direction == "sell": direction = "short"
        if lid is None: lid = f"L{idx+1}"

        # convert 'at' later (needs ds._bars for row:N), keep raw for now
        return {"id": lid, "direction": direction, "level": level, "at_raw": at}

    @staticmethod
    def _parse_at_to_epoch(at_raw: Any, ds: CSVDataSource) -> Optional[int]:
        """
        Convert at_raw into epoch seconds aligned with the datasource's bar timestamps.
        Supports:
          - "row:N" (1-based row index in CSV)
          - ISO 8601 with/without tz (naive = America/New_York)
          - integer/float epoch seconds
        """
        if at_raw is None:
            return None

        # row:N shortcut
        if isinstance(at_raw, str) and at_raw.startswith("row:"):
            n = int(at_raw.split(":", 1)[1])
            if n <= 0 or n > len(ds._bars):
                raise ValueError(f"row:{n} is out of range (1..{len(ds._bars)})")
            return int(ds._bars[n-1]['time'])

        # epoch
        if isinstance(at_raw, (int, float)):
            return int(at_raw)

        if isinstance(at_raw, str):
            if dtparser is None:
                raise RuntimeError("python-dateutil not installed; cannot parse datetime strings")
            dt = dtparser.parse(at_raw)
            ny = ZoneInfo("America/New_York")
            if dt.tzinfo is None:
                # Interpret naive strings in New York to match CSVDataSource’s NY output.
                dt = dt.replace(tzinfo=ny)
            else:
                # Normalize to New York (bars are NY-based)
                dt = dt.astimezone(ny)
            return int(dt.timestamp())

        raise ValueError(f"Unsupported 'at' value: {at_raw!r}")

    def build(self):
        """Construct the full in-memory system using fakes and return all parts (including ds)."""
        sock   = DummySocketIO()
        lines_repo  = FakeLineRepository()
        trades_repo = FakeTradeRepository()

        filters = [
            open_trades_limit_filter(1),
            max_bounce_filter(self.max_bounce),
        ]
        if self.extra_filters:
            filters.extend(self.extra_filters)

        options = StrategyOptions(
            line_removal_mode=self.line_removal,
            triggers=[retest_cross_trigger],
            entry_filters=filters
        )

        strat = LiquidityStrategy(
            min_stop_loss=self.min_sl,
            max_bounce=self.max_bounce,
            extra_sl_space=self.extra_sl,
            socketio=sock,
            line_repository=lines_repo,
            trade_repository=trades_repo,
            options=options,
            strategy_tf=self.strategy_tf,
        )

        tm = TradeManager(trade_repository=trades_repo, socketio=sock)

        ds = CSVDataSource(
            pair=self.pair,
            filename=str(self.csv_path),
            time_fmt=self.time_fmt,
            tz=self.tz,
            bars_per_second=1000.0
        )

        # Prepare scheduled lines: convert 'at' into epoch using the loaded ds
        parsed = [self._parse_line_row(r, i) for i, r in enumerate(self.lines)]
        scheduled, immediate = [], []
        for p in parsed:
            at_ts = self._parse_at_to_epoch(p["at_raw"], ds)
            rec = {"id": p["id"], "level": p["level"], "direction": p["direction"], "at_time": at_ts, "added": False}
            (scheduled if at_ts is not None else immediate).append(rec)

        def combined_bar_callback(bar):
            # add any scheduled lines whose time has arrived BEFORE evaluating
            for item in scheduled:
                if not item["added"] and bar['time'] >= item["at_time"]:
                    strat.add_strategy_line(item["id"], item["level"], item["direction"])
                    item["added"] = True

            # 1m SL/TP checks
            tm.handle_new_1m_bar(bar)
            # strategy aggregation/evaluation
            strat.on_raw_bar(bar)

        loader = BarsLoader(
            data_source=ds,
            socketio=sock,
            bar_callback=combined_bar_callback
        )

        # seed immediate lines before streaming
        for item in immediate:
            strat.add_strategy_line(item["id"], item["level"], item["direction"])

        # expose scheduled list so tests can inspect if needed
        return strat, tm, lines_repo, trades_repo, loader, sock, ds


@dataclass
class ScenarioResult:
    trade_opens: List[Dict[str, Any]]
    trade_closes: List[Dict[str, Any]]
    inserted_trades: List[Dict[str, Any]]
    closed_trades: List[Dict[str, Any]]
    

def run_scenario(s: Scenario) -> ScenarioResult:
    strat, tm, lines_repo, trades_repo, loader, sock = s.build()

    # seed lines directly into strategy (mirrors what /api/lines does eventually)
    parsed = [s._parse_line_row(row, i) for i, row in enumerate(s.lines)]
    for lid, level, direction in parsed:
        strat.add_strategy_line(lid, level, direction)

    # synchronous in DummySocketIO
    loader.start(from_time=0)

    opens  = [p for (evt, p) in sock.events if evt == "trade_open"]
    closes = [p for (evt, p) in sock.events if evt == "trade_close"]

    return ScenarioResult(
        trade_opens=opens,
        trade_closes=closes,
        inserted_trades=list(trades_repo.inserted),
        closed_trades=list(trades_repo.closed),
    )