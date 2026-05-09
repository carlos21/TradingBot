"""Tests for src/gateway/protocol.py."""

import json

import pytest

from src.infrastructure.gateway.gateway import GatewayConfig
from src.infrastructure.gateway.protocol import (
    BarMessage,
    CommandAckMessage,
    EntryFillMessage,
    ExitFillMessage,
    HeartbeatMessage,
    MessageEnvelope,
    MessageType,
    OpenOrderCommand,
    TickMessage,
)


class TestMessageType:

    def test_all_types_are_strings(self):
        for mt in MessageType:
            assert isinstance(mt.value, str)

    def test_tick_type(self):
        assert MessageType.TICK.value == "tick"

    def test_bar_type(self):
        assert MessageType.BAR.value == "bar"


class TestMessageEnvelope:

    def test_to_json(self):
        env = MessageEnvelope(
            msg_type=MessageType.TICK,
            timestamp=1700000000.0,
            seq_num=1,
            payload={"price": 5000.0},
        )
        s = env.to_json()
        data = json.loads(s)
        assert data["msg_type"] == "tick"
        assert data["timestamp"] == 1700000000.0
        assert data["seq_num"] == 1
        assert data["payload"]["price"] == 5000.0

    def test_from_json(self):
        s = json.dumps({
            "msg_type": "bar",
            "timestamp": 1700000000.0,
            "seq_num": 5,
            "payload": {"open": 100.0},
        })
        env = MessageEnvelope.from_json(s)
        assert env.msg_type == MessageType.BAR
        assert env.timestamp == 1700000000.0
        assert env.seq_num == 5
        assert env.payload["open"] == 100.0

    def test_from_json_invalid_type(self):
        s = json.dumps({
            "msg_type": "unknown_type",
            "timestamp": 1700000000.0,
            "seq_num": 1,
            "payload": {},
        })
        with pytest.raises(ValueError):
            MessageEnvelope.from_json(s)

    def test_create(self):
        env = MessageEnvelope.create(
            msg_type=MessageType.TICK,
            payload={"price": 5000.0},
            seq_num=10,
        )
        assert env.msg_type == MessageType.TICK
        assert env.seq_num == 10
        assert env.payload == {"price": 5000.0}
        assert env.timestamp > 0


class TestTickMessage:

    def test_to_envelope(self):
        msg = TickMessage(pair="MNQ", price=5000.0, volume=100, time=1700000000)
        env = msg.to_envelope(seq_num=1)
        assert env.msg_type == MessageType.TICK
        assert env.payload["price"] == 5000.0
        assert env.payload["pair"] == "MNQ"

    def test_to_envelope_with_bid_ask(self):
        msg = TickMessage(pair="MNQ", price=5000.0, volume=100, time=1700000000, bid=4999.0, ask=5001.0)
        env = msg.to_envelope()
        assert env.payload["bid"] == 4999.0
        assert env.payload["ask"] == 5001.0


class TestBarMessage:

    def test_to_envelope_complete(self):
        msg = BarMessage(
            pair="MNQ", time=1700000000, open=100.0, high=102.0,
            low=98.0, close=101.0, volume=1000, is_partial=False,
        )
        env = msg.to_envelope()
        assert env.msg_type == MessageType.BAR
        assert env.payload["close"] == 101.0

    def test_to_envelope_partial(self):
        msg = BarMessage(
            pair="MNQ", time=1700000000, open=100.0, high=102.0,
            low=98.0, close=101.0, volume=1000, is_partial=True,
        )
        env = msg.to_envelope()
        assert env.msg_type == MessageType.PARTIAL_BAR


class TestOpenOrderCommand:

    def test_to_envelope(self):
        cmd = OpenOrderCommand(
            trade_id="T1",
            pair="MNQ",
            direction="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=130.0,
            risk_points=10.0,
            rr_ratio=3.0,
        )
        env = cmd.to_envelope()
        assert env.msg_type == MessageType.ORDER_OPEN
        assert env.payload["trade_id"] == "T1"
        assert env.payload["direction"] == "long"


class TestEntryFillMessage:

    def test_to_envelope(self):
        msg = EntryFillMessage(
            trade_id="T1",
            entry_price=100.0,
        )
        env = msg.to_envelope()
        assert env.msg_type == MessageType.ENTRY_FILL
        assert env.payload["entry_price"] == 100.0


class TestExitFillMessage:

    def test_to_envelope(self):
        msg = ExitFillMessage(
            trade_id="T1",
            exit_price=130.0,
            result_type="TP",
        )
        env = msg.to_envelope()
        assert env.msg_type == MessageType.EXIT_FILL
        assert env.payload["result_type"] == "TP"
        assert env.payload["exit_price"] == 130.0


class TestCommandAckMessage:

    def test_to_envelope(self):
        msg = CommandAckMessage(
            command_type="order_open",
            seq_num=1,
            success=True,
            trade_id="T1",
        )
        env = msg.to_envelope(seq_num_out=5)
        assert env.msg_type == MessageType.COMMAND_ACK
        assert env.payload["success"] is True

    def test_from_payload(self):
        payload = {
            "command_type": "order_close",
            "seq_num": 10,
            "success": True,
            "trade_id": "T1",
        }
        msg = CommandAckMessage.from_payload(payload)
        assert msg.command_type == "order_close"
        assert msg.seq_num == 10
        assert msg.success is True
        assert msg.trade_id == "T1"


class TestHeartbeatMessage:

    def test_to_envelope(self):
        msg = HeartbeatMessage(source="ninjatrader", status="connected")
        env = msg.to_envelope()
        assert env.msg_type == MessageType.HEARTBEAT
        assert env.payload["source"] == "ninjatrader"
        assert env.payload["status"] == "connected"


class TestGatewayConfig:

    def test_default_values(self):
        config = GatewayConfig()
        assert config.market_data_pub == "tcp://127.0.0.1:5555"
        assert config.command_pull == "tcp://127.0.0.1:5556"
        assert config.query_rep == "tcp://127.0.0.1:5557"
        assert config.heartbeat_pub == "tcp://127.0.0.1:5558"
        assert config.heartbeat_interval_sec == 5.0

    def test_custom_values(self):
        config = GatewayConfig()
        config.market_data_pub = "tcp://0.0.0.0:6000"
        assert config.market_data_pub == "tcp://0.0.0.0:6000"
