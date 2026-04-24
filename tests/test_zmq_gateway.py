"""
Tests for ZeroMQ Gateway.

Run with: pytest tests/test_zmq_gateway.py -v
"""

import pytest
import threading
import time
import json
from unittest.mock import MagicMock, patch

# Skip if zmq not installed
zmq = pytest.importorskip("zmq")

from src.gateway.protocol import (
    MessageType,
    MessageEnvelope,
    TickMessage,
    BarMessage,
    OpenOrderCommand,
    CloseOrderCommand,
)
from src.gateway.gateway import TradingGateway, GatewayConfig
from src.utils.app_logger import ConsoleLogger


class TestProtocol:
    """Test message protocol serialization."""
    
    def test_message_envelope_creation(self):
        envelope = MessageEnvelope.create(
            msg_type=MessageType.TICK,
            payload={"price": 100.0},
            seq_num=1,
        )
        assert envelope.msg_type == MessageType.TICK
        assert envelope.payload == {"price": 100.0}
        assert envelope.seq_num == 1
        assert envelope.timestamp > 0
    
    def test_message_envelope_serialization(self):
        envelope = MessageEnvelope.create(
            msg_type=MessageType.TICK,
            payload={"price": 100.0},
        )
        json_str = envelope.to_json()
        data = json.loads(json_str)
        
        assert data["msg_type"] == "tick"
        assert data["payload"]["price"] == 100.0
    
    def test_message_envelope_deserialization(self):
        original = MessageEnvelope.create(
            msg_type=MessageType.BAR,
            payload={"open": 100, "close": 101},
            seq_num=42,
        )
        json_str = original.to_json()
        restored = MessageEnvelope.from_json(json_str)
        
        assert restored.msg_type == MessageType.BAR
        assert restored.seq_num == 42
        assert restored.payload["open"] == 100
    
    def test_tick_message(self):
        tick = TickMessage(
            pair="MNQ",
            price=21000.5,
            volume=100,
            time=1712789432,
        )
        envelope = tick.to_envelope(seq_num=1)
        
        assert envelope.msg_type == MessageType.TICK
        assert envelope.payload["pair"] == "MNQ"
        assert envelope.payload["price"] == 21000.5
    
    def test_bar_message(self):
        bar = BarMessage(
            pair="MNQ",
            time=1712789400,
            open=21000,
            high=21050,
            low=20990,
            close=21025,
            volume=1000,
        )
        envelope = bar.to_envelope(seq_num=1)
        
        assert envelope.msg_type == MessageType.BAR
        assert envelope.payload["high"] == 21050
    
    def test_open_order_command(self):
        cmd = OpenOrderCommand(
            trade_id="test_123",
            pair="MNQ",
            direction="long",
            entry_price=21000,
            stop_loss=20920,
            take_profit=21080,
            risk_points=80,
            rr_ratio=1.0,
        )
        envelope = cmd.to_envelope(seq_num=1)
        
        assert envelope.msg_type == MessageType.ORDER_OPEN
        assert envelope.payload["trade_id"] == "test_123"
        assert envelope.payload["direction"] == "long"


class TestGateway:
    """Test TradingGateway functionality."""
    
    def test_gateway_creation(self):
        config = GatewayConfig(
            market_data_pub="tcp://127.0.0.1:5555",
            command_pull="tcp://127.0.0.1:5556",
        )
        logger = ConsoleLogger()
        gateway = TradingGateway(logger, config=config, pair="MNQ")
        
        assert gateway.pair == "MNQ"
        assert gateway.config == config
        assert not gateway.is_connected
    
    def test_callback_registration(self):
        logger = ConsoleLogger()
        gateway = TradingGateway(logger, pair="MNQ")
        
        received = []
        def callback(payload):
            received.append(payload)
        
        gateway.on_tick(callback)
        
        # Simulate receiving a tick
        envelope = MessageEnvelope.create(
            msg_type=MessageType.TICK,
            payload={"price": 100.0},
        )
        # Manually dispatch
        callbacks = gateway._callbacks.get(MessageType.TICK, [])
        for cb in callbacks:
            cb(envelope.payload)
        
        assert len(received) == 1
        assert received[0]["price"] == 100.0
    
    def test_sequence_number_increment(self):
        logger = ConsoleLogger()
        gateway = TradingGateway(logger, pair="MNQ")
        
        seq1 = gateway._next_seq()
        seq2 = gateway._next_seq()
        seq3 = gateway._next_seq()
        
        assert seq2 == seq1 + 1
        assert seq3 == seq2 + 1


