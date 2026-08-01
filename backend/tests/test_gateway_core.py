"""
Comprehensive pure-logic tests for src/gateway/gateway.py.

These tests exercise TradingGateway and GatewayConfig without creating real
ZeroMQ sockets or long-running background threads.  zmq is mocked where needed.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

zmq = pytest.importorskip("zmq")

from src.infrastructure.gateway.gateway import GatewayConfig, TradingGateway
from src.infrastructure.gateway.protocol import (
    BarMessage,
    CommandAckMessage,
    ConnectMessage,
    EntryFillMessage,
    ExitFillMessage,
    HeartbeatMessage,
    MessageEnvelope,
    MessageType,
    PositionResponseMessage,
    TickMessage,
)
from tests.fakes import FakeLogger

# ---------------------------------------------------------------------------
# Capturing logger
# ---------------------------------------------------------------------------


class CapturingLogger(FakeLogger):
    """FakeLogger that records every logged message."""

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []
        self.levels: list[str] = []

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return CapturingLogger()


@pytest.fixture
def gateway(logger):
    return TradingGateway(logger=logger, config=GatewayConfig())


# ---------------------------------------------------------------------------
# GatewayConfig
# ---------------------------------------------------------------------------


class TestGatewayConfig:
    def test_all_defaults(self):
        c = GatewayConfig()
        assert c.market_data_pub == "tcp://127.0.0.1:5555"
        assert c.command_pull == "tcp://127.0.0.1:5556"
        assert c.query_rep == "tcp://127.0.0.1:5557"
        assert c.heartbeat_pub == "tcp://127.0.0.1:5558"
        assert c.heartbeat_interval_sec == 5.0
        assert c.heartbeat_timeout_sec == 15.0
        assert c.command_timeout_ms == 5000
        assert c.max_queue_size == 10000
        assert c.platform_connects is True
        assert c.verbose is False

    def test_custom_values(self):
        c = GatewayConfig(
            market_data_pub="tcp://0.0.0.0:6000",
            command_pull="tcp://0.0.0.0:6001",
            query_rep="tcp://0.0.0.0:6002",
            heartbeat_pub="tcp://0.0.0.0:6003",
            heartbeat_interval_sec=1.0,
            heartbeat_timeout_sec=3.0,
            command_timeout_ms=1000,
            max_queue_size=500,
            platform_connects=False,
            verbose=True,
        )
        assert c.market_data_pub == "tcp://0.0.0.0:6000"
        assert c.heartbeat_timeout_sec == 3.0
        assert c.max_queue_size == 500
        assert c.platform_connects is False
        assert c.verbose is True

    def test_get_platform_addresses_when_platform_connects_replaces_wildcard(self):
        c = GatewayConfig(
            market_data_pub="tcp://*:5555",
            command_pull="tcp://*:5556",
            query_rep="tcp://*:5557",
            heartbeat_pub="tcp://*:5558",
            platform_connects=True,
        )
        addrs = c.get_platform_addresses()
        assert addrs["market_data"] == "tcp://127.0.0.1:5555"
        assert addrs["commands"] == "tcp://127.0.0.1:5556"
        assert addrs["queries"] == "tcp://127.0.0.1:5557"
        assert addrs["heartbeat"] == "tcp://127.0.0.1:5558"

    def test_get_platform_addresses_when_platform_connects_no_wildcard(self):
        c = GatewayConfig(platform_connects=True)
        addrs = c.get_platform_addresses()
        # No * to replace, should stay the same
        assert addrs["market_data"] == "tcp://127.0.0.1:5555"

    def test_get_platform_addresses_when_python_connects(self):
        c = GatewayConfig(
            market_data_pub="tcp://192.168.1.1:5555",
            platform_connects=False,
        )
        addrs = c.get_platform_addresses()
        assert addrs["market_data"] == "tcp://192.168.1.1:5555"
        assert addrs["commands"] == "tcp://127.0.0.1:5556"


# ---------------------------------------------------------------------------
# TradingGateway initialisation
# ---------------------------------------------------------------------------


class TestGatewayInitialization:
    def test_default_config(self, logger):
        gw = TradingGateway(logger=logger)
        assert isinstance(gw.config, GatewayConfig)
        assert gw.config.market_data_pub == "tcp://127.0.0.1:5555"

    def test_custom_config(self, logger):
        config = GatewayConfig()
        config.market_data_pub = "tcp://0.0.0.0:9000"
        gw = TradingGateway(logger=logger, config=config)
        assert gw.config.market_data_pub == "tcp://0.0.0.0:9000"

    def test_initial_state(self, gateway):
        assert gateway.is_running is False
        assert gateway.is_connected is False
        assert gateway.platform_info is None
        assert gateway._platform_connected is False
        assert gateway._last_heartbeat_time is None
        assert gateway._seq_num == 0
        assert gateway._context is None
        assert gateway._market_sub is None
        assert gateway._command_push is None
        assert gateway._query_rep is None
        assert gateway._query_req is None
        assert gateway._heartbeat_sub is None

    def test_callback_dict_populated(self, gateway):
        expected = {
            MessageType.TICK,
            MessageType.BAR,
            MessageType.PARTIAL_BAR,
            MessageType.HISTORY_BATCH,
            MessageType.HISTORY_END,
            MessageType.ENTRY_FILL,
            MessageType.EXIT_FILL,
            MessageType.ORDER_REJECTED,
            MessageType.TRADE_LOG,
            MessageType.ERROR,
            MessageType.HEARTBEAT,
            MessageType.CONNECT,
            MessageType.TEST_PING,
            MessageType.TEST_PONG,
            MessageType.POSITION_SYNC,
            MessageType.COMMAND_ACK,
            MessageType.MARKET_STATUS,
            MessageType.AUDIT_RESPONSE,
        }
        assert set(gateway._callbacks.keys()) == expected
        for lst in gateway._callbacks.values():
            assert lst == []

    def test_queues_initialized_with_maxlen(self, gateway):
        assert gateway._command_queue.maxlen == gateway.config.max_queue_size

    def test_pending_and_retries_empty(self, gateway):
        assert gateway._pending_commands == {}
        assert gateway._command_retries == {}

    def test_connection_listeners_empty(self, gateway):
        assert gateway._connection_listeners == []

    def test_position_query_handler_none(self, gateway):
        assert gateway._position_query_handler is None


# ---------------------------------------------------------------------------
# Sequence numbers
# ---------------------------------------------------------------------------


class TestNextSeq:
    def test_starts_at_one(self, gateway):
        assert gateway._next_seq() == 1

    def test_increments_monotonically(self, gateway):
        seqs = [gateway._next_seq() for _ in range(100)]
        assert seqs == list(range(1, 101))

    def test_thread_safety(self, gateway):
        results: list[int] = []
        lock = threading.Lock()

        def worker():
            for _ in range(50):
                s = gateway._next_seq()
                with lock:
                    results.append(s)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(results)) == len(results)
        assert max(results) == 200


# ---------------------------------------------------------------------------
# Callback registration
# ---------------------------------------------------------------------------


class TestCallbackRegistration:
    def test_on_tick(self, gateway):
        def cb(_):
            return None
        gateway.on_tick(cb)
        assert cb in gateway._callbacks[MessageType.TICK]

    def test_on_bar(self, gateway):
        def cb(_):
            return None
        gateway.on_bar(cb)
        assert cb in gateway._callbacks[MessageType.BAR]

    def test_on_partial_bar(self, gateway):
        def cb(_):
            return None
        gateway.on_partial_bar(cb)
        assert cb in gateway._callbacks[MessageType.PARTIAL_BAR]

    def test_on_history_batch(self, gateway):
        def cb(_):
            return None
        gateway.on_history_batch(cb)
        assert cb in gateway._callbacks[MessageType.HISTORY_BATCH]

    def test_on_history_end(self, gateway):
        called = []
        gateway.on_history_end(lambda: called.append(1))
        cbs = gateway._callbacks[MessageType.HISTORY_END]
        assert len(cbs) == 1
        cbs[0]({})  # The wrapper ignores the payload
        assert called == [1]

    def test_on_entry_fill(self, gateway):
        def cb(_):
            return None
        gateway.on_entry_fill(cb)
        assert cb in gateway._callbacks[MessageType.ENTRY_FILL]

    def test_on_exit_fill(self, gateway):
        def cb(_):
            return None
        gateway.on_exit_fill(cb)
        assert cb in gateway._callbacks[MessageType.EXIT_FILL]

    def test_on_trade_log(self, gateway):
        def cb(_):
            return None
        gateway.on_trade_log(cb)
        assert cb in gateway._callbacks[MessageType.TRADE_LOG]

    def test_on_error(self, gateway):
        def cb(_):
            return None
        gateway.on_error(cb)
        assert cb in gateway._callbacks[MessageType.ERROR]

    def test_on_test_ping(self, gateway):
        def cb(_):
            return None
        gateway.on_test_ping(cb)
        assert cb in gateway._callbacks[MessageType.TEST_PING]

    def test_on_test_pong(self, gateway):
        def cb(_):
            return None
        gateway.on_test_pong(cb)
        assert cb in gateway._callbacks[MessageType.TEST_PONG]

    def test_on_position_sync(self, gateway):
        def cb(_):
            return None
        gateway.on_position_sync(cb)
        assert cb in gateway._callbacks[MessageType.POSITION_SYNC]

    def test_on_market_status(self, gateway):
        def cb(_):
            return None
        gateway.on_market_status(cb)
        assert cb in gateway._callbacks[MessageType.MARKET_STATUS]

    def test_on_connection_change(self, gateway):
        def cb(_):
            return None
        gateway.on_connection_change(cb)
        assert cb in gateway._connection_listeners

    def test_on_position_query(self, gateway):
        def handler():
            return []
        gateway.on_position_query(handler)
        assert gateway._position_query_handler is handler

    def test_on_unknown_message_type(self, gateway):
        def cb(_):
            return None
        gateway.on(MessageType.DISCONNECT, cb)
        assert cb in gateway._callbacks[MessageType.DISCONNECT]

    def test_on_creates_list_for_truly_unknown_type(self, gateway):
        def cb(_):
            return None
        gateway.on(MessageType.ACCOUNT_QUERY, cb)
        assert cb in gateway._callbacks[MessageType.ACCOUNT_QUERY]


# ---------------------------------------------------------------------------
# Callback registration idempotency
# ---------------------------------------------------------------------------


class TestCallbackIdempotency:
    """on() / on_connection_change() are idempotent: registering the same
    callback object twice is a no-op, so a stop→start cycle (which
    re-registers all handlers) never double-delivers a message."""

    def test_on_same_callback_registered_once(self, gateway):
        def cb(_):
            return None
        gateway.on(MessageType.TICK, cb)
        gateway.on(MessageType.TICK, cb)
        assert gateway._callbacks[MessageType.TICK].count(cb) == 1

    def test_on_same_callback_dispatched_exactly_once(self, gateway):
        received = []
        cb = lambda p: received.append(p)  # noqa: E731
        gateway.on_tick(cb)
        gateway.on_tick(cb)
        env = TickMessage(pair="MNQ", price=100.0, volume=10, time=1234).to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert len(received) == 1

    def test_on_different_callbacks_both_dispatched(self, gateway):
        received_a = []
        received_b = []
        gateway.on_tick(lambda p: received_a.append(p))
        gateway.on_tick(lambda p: received_b.append(p))
        env = TickMessage(pair="MNQ", price=100.0, volume=10, time=1234).to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert len(received_a) == 1
        assert len(received_b) == 1

    def test_on_connection_change_same_callback_notified_once(self, gateway):
        calls = []
        cb = lambda connected: calls.append(connected)  # noqa: E731
        gateway.on_connection_change(cb)
        gateway.on_connection_change(cb)
        assert gateway._connection_listeners.count(cb) == 1
        env = ConnectMessage(platform="nt", version="1").to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert calls == [True]

    def test_on_connection_change_different_callbacks_both_notified(self, gateway):
        calls_a = []
        calls_b = []
        gateway.on_connection_change(lambda c: calls_a.append(c))
        gateway.on_connection_change(lambda c: calls_b.append(c))
        env = ConnectMessage(platform="nt", version="1").to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert calls_a == [True]
        assert calls_b == [True]


# ---------------------------------------------------------------------------
# Connection state management
# ---------------------------------------------------------------------------


class TestConnectionState:
    def test_handle_connect_sets_state(self, gateway, logger):
        env = ConnectMessage(platform="ninjatrader", version="1.0", account="Sim101", pair="MNQ").to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert gateway._platform_connected is True
        assert gateway._last_heartbeat_time is not None
        assert gateway.platform_info == {
            "platform": "ninjatrader",
            "version": "1.0",
            "account": "Sim101",
            "pair": "MNQ",
        }
        assert any("Platform connected" in m for m in logger.messages)

    def test_handle_connect_calls_listeners(self, gateway):
        calls = []
        gateway.on_connection_change(lambda connected: calls.append(connected))
        env = ConnectMessage(platform="nt", version="1").to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert True in calls

    def test_handle_connect_listener_exception_ignored(self, gateway):
        gateway.on_connection_change(lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
        env = ConnectMessage(platform="nt", version="1").to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert gateway._platform_connected is True

    def test_handle_heartbeat_sets_connected(self, gateway):
        env = HeartbeatMessage(source="nt", status="ok").to_envelope(seq_num=1)
        gateway._handle_heartbeat(env.to_json())
        assert gateway._platform_connected is True
        assert gateway._last_heartbeat_time is not None

    def test_handle_heartbeat_calls_listeners_on_reconnect(self, gateway):
        gateway._platform_connected = False
        calls = []
        gateway.on_connection_change(lambda c: calls.append(c))
        env = HeartbeatMessage(source="nt", status="ok").to_envelope(seq_num=1)
        gateway._handle_heartbeat(env.to_json())
        assert True in calls

    def test_handle_heartbeat_notifies_listeners_after_brief_blip(self, gateway):
        gateway._platform_connected = False
        gateway._disconnect_time = time.time() - 0.5  # < _reconnect_debounce_sec
        calls = []
        gateway.on_connection_change(lambda c: calls.append(c))
        env = HeartbeatMessage(source="nt", status="ok").to_envelope(seq_num=1)
        gateway._handle_heartbeat(env.to_json())
        assert True in calls
        assert gateway.was_last_disconnect_real is False

    def test_handle_heartbeat_flags_real_disconnect_after_long_outage(self, gateway):
        gateway._platform_connected = False
        gateway._disconnect_time = time.time() - 10.0  # > _reconnect_debounce_sec
        env = HeartbeatMessage(source="nt", status="ok").to_envelope(seq_num=1)
        gateway._handle_heartbeat(env.to_json())
        assert gateway.was_last_disconnect_real is True

    def test_handle_heartbeat_does_not_recall_if_already_connected(self, gateway):
        gateway._platform_connected = True
        calls = []
        gateway.on_connection_change(lambda c: calls.append(c))
        env = HeartbeatMessage(source="nt", status="ok").to_envelope(seq_num=1)
        gateway._handle_heartbeat(env.to_json())
        assert True not in calls

    def test_handle_heartbeat_bad_json(self, gateway, logger):
        gateway._handle_heartbeat("not json")
        # Should not raise; debug message logged

    def test_handle_heartbeat_non_heartbeat_type(self, gateway):
        env = MessageEnvelope.create(msg_type=MessageType.TICK, payload={}, seq_num=1)
        gateway._handle_heartbeat(env.to_json())
        assert gateway._platform_connected is False

    def test_handle_heartbeat_listener_exception_ignored(self, gateway):
        gateway.on_connection_change(lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
        env = HeartbeatMessage(source="nt", status="ok").to_envelope(seq_num=1)
        gateway._handle_heartbeat(env.to_json())
        assert gateway._platform_connected is True

    def test_heartbeat_loop_timeout_disconnects(self, gateway, logger):
        gateway._running = True
        gateway._last_heartbeat_time = time.time() - 100
        gateway._platform_connected = True
        calls = []
        gateway.on_connection_change(lambda c: calls.append(c))
        def _sleep_and_stop(_duration):
            gateway._running = False

        with patch("src.infrastructure.gateway.gateway.time.sleep", side_effect=_sleep_and_stop):
            gateway._heartbeat_loop()
        assert gateway._platform_connected is False
        assert False in calls
        assert any("heartbeat timeout" in m.lower() for m in logger.messages)

    def test_heartbeat_loop_no_heartbeat_yet(self, gateway):
        gateway._running = True
        gateway._last_heartbeat_time = None
        gateway._platform_connected = False
        def _sleep_and_stop(_duration):
            gateway._running = False

        with patch("src.infrastructure.gateway.gateway.time.sleep", side_effect=_sleep_and_stop):
            gateway._heartbeat_loop()
        assert gateway._platform_connected is False

    def test_heartbeat_loop_exception_recovery(self, gateway, logger):
        gateway._running = True
        original = gateway.config
        gateway.config = None
        def _sleep_and_stop(_duration):
            gateway._running = False

        with patch("src.infrastructure.gateway.gateway.time.sleep", side_effect=_sleep_and_stop):
            gateway._heartbeat_loop()
        gateway.config = original
        assert any("Error in heartbeat loop" in m for m in logger.messages)

    def test_is_connected_requires_both_flags(self, gateway):
        gateway._running = True
        gateway._platform_connected = False
        assert gateway.is_connected is False
        gateway._platform_connected = True
        assert gateway.is_connected is True
        gateway._running = False
        assert gateway.is_connected is False


# ---------------------------------------------------------------------------
# Message routing (_handle_message)
# ---------------------------------------------------------------------------


class TestMessageRouting:
    def test_updates_seq_num(self, gateway):
        env = MessageEnvelope.create(msg_type=MessageType.TICK, payload={}, seq_num=42)
        gateway._handle_message(env.to_json())
        assert gateway._seq_num == 42

    def test_seq_num_does_not_decrease(self, gateway):
        gateway._seq_num = 100
        env = MessageEnvelope.create(msg_type=MessageType.TICK, payload={}, seq_num=50)
        gateway._handle_message(env.to_json())
        assert gateway._seq_num == 100

    def test_invalid_json_logs_error(self, gateway, logger):
        gateway._handle_message("not json at all")
        assert any("Invalid JSON" in m for m in logger.messages)

    def test_invalid_envelope_logs_error(self, gateway, logger):
        gateway._handle_message("{}")  # Missing required fields
        assert any("Invalid message envelope" in m for m in logger.messages)

    def test_tick_dispatch(self, gateway):
        received = []
        gateway.on_tick(lambda p: received.append(p))
        env = TickMessage(pair="MNQ", price=100.0, volume=10, time=1234).to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert len(received) == 1
        assert received[0]["price"] == 100.0

    def test_bar_dispatch(self, gateway):
        received = []
        gateway.on_bar(lambda p: received.append(p))
        env = BarMessage(pair="MNQ", time=100, open=10, high=11, low=9, close=10.5, volume=100).to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert len(received) == 1

    def test_partial_bar_dispatch(self, gateway):
        received = []
        gateway.on_partial_bar(lambda p: received.append(p))
        env = BarMessage(pair="MNQ", time=100, open=10, high=11, low=9, close=10.5, volume=100, is_partial=True).to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert len(received) == 1

    def test_callback_exception_caught(self, gateway, logger):
        gateway.on_tick(lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
        env = TickMessage(pair="MNQ", price=100.0, volume=10, time=1234).to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert any("Callback error" in m for m in logger.messages)

    def test_high_frequency_types_not_logged(self, gateway, logger):
        for mt in (MessageType.TICK, MessageType.BAR, MessageType.PARTIAL_BAR):
            env = MessageEnvelope.create(msg_type=mt, payload={}, seq_num=1)
            gateway._handle_message(env.to_json())
        # No "RECV" logs for these types
        assert not any("RECV" in m for m in logger.messages)

    def test_important_types_logged(self, gateway, logger):
        env = ConnectMessage(platform="nt", version="1").to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert any("RECV" in m for m in logger.messages)

    def test_entry_fill_logs(self, gateway, logger):
        env = EntryFillMessage(trade_id="T1", entry_price=100.0).to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert any("Entry fill" in m for m in logger.messages)

    def test_exit_fill_logs(self, gateway, logger):
        env = ExitFillMessage(trade_id="T1", exit_price=110.0, result_type="TP").to_envelope(seq_num=1)
        gateway._handle_message(env.to_json())
        assert any("Exit fill" in m for m in logger.messages)

    def test_order_rejected_dispatch(self, gateway):
        received = []
        gateway.on(MessageType.ORDER_REJECTED, lambda p: received.append(p))
        env = MessageEnvelope.create(msg_type=MessageType.ORDER_REJECTED, payload={"trade_id": "T1", "reason": "bad"}, seq_num=1)
        gateway._handle_message(env.to_json())
        assert len(received) == 1

    def test_trade_log_dispatch(self, gateway):
        received = []
        gateway.on_trade_log(lambda p: received.append(p))
        env = MessageEnvelope.create(msg_type=MessageType.TRADE_LOG, payload={"trade_id": "T1", "event": "E", "message": "M"}, seq_num=1)
        gateway._handle_message(env.to_json())
        assert len(received) == 1

    def test_warning_dispatch(self, gateway):
        received = []
        gateway.on(MessageType.WARNING, lambda p: received.append(p))
        env = MessageEnvelope.create(msg_type=MessageType.WARNING, payload={"text": "warn"}, seq_num=1)
        gateway._handle_message(env.to_json())
        assert len(received) == 1

    def test_disconnect_dispatch(self, gateway):
        received = []
        gateway.on(MessageType.DISCONNECT, lambda p: received.append(p))
        env = MessageEnvelope.create(msg_type=MessageType.DISCONNECT, payload={}, seq_num=1)
        gateway._handle_message(env.to_json())
        assert len(received) == 1

    def test_debug_logging_for_other_types(self, gateway, logger):
        env = MessageEnvelope.create(msg_type=MessageType.TICK, payload={"pair": "MNQ", "price": 100.0}, seq_num=1)
        gateway._handle_message(env.to_json())
        # Should not raise; debug path exercised


# ---------------------------------------------------------------------------
# Error handling paths
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_handle_error_with_details(self, gateway, logger):
        gateway._handle_error({
            "source": "nt",
            "error_type": "order_failed",
            "message": "fail",
            "details": "a | b | c | d | e | f",
        })
        assert any("PLATFORM ERROR" in m for m in logger.messages)
        assert any("→ a" in m for m in logger.messages)
        # Limited to 5 detail lines
        assert sum("→" in m for m in logger.messages) == 5

    def test_handle_error_without_details(self, gateway, logger):
        gateway._handle_error({
            "source": "nt",
            "error_type": "order_failed",
            "message": "fail",
        })
        assert any("PLATFORM ERROR" in m for m in logger.messages)
        assert not any("→" in m for m in logger.messages)

    def test_handle_error_defaults(self, gateway, logger):
        gateway._handle_error({})
        assert any("unknown" in m.lower() for m in logger.messages)


# ---------------------------------------------------------------------------
# Command acknowledgment handling
# ---------------------------------------------------------------------------


class TestCommandAckHandling:
    def test_success_removes_pending(self, gateway, logger):
        gateway._pending_commands[5] = {"type": "order_open", "sent_time": time.time(), "payload": {}}
        ack = CommandAckMessage(command_type="order_open", seq_num=5, success=True, trade_id="T1")
        env = ack.to_envelope(seq_num=10)
        gateway._handle_message(env.to_json())
        assert 5 not in gateway._pending_commands
        assert any("Command ACK" in m for m in logger.messages)

    def test_failure_removes_pending(self, gateway, logger):
        gateway._pending_commands[6] = {"type": "order_close", "sent_time": time.time(), "payload": {}}
        ack = CommandAckMessage(command_type="order_close", seq_num=6, success=False, trade_id="T1", message="rej")
        env = ack.to_envelope(seq_num=10)
        gateway._handle_message(env.to_json())
        assert 6 not in gateway._pending_commands
        assert any("Command FAILED" in m for m in logger.messages)

    def test_failure_notifies_registered_listener(self, gateway):
        failures = []
        gateway.on_command_failed(lambda *args: failures.append(args))
        gateway._pending_commands[7] = {
            "type": "order_open",
            "sent_time": time.time(),
            "payload": {"trade_id": "T1"},
        }
        ack = CommandAckMessage(
            command_type="order_open",
            seq_num=7,
            success=False,
            trade_id="T1",
            message="rej",
        )
        gateway._handle_command_ack(ack.to_envelope(seq_num=10).payload)
        assert failures == [("order_open", "T1", 7, "rej")]

    def test_timeout_notifies_registered_listener(self, gateway):
        failures = []
        gateway.on_command_failed(lambda *args: failures.append(args))
        gateway._pending_commands[8] = {
            "type": "order_modify",
            "sent_time": time.time() - 120,
            "payload": {"trade_id": "T2"},
        }
        gateway._cleanup_pending_commands()
        assert 8 not in gateway._pending_commands
        assert failures == [("order_modify", "T2", 8, "timeout")]

    def test_unknown_ack_logged_debug(self, gateway, logger):
        ack = CommandAckMessage(command_type="order_open", seq_num=999, success=True, trade_id="T1")
        env = ack.to_envelope(seq_num=10)
        gateway._handle_message(env.to_json())
        assert any("unknown" in m.lower() for m in logger.messages)

    def test_parse_error_caught(self, gateway, logger):
        env = MessageEnvelope.create(msg_type=MessageType.COMMAND_ACK, payload="bad", seq_num=1)
        gateway._handle_message(env.to_json())
        assert any("Invalid message envelope" in m for m in logger.messages)


# ---------------------------------------------------------------------------
# Command timeout listeners (infra commands without trade_id)
# ---------------------------------------------------------------------------


class TestCommandTimeoutListeners:
    def test_timeout_fires_for_trade_id_less_command(self, gateway):
        timeouts = []
        gateway.on_command_timeout(lambda *args: timeouts.append(args))
        gateway._pending_commands[8] = {
            "type": "subscribe",
            "sent_time": time.time() - 120,
            "payload": {"instrument": "MES 09-26"},
        }
        gateway._cleanup_pending_commands()
        assert timeouts == [("subscribe", {"instrument": "MES 09-26"}, 8)]

    def test_on_command_failed_still_drops_trade_id_less_failures(self, gateway):
        failures = []
        gateway.on_command_failed(lambda *args: failures.append(args))
        gateway._pending_commands[8] = {
            "type": "subscribe",
            "sent_time": time.time() - 120,
            "payload": {"instrument": "MES 09-26"},
        }
        gateway._cleanup_pending_commands()
        assert failures == []

    def test_registration_is_idempotent(self, gateway):
        timeouts = []

        def cb(*args):
            timeouts.append(args)

        gateway.on_command_timeout(cb)
        gateway.on_command_timeout(cb)
        gateway._pending_commands[8] = {
            "type": "refresh_request",
            "sent_time": time.time() - 120,
            "payload": {"days": 30, "instrument": "MES 09-26"},
        }
        gateway._cleanup_pending_commands()
        assert len(timeouts) == 1

    def test_timeout_listener_deduped_for_same_instrument(self, gateway):
        timeouts = []
        gateway.on_command_timeout(lambda *args: timeouts.append(args))
        for seq in range(10, 20):
            gateway._pending_commands[seq] = {
                "type": "subscribe",
                "sent_time": time.time() - 120,
                "payload": {"instrument": "MES 09-26"},
            }
        gateway._cleanup_pending_commands()
        assert len(timeouts) == 1
        assert timeouts[0][0] == "subscribe"
        assert timeouts[0][1]["instrument"] == "MES 09-26"
        assert all(seq not in gateway._pending_commands for seq in range(10, 20))

    def test_timeout_listener_keeps_distinct_instruments(self, gateway):
        timeouts = []
        gateway.on_command_timeout(lambda *args: timeouts.append(args))
        gateway._pending_commands[10] = {
            "type": "subscribe",
            "sent_time": time.time() - 120,
            "payload": {"instrument": "MES 09-26"},
        }
        gateway._pending_commands[11] = {
            "type": "subscribe",
            "sent_time": time.time() - 120,
            "payload": {"instrument": "MNQ 09-26"},
        }
        gateway._cleanup_pending_commands()
        assert len(timeouts) == 2
        assert {t[1]["instrument"] for t in timeouts} == {"MES 09-26", "MNQ 09-26"}

    def test_timeout_listener_deduped_by_trade_id_for_orders(self, gateway):
        timeouts = []
        gateway.on_command_timeout(lambda *args: timeouts.append(args))
        for seq in range(3):
            gateway._pending_commands[seq] = {
                "type": "order_open",
                "sent_time": time.time() - 120,
                "payload": {"trade_id": "T1"},
            }
        gateway._cleanup_pending_commands()
        assert len(timeouts) == 1
        assert timeouts[0][0] == "order_open"

    def test_heartbeat_loop_reaps_timeout_without_new_command(self, gateway):
        """The periodic reaper detects an ACK timeout even when no further
        command is ever sent."""
        timeouts = []
        gateway.on_command_timeout(lambda *args: timeouts.append(args))
        gateway._command_ack_timeout_sec = 0.05
        gateway._running = True
        gateway._last_cleanup_time = 0.0
        gateway._pending_commands[8] = {
            "type": "subscribe",
            "sent_time": time.time() - 1.0,
            "payload": {"instrument": "MES 09-26"},
        }
        thread = threading.Thread(target=gateway._heartbeat_loop, daemon=True)
        thread.start()
        try:
            deadline = time.time() + 2.0
            while time.time() < deadline and not timeouts:
                time.sleep(0.02)
            assert timeouts == [("subscribe", {"instrument": "MES 09-26"}, 8)]
            assert 8 not in gateway._pending_commands
        finally:
            gateway._running = False
            thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Position sync handling
# ---------------------------------------------------------------------------


class TestPositionSyncHandling:
    def test_position_sync_with_positions(self, gateway, logger):
        env = MessageEnvelope.create(
            msg_type=MessageType.POSITION_SYNC,
            payload={
                "positions": [
                    {"trade_id": "T1", "direction": "long", "entry_price": 100.0},
                    {"trade_id": "T2", "direction": "short", "entry_price": 200.0},
                ],
                "count": 2,
                "source": "ninjatrader",
                "untracked_orders": [],
            },
            seq_num=1,
        )
        gateway._handle_message(env.to_json())
        assert any("POSITION SYNC" in m for m in logger.messages)
        assert any("T1" in m for m in logger.messages)
        assert any("T2" in m for m in logger.messages)

    def test_position_sync_with_untracked(self, gateway, logger):
        env = MessageEnvelope.create(
            msg_type=MessageType.POSITION_SYNC,
            payload={
                "positions": [],
                "count": 0,
                "source": "nt",
                "untracked_orders": [{"order_name": "orphan1"}, {"order_name": "orphan2"}],
            },
            seq_num=1,
        )
        gateway._handle_message(env.to_json())
        assert any("untracked" in m.lower() for m in logger.messages)
        assert any("orphan1" in m for m in logger.messages)

    def test_position_sync_callback(self, gateway):
        received = []
        gateway.on_position_sync(lambda p: received.append(p))
        env = MessageEnvelope.create(
            msg_type=MessageType.POSITION_SYNC,
            payload={"positions": [], "count": 0, "source": "nt"},
            seq_num=1,
        )
        gateway._handle_message(env.to_json())
        assert len(received) == 1


# ---------------------------------------------------------------------------
# Market status handling
# ---------------------------------------------------------------------------


class TestMarketStatusHandling:
    def test_market_status_open_logged(self, gateway, logger):
        env = MessageEnvelope.create(
            msg_type=MessageType.MARKET_STATUS,
            payload={"market_open": True, "next_open": 1700000000, "pair": "MNQ"},
            seq_num=1,
        )
        gateway._handle_message(env.to_json())
        assert any("market is OPEN" in m for m in logger.messages)

    def test_market_status_closed_logged(self, gateway, logger):
        env = MessageEnvelope.create(
            msg_type=MessageType.MARKET_STATUS,
            payload={"market_open": False, "next_open": 1700003600, "pair": "MNQ"},
            seq_num=2,
        )
        gateway._handle_message(env.to_json())
        assert any("market is CLOSED" in m for m in logger.messages)

    def test_market_status_callback(self, gateway):
        received = []
        gateway.on_market_status(lambda p: received.append(p))
        env = MessageEnvelope.create(
            msg_type=MessageType.MARKET_STATUS,
            payload={"market_open": True, "next_open": 1700000000, "pair": "MNQ"},
            seq_num=1,
        )
        gateway._handle_message(env.to_json())
        assert len(received) == 1
        assert received[0]["market_open"] is True


# ---------------------------------------------------------------------------
# Payload preview formatting
# ---------------------------------------------------------------------------


class TestFormatPayloadPreview:
    def test_empty(self, gateway):
        assert gateway._format_payload_preview({}) == "{}"

    def test_known_fields(self, gateway):
        result = gateway._format_payload_preview({
            "pair": "MNQ",
            "price": 100.0,
            "trade_id": "T1",
            "direction": "long",
        })
        assert "pair=MNQ" in result
        assert "price=100.00" in result
        assert "trade_id=T1" in result
        assert "direction=long" in result

    def test_bars_count(self, gateway):
        result = gateway._format_payload_preview({"bars": [{}, {}, {}]})
        assert "bars=3" in result

    def test_fallback_first_key(self, gateway):
        result = gateway._format_payload_preview({"custom": "value"})
        assert "custom=value" in result

    def test_limits_to_four_parts(self, gateway):
        result = gateway._format_payload_preview({
            "pair": "MNQ",
            "price": 100.0,
            "time": 123,
            "trade_id": "T1",
            "direction": "long",
            "status": "ok",
        })
        parts = result.split(" | ")
        assert len(parts) <= 4


# ---------------------------------------------------------------------------
# Command sending
# ---------------------------------------------------------------------------


class TestCommandSending:
    def test_send_command_when_not_running(self, gateway, logger):
        gateway._running = False
        env = MessageEnvelope.create(msg_type=MessageType.ORDER_OPEN, payload={}, seq_num=1)
        gateway._send_command(env)
        assert any("not running" in m.lower() for m in logger.messages)

    def test_send_command_tracks_pending(self, gateway):
        gateway._running = True
        env = MessageEnvelope.create(msg_type=MessageType.ORDER_OPEN, payload={"trade_id": "T1"}, seq_num=1)
        gateway._send_command(env)
        assert 1 in gateway._pending_commands
        assert gateway._pending_commands[1]["type"] == "order_open"

    def test_send_command_adds_to_queue(self, gateway):
        gateway._running = True
        env = MessageEnvelope.create(msg_type=MessageType.ORDER_OPEN, payload={}, seq_num=1)
        gateway._send_command(env)
        assert len(gateway._command_queue) == 1

    def test_send_command_queue_near_capacity(self, gateway, logger):
        gateway._running = True
        gateway._command_queue = MagicMock()
        gateway._command_queue.maxlen = 10
        gateway._command_queue.__len__ = MagicMock(return_value=9)
        env = MessageEnvelope.create(msg_type=MessageType.ORDER_OPEN, payload={}, seq_num=1)
        gateway._send_command(env)
        assert any("NEAR CAPACITY" in m for m in logger.messages)

    def test_send_command_cleanup_time_throttled(self, gateway):
        gateway._running = True
        # Force last cleanup to be >1s ago so cleanup fires
        gateway._last_cleanup_time = time.time() - 2.0
        old_pending = {99: {"type": "old", "sent_time": time.time() - 120}}
        gateway._pending_commands.update(old_pending)
        env = MessageEnvelope.create(msg_type=MessageType.ORDER_OPEN, payload={}, seq_num=1)
        gateway._send_command(env)
        assert 99 not in gateway._pending_commands

    def test_cleanup_pending_commands_removes_old(self, gateway, logger):
        gateway._pending_commands[1] = {"type": "order_open", "sent_time": time.time() - 120}
        gateway._cleanup_pending_commands()
        assert 1 not in gateway._pending_commands
        assert any("timed out" in m.lower() for m in logger.messages)

    def test_cleanup_pending_commands_keeps_recent(self, gateway):
        gateway._pending_commands[1] = {"type": "order_open", "sent_time": time.time()}
        gateway._cleanup_pending_commands()
        assert 1 in gateway._pending_commands

    def test_send_open_order(self, gateway, logger):
        gateway._running = True
        gateway.send_open_order(
            trade_id="T1",
            direction="long",
            entry_price=100.0,
            stop_loss=90.0,
            take_profit=110.0,
            risk_points=10.0,
            rr_ratio=1.0,
            pair="MNQ",
            instrument="MNQ 09-26",
        )
        assert len(gateway._pending_commands) == 1
        assert gateway._pending_commands[1]["payload"]["instrument"] == "MNQ 09-26"
        assert any("OPEN order" in m for m in logger.messages)

    def test_send_open_order_all_args(self, gateway):
        gateway._running = True
        gateway.send_open_order(
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
            instrument="ES 09-26",
        )
        assert len(gateway._pending_commands) == 1
        assert gateway._pending_commands[1]["payload"]["instrument"] == "ES 09-26"

    def test_send_open_order_without_instrument_raises(self, logger):
        gw = TradingGateway(logger=logger, config=GatewayConfig())
        gw._running = True
        with pytest.raises(ValueError, match="instrument is required"):
            gw.send_open_order(
                trade_id="T1",
                direction="long",
                entry_price=100.0,
                stop_loss=90.0,
                take_profit=110.0,
                risk_points=10.0,
                rr_ratio=1.0,
                pair="MNQ",
            )

    def test_send_open_order_without_pair_raises(self, gateway):
        gateway._running = True
        with pytest.raises(ValueError, match="pair is required"):
            gateway.send_open_order(
                trade_id="T1",
                direction="long",
                entry_price=100.0,
                stop_loss=90.0,
                take_profit=110.0,
                risk_points=10.0,
                rr_ratio=1.0,
                instrument="MNQ 09-26",
            )

    def test_send_open_order_invalid_direction_raises(self, logger):
        gw = TradingGateway(logger=logger, config=GatewayConfig())
        gw._running = True
        with pytest.raises(ValueError, match="Invalid order direction"):
            gw.send_open_order(
                trade_id="T1",
                direction="sideways",
                entry_price=100.0,
                stop_loss=90.0,
                take_profit=110.0,
                risk_points=10.0,
                rr_ratio=1.0,
            )

    def test_send_close_order(self, gateway, logger):
        gateway._running = True
        gateway.send_close_order("T1", reason="manual", account="Sim101", instrument="MNQ 09-26")
        assert len(gateway._pending_commands) == 1
        assert gateway._pending_commands[1]["payload"]["instrument"] == "MNQ 09-26"
        assert any("CLOSE order" in m for m in logger.messages)

    def test_send_close_order_without_instrument_raises(self, logger):
        gw = TradingGateway(logger=logger, config=GatewayConfig())
        gw._running = True
        with pytest.raises(ValueError, match="instrument is required"):
            gw.send_close_order("T1", reason="manual", account="Sim101")

    def test_send_modify_order(self, gateway, logger):
        gateway._running = True
        gateway.send_modify_order("T1", stop_loss=95.0, take_profit=115.0, account="Sim101", instrument="MNQ 09-26")
        assert len(gateway._pending_commands) == 1
        assert gateway._pending_commands[1]["payload"]["instrument"] == "MNQ 09-26"
        assert any("MODIFY order" in m for m in logger.messages)

    def test_send_modify_order_without_instrument_raises(self, logger):
        gw = TradingGateway(logger=logger, config=GatewayConfig())
        gw._running = True
        with pytest.raises(ValueError, match="instrument is required"):
            gw.send_modify_order("T1", stop_loss=95.0, account="Sim101")

    def test_send_refresh_request(self, gateway, logger):
        gateway._running = True
        gateway.send_refresh_request(days=5, instrument="MNQ 09-26")
        assert len(gateway._pending_commands) == 1
        assert gateway._pending_commands[1]["payload"]["instrument"] == "MNQ 09-26"
        assert any("REFRESH" in m for m in logger.messages)

    def test_send_refresh_request_without_instrument_raises(self, logger):
        gw = TradingGateway(logger=logger, config=GatewayConfig())
        gw._running = True
        with pytest.raises(ValueError, match="instrument is required"):
            gw.send_refresh_request(days=5)

    def test_send_subscribe(self, gateway, logger):
        gateway._running = True
        gateway.send_subscribe("MNQ 09-26")
        assert len(gateway._pending_commands) == 1
        assert gateway._pending_commands[1]["payload"]["instrument"] == "MNQ 09-26"
        assert any("SUBSCRIBE" in m for m in logger.messages)

    def test_send_subscribe_empty_instrument_raises(self, logger):
        gw = TradingGateway(logger=logger, config=GatewayConfig())
        gw._running = True
        with pytest.raises(ValueError, match="instrument is required"):
            gw.send_subscribe("")

    def test_send_disconnect(self, gateway, logger):
        gateway._running = True
        gateway.send_disconnect("stream stopped")
        assert len(gateway._pending_commands) == 1
        cmd = gateway._pending_commands[1]
        assert cmd["type"] == MessageType.DISCONNECT.value
        assert cmd["payload"]["reason"] == "stream stopped"

    def test_send_disconnect_failure_is_swallowed(self, gateway, logger):
        gateway._running = True
        gateway._send_command = MagicMock(side_effect=Exception("socket gone"))
        gateway.send_disconnect()  # must not raise
        assert any("disconnect" in m.lower() for m in logger.messages)

    def test_send_audit_request(self, gateway, logger):
        gateway._running = True
        gateway.send_audit_request(bars_back=30, instrument="MNQ 09-26")
        assert len(gateway._pending_commands) == 1
        assert gateway._pending_commands[1]["payload"]["instrument"] == "MNQ 09-26"
        assert gateway._pending_commands[1]["payload"]["bars_back"] == 30
        assert any("AUDIT" in m for m in logger.messages)

    def test_send_audit_request_without_instrument_raises(self, logger):
        gw = TradingGateway(logger=logger, config=GatewayConfig())
        gw._running = True
        with pytest.raises(ValueError, match="instrument is required"):
            gw.send_audit_request(bars_back=30)

    def test_send_error_with_details(self, gateway, logger):
        gateway._running = True
        gateway.send_error("src", "etype", "msg", details="extra")
        assert len(gateway._pending_commands) == 1
        assert any("Sent error" in m for m in logger.messages)

    def test_send_error_without_details(self, gateway):
        gateway._running = True
        gateway.send_error("src", "etype", "msg")
        assert len(gateway._pending_commands) == 1

    def test_send_test_pong(self, gateway):
        gateway._running = True
        gateway.send_test_pong(timestamp=12345.0)
        assert len(gateway._pending_commands) == 1

    def test_command_methods_noop_when_not_running(self, gateway, logger):
        gateway._running = False
        gateway.send_open_order("T1", "long", 100, 90, 110, 10, 1.0, pair="MNQ", instrument="MNQ 09-26")
        gateway.send_close_order("T1", instrument="MNQ 09-26")
        gateway.send_modify_order("T1", instrument="MNQ 09-26")
        gateway.send_refresh_request(instrument="MNQ 09-26")
        gateway.send_error("s", "t", "m")
        gateway.send_test_pong(0.0)
        # All should warn about not running
        assert sum("not running" in m.lower() for m in logger.messages) == 6


# ---------------------------------------------------------------------------
# Command sender loop
# ---------------------------------------------------------------------------


def _run_loop_briefly(gw, target, delay=0.02):
    """Run a background loop in a thread and stop it after a short delay."""
    t = threading.Thread(target=target, daemon=True)
    t.start()
    time.sleep(delay)
    gw._running = False
    t.join(timeout=1.0)


class TestCommandSenderLoop:
    def test_empty_queue_returns_quickly(self, gateway):
        gateway._running = True
        gateway._command_queue.clear()
        _run_loop_briefly(gateway, gateway._command_sender_loop, delay=0.01)

    def test_socket_none_drops_command(self, gateway, logger):
        gateway._running = True
        gateway._command_push = None
        env = MessageEnvelope.create(msg_type=MessageType.ORDER_OPEN, payload={"trade_id": "T1"}, seq_num=1)
        gateway._command_queue.append(env)
        gateway._pending_commands[1] = {"type": "order_open", "sent_time": time.time()}
        _run_loop_briefly(gateway, gateway._command_sender_loop, delay=0.02)
        assert 1 not in gateway._pending_commands
        assert any("socket not available" in m.lower() for m in logger.messages)

    def test_successful_send_removes_retries(self, gateway):
        gateway._running = True
        mock_socket = MagicMock()
        gateway._command_push = mock_socket
        env = MessageEnvelope.create(msg_type=MessageType.ORDER_OPEN, payload={"trade_id": "T1"}, seq_num=1)
        gateway._command_queue.append(env)
        gateway._command_retries[1] = 2
        _run_loop_briefly(gateway, gateway._command_sender_loop, delay=0.02)
        assert 1 not in gateway._command_retries
        mock_socket.send_string.assert_called_once()

    def test_send_exception_retries_and_requeues(self, gateway, logger):
        gateway._running = True
        mock_socket = MagicMock()
        mock_socket.send_string.side_effect = Exception("send failed")
        gateway._command_push = mock_socket
        env = MessageEnvelope.create(msg_type=MessageType.ORDER_OPEN, payload={"trade_id": "T1"}, seq_num=1)
        gateway._command_queue.append(env)
        gateway._pending_commands[1] = {"type": "order_open", "sent_time": time.time()}
        gateway._command_retries[1] = 1
        _run_loop_briefly(gateway, gateway._command_sender_loop, delay=0.02)
        assert any("retry" in m.lower() for m in logger.messages)

    def test_send_exception_exhausted_drops(self, gateway, logger):
        gateway._running = True
        mock_socket = MagicMock()
        mock_socket.send_string.side_effect = Exception("send failed")
        gateway._command_push = mock_socket
        env = MessageEnvelope.create(msg_type=MessageType.ORDER_OPEN, payload={"trade_id": "T1"}, seq_num=1)
        gateway._command_queue.append(env)
        gateway._pending_commands[1] = {"type": "order_open", "sent_time": time.time()}
        gateway._command_retries[1] = 3
        _run_loop_briefly(gateway, gateway._command_sender_loop, delay=0.02)
        assert 1 not in gateway._command_retries
        assert 1 not in gateway._pending_commands
        assert any("permanently" in m.lower() for m in logger.messages)

    def test_general_exception_caught(self, gateway, logger):
        gateway._running = True
        gateway._command_queue = MagicMock()
        gateway._command_queue.popleft.side_effect = Exception("unexpected")
        _run_loop_briefly(gateway, gateway._command_sender_loop, delay=0.02)
        assert any("Error sending command" in m for m in logger.messages)


# ---------------------------------------------------------------------------
# Query handler loop
# ---------------------------------------------------------------------------


class TestQueryHandlerLoop:
    def test_no_socket_sleeps(self, gateway):
        gateway._running = True
        gateway._query_rep = None
        _run_loop_briefly(gateway, gateway._query_handler_loop, delay=0.02)

    def test_test_ping(self, gateway, logger):
        gateway._running = True
        mock_socket = MagicMock()
        ping = MessageEnvelope.create(msg_type=MessageType.TEST_PING, payload={"timestamp": 1.0}, seq_num=1)
        mock_socket.recv_string.return_value = ping.to_json()
        gateway._query_rep = mock_socket
        _run_loop_briefly(gateway, gateway._query_handler_loop, delay=0.02)
        assert mock_socket.send_string.called
        sent = MessageEnvelope.from_json(mock_socket.send_string.call_args[0][0])
        assert sent.msg_type == MessageType.TEST_PONG

    def test_position_query_with_handler(self, gateway, logger):
        gateway._running = True
        mock_socket = MagicMock()
        query = MessageEnvelope.create(msg_type=MessageType.POSITION_QUERY, payload={}, seq_num=1)
        mock_socket.recv_string.return_value = query.to_json()
        gateway._query_rep = mock_socket
        gateway._position_query_handler = lambda: [{"trade_id": "T1"}]
        _run_loop_briefly(gateway, gateway._query_handler_loop, delay=0.02)
        sent = MessageEnvelope.from_json(mock_socket.send_string.call_args[0][0])
        assert sent.msg_type == MessageType.POSITION_RESPONSE
        assert sent.payload["count"] == 1

    def test_position_query_handler_error(self, gateway, logger):
        gateway._running = True
        mock_socket = MagicMock()
        query = MessageEnvelope.create(msg_type=MessageType.POSITION_QUERY, payload={}, seq_num=1)
        mock_socket.recv_string.return_value = query.to_json()
        gateway._query_rep = mock_socket
        gateway._position_query_handler = lambda: (_ for _ in ()).throw(Exception("bad"))
        _run_loop_briefly(gateway, gateway._query_handler_loop, delay=0.02)
        assert any("Error in position query handler" in m for m in logger.messages)
        assert mock_socket.send_string.called

    def test_config_query_accounts(self, gateway, logger):
        gateway._running = True
        mock_socket = MagicMock()
        query = MessageEnvelope.create(msg_type=MessageType.CONFIG_QUERY, payload={"key": "accounts"}, seq_num=1)
        mock_socket.recv_string.return_value = query.to_json()
        gateway._query_rep = mock_socket
        with patch.object(gateway, "_refresh_account_names"):
            gateway._account_names = ["Sim101"]
            _run_loop_briefly(gateway, gateway._query_handler_loop, delay=0.02)
        sent = MessageEnvelope.from_json(mock_socket.send_string.call_args[0][0])
        assert sent.msg_type == MessageType.CONFIG_RESPONSE
        assert sent.payload["accounts"] == "Sim101"

    def test_config_query_all(self, gateway):
        gateway._running = True
        mock_socket = MagicMock()
        query = MessageEnvelope.create(msg_type=MessageType.CONFIG_QUERY, payload={"key": "all"}, seq_num=1)
        mock_socket.recv_string.return_value = query.to_json()
        gateway._query_rep = mock_socket
        with patch.object(gateway, "_refresh_account_names"):
            gateway._account_names = ["Sim101"]
            _run_loop_briefly(gateway, gateway._query_handler_loop, delay=0.02)
        sent = MessageEnvelope.from_json(mock_socket.send_string.call_args[0][0])
        assert sent.payload["accounts"] == "Sim101"

    def test_config_query_other_key(self, gateway):
        gateway._running = True
        mock_socket = MagicMock()
        query = MessageEnvelope.create(msg_type=MessageType.CONFIG_QUERY, payload={"key": "foo"}, seq_num=1)
        mock_socket.recv_string.return_value = query.to_json()
        gateway._query_rep = mock_socket
        _run_loop_briefly(gateway, gateway._query_handler_loop, delay=0.02)
        sent = MessageEnvelope.from_json(mock_socket.send_string.call_args[0][0])
        assert sent.payload.get("foo") is None

    def test_unknown_query_type(self, gateway, logger):
        gateway._running = True
        mock_socket = MagicMock()
        query = MessageEnvelope.create(msg_type=MessageType.DISCONNECT, payload={}, seq_num=1)
        mock_socket.recv_string.return_value = query.to_json()
        gateway._query_rep = mock_socket
        _run_loop_briefly(gateway, gateway._query_handler_loop, delay=0.02)
        assert any("Unknown query type" in m for m in logger.messages)
        assert mock_socket.send_string.called

    def test_zmq_again_continues(self, gateway):
        gateway._running = True
        mock_socket = MagicMock()
        mock_socket.recv_string.side_effect = zmq.Again()
        gateway._query_rep = mock_socket
        _run_loop_briefly(gateway, gateway._query_handler_loop, delay=0.02)
        assert not mock_socket.send_string.called

    def test_general_exception(self, gateway, logger):
        gateway._running = True
        mock_socket = MagicMock()
        mock_socket.recv_string.side_effect = Exception("boom")
        gateway._query_rep = mock_socket
        _run_loop_briefly(gateway, gateway._query_handler_loop, delay=0.02)
        assert any("Error in query handler loop" in m for m in logger.messages)


# ---------------------------------------------------------------------------
# Market data loop
# ---------------------------------------------------------------------------


class TestMarketDataLoop:
    def test_zmq_eterm_breaks_loop(self, gateway):
        gateway._running = True
        mock_sub = MagicMock()
        mock_sub.recv_string.side_effect = zmq.ZMQError(zmq.ETERM)
        gateway._market_sub = mock_sub
        gateway._heartbeat_sub = MagicMock()
        with patch.object(zmq.Poller, 'poll', return_value=[(mock_sub, zmq.POLLIN)]):
            gateway._market_data_loop()
        # Should exit without error

    def test_general_exception_logged(self, gateway, logger):
        gateway._running = True
        mock_sub = MagicMock()
        mock_sub.recv_string.side_effect = Exception("boom")
        gateway._market_sub = mock_sub
        gateway._heartbeat_sub = MagicMock()
        with patch.object(zmq.Poller, 'poll', return_value=[(mock_sub, zmq.POLLIN)]):
            _run_loop_briefly(gateway, gateway._market_data_loop, delay=0.02)
        assert any("Error in market data loop" in m for m in logger.messages)


# ---------------------------------------------------------------------------
# Query positions (public API)
# ---------------------------------------------------------------------------


class TestQueryPositions:
    def test_no_socket_returns_none(self, gateway):
        assert gateway.query_positions() is None

    def test_successful_query(self, gateway):
        gateway._running = True
        gateway._query_req = MagicMock()
        resp = PositionResponseMessage(positions=[{"trade_id": "T1"}], count=1)
        gateway._query_req.recv_string.return_value = resp.to_envelope(seq_num=1).to_json()
        result = gateway.query_positions()
        assert result == [{"trade_id": "T1"}]

    def test_timeout_returns_none(self, gateway, logger):
        gateway._running = True
        gateway._query_req = MagicMock()
        gateway._query_req.recv_string.side_effect = zmq.Again()
        assert gateway.query_positions() is None
        assert any("timeout" in m.lower() for m in logger.messages)

    def test_error_returns_none(self, gateway, logger):
        gateway._running = True
        gateway._query_req = MagicMock()
        gateway._query_req.recv_string.side_effect = Exception("boom")
        assert gateway.query_positions() is None
        assert any("error" in m.lower() for m in logger.messages)

    def test_unexpected_response_type_returns_none(self, gateway):
        gateway._running = True
        gateway._query_req = MagicMock()
        resp = MessageEnvelope.create(msg_type=MessageType.ERROR, payload={}, seq_num=1)
        gateway._query_req.recv_string.return_value = resp.to_json()
        assert gateway.query_positions() is None

    def test_not_running_returns_none(self, gateway):
        gateway._running = False
        gateway._query_req = MagicMock()
        assert gateway.query_positions() is None


# ---------------------------------------------------------------------------
# Lifecycle (start / stop) with mocked zmq
# ---------------------------------------------------------------------------


class TestLifecycle:
    @patch("src.infrastructure.gateway.gateway.zmq.Context")
    def test_start_creates_context_and_sockets(self, mock_ctx_cls, logger):
        mock_ctx = MagicMock()
        mock_socket = MagicMock()
        mock_ctx.socket.return_value = mock_socket
        mock_ctx_cls.return_value = mock_ctx

        gw = TradingGateway(logger=logger, config=GatewayConfig(platform_connects=True))
        gw.start()
        assert gw._running is True
        assert len(gw._threads) == 4
        mock_ctx_cls.assert_called_once()
        assert mock_ctx.socket.call_count >= 4  # SUB, PUSH, REP, SUB

        gw.stop()
        assert gw._running is False
        assert gw._context is None
        assert len(gw._threads) == 0

    @patch("src.infrastructure.gateway.gateway.zmq.Context")
    def test_start_when_already_running_warns(self, mock_ctx_cls, logger):
        gw = TradingGateway(logger=logger)
        gw._running = True
        gw.start()
        assert any("already running" in m.lower() for m in logger.messages)
        mock_ctx_cls.assert_not_called()

    def test_stop_when_not_running_is_noop(self, gateway):
        gateway.stop()
        assert gateway._running is False

    @patch("src.infrastructure.gateway.gateway.zmq.Context")
    def test_stop_clears_all_state(self, mock_ctx_cls, logger):
        mock_ctx = MagicMock()
        mock_socket = MagicMock()
        mock_ctx.socket.return_value = mock_socket
        mock_ctx_cls.return_value = mock_ctx

        gw = TradingGateway(logger=logger, config=GatewayConfig(platform_connects=True))
        gw.start()
        gw._pending_commands[1] = {"type": "test", "sent_time": time.time()}
        gw._command_retries[1] = 1
        gw._command_queue.append(MagicMock())
        gw._seq_num = 5
        gw._platform_connected = True
        gw._platform_info = {"foo": "bar"}

        gw.stop()
        assert gw._running is False
        assert gw._context is None
        assert len(gw._threads) == 0
        assert len(gw._pending_commands) == 0
        assert len(gw._command_retries) == 0
        assert len(gw._command_queue) == 0
        assert gw._seq_num == 0
        assert gw._platform_connected is False
        assert gw._platform_info is None

    @patch("src.infrastructure.gateway.gateway.zmq.Context")
    def test_setup_python_connects_creates_req_socket(self, mock_ctx_cls, logger):
        mock_ctx = MagicMock()
        mock_socket = MagicMock()
        mock_ctx.socket.return_value = mock_socket
        mock_ctx_cls.return_value = mock_ctx

        config = GatewayConfig(platform_connects=False)
        gw = TradingGateway(logger=logger, config=config)
        gw.start()
        assert gw._query_req is not None
        assert gw._query_rep is None
        gw.stop()

    @patch("src.infrastructure.gateway.gateway.zmq.Context")
    def test_setup_python_binds_creates_rep_socket(self, mock_ctx_cls, logger):
        mock_ctx = MagicMock()
        mock_socket = MagicMock()
        mock_ctx.socket.return_value = mock_socket
        mock_ctx_cls.return_value = mock_ctx

        config = GatewayConfig(platform_connects=True)
        gw = TradingGateway(logger=logger, config=config)
        gw.start()
        assert gw._query_rep is not None
        assert gw._query_req is None
        gw.stop()


# ---------------------------------------------------------------------------
# _refresh_account_names
# ---------------------------------------------------------------------------


class TestRefreshAccountNames:
    def test_success(self, gateway):
        with patch("src.infrastructure.repositories.accounts_repository.NtAccountRepository") as MockRepo:
            mock_repo = MagicMock()
            a1 = MagicMock()
            a1.name = "Sim101"
            a2 = MagicMock()
            a2.name = "Sim102"
            mock_repo.list_accounts.return_value = [a1, a2]
            MockRepo.return_value = mock_repo
            gateway._refresh_account_names()
            assert gateway._account_names == ["Sim101", "Sim102"]

    def test_failure_logs_warning(self, gateway, logger):
        with patch("src.infrastructure.repositories.accounts_repository.NtAccountRepository") as MockRepo:
            MockRepo.side_effect = Exception("DB error")
            gateway._refresh_account_names()
            assert any("Failed to refresh" in m for m in logger.messages)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_is_running(self, gateway):
        assert gateway.is_running is False
        gateway._running = True
        assert gateway.is_running is True

    def test_is_connected(self, gateway):
        assert gateway.is_connected is False
        gateway._running = True
        gateway._platform_connected = True
        assert gateway.is_connected is True

    def test_platform_info(self, gateway):
        assert gateway.platform_info is None
        gateway._platform_info = {"v": "1"}
        assert gateway.platform_info == {"v": "1"}


# ---------------------------------------------------------------------------
# Heartbeat listener resilience
# ---------------------------------------------------------------------------


class TestHeartbeatListenerResilience:
    def test_heartbeat_loop_listener_exception_ignored(self, gateway):
        gateway._running = True
        gateway._last_heartbeat_time = time.time() - 100
        gateway._platform_connected = True
        gateway.on_connection_change(lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
        def _sleep_and_stop(_duration):
            gateway._running = False

        with patch("src.infrastructure.gateway.gateway.time.sleep", side_effect=_sleep_and_stop):
            gateway._heartbeat_loop()
        assert gateway._platform_connected is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
