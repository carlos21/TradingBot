"""
Expanded tests for TradingGateway to increase coverage.

Focuses on untested methods: connect/disconnect, message routing,
heartbeat handling, error handling, callback registration, command
sending, and query operations.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

zmq = pytest.importorskip("zmq")

from src.infrastructure.gateway.gateway import GatewayConfig, TradingGateway
from src.infrastructure.gateway.protocol import (
    CommandAckMessage,
    ConnectMessage,
    HeartbeatMessage,
    MessageEnvelope,
    MessageType,
)


class FakeLogger:
    """Captures log messages for assertion."""

    def __init__(self):
        self.messages = []
        self.levels = []

    def debug(self, msg: str) -> None:
        self.messages.append(msg)
        self.levels.append("debug")

    def info(self, msg: str) -> None:
        self.messages.append(msg)
        self.levels.append("info")

    def warning(self, msg: str) -> None:
        self.messages.append(msg)
        self.levels.append("warning")

    def error(self, msg: str) -> None:
        self.messages.append(msg)
        self.levels.append("error")

    def critical(self, msg: str) -> None:
        self.messages.append(msg)
        self.levels.append("critical")

    def close(self) -> None:
        pass


class TestGatewayConfig:
    """Tests for GatewayConfig."""

    def test_get_platform_addresses_when_platform_connects(self):
        config = GatewayConfig(
            market_data_pub="tcp://*:5555",
            command_pull="tcp://*:5556",
            query_rep="tcp://*:5557",
            heartbeat_pub="tcp://*:5558",
            platform_connects=True,
        )
        addrs = config.get_platform_addresses()
        assert addrs["market_data"] == "tcp://127.0.0.1:5555"
        assert addrs["commands"] == "tcp://127.0.0.1:5556"
        assert addrs["queries"] == "tcp://127.0.0.1:5557"
        assert addrs["heartbeat"] == "tcp://127.0.0.1:5558"

    def test_get_platform_addresses_when_python_connects(self):
        config = GatewayConfig(
            market_data_pub="tcp://127.0.0.1:5555",
            command_pull="tcp://127.0.0.1:5556",
            query_rep="tcp://127.0.0.1:5557",
            heartbeat_pub="tcp://127.0.0.1:5558",
            platform_connects=False,
        )
        addrs = config.get_platform_addresses()
        assert addrs["market_data"] == "tcp://127.0.0.1:5555"
        assert addrs["commands"] == "tcp://127.0.0.1:5556"


class TestGatewayLifecycle:
    """Tests for gateway start/stop and lifecycle methods."""

    def test_start_when_already_running_warns(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw.start()
        assert any("already running" in m for m in logger.messages)

    def test_stop_when_not_running_is_noop(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw.stop()
        assert not gw._running

    def test_stop_closes_sockets_and_clears_state(self):
        import random
        base_port = random.randint(30000, 40000)
        config = GatewayConfig(
            market_data_pub=f"tcp://127.0.0.1:{base_port}",
            command_pull=f"tcp://127.0.0.1:{base_port + 1}",
            query_rep=f"tcp://127.0.0.1:{base_port + 2}",
            heartbeat_pub=f"tcp://127.0.0.1:{base_port + 3}",
        )
        logger = FakeLogger()
        gw = TradingGateway(logger, config=config)
        gw.start()
        time.sleep(0.15)

        # Seed some state
        gw._pending_commands[1] = {"type": "test", "sent_time": time.time()}
        gw._command_retries[1] = 1
        gw._command_queue.append(MagicMock())
        gw._outbound_queue.append(MagicMock())
        gw._seq_num = 5
        gw._platform_connected = True
        gw._platform_info = {"foo": "bar"}

        gw.stop()
        time.sleep(0.15)

        assert not gw._running
        assert gw._context is None
        assert len(gw._threads) == 0
        assert len(gw._pending_commands) == 0
        assert len(gw._command_retries) == 0
        assert len(gw._command_queue) == 0
        assert len(gw._outbound_queue) == 0
        assert gw._seq_num == 0
        assert not gw._platform_connected
        assert gw._platform_info is None

    def test_setup_python_connects(self):
        import random
        base_port = random.randint(30000, 40000)
        config = GatewayConfig(
            market_data_pub=f"tcp://127.0.0.1:{base_port}",
            command_pull=f"tcp://127.0.0.1:{base_port + 1}",
            query_rep=f"tcp://127.0.0.1:{base_port + 2}",
            heartbeat_pub=f"tcp://127.0.0.1:{base_port + 3}",
            platform_connects=False,
        )
        logger = FakeLogger()
        gw = TradingGateway(logger, config=config)
        gw.start()
        time.sleep(0.1)

        assert gw._query_req is not None
        assert gw._query_rep is None

        gw.stop()


class TestMessageHandling:
    """Tests for _handle_message and related dispatch methods."""

    def test_handle_message_updates_seq_num(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        envelope = MessageEnvelope.create(
            msg_type=MessageType.TICK,
            payload={"price": 100.0},
            seq_num=42,
        )
        gw._handle_message(envelope.to_json())
        assert gw._seq_num == 42

    def test_handle_message_invalid_json(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._handle_message("not json")
        assert any("Invalid JSON" in m for m in logger.messages)

    def test_handle_message_callback_error_caught(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)

        def bad_callback(_payload):
            raise RuntimeError("boom")

        gw.on_tick(bad_callback)
        envelope = MessageEnvelope.create(
            msg_type=MessageType.TICK,
            payload={"price": 100.0},
            seq_num=1,
        )
        gw._handle_message(envelope.to_json())
        assert any("Callback error" in m for m in logger.messages)

    def test_handle_message_connect(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        envelope = ConnectMessage(
            platform="ninjatrader",
            version="1.0",
            account="Sim101",
            pair="MNQ",
        ).to_envelope(seq_num=1)
        gw._handle_message(envelope.to_json())
        assert gw._platform_connected
        assert gw._platform_info is not None
        assert gw._platform_info["platform"] == "ninjatrader"

    def test_handle_message_entry_fill(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        envelope = MessageEnvelope.create(
            msg_type=MessageType.ENTRY_FILL,
            payload={"trade_id": "T1", "entry_price": 100.0},
            seq_num=1,
        )
        gw._handle_message(envelope.to_json())
        assert any("Entry fill" in m for m in logger.messages)

    def test_handle_message_exit_fill(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        envelope = MessageEnvelope.create(
            msg_type=MessageType.EXIT_FILL,
            payload={"trade_id": "T1", "exit_price": 110.0, "result_type": "TP"},
            seq_num=1,
        )
        gw._handle_message(envelope.to_json())
        assert any("Exit fill" in m for m in logger.messages)

    def test_handle_message_error(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        envelope = MessageEnvelope.create(
            msg_type=MessageType.ERROR,
            payload={
                "source": "nt",
                "error_type": "order_failed",
                "message": "Something broke",
                "details": "a | b | c | d | e | f",
            },
            seq_num=1,
        )
        gw._handle_message(envelope.to_json())
        assert any("PLATFORM ERROR" in m for m in logger.messages)
        # Details limited to 5 lines

    def test_handle_message_test_ping(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw._command_queue = MagicMock()
        envelope = MessageEnvelope.create(
            msg_type=MessageType.TEST_PING,
            payload={"timestamp": 12345.0},
            seq_num=1,
        )
        gw._handle_message(envelope.to_json())
        assert any("TEST PING RECEIVED" in m for m in logger.messages)

    def test_handle_message_position_sync(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        envelope = MessageEnvelope.create(
            msg_type=MessageType.POSITION_SYNC,
            payload={
                "positions": [
                    {"trade_id": "T1", "direction": "long", "entry_price": 100.0},
                ],
                "count": 1,
                "source": "ninjatrader",
                "untracked_orders": [
                    {"order_name": " orphan"},
                ],
            },
            seq_num=1,
        )
        gw._handle_message(envelope.to_json())
        assert any("POSITION SYNC" in m for m in logger.messages)
        assert any("untracked" in m for m in logger.messages)

    def test_handle_message_command_ack_success(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._pending_commands[5] = {
            "type": "order_open",
            "sent_time": time.time(),
            "payload": {},
        }
        ack = CommandAckMessage(
            command_type="order_open",
            seq_num=5,
            success=True,
            trade_id="T1",
        )
        gw._handle_message(ack.to_envelope(seq_num_out=10).to_json())
        assert 5 not in gw._pending_commands
        assert any("Command ACK" in m for m in logger.messages)

    def test_handle_message_command_ack_failure(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._pending_commands[6] = {
            "type": "order_close",
            "sent_time": time.time(),
            "payload": {},
        }
        ack = CommandAckMessage(
            command_type="order_close",
            seq_num=6,
            success=False,
            trade_id="T1",
            message="rejected",
        )
        gw._handle_message(ack.to_envelope(seq_num_out=11).to_json())
        assert 6 not in gw._pending_commands
        assert any("Command FAILED" in m for m in logger.messages)

    def test_handle_message_command_ack_unknown(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        ack = CommandAckMessage(
            command_type="order_open",
            seq_num=999,
            success=True,
            trade_id="T1",
        )
        gw._handle_message(ack.to_envelope(seq_num_out=12).to_json())
        assert any("unknown" in m.lower() for m in logger.messages)

    def test_handle_message_command_ack_parse_error(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        envelope = MessageEnvelope.create(
            msg_type=MessageType.COMMAND_ACK,
            payload="bad",
            seq_num=1,
        )
        # Should not raise
        gw._handle_message(envelope.to_json())
        assert any("Error handling command ack" in m for m in logger.messages)

    def test_handle_message_logs_important_messages(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        envelope = MessageEnvelope.create(
            msg_type=MessageType.CONNECT,
            payload={"platform": "nt", "version": "1"},
            seq_num=1,
        )
        gw._handle_message(envelope.to_json())
        assert any("RECV" in m for m in logger.messages)

    def test_handle_message_debug_for_other_types(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        envelope = MessageEnvelope.create(
            msg_type=MessageType.TEST_START,
            payload={"scenario": "tp_hit"},
            seq_num=1,
        )
        gw._handle_message(envelope.to_json())
        # debug messages don't appear in FakeLogger unless we track them
        # test mainly ensures no exception

    def test_handle_heartbeat_sets_connected(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        envelope = HeartbeatMessage(source="nt", status="ok").to_envelope(seq_num=1)
        gw._handle_heartbeat(envelope.to_json())
        assert gw._platform_connected
        assert gw._last_heartbeat_time is not None

    def test_handle_heartbeat_bad_json(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._handle_heartbeat("not json")
        # Should not raise; debug message logged

    def test_handle_heartbeat_non_heartbeat_type(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        envelope = MessageEnvelope.create(
            msg_type=MessageType.TICK,
            payload={},
            seq_num=1,
        )
        gw._handle_heartbeat(envelope.to_json())
        assert not gw._platform_connected

    def test_heartbeat_loop_timeout_disconnects(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw._last_heartbeat_time = time.time() - 100
        gw._platform_connected = True

        calls = []

        def listener(connected):
            calls.append(connected)

        gw.on_connection_change(listener)

        # Run one iteration manually
        def _sleep_and_stop(_duration):
            gw._running = False

        with patch('time.sleep', side_effect=_sleep_and_stop):
            gw._heartbeat_loop()
        assert not gw._platform_connected
        assert False in calls

    def test_heartbeat_loop_listener_exception_ignored(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw._last_heartbeat_time = time.time() - 100
        gw._platform_connected = True

        def bad_listener(_connected):
            raise RuntimeError("boom")

        gw.on_connection_change(bad_listener)
        # Should not raise
        def _sleep_and_stop(_duration):
            gw._running = False

        with patch('time.sleep', side_effect=_sleep_and_stop):
            gw._heartbeat_loop()

    def test_heartbeat_loop_no_heartbeat_yet(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw._last_heartbeat_time = None
        gw._platform_connected = False
        # Should not crash or change state
        def _sleep_and_stop(_duration):
            gw._running = False

        with patch('time.sleep', side_effect=_sleep_and_stop):
            gw._heartbeat_loop()

    def test_heartbeat_loop_exception_recovery(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        # Force an exception by making config None where accessed
        original_config = gw.config
        gw.config = None
        # Should not raise; error logged
        def _sleep_and_stop(_duration):
            gw._running = False

        with patch('time.sleep', side_effect=_sleep_and_stop):
            gw._heartbeat_loop()
        gw.config = original_config
        assert any("Error in heartbeat loop" in m for m in logger.messages)

    def test_handle_connect_listener_exception_ignored(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)

        def bad_listener(_connected):
            raise RuntimeError("boom")

        gw.on_connection_change(bad_listener)
        envelope = ConnectMessage(platform="nt", version="1").to_envelope(seq_num=1)
        gw._handle_message(envelope.to_json())
        assert gw._platform_connected


class TestCallbackRegistration:
    """Tests for all on_* callback registration methods."""

    def test_on_tick(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_tick(cb)
        assert cb in gw._callbacks[MessageType.TICK]

    def test_on_bar(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_bar(cb)
        assert cb in gw._callbacks[MessageType.BAR]

    def test_on_partial_bar(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_partial_bar(cb)
        assert cb in gw._callbacks[MessageType.PARTIAL_BAR]

    def test_on_history_batch(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_history_batch(cb)
        assert cb in gw._callbacks[MessageType.HISTORY_BATCH]

    def test_on_history_end(self):
        gw = TradingGateway(FakeLogger())
        received = []
        gw.on_history_end(lambda: received.append(1))
        callbacks = gw._callbacks[MessageType.HISTORY_END]
        assert len(callbacks) == 1
        callbacks[0]({})
        assert received == [1]

    def test_on_entry_fill(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_entry_fill(cb)
        assert cb in gw._callbacks[MessageType.ENTRY_FILL]

    def test_on_exit_fill(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_exit_fill(cb)
        assert cb in gw._callbacks[MessageType.EXIT_FILL]

    def test_on_trade_log(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_trade_log(cb)
        assert cb in gw._callbacks[MessageType.TRADE_LOG]

    def test_on_error(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_error(cb)
        assert cb in gw._callbacks[MessageType.ERROR]

    def test_on_test_ping(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_test_ping(cb)
        assert cb in gw._callbacks[MessageType.TEST_PING]

    def test_on_test_pong(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_test_pong(cb)
        assert cb in gw._callbacks[MessageType.TEST_PONG]

    def test_on_position_query(self):
        gw = TradingGateway(FakeLogger())
        def handler():
            return []
        gw.on_position_query(handler)
        assert gw._position_query_handler is handler

    def test_on_position_sync(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_position_sync(cb)
        assert cb in gw._callbacks[MessageType.POSITION_SYNC]

    def test_on_connection_change(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_connection_change(cb)
        assert cb in gw._connection_listeners

    def test_on_test_start(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_test_start(cb)
        assert cb in gw._callbacks[MessageType.TEST_START]

    def test_on_test_result(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on_test_result(cb)
        assert cb in gw._callbacks[MessageType.TEST_RESULT]

    def test_on_unknown_message_type(self):
        gw = TradingGateway(FakeLogger())
        def cb(x):
            return None
        gw.on(MessageType.DISCONNECT, cb)
        assert cb in gw._callbacks[MessageType.DISCONNECT]


class TestCommandSending:
    """Tests for command sending methods."""

    def test_send_command_when_not_running(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = False
        envelope = MessageEnvelope.create(
            msg_type=MessageType.ORDER_OPEN,
            payload={},
            seq_num=1,
        )
        gw._send_command(envelope)
        assert any("not running" in m.lower() for m in logger.messages)

    def test_send_command_tracks_pending(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        envelope = MessageEnvelope.create(
            msg_type=MessageType.ORDER_OPEN,
            payload={"trade_id": "T1"},
            seq_num=1,
        )
        gw._send_command(envelope)
        assert 1 in gw._pending_commands
        assert gw._pending_commands[1]["type"] == "order_open"

    def test_send_command_queue_near_capacity_warning(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw._command_queue = MagicMock()
        gw._command_queue.maxlen = 10
        gw._command_queue.__len__ = MagicMock(return_value=9)
        envelope = MessageEnvelope.create(
            msg_type=MessageType.ORDER_OPEN,
            payload={},
            seq_num=1,
        )
        gw._send_command(envelope)
        assert any("NEAR CAPACITY" in m for m in logger.messages)

    def test_cleanup_pending_commands_removes_old(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._pending_commands[1] = {
            "type": "order_open",
            "sent_time": time.time() - 120,
        }
        gw._cleanup_pending_commands()
        assert 1 not in gw._pending_commands
        assert any("timed out" in m.lower() for m in logger.messages)

    def test_cleanup_pending_commands_keeps_recent(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._pending_commands[1] = {
            "type": "order_open",
            "sent_time": time.time(),
        }
        gw._cleanup_pending_commands()
        assert 1 in gw._pending_commands

    def test_send_open_order(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw.send_open_order(
            trade_id="T1",
            direction="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=110.0,
            risk_points=10.0,
            rr_ratio=1.0,
        )
        assert len(gw._pending_commands) == 1
        assert any("OPEN order" in m for m in logger.messages)

    def test_send_open_order_with_account(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw.send_open_order(
            trade_id="T1",
            direction="short",
            entry_price=100.0,
            stop_loss=110.0,
            take_profit=90.0,
            risk_points=10.0,
            rr_ratio=1.0,
            pair="ES",
            risk_usd=500.0,
            risk_pct=0.01,
            account="Sim101",
        )
        assert len(gw._pending_commands) == 1

    def test_send_close_order(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw.send_close_order("T1", reason="manual", account="Sim101")
        assert len(gw._pending_commands) == 1
        assert any("CLOSE order" in m for m in logger.messages)

    def test_send_modify_order(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw.send_modify_order("T1", stop_loss=95.0, take_profit=115.0, account="Sim101")
        assert len(gw._pending_commands) == 1
        assert any("MODIFY order" in m for m in logger.messages)

    def test_send_refresh_request(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw.send_refresh_request(days=5)
        assert len(gw._pending_commands) == 1
        assert any("REFRESH" in m for m in logger.messages)

    def test_send_error(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw.send_error("test", "type", "msg", details="extra")
        assert len(gw._pending_commands) == 1
        assert any("Sent error" in m for m in logger.messages)

    def test_send_error_no_details(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw.send_error("test", "type", "msg")
        assert len(gw._pending_commands) == 1

    def test_send_test_pong(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw.send_test_pong(timestamp=12345.0)
        assert len(gw._pending_commands) == 1

    def test_send_test_result(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw.send_test_result("tp_hit", True, trade_id="T1", message="all good")
        assert len(gw._pending_commands) == 1
        assert any("TEST_RESULT" in m for m in logger.messages)

    def test_send_test_result_failed(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw.send_test_result("sl_hit", False)
        assert any("FAILED" in m for m in logger.messages)


class TestQueryPositions:
    """Tests for query_positions."""

    def test_query_positions_no_socket_returns_none(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        assert gw.query_positions() is None


class TestProperties:
    """Tests for gateway properties."""

    def test_is_running(self):
        gw = TradingGateway(FakeLogger())
        assert not gw.is_running
        gw._running = True
        assert gw.is_running

    def test_is_connected(self):
        gw = TradingGateway(FakeLogger())
        assert not gw.is_connected
        gw._running = True
        gw._platform_connected = True
        assert gw.is_connected

    def test_platform_info(self):
        gw = TradingGateway(FakeLogger())
        assert gw.platform_info is None
        gw._platform_info = {"version": "1"}
        assert gw.platform_info == {"version": "1"}


class TestFormatPayloadPreview:
    """Tests for _format_payload_preview."""

    def test_empty_payload(self):
        gw = TradingGateway(FakeLogger())
        assert gw._format_payload_preview({}) == "{}"

    def test_known_fields(self):
        gw = TradingGateway(FakeLogger())
        result = gw._format_payload_preview({
            "pair": "MNQ",
            "price": 100.0,
            "trade_id": "T1",
            "direction": "long",
        })
        assert "pair=MNQ" in result
        assert "price=100.00" in result

    def test_bars_field(self):
        gw = TradingGateway(FakeLogger())
        result = gw._format_payload_preview({"bars": [{}, {}, {}]})
        assert "bars=3" in result

    def test_fallback_to_first_key(self):
        gw = TradingGateway(FakeLogger())
        result = gw._format_payload_preview({"custom_key": "custom_value"})
        assert "custom_key=custom_value" in result

    def test_limits_parts(self):
        gw = TradingGateway(FakeLogger())
        result = gw._format_payload_preview({
            "pair": "MNQ",
            "price": 100.0,
            "time": 123,
            "trade_id": "T1",
            "direction": "long",
        })
        parts = result.split(" | ")
        assert len(parts) <= 4


class TestRefreshAccountNames:
    """Tests for _refresh_account_names."""

    def test_refresh_account_names_success(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        with patch("src.infrastructure.repositories.accounts_repository.NtAccountRepository") as MockRepo:
            mock_repo = MagicMock()
            a1 = MagicMock()
            a1.name = "Sim101"
            a2 = MagicMock()
            a2.name = "Sim102"
            mock_repo.list_accounts.return_value = [a1, a2]
            MockRepo.return_value = mock_repo
            gw._refresh_account_names()
            assert gw._account_names == ["Sim101", "Sim102"]

    def test_refresh_account_names_failure(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        with patch("src.infrastructure.repositories.accounts_repository.NtAccountRepository") as MockRepo:
            MockRepo.side_effect = Exception("DB error")
            gw._refresh_account_names()
            assert any("Failed to refresh" in m for m in logger.messages)


class TestHandleTestStartSingleAccount:
    """Tests for single-account test start."""

    def test_handle_test_start_default_scenario(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw._command_queue = MagicMock()
        gw._command_queue.maxlen = 10000
        gw._command_queue.__len__ = MagicMock(return_value=0)
        gw._handle_test_start({
            "scenario": "tp_hit",
            "entry_price": 100.0,
            "risk_points": 10.0,
            "rr_ratio": 2.0,
        })
        assert len(gw._test_sequences) >= 1
        assert any("E2E TEST START" in m for m in logger.messages)

    def test_handle_test_start_non_multi_account(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw._command_queue = MagicMock()
        gw._command_queue.maxlen = 10000
        gw._command_queue.__len__ = MagicMock(return_value=0)
        gw._handle_test_start({
            "scenario": "sl_hit",
            "entry_price": 200.0,
            "risk_points": 20.0,
            "rr_ratio": 1.5,
        })
        # Find the trade sequence (skip group key if present)
        seq_keys = [k for k in gw._test_sequences if not k.startswith("__")]
        assert len(seq_keys) == 1
        seq = gw._test_sequences[seq_keys[0]]
        assert seq["scenario"] == "sl_hit"


class TestCommandSenderLoop:
    """Tests for _command_sender_loop."""

    def _run_loop_briefly(self, gw):
        """Run a loop in a thread and stop it after a short delay."""
        t = threading.Thread(target=gw._command_sender_loop)
        t.start()
        time.sleep(0.03)
        gw._running = False
        t.join(timeout=1.0)

    def test_sender_loop_no_commands_sleeps(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw._command_queue.clear()
        self._run_loop_briefly(gw)

    def test_sender_loop_socket_none_drops_command(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw._command_push = None
        envelope = MessageEnvelope.create(
            msg_type=MessageType.ORDER_OPEN,
            payload={"trade_id": "T1"},
            seq_num=1,
        )
        gw._command_queue.append(envelope)
        gw._pending_commands[1] = {"type": "order_open", "sent_time": time.time()}
        # Run until queue is empty
        def _sleep_and_stop(_duration):
            gw._running = False

        with patch('time.sleep', side_effect=_sleep_and_stop):
            gw._command_sender_loop()
        assert 1 not in gw._pending_commands
        assert any("socket not available" in m.lower() for m in logger.messages)

    def test_sender_loop_send_exception_retries(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_socket = MagicMock()
        mock_socket.send_string.side_effect = Exception("send failed")
        gw._command_push = mock_socket
        envelope = MessageEnvelope.create(
            msg_type=MessageType.ORDER_OPEN,
            payload={"trade_id": "T1"},
            seq_num=1,
        )
        gw._command_queue.append(envelope)
        gw._pending_commands[1] = {"type": "order_open", "sent_time": time.time()}
        gw._command_retries[1] = 2  # One more retry allowed

        def _sleep_and_stop(_duration):
            gw._running = False

        with patch('time.sleep', side_effect=_sleep_and_stop):
            gw._command_sender_loop()
        assert any("retry" in m.lower() for m in logger.messages)

    def test_sender_loop_send_exception_exhausted_retries(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_socket = MagicMock()
        mock_socket.send_string.side_effect = Exception("send failed")
        gw._command_push = mock_socket
        envelope = MessageEnvelope.create(
            msg_type=MessageType.ORDER_OPEN,
            payload={"trade_id": "T1"},
            seq_num=1,
        )
        gw._command_queue.append(envelope)
        gw._pending_commands[1] = {"type": "order_open", "sent_time": time.time()}
        gw._command_retries[1] = 3  # Already at max

        def _sleep_and_stop(_duration):
            gw._running = False

        with patch('time.sleep', side_effect=_sleep_and_stop):
            gw._command_sender_loop()
        assert 1 not in gw._command_retries
        assert 1 not in gw._pending_commands
        assert any("permanently" in m.lower() for m in logger.messages)

    def test_sender_loop_general_exception(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        # Make command_queue something that raises on popleft
        gw._command_queue = MagicMock()

        def _popleft_and_stop():
            gw._running = False
            raise Exception("unexpected")

        gw._command_queue.popleft = _popleft_and_stop
        gw._command_sender_loop()
        assert any("Error sending command" in m for m in logger.messages)


class TestQueryHandlerLoop:
    """Tests for _query_handler_loop."""

    def _run_loop_briefly(self, gw):
        t = threading.Thread(target=gw._query_handler_loop)
        t.start()
        time.sleep(0.03)
        gw._running = False
        t.join(timeout=1.0)

    def test_query_handler_no_socket(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        gw._query_rep = None
        self._run_loop_briefly(gw)

    def test_query_handler_test_ping(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_socket = MagicMock()
        ping = MessageEnvelope.create(
            msg_type=MessageType.TEST_PING,
            payload={"timestamp": 1.0},
            seq_num=1,
        )
        def _recv_and_stop(*args, **kwargs):
            gw._running = False
            return ping.to_json()

        mock_socket.recv_string = _recv_and_stop
        gw._query_rep = mock_socket
        gw._query_handler_loop()
        assert mock_socket.send_string.called
        sent_json = mock_socket.send_string.call_args[0][0]
        sent = MessageEnvelope.from_json(sent_json)
        assert sent.msg_type == MessageType.TEST_PONG

    def test_query_handler_position_query(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_socket = MagicMock()
        query = MessageEnvelope.create(
            msg_type=MessageType.POSITION_QUERY,
            payload={},
            seq_num=1,
        )
        def _recv_and_stop(*args, **kwargs):
            gw._running = False
            return query.to_json()

        mock_socket.recv_string = _recv_and_stop
        gw._query_rep = mock_socket
        gw._position_query_handler = lambda: [{"trade_id": "T1"}]
        gw._query_handler_loop()
        assert mock_socket.send_string.called
        sent_json = mock_socket.send_string.call_args[0][0]
        sent = MessageEnvelope.from_json(sent_json)
        assert sent.msg_type == MessageType.POSITION_RESPONSE
        assert sent.payload.get("count") == 1

    def test_query_handler_position_query_handler_error(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_socket = MagicMock()
        query = MessageEnvelope.create(
            msg_type=MessageType.POSITION_QUERY,
            payload={},
            seq_num=1,
        )
        def _recv_and_stop(*args, **kwargs):
            gw._running = False
            return query.to_json()

        mock_socket.recv_string = _recv_and_stop
        gw._query_rep = mock_socket
        gw._position_query_handler = lambda: (_ for _ in ()).throw(Exception("bad"))
        gw._query_handler_loop()
        assert any("Error in position query handler" in m for m in logger.messages)
        assert mock_socket.send_string.called

    def test_query_handler_config_query_accounts(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_socket = MagicMock()
        query = MessageEnvelope.create(
            msg_type=MessageType.CONFIG_QUERY,
            payload={"key": "accounts"},
            seq_num=1,
        )
        def _recv_and_stop(*args, **kwargs):
            gw._running = False
            return query.to_json()

        mock_socket.recv_string = _recv_and_stop
        gw._query_rep = mock_socket
        with patch.object(gw, "_refresh_account_names"):
            gw._account_names = ["Sim101"]
            gw._query_handler_loop()
        assert mock_socket.send_string.called
        sent_json = mock_socket.send_string.call_args[0][0]
        sent = MessageEnvelope.from_json(sent_json)
        assert sent.msg_type == MessageType.CONFIG_RESPONSE
        assert sent.payload["accounts"] == "Sim101"

    def test_query_handler_config_query_all(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_socket = MagicMock()
        query = MessageEnvelope.create(
            msg_type=MessageType.CONFIG_QUERY,
            payload={"key": "all"},
            seq_num=1,
        )
        def _recv_and_stop(*args, **kwargs):
            gw._running = False
            return query.to_json()

        mock_socket.recv_string = _recv_and_stop
        gw._query_rep = mock_socket
        with patch.object(gw, "_refresh_account_names"):
            gw._account_names = ["Sim101"]
            gw._query_handler_loop()
        sent_json = mock_socket.send_string.call_args[0][0]
        sent = MessageEnvelope.from_json(sent_json)
        assert sent.payload["accounts"] == "Sim101"

    def test_query_handler_config_query_other_key(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_socket = MagicMock()
        query = MessageEnvelope.create(
            msg_type=MessageType.CONFIG_QUERY,
            payload={"key": "some_key"},
            seq_num=1,
        )
        def _recv_and_stop(*args, **kwargs):
            gw._running = False
            return query.to_json()

        mock_socket.recv_string = _recv_and_stop
        gw._query_rep = mock_socket
        gw._query_handler_loop()
        sent_json = mock_socket.send_string.call_args[0][0]
        sent = MessageEnvelope.from_json(sent_json)
        assert sent.payload.get("some_key") is None

    def test_query_handler_unknown_type(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_socket = MagicMock()
        query = MessageEnvelope.create(
            msg_type=MessageType.DISCONNECT,
            payload={},
            seq_num=1,
        )
        def _recv_and_stop(*args, **kwargs):
            gw._running = False
            return query.to_json()

        mock_socket.recv_string = _recv_and_stop
        gw._query_rep = mock_socket
        gw._query_handler_loop()
        assert any("Unknown query type" in m for m in logger.messages)
        assert mock_socket.send_string.called

    def test_query_handler_zmq_again(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_socket = MagicMock()
        import zmq

        def _recv_and_stop(*args, **kwargs):
            gw._running = False
            raise zmq.Again()

        mock_socket.recv_string = _recv_and_stop
        gw._query_rep = mock_socket
        gw._query_handler_loop()
        # No send_string called because we continue on Again
        assert not mock_socket.send_string.called

    def test_query_handler_general_exception(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_socket = MagicMock()
        def _recv_and_stop(*args, **kwargs):
            gw._running = False
            raise Exception("boom")

        mock_socket.recv_string = _recv_and_stop
        gw._query_rep = mock_socket
        gw._query_handler_loop()
        assert any("Error in query handler loop" in m for m in logger.messages)


class TestMarketDataLoop:
    """Tests for _market_data_loop."""

    def _run_loop_briefly(self, gw):
        t = threading.Thread(target=gw._market_data_loop)
        t.start()
        time.sleep(0.03)
        gw._running = False
        t.join(timeout=1.0)

    def test_market_data_loop_zmq_eterm(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_sub = MagicMock()
        import zmq
        mock_sub.recv_string.side_effect = zmq.ZMQError(zmq.ETERM)
        gw._market_sub = mock_sub
        gw._heartbeat_sub = MagicMock()
        self._run_loop_briefly(gw)
        # Loop should exit without error on ETERM

    def test_market_data_loop_general_exception(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        gw._running = True
        mock_sub = MagicMock()
        mock_sub.recv_string.side_effect = Exception("boom")
        gw._market_sub = mock_sub
        gw._heartbeat_sub = MagicMock()
        import zmq
        with patch.object(zmq.Poller, 'poll', return_value=[(mock_sub, zmq.POLLIN)]):
            self._run_loop_briefly(gw)
        assert any("Error in market data loop" in m for m in logger.messages)


class TestNextSeq:
    """Additional tests for _next_seq thread safety."""

    def test_next_seq_thread_safety(self):
        logger = FakeLogger()
        gw = TradingGateway(logger)
        results = []
        lock = threading.Lock()

        def worker():
            for _ in range(100):
                seq = gw._next_seq()
                with lock:
                    results.append(seq)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(results)) == len(results)
        assert max(results) == 500


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
