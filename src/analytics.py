from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class AnalyticsReporter(ABC):
    """Injectable interface for crash/event reporting."""

    @abstractmethod
    def capture_exception(self, exc: BaseException, context: Optional[Dict[str, Any]] = None) -> None:
        ...

    @abstractmethod
    def capture_trade_event(self, event_type: str, trade_data: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def capture_signal_event(self, event_type: str, details: Dict[str, Any]) -> None:
        ...

    @abstractmethod
    def set_context(self, name: str, data: Dict[str, Any]) -> None:
        ...


class NoOpReporter(AnalyticsReporter):
    def capture_exception(self, exc, context=None):
        pass

    def capture_trade_event(self, event_type, trade_data):
        pass

    def capture_signal_event(self, event_type, details):
        pass

    def set_context(self, name, data):
        pass


class SentryReporter(AnalyticsReporter):
    def __init__(self, dsn: str):
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.0,
            environment="live",
        )

    def capture_exception(self, exc, context=None):
        import sentry_sdk
        if context:
            sentry_sdk.set_context("error_context", context)
        sentry_sdk.capture_exception(exc)

    def capture_trade_event(self, event_type, trade_data):
        import sentry_sdk
        sentry_sdk.add_breadcrumb(
            category="trade",
            message=event_type,
            data=trade_data,
            level="info",
        )

    def capture_signal_event(self, event_type, details):
        import sentry_sdk
        sentry_sdk.add_breadcrumb(
            category="strategy",
            message=event_type,
            data=details,
            level="info",
        )

    def set_context(self, name, data):
        import sentry_sdk
        sentry_sdk.set_context(name, data)
