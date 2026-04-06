"""Pure function utility for bar aggregation operations.

This module provides a single source of truth for bar aggregation logic,
eliminating duplication across BarsLoader, CSVDataSource, and Strategy classes.
"""

from typing import Dict, List, Optional


class BarAggregator:
    """Pure function utility for bar aggregation operations.
    
    All methods are stateless and operate only on provided arguments.
    This ensures consistent behavior across all parts of the system.
    """

    @staticmethod
    def parse_timeframe(tf: str) -> int:
        """Parse timeframe string into seconds.
        
        Args:
            tf: Timeframe string like "5m", "1h", "15m"
            
        Returns:
            Number of seconds in the timeframe
            
        Raises:
            ValueError: If timeframe format is unsupported
        """
        if not tf:
            raise ValueError("Timeframe cannot be empty")
            
        unit = tf[-1].lower()
        try:
            val = int(tf[:-1])
        except ValueError as e:
            raise ValueError(f"Invalid timeframe format: {tf}") from e
        
        if val <= 0:
            raise ValueError(f"Timeframe value must be positive: {tf}")
        
        multipliers = {
            'm': 60,
            'h': 3600,
            'd': 86400,
        }
        
        if unit not in multipliers:
            raise ValueError(f"Unsupported timeframe unit '{unit}' in '{tf}'")
            
        return val * multipliers[unit]

    @staticmethod
    def aggregate(bars: List[Dict], timeframe: Optional[str] = None) -> Optional[Dict]:
        """Aggregate multiple bars into a single bar.
        
        Args:
            bars: List of bar dictionaries with 'open', 'high', 'low', 'close', 'volume', 'time', 'pair'
            timeframe: Optional timeframe string to calculate window start
            
        Returns:
            Aggregated bar dict, or None if bars list is empty
        """
        if not bars:
            return None
            
        first_bar = bars[0]
        last_bar = bars[-1]
        
        # Calculate window start if timeframe provided
        window_start = first_bar['time']
        if timeframe:
            window_secs = BarAggregator.parse_timeframe(timeframe)
            window_start = (first_bar['time'] // window_secs) * window_secs
        
        return {
            'time': window_start,
            'open': first_bar['open'],
            'high': max(b['high'] for b in bars),
            'low': min(b['low'] for b in bars),
            'close': last_bar['close'],
            'volume': sum(b.get('volume', 0) for b in bars),
            'pair': first_bar.get('pair', ''),
        }

    @staticmethod
    def aggregate_with_window(bars: List[Dict], window_start: int, window_secs: int) -> Optional[Dict]:
        """Aggregate bars with explicit window parameters.
        
        Args:
            bars: List of bar dictionaries
            window_start: Start timestamp of the window
            window_secs: Window size in seconds
            
        Returns:
            Aggregated bar dict with time = window_start + window_secs, or None if bars empty
        """
        if not bars:
            return None
            
        return {
            'time': window_start + window_secs,
            'open': bars[0]['open'],
            'high': max(b['high'] for b in bars),
            'low': min(b['low'] for b in bars),
            'close': bars[-1]['close'],
            'volume': sum(b.get('volume', 0) for b in bars),
            'pair': bars[0].get('pair', ''),
        }

    @staticmethod
    def bucket_by_timeframe(bars: List[Dict], timeframe: str) -> Dict[int, List[Dict]]:
        """Group bars into buckets by timeframe window.
        
        Args:
            bars: List of bar dictionaries
            timeframe: Timeframe string like "5m", "1h"
            
        Returns:
            Dictionary mapping window start timestamps to lists of bars
        """
        from collections import defaultdict
        
        window_secs = BarAggregator.parse_timeframe(timeframe)
        buckets: Dict[int, List[Dict]] = defaultdict(list)
        
        for bar in bars:
            win = (bar['time'] // window_secs) * window_secs
            buckets[win].append(bar)
            
        return dict(buckets)

    @staticmethod
    def merge_partial(partial_bar: Dict, buffered_bars: List[Dict], window_start: int) -> Dict:
        """Merge a partial tick/bar with buffered completed bars.
        
        Used for live mode to show correct aggregated candle with partial data.
        
        Args:
            partial_bar: The partial/incomplete bar dict
            buffered_bars: List of completed 1m bars in the current window
            window_start: The window start timestamp
            
        Returns:
            Merged bar dict representing current aggregated state
        """
        if not buffered_bars:
            # No buffered bars, use partial as-is but fix time
            return {
                'time': window_start,
                'open': partial_bar['open'],
                'high': partial_bar['high'],
                'low': partial_bar['low'],
                'close': partial_bar['close'],
                'volume': partial_bar.get('volume', 0),
                'pair': partial_bar.get('pair', ''),
            }
        
        return {
            'time': window_start,
            'open': buffered_bars[0]['open'],
            'high': max(max(b['high'] for b in buffered_bars), partial_bar['high']),
            'low': min(min(b['low'] for b in buffered_bars), partial_bar['low']),
            'close': partial_bar['close'],
            'volume': sum(b.get('volume', 0) for b in buffered_bars) + partial_bar.get('volume', 0),
            'pair': partial_bar.get('pair', buffered_bars[0].get('pair', '')),
        }
