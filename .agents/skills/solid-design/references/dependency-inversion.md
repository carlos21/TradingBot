# Dependency Inversion Patterns

Advanced patterns for applying Dependency Inversion in Python.

## Table of Contents
1. Constructor Injection
2. Factory Injection
3. Decorator / Middleware Injection
4. Context Provider Pattern
5. Composition Root

## Constructor Injection

The default and preferred pattern. All dependencies are explicit in the constructor.

```python
class OrderProcessor:
    def __init__(
        self,
        repository: OrderRepository,
        validator: OrderValidator,
        notifier: Notifier,
    ):
        self._repo = repository
        self._validator = validator
        self._notifier = notifier
```

### Optional Dependencies with Null Object

```python
class Notifier(Protocol):
    def send(self, message: str) -> None: ...

class NullNotifier:
    def send(self, message: str) -> None:
        pass

class OrderProcessor:
    def __init__(self, notifier: Notifier | None = None):
        self._notifier = notifier or NullNotifier()
```

## Factory Injection

When a dependency cannot be created at composition time (e.g., it needs runtime data), inject a factory:

```python
from typing import Callable

class ReportGenerator(Protocol):
    def generate(self, data: list[Row]) -> Report: ...

class ReportService:
    def __init__(
        self,
        report_factory: Callable[[str], ReportGenerator],  # Returns a generator for a given format
    ):
        self._factory = report_factory

    def create_report(self, format: str, data: list[Row]) -> Report:
        generator = self._factory(format)
        return generator.generate(data)
```

## Decorator / Middleware Injection

Add cross-cutting concerns (logging, caching, retry) by wrapping implementations:

```python
class LoggingExecutor:
    def __init__(self, wrapped: OrderExecutor, logger: Logger):
        self._wrapped = wrapped
        self._logger = logger

    def submit_order(self, order: Order) -> ExecutionResult:
        self._logger.info(f"Submitting order: {order}")
        try:
            result = self._wrapped.submit_order(order)
            self._logger.info(f"Order executed: {result}")
            return result
        except ExecutionError as e:
            self._logger.error(f"Order failed: {e}")
            raise
```

Wire at composition:

```python
executor = LoggingExecutor(
    wrapped=AlpacaExecutor(api_key),
    logger=StructuredLogger(),
)
```

## Context Provider Pattern

For request-scoped data (user context, trace IDs), inject a provider rather than the raw value:

```python
from typing import Protocol

class UserContextProvider(Protocol):
    def get_current_user(self) -> User: ...

class TradeService:
    def __init__(self, user_provider: UserContextProvider):
        self._user_provider = user_provider

    def place_order(self, order: Order) -> None:
        user = self._user_provider.get_current_user()
        if not user.can_trade(order.symbol):
            raise PermissionError()
        # ...
```

This avoids threading the `user` parameter through every method call while keeping testability:

```python
class FakeUserProvider:
    def __init__(self, user: User):
        self._user = user
    def get_current_user(self) -> User:
        return self._user
```

## Composition Root

A single location where the entire object graph is wired. For a web app, this is near the request handler or app factory:

```python
# app/factory.py
from domain import TradingStrategy
from infrastructure import AlpacaPriceFeed, SqliteRepository
from application import TradeService

class App:
    def __init__(self, config: Config):
        # Infrastructure
        price_feed = AlpacaPriceFeed(config.api_key)
        repo = SqliteRepository(config.db_path)
        
        # Domain
        strategy = TradingStrategy(price_feed=price_feed)
        
        # Application
        self.trade_service = TradeService(
            strategy=strategy,
            repository=repo,
        )
```

**Rule**: Only the composition root imports concrete infrastructure classes.
