"""Unit tests for LiveBarBuffer."""

from __future__ import annotations

import threading
import time

from src.application.live_readiness.live_bar_buffer import LiveBarBuffer


def _make_bar(t: int) -> dict:
    return {"time": t, "open": 1.0, "high": 2.0, "low": 0.0, "close": 1.0, "volume": 1, "pair": "MNQ"}


class TestLiveBarBuffer:
    def test_append_stores_bar(self) -> None:
        processed: list[dict] = []
        buf = LiveBarBuffer(processor=processed.append)
        bar = _make_bar(1)
        buf.append(bar)
        assert len(buf) == 1
        assert processed == []

    def test_flush_processes_in_order_and_clears(self) -> None:
        processed: list[dict] = []
        buf = LiveBarBuffer(processor=processed.append)
        bars = [_make_bar(1), _make_bar(2), _make_bar(3)]
        for bar in bars:
            buf.append(bar)
        assert len(buf) == 3

        buf.flush()
        assert processed == bars
        assert len(buf) == 0

    def test_clear_drops_buffered_bars(self) -> None:
        processed: list[dict] = []
        buf = LiveBarBuffer(processor=processed.append)
        buf.append(_make_bar(1))
        buf.clear()
        assert len(buf) == 0
        buf.flush()
        assert processed == []

    def test_flush_is_idempotent(self) -> None:
        processed: list[dict] = []
        buf = LiveBarBuffer(processor=processed.append)
        buf.append(_make_bar(1))
        buf.flush()
        buf.flush()
        assert len(processed) == 1
        assert len(buf) == 0

    def test_multiple_flushes_preserve_order(self) -> None:
        processed: list[dict] = []
        buf = LiveBarBuffer(processor=processed.append)
        buf.append(_make_bar(1))
        buf.flush()
        buf.append(_make_bar(2))
        buf.append(_make_bar(3))
        buf.flush()
        assert [b["time"] for b in processed] == [1, 2, 3]

    def test_concurrent_append_and_flush(self) -> None:
        processed: list[dict] = []
        buf = LiveBarBuffer(processor=processed.append)
        errors: list[Exception] = []

        def appender():
            try:
                for i in range(100):
                    buf.append(_make_bar(i))
                    time.sleep(0.0001)
            except Exception as e:
                errors.append(e)

        def flusher():
            try:
                for _ in range(50):
                    buf.flush()
                    time.sleep(0.0002)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=appender)
        t2 = threading.Thread(target=flusher)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        # Final flush to drain anything left
        buf.flush()
        # Every appended bar should have been processed exactly once
        assert len(processed) == 100
        assert len({b["time"] for b in processed}) == 100
