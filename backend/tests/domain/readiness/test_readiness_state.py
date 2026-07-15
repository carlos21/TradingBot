"""Tests for src.domain.readiness.readiness_state."""
from src.domain.readiness.readiness_state import ReadinessState


class TestReadinessState:
    def test_states_are_distinct(self):
        states = list(ReadinessState)
        assert len(states) == len(set(states))

    def test_expected_states_exist(self):
        assert ReadinessState.DISCONNECTED
        assert ReadinessState.CONNECTED
        assert ReadinessState.WAITING_FOR_HISTORY
        assert ReadinessState.REFRESHING
        assert ReadinessState.WARMING_UP
        assert ReadinessState.READY
        assert ReadinessState.LIVE
        assert ReadinessState.DEGRADED

    def test_state_name(self):
        assert ReadinessState.READY.name == "READY"
        assert ReadinessState.LIVE.name == "LIVE"