class TestIntegration:
    """Integration tests requiring actual ZeroMQ sockets."""
    
    @pytest.fixture
    def gateway(self):
        """Create a test gateway with unique ports."""
        import random
        base_port = random.randint(30000, 40000)
        
        config = GatewayConfig(
            market_data_pub=f"tcp://127.0.0.1:{base_port}",
            command_pull=f"tcp://127.0.0.1:{base_port + 1}",
            query_rep=f"tcp://127.0.0.1:{base_port + 2}",
            heartbeat_pub=f"tcp://127.0.0.1:{base_port + 3}",
        )
        logger = ConsoleLogger()
        gateway = TradingGateway(logger, config=config, pair="TEST")
        yield gateway
        gateway.stop()
    
    def test_gateway_start_stop(self, gateway):
        """Test gateway can start and stop."""
        gateway.start()
        time.sleep(0.1)  # Let threads start
        
        assert gateway._running
        assert len(gateway._threads) == 4
        
        gateway.stop()
        
        assert not gateway._running
    
    def test_command_queueing(self, gateway):
        """Test commands are queued correctly."""
        gateway.start()
        time.sleep(0.1)
        
        # Queue a command
        gateway.send_open_order(
            trade_id="test_123",
            direction="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=110.0,
            risk_points=10.0,
            rr_ratio=1.0,
        )
        
        # Check it was queued
        assert len(gateway._command_queue) == 1
        
        gateway.stop()


class TestDataSource:
    """Test ZMQDataSource."""
    
    def test_datasource_creation(self):
        from src.gateway.datasource import ZMQDataSource
        from src.utils.app_logger import ConsoleLogger
        
        logger = ConsoleLogger()
        ds = ZMQDataSource(logger, pair="MNQ")
        
        assert ds.pair == "MNQ"
        assert not ds.is_live
        assert ds._historical_bars == []
    
    def test_bar_aggregation(self):
        from src.gateway.datasource import ZMQDataSource
        from src.utils.app_logger import ConsoleLogger
        
        logger = ConsoleLogger()
        ds = ZMQDataSource(logger, pair="MNQ")
        
        # Add some bars
        bars = [
            {"time": 1000, "open": 100, "high": 110, "low": 90, "close": 105, "volume": 100, "pair": "MNQ"},
            {"time": 1060, "open": 105, "high": 115, "low": 100, "close": 110, "volume": 200, "pair": "MNQ"},
        ]
        ds._historical_bars = bars
        
        # Test 1m aggregation (returns copy)
        result = ds.load_historical_bars("1m")
        assert len(result) == 2
        
        # Test 5m aggregation
        result = ds._aggregate_bars(bars, "5m")
        assert len(result) == 1
        assert result[0]["open"] == 100
        assert result[0]["high"] == 115
        assert result[0]["low"] == 90


class TestExecutor:
    """Test ZMQTradeExecutor."""
    
    def test_executor_creation(self):
        from src.gateway.executor import ZMQTradeExecutor
        
        from src.utils.app_logger import ConsoleLogger
        
        mock_gateway = MagicMock()
        logger = ConsoleLogger()
        executor = ZMQTradeExecutor(
            mock_gateway,
            logger,
            risk_usd=500,
        )
        
        assert executor._risk_usd == 500
        assert executor._gateway == mock_gateway
    
    def test_on_trade_open(self):
        from src.gateway.executor import ZMQTradeExecutor
        from src.utils.app_logger import ConsoleLogger
        
        mock_gateway = MagicMock()
        logger = ConsoleLogger()
        executor = ZMQTradeExecutor(mock_gateway, logger, risk_usd=500)
        
        trade = {
            "trade_id": "test_123",
            "type": "long",
            "entry": 21000,
            "stop_loss": 20920,
            "take_profit": 21080,
            "risk": 80,
        }
        
        executor.on_trade_open(trade)
        
        mock_gateway.send_open_order.assert_called_once()
        args = mock_gateway.send_open_order.call_args[1]
        assert args["trade_id"] == "test_123"
        assert args["direction"] == "long"
    
    def test_on_trade_close(self):
        from src.gateway.executor import ZMQTradeExecutor
        from src.utils.app_logger import ConsoleLogger
        
        mock_gateway = MagicMock()
        logger = ConsoleLogger()
        executor = ZMQTradeExecutor(mock_gateway, logger)
        
        executor.on_trade_close("test_123", 21050)
        
        mock_gateway.send_close_order.assert_called_once_with(
            trade_id="test_123",
            reason="strategy",
        )
    
    def test_on_sl_update(self):
        from src.gateway.executor import ZMQTradeExecutor
        from src.utils.app_logger import ConsoleLogger
        
        mock_gateway = MagicMock()
        logger = ConsoleLogger()
        executor = ZMQTradeExecutor(mock_gateway, logger)
        
        executor.on_sl_update("test_123", 21000)
        
        mock_gateway.send_modify_order.assert_called_once_with(
            trade_id="test_123",
            stop_loss=21000,
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
