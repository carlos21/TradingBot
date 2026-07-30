"""Tests for the SubscriptionSupervisor."""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from src.infrastructure.gateway.subscription_supervisor import SubscriptionSupervisor
from tests.fakes import FakeLogger


@dataclass
class _Instrument:
    symbol: str
    full_name: str


class FakeTransport:
    def __init__(self):
        self.subscribed: list[str] = []
        self.refreshed: list[tuple[int, str]] = []

    def subscribe(self, instrument: str) -> None:
        self.subscribed.append(instrument)

    def refresh(self, days: int, instrument: str) -> None:
        self.refreshed.append((days, instrument))


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


@pytest.fixture
def transport():
    return FakeTransport()


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def make_supervisor(transport, notifier):
    """Factory with tiny delays; every supervisor is stopped on teardown."""
    supervisors = []

    def _make(retries: list | None = None, **kwargs) -> SubscriptionSupervisor:
        kwargs.setdefault("base_delay_sec", 0.02)
        kwargs.setdefault("max_delay_sec", 0.04)
        sup = SubscriptionSupervisor(
            transport=transport,
            notifier=notifier,
            on_retry=(lambda inst, att: retries.append((inst, att))) if retries is not None else None,
            logger=FakeLogger(),
            days=2,
            **kwargs,
        )
        supervisors.append(sup)
        return sup

    yield _make
    for sup in supervisors:
        sup.stop()


def _wait_for(predicate, timeout: float = 2.0, description: str = "condition") -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError(f"Timed out ({timeout}s) waiting for {description}")


MNQ = _Instrument(symbol="MNQ", full_name="MNQ 09-26")
MES = _Instrument(symbol="MES", full_name="MES 09-26")


class TestTrackAndRetry:
    def test_track_is_idempotent(self, make_supervisor):
        sup = make_supervisor()
        sup.track(MNQ)
        sup.track(MNQ)
        assert list(sup._awaiting) == ["MNQ 09-26"]
        assert sup._timer is not None

    def test_track_without_full_name_is_ignored(self, make_supervisor):
        sup = make_supervisor()
        sup.track(_Instrument(symbol="MNQ", full_name=""))
        assert sup._awaiting == {}
        assert sup._timer is None

    def test_retry_resends_subscribe_and_refresh(self, make_supervisor, transport):
        sup = make_supervisor()
        sup.track(MNQ)
        _wait_for(lambda: len(transport.subscribed) >= 1, description="first retry")
        assert transport.subscribed == ["MNQ 09-26"]
        assert transport.refreshed == [(2, "MNQ 09-26")]

    def test_retries_only_awaiting_instruments(self, make_supervisor, transport):
        sup = make_supervisor()
        sup.track(MNQ)
        sup.track(MES)
        _wait_for(lambda: len(transport.subscribed) >= 2, description="first cycle")

        sup.mark_responded("MNQ 09-26")
        transport.subscribed.clear()
        transport.refreshed.clear()

        _wait_for(lambda: len(transport.subscribed) >= 1, description="next cycle")
        time.sleep(0.06)  # a couple more cycles
        assert set(transport.subscribed) == {"MES 09-26"}
        assert all(name == "MES 09-26" for _days, name in transport.refreshed)

    def test_backoff_progression_capped(self, make_supervisor, transport):
        sup = make_supervisor(base_delay_sec=0.02, max_delay_sec=0.04)
        sup.track(MNQ)
        assert sup._timer.interval == pytest.approx(0.02)

        _wait_for(lambda: len(transport.subscribed) >= 1, description="first retry")
        assert sup._timer.interval == pytest.approx(0.04)

        _wait_for(lambda: len(transport.subscribed) >= 2, description="second retry")
        # base * 2**2 = 0.08 would exceed the cap
        assert sup._timer.interval == pytest.approx(0.04)

    def test_on_retry_callback_invoked_with_attempt(self, make_supervisor):
        retries: list = []
        sup = make_supervisor(retries=retries)
        sup.track(MNQ)
        _wait_for(lambda: len(retries) >= 2, description="two retries")
        assert retries[0] == (MNQ, 1)
        assert retries[1] == (MNQ, 2)


