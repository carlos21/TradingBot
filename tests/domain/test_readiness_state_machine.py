"""Unit tests for ReadinessStateMachine.

These tests exercise every explicit transition, no-op guards, retry counting,
observer notification, and event-publisher emission in isolation.
"""

from __future__ import annotations

import pytest

from src.domain.readiness import ReadinessState, ReadinessStateMachine
from tests.fakes import DummySocketIO, FakeLogger


class _FakeObserver:
    def __init__(self):
        self.notifications: list[tuple[ReadinessState, ReadinessState, str]] = []

    def on_readiness_changed(self, new_state, previous_state, reason):
        self.notifications.append((new_state, previous_state, reason))


class _RaisingObserver:
    def on_readiness_changed(self, _new_state, _previous_state, _reason):
        raise RuntimeError("observer boom")


@pytest.fixture
def sm():
    return ReadinessStateMachine()


class TestInitialState:
    def test_initial_state_is_disconnected(self, sm: ReadinessStateMachine) -> None:
        assert sm.state == ReadinessState.DISCONNECTED
        assert sm.reason == "Initializing"
        assert sm.is_ready() is False


class TestValidTransitions:
    def test_connect(self, sm: ReadinessStateMachine) -> None:
        assert sm.connect() is True
        assert sm.state == ReadinessState.CONNECTED
        assert sm.reason == "Platform connected"

    def test_start_refresh(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        assert sm.start_refresh() is True
        assert sm.state == ReadinessState.REFRESHING

    def test_history_empty(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        assert sm.history_empty() is True
        assert sm.state == ReadinessState.WAITING_FOR_HISTORY
        assert sm.retry_count == 1

    def test_history_retry_scheduled(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_empty()
        assert sm.history_retry_scheduled() is True
        assert sm.state == ReadinessState.WAITING_FOR_HISTORY
        assert "attempt 1" in sm.reason

    def test_history_loaded(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_empty()
        assert sm.history_loaded() is True
        assert sm.state == ReadinessState.WARMING_UP
        assert sm.retry_count == 0

    def test_warmup_complete(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_loaded()
        assert sm.warmup_complete() is True
        assert sm.state == ReadinessState.READY
        assert sm.is_ready() is True

    def test_live_bar_received(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_loaded()
        sm.warmup_complete()
        assert sm.live_bar_received() is True
        assert sm.state == ReadinessState.LIVE
        assert sm.is_ready() is True

    def test_degrade_from_ready(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_loaded()
        sm.warmup_complete()
        assert sm.degrade("gap detected") is True
        assert sm.state == ReadinessState.DEGRADED
        assert sm.is_ready() is False

    def test_degrade_from_live(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_loaded()
        sm.warmup_complete()
        sm.live_bar_received()
        assert sm.degrade("stale bars") is True
        assert sm.state == ReadinessState.DEGRADED

    def test_recover(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_loaded()
        sm.warmup_complete()
        sm.degrade("gap detected")
        assert sm.recover() is True
        assert sm.state == ReadinessState.READY
        assert sm.is_ready() is True

    def test_disconnect(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        assert sm.disconnect() is True
        assert sm.state == ReadinessState.DISCONNECTED


class TestNoOpTransitions:
    def test_connect_when_already_connected(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        # Same state AND same reason -> no-op
        assert sm.connect() is False
        assert sm.state == ReadinessState.CONNECTED

    def test_history_empty_when_already_waiting_is_no_op(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_empty()
        # Same state AND same reason -> no-op
        assert sm.history_empty() is False
        assert sm.state == ReadinessState.WAITING_FOR_HISTORY

    def test_warmup_complete_when_already_ready_is_no_op(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_loaded()
        sm.warmup_complete()
        # Same state AND same reason -> no-op
        assert sm.warmup_complete() is False
        assert sm.state == ReadinessState.READY

    def test_live_bar_received_from_warming_up_is_no_op(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_loaded()
        assert sm.live_bar_received() is False
        assert sm.state == ReadinessState.WARMING_UP

    def test_degrade_from_non_ready_is_no_op(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        assert sm.degrade("anything") is False
        sm.history_loaded()
        assert sm.degrade("anything") is False

    def test_recover_from_non_degraded_is_no_op(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        assert sm.recover() is False
        sm.history_loaded()
        sm.warmup_complete()
        assert sm.recover() is False


class TestRetryCount:
    def test_retry_count_increments_on_empty_history(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_empty()
        assert sm.retry_count == 1
        sm.history_retry_scheduled()
        assert sm.retry_count == 1
        sm.history_empty()
        assert sm.retry_count == 2

    def test_retry_count_resets_on_history_loaded(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_empty()
        sm.history_empty()
        assert sm.retry_count == 2
        sm.history_loaded()
        assert sm.retry_count == 0

    def test_start_refresh_resets_retry_count(self, sm: ReadinessStateMachine) -> None:
        sm.connect()
        sm.history_empty()
        sm.history_empty()
        assert sm.retry_count == 2
        sm.start_refresh()
        assert sm.retry_count == 0


class TestObserverNotification:
    def test_observer_receives_notifications(self, sm: ReadinessStateMachine) -> None:
        observer = _FakeObserver()
        sm.add_observer(observer)
        sm.connect()
        sm.history_loaded()
        sm.warmup_complete()

        assert len(observer.notifications) == 3
        new, prev, reason = observer.notifications[0]
        assert new == ReadinessState.CONNECTED
        assert prev == ReadinessState.DISCONNECTED
        assert reason == "Platform connected"

    def test_removed_observer_does_not_receive_notifications(self, sm: ReadinessStateMachine) -> None:
        observer = _FakeObserver()
        sm.add_observer(observer)
        sm.remove_observer(observer)
        sm.connect()
        assert len(observer.notifications) == 0

    def test_observer_exception_does_not_break_machine(self, sm: ReadinessStateMachine) -> None:
        bad = _RaisingObserver()
        good = _FakeObserver()
        sm.add_observer(bad)
        sm.add_observer(good)
        sm.connect()
        assert sm.state == ReadinessState.CONNECTED
        assert len(good.notifications) == 1


class TestEventPublisher:
    def test_readiness_changed_event_emitted(self) -> None:
        publisher = DummySocketIO()
        sm = ReadinessStateMachine(event_publisher=publisher)
        sm.connect()

        events = [e for e, _ in publisher.events]
        assert "readiness_changed" in events
        payload = next(p for e, p in publisher.events if e == "readiness_changed")
        assert payload["previous_state"] == "DISCONNECTED"
        assert payload["state"] == "CONNECTED"
        assert payload["reason"] == "Platform connected"

    def test_publisher_exception_does_not_break_machine(self) -> None:
        class _BadPublisher:
            def emit(self, _event, _payload):
                raise RuntimeError("publish boom")

        sm = ReadinessStateMachine(event_publisher=_BadPublisher())
        sm.connect()
        assert sm.state == ReadinessState.CONNECTED


class TestLogger:
    def test_logger_is_used(self) -> None:
        logger = FakeLogger()
        sm = ReadinessStateMachine(logger=logger)
        # FakeLogger is no-op, but we exercise the path without error.
        sm.connect()
        assert sm.state == ReadinessState.CONNECTED

    def test_logger_exception_does_not_break_machine(self) -> None:
        class _BadLogger:
            def info(self, _msg):
                raise RuntimeError("log boom")

        sm = ReadinessStateMachine(logger=_BadLogger())
        sm.connect()
        assert sm.state == ReadinessState.CONNECTED
