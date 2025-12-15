#!/usr/bin/env python
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt

from src.strategies.strategy_config import CandleConfig

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
        # Must return an object with trade_id
        return type("MockTrade", (), {"trade_id": "debug_trade_3c"})()
    def __getattr__(self, name):
        def _(*args, **kwargs): return None
        return _

# --------------------------------------------------------------------
# Data Generation: 3-Candle Short Pattern on 5m Timeframe
# --------------------------------------------------------------------

def build_3candle_scenario_bars() -> List[Dict[str, Any]]:
    """
    Generates 16 mins of data to form a 3-candle pattern on the 5m chart.
    
    Pattern Target (Short):
    1. 5m Candle 1 (0-5m):  Big Green Candle (Bullish)
    2. 5m Candle 2 (5-10m): Hammer/Hanging Man at Top (Small body, long lower wick)
    3. 5m Candle 3 (10-15m): Red Candle (Bearish) -> TRIGGER
    """
    bars = []
    
    # --- Candle 1: Big Green (0-4 mins) ---
    # Open 100 -> Close 105
    for i in range(5):
        bars.append({
            "time": i * 60, 
            "open": 100.0 + i, 
            "high": 100.0 + i + 0.8, 
            "low":  100.0 + i - 0.2, 
            "close": 100.0 + i + 1.0, 
            "volume": 100, "pair": "NQ"
        })

    # --- Candle 2: Hammer at Top (5-9 mins) ---
    # Open 105.0 -> Dip to 102.0 -> Close 104.8
    # Result: High=105.2, Low=102.0, Open=105.0, Close=104.8
    # Body is at top (104.8-105.0). Upper wick is small (0.2). Lower wick is long (2.8).
    for i in range(5):
        # Simulate the dip and recovery
        if i < 3: # Dip
            price = 105.0 - i
        else: # Recover
            price = 102.0 + (i-2)*1.4
            
        bars.append({
            "time": (5+i) * 60,
            "open": price,
            "high": 105.2, # Keep high constant to form the 'top'
            "low":  102.0 if i == 2 else price - 0.5,
            "close": 104.8 if i == 4 else price - 0.2,
            "volume": 80, "pair": "NQ"
        })

    # --- Candle 3: Big Red (10-14 mins) ---
    # Open 104.8 -> Close 101.0
    for i in range(5):
        bars.append({
            "time": (10+i) * 60,
            "open": 104.8 - (i*0.5),
            "high": 104.8 - (i*0.5) + 0.2,
            "low":  104.8 - (i*0.5) - 0.5,
            "close": 104.8 - (i*0.5) - 0.8,
            "volume": 120, "pair": "NQ"
        })
        
    # --- Trigger Bar (15 min) ---
    # Start of next window to force Candle 3 to close
    bars.append({
        "time": 15 * 60, 
        "open": 101.0, "high": 101.2, "low": 100.8, "close": 101.0, 
        "volume": 10, "pair": "NQ"
    })

    return bars

# --------------------------------------------------------------------
# Aggregation Helper
# --------------------------------------------------------------------

def aggregate_bars(bars: List[Dict], timeframe_minutes: int) -> List[Dict]:
    window_secs = timeframe_minutes * 60
    groups = {}
    
    for b in bars:
        w_start = (b['time'] // window_secs) * window_secs
        if w_start not in groups: groups[w_start] = []
        groups[w_start].append(b)
    
    agg = []
    for start_time in sorted(groups.keys()):
        group = groups[start_time]
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

    # Strategy Line
    ax.axhline(line_level, linestyle="--", color="blue", linewidth=1.5, label=f"Line {line_level}")

    # Trades
    for trade in trades:
        entry_ts = trade['entry_time']
        
        # Find bar index based on time
        try:
            # For aggregated bars, 'time' is the close time. 
            # We want the bar that *contains* the entry trigger.
            idx = next(i for i, b in enumerate(bars) if b['time'] >= entry_ts)
        except StopIteration:
            continue 

        # Draw Marker
        ax.scatter(idx, trade['entry'], s=200, marker='v', color='purple', edgecolor='black', zorder=10, label='Short Entry')

        # Draw Levels
        x_start, x_end = idx - 0.5, len(bars)
        
        # ENTRY: Blue Solid
        ax.hlines(trade['entry'], x_start, x_end, colors='blue', linestyles='-', linewidth=2)
        ax.text(x_end, trade['entry'], " ENTRY", color='blue', verticalalignment='center', fontweight='bold', fontsize=9)

        # STOP LOSS: Red Dashed
        ax.hlines(trade['stop_loss'], x_start, x_end, colors='red', linestyles='--', linewidth=2)
        ax.text(x_end, trade['stop_loss'], " SL", color='red', verticalalignment='center', fontweight='bold', fontsize=9)

        # TAKE PROFIT: Green Dashed
        ax.hlines(trade['take_profit'], x_start, x_end, colors='green', linestyles='--', linewidth=2)
        ax.text(x_end, trade['take_profit'], " TP", color='green', verticalalignment='center', fontweight='bold', fontsize=9)

    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    
    # Scale Y
    all_prices = highs + lows + [line_level]
    for t in trades:
        all_prices.extend([t['stop_loss'], t['take_profit']])
        
    y_min, y_max = min(all_prices), max(all_prices)
    pad = (y_max - y_min) * 0.1
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_xlim(-1, len(bars) + 2) # Add space for labels

def plot_simulation(raw_bars, line_level, trades):
    bars_5m = aggregate_bars(raw_bars, 5)
    bars_15m = aggregate_bars(raw_bars, 15)

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 14))
    fig.canvas.manager.set_window_title("3-Candle Pattern Debugger")

    draw_candles_on_ax(ax1, raw_bars, "1 Minute (Raw)", line_level, trades)
    draw_candles_on_ax(ax2, bars_5m, "5 Minute (Pattern Visible Here: Green -> Hammer -> Red)", line_level, trades)
    draw_candles_on_ax(ax3, bars_15m, "15 Minute (Context)", line_level, trades)

    plt.tight_layout()
    plt.show()

# --------------------------------------------------------------------
# Main
# --------------------------------------------------------------------

def main():
    cfg = CandleConfig()
    
    # 1. Setup Strategy
    strategy = LiquidityStrategyV2(
        min_stop_loss=1.0,
        max_bounce=10.0,
        socketio=DummySocketIO(),
        line_repository=DummyRepo(),
        trade_repository=DummyRepo(),
        extra_sl_space=0.0,
        timeframes=["5m", "15m"], 
        candle_config=cfg
    )

    # 2. Add Line near the Hammer's high (approx 105.0)
    strategy.add_strategy_line("L1", 105.0)

    # 3. Generate Data
    bars = build_3candle_scenario_bars()
    print(f"Generated {len(bars)} bars. Feeding to strategy...")

    # 4. Capture Trades
    captured_trades = []
    original_store = strategy._store_and_emit_open
    def mock_store(trade):
        print(f"✅ Trade Triggered! Type: {trade['type']} @ {trade['entry']}")
        captured_trades.append(trade)
        original_store(trade)
    
    strategy._store_and_emit_open = mock_store

    # 5. Run Simulation
    for bar in bars:
        strategy.on_raw_bar(bar)

    # 6. Plot
    if not captured_trades:
        print("❌ No trades triggered. Check logic/ratios.")
    
    plot_simulation(bars, 105.0, captured_trades)

if __name__ == "__main__":
    main()