class TestMarkResponded:
    def test_mark_responded_stops_that_instruments_retries(self, make_supervisor, transport):
        sup = make_supervisor()
        sup.track(MNQ)
        sup.mark_responded("MNQ 09-26")
        assert sup._awaiting == {}
        time.sleep(0.06)
        assert transport.subscribed == []

    def test_mark_responded_on_last_instrument_cancels_timer(self, make_supervisor):
        sup = make_supervisor()
        sup.track(MNQ)
        sup.track(MES)
        sup.mark_responded("MNQ 09-26")
        assert sup._timer is not None
        sup.mark_responded("MES 09-26")
        assert sup._timer is None

    def test_mark_responded_unknown_name_is_noop(self, make_supervisor):
        sup = make_supervisor()
        sup.track(MNQ)
        sup.mark_responded("ES 09-26")
        assert list(sup._awaiting) == ["MNQ 09-26"]
        assert sup._timer is not None


class TestStop:
    def test_stop_cancels_permanently(self, make_supervisor, transport):
        sup = make_supervisor()
        sup.track(MNQ)
        sup.stop()
        assert sup._timer is None
        assert sup._awaiting == {}
        time.sleep(0.08)  # several base delays
        assert transport.subscribed == []

    def test_timer_does_not_resurrect_after_stop(self, make_supervisor, transport):
        sup = make_supervisor()
        sup.track(MNQ)
        _wait_for(lambda: len(transport.subscribed) >= 1, description="first retry")
        sup.stop()
        sent_at_stop = len(transport.subscribed)
        time.sleep(0.1)  # longer than the capped delay
        assert len(transport.subscribed) == sent_at_stop
        assert sup._timer is None

    def test_track_after_stop_starts_fresh_episode(self, make_supervisor, transport):
        sup = make_supervisor()
        sup.track(MNQ)
        _wait_for(lambda: len(transport.subscribed) >= 1, description="first retry")
        sup.stop()

        sup.track(MNQ)
        assert sup._attempt == 0
        assert sup._timer is not None
        _wait_for(lambda: len(transport.subscribed) >= 2, description="fresh retry")


class TestEscalation:
    def test_alert_sent_once_at_configured_attempt(self, make_supervisor, transport, notifier):
        sup = make_supervisor(escalate_after=2)
        sup.track(MNQ)
        _wait_for(lambda: len(transport.subscribed) >= 3, description="three retries")
        assert len(notifier.messages) == 1
        assert "not responding to commands" in notifier.messages[0]
        assert "(attempt 2)" in notifier.messages[0]

        # Keeps retrying at the capped delay without re-alerting.
        _wait_for(lambda: len(transport.subscribed) >= 4, description="fourth retry")
        assert len(notifier.messages) == 1


class TestFailFast:
    def test_fail_fast_triggers_immediate_cycle(self, make_supervisor, transport):
        sup = make_supervisor(base_delay_sec=60.0)
        sup.track(MNQ)
        sup.fail_fast("MNQ 09-26")
        assert transport.subscribed == ["MNQ 09-26"]
        assert transport.refreshed == [(2, "MNQ 09-26")]
        # The backoff timer is re-armed for the next cycle.
        assert sup._timer is not None

    def test_fail_fast_untracked_instrument_is_noop(self, make_supervisor, transport):
        sup = make_supervisor(base_delay_sec=60.0)
        sup.track(MNQ)
        sup.fail_fast("MES 09-26")
        assert transport.subscribed == []

    def test_fail_fast_after_stop_is_noop(self, make_supervisor, transport):
        sup = make_supervisor()
        sup.track(MNQ)
        sup.stop()
        sup.fail_fast("MNQ 09-26")
        assert transport.subscribed == []

    def test_fail_fast_coalesces_rapid_calls(self, make_supervisor, transport):
        sup = make_supervisor(
            base_delay_sec=60.0, fail_fast_cooldown_sec=0.05
        )
        sup.track(MNQ)
        for _ in range(20):
            sup.fail_fast("MNQ 09-26")
        # Only the first call should have run a retry cycle.
        assert len(transport.subscribed) == 1
        assert len(transport.refreshed) == 1

        # After the cooldown expires a new call runs a second cycle.
        time.sleep(0.06)
        sup.fail_fast("MNQ 09-26")
        assert len(transport.subscribed) == 2
        assert len(transport.refreshed) == 2

    def test_fail_fast_retries_all_awaiting_instruments(self, make_supervisor, transport):
        sup = make_supervisor(base_delay_sec=60.0)
        sup.track(MNQ)
        sup.track(MES)
        transport.subscribed.clear()
        transport.refreshed.clear()
        sup.fail_fast("MNQ 09-26")
        assert set(transport.subscribed) == {"MNQ 09-26", "MES 09-26"}
        assert set(name for _days, name in transport.refreshed) == {
            "MNQ 09-26",
            "MES 09-26",
        }
