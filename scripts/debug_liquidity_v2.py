#!/usr/bin/env python
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt

# --------------------------------------------------------------------
# Make project imports work
# --------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.liquidity_strategy_v2 import LiquidityStrategyV2

# --------------------------------------------------------------------
# Mocks
# --------------------------------------------------------------------

@dataclass
class DummySocketIO:
    def emit(self, *args, **kwargs): pass

class DummyRepo:
    def insert_trade(self, *args, **kwargs):
        return type("MockTrade", (), {"trade_id": "debug_trade_001"})()
    def __getattr__(self, name):
        def _(*args, **kwargs): return None
        return _

# --------------------------------------------------------------------
# Data Generation
# --------------------------------------------------------------------

def build_15m_scenario_bars() -> List[Dict[str, Any]]:
    """
    Generates 16 one-minute bars.
    The aggregate 15m candle will look like a Hammer/Pinbar.
    """
    bars = []
    
    # 0-4: Price hovers around 100
    for i in range(5):
        bars.append({
            "time": i * 60, 
            "open": 100.0, "high": 100.5, "low": 99.8, "close": 100.2, 
            "volume": 10, "pair": "MNQ"
        })

    # 5-9: Price crashes down to 98.0 (The Wick)
    for i in range(5):
        bars.append({
            "time": (5+i) * 60,
            "open": 100.0 - (i*0.4),
            "high": 100.0 - (i*0.4),
            "low": 98.0, # The extreme low
            "close": 98.5,
            "volume": 50, "pair": "MNQ"
        })

    # 10-14: Price recovers back to 100.0
    for i in range(5):
        bars.append({
            "time": (10+i) * 60,
            "open": 98.5 + (i*0.3),
            "high": 99.0 + (i*0.3),
            "low": 98.5 + (i*0.3),
            "close": 100.1, # Closes near open -> Hammer shape on 15m
            "volume": 20, "pair": "MNQ"
        })
        
    # Bar 15: The start of the NEXT 15m candle (triggers the previous one to close)
    bars.append({
        "time": 15 * 60, 
        "open": 100.1, "high": 100.2, "low": 100.0, "close": 100.1, 
        "volume": 5, "pair": "MNQ"
    })

    return bars

# --------------------------------------------------------------------
# Aggregation Helper for Plotting
# --------------------------------------------------------------------

def aggregate_bars(bars: List[Dict], timeframe_minutes: int) -> List[Dict]:
    """
    Manually aggregates 1m bars into X-minute bars for visualization.
    Matches the logic used inside the strategy.
    """
    window_secs = timeframe_minutes * 60
    groups = {}
    
    for b in bars:
        # Determine window start
        w_start = (b['time'] // window_secs) * window_secs
        if w_start not in groups:
            groups[w_start] = []
        groups[w_start].append(b)
    
    agg = []
    for start_time in sorted(groups.keys()):
        group = groups[start_time]
        # Strategy emits bar at END of window (start + duration)
        agg.append({
            'time': start_time + window_secs, 
            'open': group[0]['open'],
            'high': max(b['high'] for b in group),
            'low': min(b['low'] for b in group),
            'close': group[-1]['close'],
            'pair': group[0]['pair']
        })
    return agg

# --------------------------------------------------------------------
# Plotting Logic
# --------------------------------------------------------------------

def draw_candles_on_ax(ax, bars, title, line_level, trades):
    indices = list(range(len(bars)))
    opens   = [b["open"] for b in bars]
    highs   = [b["high"] for b in bars]
    lows    = [b["low"] for b in bars]
    closes  = [b["close"] for b in bars]

    candle_width = 0.6  
    wick_width   = 1.5

    # 1. Draw Candles
    for i in indices:
        o, c, h, l = opens[i], closes[i], highs[i], lows[i]
        color = 'green' if c >= o else 'red'
        
        # Wick
        ax.vlines(i, l, h, color='black', linewidth=wick_width, zorder=1)
        
        # Body
        lower_body = min(o, c)
        body_height = abs(c - o)
        if body_height == 0: body_height = 0.05 

        ax.bar(i, body_height, bottom=lower_body, width=candle_width, color=color, alpha=0.9, zorder=2)

    # 2. Draw Strategy Line
    ax.axhline(line_level, linestyle="--", color="blue", linewidth=1.0, alpha=0.7, label=f"Line {line_level}")

    # 3. Draw Trades
    for trade in trades:
        entry_ts = trade['entry_time']
        
        # Find the bar that covers this timestamp
        # Since aggregated bars have 'time' as the CLOSE time of the window:
        # We look for the first bar where bar['time'] >= entry_ts
        try:
            idx = next(i for i, b in enumerate(bars) if b['time'] >= entry_ts)
        except StopIteration:
            continue # Trade happened after these bars?

        # Draw Marker
        ax.scatter(idx, trade['entry'], s=150, marker='*', color='gold', edgecolor='black', zorder=10)

        # Draw Levels (Entry/SL/TP)
        x_start, x_end = idx - 0.5, len(bars)
        ax.hlines(trade['entry'], x_start, x_end, colors='gray', linestyles='-', linewidth=1)
        ax.hlines(trade['stop_loss'], x_start, x_end, colors='red', linestyles='--', linewidth=1)
        ax.hlines(trade['take_profit'], x_start, x_end, colors='green', linestyles='--', linewidth=1)

    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    
    # Auto-scale Y
    all_prices = highs + lows + [line_level]
    for t in trades:
        all_prices.extend([t['stop_loss'], t['take_profit']])
    
    y_min, y_max = min(all_prices), max(all_prices)
    pad = (y_max - y_min) * 0.1
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_xlim(-1, len(bars))

def plot_simulation(raw_bars, line_level, trades):
    # Aggregate data
    bars_5m = aggregate_bars(raw_bars, 5)
    bars_15m = aggregate_bars(raw_bars, 15)

    # Create 3 subplots
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 12))
    fig.canvas.manager.set_window_title("Liquidity V2 Multi-TF Analysis")

    draw_candles_on_ax(ax1, raw_bars, "1 Minute (Raw Data)", line_level, trades)
    draw_candles_on_ax(ax2, bars_5m, "5 Minute (Aggregated)", line_level, trades)
    draw_candles_on_ax(ax3, bars_15m, "15 Minute (Aggregated - Trigger Found Here)", line_level, trades)

    plt.tight_layout()
    plt.show()

# --------------------------------------------------------------------
# Main Execution
# --------------------------------------------------------------------

def main():
    # 1. Setup Strategy
    strategy = LiquidityStrategyV2(
        min_stop_loss=1.0,
        max_bounce=10.0,
        socketio=DummySocketIO(),
        line_repository=DummyRepo(),
        trade_repository=DummyRepo(),
        extra_sl_space=0.0,
        timeframes=["5m", "15m"], 
    )

    # 2. Add Line
    strategy.add_strategy_line("L1", 98.0)

    # 3. Generate Data
    bars = build_15m_scenario_bars()
    print(f"Generated {len(bars)} bars. Feeding to strategy...")

    # 4. Capture Trades
    captured_trades = []
    original_store = strategy._store_and_emit_open
    def mock_store(trade):
        print("✅ Trade Triggered!")
        captured_trades.append(trade)
        original_store(trade)
    
    strategy._store_and_emit_open = mock_store

    # 5. Run Simulation
    for bar in bars:
        strategy.on_raw_bar(bar)

    # 6. Plot
    if not captured_trades:
        print("❌ No trades triggered.")
    
    plot_simulation(bars, 98.0, captured_trades)

if __name__ == "__main__":
    main()