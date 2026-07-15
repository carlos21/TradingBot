"""Tests for src.application.ports protocol definitions."""
from typing import Any

import pytest

from src.application.ports import (
    EventPublisher,
    IWarmupProgressListener,
    IReadinessProgressEmitter,
    Notifier,
    PlatformLifecycleService,
)


class FakeLifecycle(PlatformLifecycleService):
    def validate_before_start(self, data_source) -> tuple[bool, str | None]:
        return True, None

    def maybe_launch_after_delay(self, data_source) -> None:
        return None

    def has_accounts_configured(self) -> bool:
        return True


class FakeNotifier(Notifier):
    def __init__(self):
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


class FakeSocketIO(EventPublisher):
    def __init__(self):
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event: str, data: dict[str, Any], **kwargs: Any) -> None:
        self.events.append((event, data))


class FakeWarmupListener(IWarmupProgressListener):
    def __init__(self):
        self.updates: list[tuple[int, int]] = []

    def on_warmup_progress(self, current: int, total: int) -> None:
        self.updates.append((current, total))


class FakeReadinessEmitter(IReadinessProgressEmitter):
    def __init__(self):
        self.progress: list[tuple[int, int]] = []
        self.phases: list[tuple[str, str]] = []

    def emit_warmup_progress(self, current: int, total: int) -> None:
        self.progress.append((current, total))

    def emit_phase_started(self, phase: str, reason: str) -> None:
        self.phases.append((phase, reason))


class TestPorts:
    def test_platform_lifecycle_methods(self):
        fake = FakeLifecycle()
        assert fake.validate_before_start(None) == (True, None)
        assert fake.maybe_launch_after_delay(None) is None
        assert fake.has_accounts_configured() is True

    def test_event_publisher(self):
        pub = FakeSocketIO()
        pub.emit("test_event", {"foo": "bar"})
        assert pub.events == [("test_event", {"foo": "bar"})]

    def test_notifier(self):
        notifier = FakeNotifier()
        notifier.send("hello")
        assert notifier.messages == ["hello"]

    def test_warmup_progress_listener(self):
        listener = FakeWarmupListener()
        listener.on_warmup_progress(5, 10)
        assert listener.updates == [(5, 10)]

    def test_readiness_progress_emitter(self):
        emitter = FakeReadinessEmitter()
        emitter.emit_warmup_progress(1, 100)
        emitter.emit_phase_started("warmup", "starting")
        assert emitter.progress == [(1, 100)]
        assert emitter.phases == [("warmup", "starting")]
