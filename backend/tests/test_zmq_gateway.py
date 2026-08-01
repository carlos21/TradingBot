"""
Tests for ZeroMQ Gateway.

Run with: pytest tests/test_zmq_gateway.py -v
"""

import json
import time
from unittest.mock import MagicMock

import pytest

# Skip if zmq not installed
zmq = pytest.importorskip("zmq")

from src.infrastructure.gateway.gateway import (  # noqa: E402
    GatewayConfig,
    TradingGateway,
)
from src.infrastructure.gateway.protocol import (  # noqa: E402
    BarMessage,
    MessageEnvelope,
    MessageType,
    OpenOrderCommand,
    TickMessage,
)
from src.utils.app_logger import ConsoleLogger  # noqa: E402


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
            instrument="MNQ 09-26",
        )
        envelope = cmd.to_envelope(seq_num=1)

        assert envelope.msg_type == MessageType.ORDER_OPEN
        assert envelope.payload["trade_id"] == "test_123"
        assert envelope.payload["direction"] == "long"
        assert envelope.payload["instrument"] == "MNQ 09-26"


class TestGateway:
    """Test TradingGateway functionality."""

    def test_gateway_creation(self):
        config = GatewayConfig(
            market_data_pub="tcp://127.0.0.1:5555",
            command_pull="tcp://127.0.0.1:5556",
        )
        logger = ConsoleLogger()
        gateway = TradingGateway(logger, config=config)

        assert gateway.config == config
        assert not gateway.is_connected

    def test_callback_registration(self):
        logger = ConsoleLogger()
        gateway = TradingGateway(logger)

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
        gateway = TradingGateway(logger)

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
        from tests.fake_ninjatrader.port_helper import get_free_ports
        ports = get_free_ports(4)

        config = GatewayConfig(
            market_data_pub=f"tcp://127.0.0.1:{ports[0]}",
            command_pull=f"tcp://127.0.0.1:{ports[1]}",
            query_rep=f"tcp://127.0.0.1:{ports[2]}",
            heartbeat_pub=f"tcp://127.0.0.1:{ports[3]}",
        )
        logger = ConsoleLogger()
        gateway = TradingGateway(logger, config=config)
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
            pair="TEST",
            instrument="MNQ 09-26",
        )

        # Check it was queued (check pending_commands since sender thread may pop queue)
        assert len(gateway._pending_commands) >= 1

        gateway.stop()


class TestDataSource:
    """Test ZMQDataSource."""

    def test_datasource_creation(self):
        from src.infrastructure.gateway.datasource import ZMQDataSource
        from src.utils.app_logger import ConsoleLogger  # noqa: E402

        logger = ConsoleLogger()
        ds = ZMQDataSource(logger)

        assert not ds.is_streaming
        assert ds._bars_by_pair == {}

    def test_bar_aggregation(self):
        from src.infrastructure.gateway.datasource import ZMQDataSource
        from src.utils.app_logger import ConsoleLogger  # noqa: E402

        logger = ConsoleLogger()
        ds = ZMQDataSource(logger)

        # Add some bars
        bars = [
            {"time": 1000, "open": 100, "high": 110, "low": 90, "close": 105, "volume": 100, "pair": "MNQ"},
            {"time": 1060, "open": 105, "high": 115, "low": 100, "close": 110, "volume": 200, "pair": "MNQ"},
        ]
        ds._bars_by_pair["MNQ"] = bars

        # Test 1m aggregation (returns copy)
        result = ds.load_historical_bars("1m", pair="MNQ")
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
        from src.infrastructure.gateway.executor import ZMQTradeExecutor
        from src.utils.app_logger import ConsoleLogger  # noqa: E402

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
        from src.infrastructure.gateway.executor import ZMQTradeExecutor
        from src.utils.app_logger import ConsoleLogger  # noqa: E402

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
        from src.infrastructure.gateway.executor import ZMQTradeExecutor
        from src.utils.app_logger import ConsoleLogger  # noqa: E402

        mock_gateway = MagicMock()
        logger = ConsoleLogger()
        executor = ZMQTradeExecutor(mock_gateway, logger)
        # Instrument and account are resolved from the trade itself, never
        # from a gateway-level default.
        executor.trade_resolver = lambda trade_id: {
            "trade_id": trade_id,
            "instrument": "MNQ 09-26",
            "account": "Sim101",
        }

        executor.on_trade_close("test_123", 21050)

        mock_gateway.send_close_order.assert_called_once_with(
            trade_id="test_123",
            reason="strategy",
            account="Sim101",
            instrument="MNQ 09-26",
        )

    def test_on_sl_update(self):
        from src.infrastructure.gateway.executor import ZMQTradeExecutor
        from src.utils.app_logger import ConsoleLogger  # noqa: E402

        mock_gateway = MagicMock()
        logger = ConsoleLogger()
        executor = ZMQTradeExecutor(mock_gateway, logger)
        executor.trade_resolver = lambda trade_id: {
            "trade_id": trade_id,
            "instrument": "MNQ 09-26",
            "account": "Sim101",
        }

        executor.on_sl_update("test_123", 21000)

        mock_gateway.send_modify_order.assert_called_once_with(
            trade_id="test_123",
            stop_loss=21000,
            account="Sim101",
            instrument="MNQ 09-26",
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
