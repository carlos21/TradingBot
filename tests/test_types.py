"""Tests for core type definitions."""

import pytest
from src.types import (
    Direction,
    ResultType,
    TimeFrame,
    TradeSource,
)


class TestDirection:
    def test_enum_values(self):
        assert Direction.LONG.value == "long"
        assert Direction.SHORT.value == "short"
    
    def test_from_string_long(self):
        assert Direction.from_string("long") == Direction.LONG
        assert Direction.from_string("LONG") == Direction.LONG
        assert Direction.from_string("Long") == Direction.LONG
    
    def test_from_string_short(self):
        assert Direction.from_string("short") == Direction.SHORT
        assert Direction.from_string("SHORT") == Direction.SHORT
        assert Direction.from_string("Short") == Direction.SHORT
    
    def test_from_string_invalid(self):
        with pytest.raises(ValueError, match="Invalid direction"):
            Direction.from_string("buy")  # 'buy' no longer accepted
        
        with pytest.raises(ValueError, match="Invalid direction"):
            Direction.from_string("sell")  # 'sell' no longer accepted
        
        with pytest.raises(ValueError, match="Invalid direction"):
            Direction.from_string("invalid")
        
        with pytest.raises(ValueError, match="Invalid direction"):
            Direction.from_string("")
    
    def test_is_long_property(self):
        assert Direction.LONG.is_long is True
        assert Direction.SHORT.is_long is False
    
    def test_is_short_property(self):
        assert Direction.SHORT.is_short is True
        assert Direction.LONG.is_short is False
    
    def test_opposite(self):
        assert Direction.LONG.opposite() == Direction.SHORT
        assert Direction.SHORT.opposite() == Direction.LONG
    
    def test_sign(self):
        assert Direction.LONG.sign() == 1
        assert Direction.SHORT.sign() == -1
    
    def test_str(self):
        """Test __str__ returns the value."""
        assert str(Direction.LONG) == "long"
        assert str(Direction.SHORT) == "short"
    
    def test_immutable(self):
        # Enum members are immutable
        with pytest.raises(AttributeError):
            Direction.LONG.value = "invalid"


class TestResultType:
    def test_enum_values(self):
        assert ResultType.STOP_LOSS.value == "SL"
        assert ResultType.TAKE_PROFIT.value == "TP"
        assert ResultType.BREAKEVEN.value == "BE"
        assert ResultType.MANUAL.value == "SP"
    
    def test_from_string(self):
        assert ResultType.from_string("SL") == ResultType.STOP_LOSS
        assert ResultType.from_string("sl") == ResultType.STOP_LOSS
        assert ResultType.from_string("TP") == ResultType.TAKE_PROFIT
        assert ResultType.from_string("stop_loss") == ResultType.STOP_LOSS
        assert ResultType.from_string("take_profit") == ResultType.TAKE_PROFIT
    
    def test_from_string_invalid(self):
        with pytest.raises(ValueError, match="Invalid result type"):
            ResultType.from_string("invalid")


class TestTimeFrame:
    def test_enum_values(self):
        assert TimeFrame.M1.label == "1m"
        assert TimeFrame.M1.seconds == 60
        assert TimeFrame.H1.label == "1h"
        assert TimeFrame.H1.seconds == 3600
    
    def test_from_string(self):
        assert TimeFrame.from_string("5m") == TimeFrame.M5
        assert TimeFrame.from_string("1h") == TimeFrame.H1
        assert TimeFrame.from_string("15M") == TimeFrame.M15
    
    def test_from_string_invalid(self):
        with pytest.raises(ValueError, match="Invalid timeframe"):
            TimeFrame.from_string("invalid")
    
    def test_to_seconds(self):
        assert TimeFrame.M5.to_seconds() == 300
        assert TimeFrame.H4.to_seconds() == 14400


class TestDirectionInConditionals:
    """Test that Direction works in typical use cases."""
    
    def test_direction_in_if_statements(self):
        direction = Direction.LONG
        
        if direction.is_long:
            result = "long"
        else:
            result = "short"
        
        assert result == "long"
    
    def test_direction_in_dictionary(self):
        # Direction can be used as dict key
        data = {
            Direction.LONG: {"stop_loss": -50, "take_profit": 200},
            Direction.SHORT: {"stop_loss": 50, "take_profit": -200},
        }
        
        assert data[Direction.LONG]["stop_loss"] == -50
        assert data[Direction.SHORT]["stop_loss"] == 50
    
    def test_direction_comparison(self):
        # Direction equality works as expected
        assert Direction.LONG == Direction.LONG
        assert Direction.LONG != Direction.SHORT
        assert Direction.SHORT == Direction.SHORT
    
    def test_direction_str_in_comparisons(self):
        """Direction can be compared to its string value."""
        assert Direction.LONG.value == "long"
        assert Direction.SHORT.value == "short"


class TestTradeSource:
    def test_enum_values(self):
        assert TradeSource.STRATEGY.value == "strategy"
        assert TradeSource.MANUAL.value == "manual"
        assert TradeSource.TEST.value == "test"
        assert TradeSource.BROKER_SYNC.value == "broker_sync"

    def test_from_string(self):
        assert TradeSource.from_string("strategy") == TradeSource.STRATEGY
        assert TradeSource.from_string("manual") == TradeSource.MANUAL
        assert TradeSource.from_string("test") == TradeSource.TEST
        assert TradeSource.from_string("broker_sync") == TradeSource.BROKER_SYNC

    def test_from_string_invalid(self):
        with pytest.raises(ValueError, match="Invalid trade source"):
            TradeSource.from_string("unknown")

    def test_str(self):
        assert str(TradeSource.STRATEGY) == "strategy"
        assert str(TradeSource.MANUAL) == "manual"
