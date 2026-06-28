"""Bridge between WarmupOrchestrator and readiness progress infrastructure."""

from __future__ import annotations

from src.application.ports import IReadinessProgressEmitter, IWarmupProgressListener


class WarmupProgressReporter(IWarmupProgressListener):
    """Forwards warmup replay progress to a readiness progress emitter.

    This keeps WarmupOrchestrator free of messaging concerns while ensuring
    progress events reach the UI/metrics layer through a small port.
    """

    def __init__(
        self,
        emitter: IReadinessProgressEmitter,
    ) -> None:
        self._emitter = emitter

    def on_warmup_progress(self, current: int, total: int) -> None:
        self._emitter.emit_warmup_progress(current, total)


class WarmupProgressMulticaster(IWarmupProgressListener):
    """Broadcasts warmup progress to multiple listeners.

    Useful when the application needs to update its own progress tracker
    while also forwarding events to infrastructure (e.g. SocketIO).
    """

    def __init__(self, listeners: list[IWarmupProgressListener]) -> None:
        self._listeners = list(listeners)

    def on_warmup_progress(self, current: int, total: int) -> None:
        for listener in self._listeners:
            listener.on_warmup_progress(current, total)
