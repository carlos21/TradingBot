# Testable Design Patterns

Strategies for designing code that is easy to test without heavy mocking frameworks.

## Table of Contents
1. Test Pyramid Strategy
2. Fake Implementations
3. Fixture Patterns
4. Avoiding Test-Induced Design Damage
5. Testing Asynchronous Code

## Test Pyramid Strategy

Design your code so the majority of tests are fast unit tests:

```
    /\
   /  \     E2E tests (slow, few)
  /----\
 /      \  Integration tests (medium speed)
/--------\\
\\\\\\\\\\\ Unit tests (fast, many)
```

Unit tests exercise domain and application logic with fakes. They should run in milliseconds.

## Fake Implementations

Build lightweight fakes that implement your protocols. They are simpler and more robust than mocking frameworks:

```python
# tests/fakes.py
from decimal import Decimal
from domain.protocols import PriceFeed, OrderExecutor
from domain.models import ExecutionResult, Order

class FakePriceFeed:
    def __init__(self, prices: dict[str, Decimal] | None = None):
        self._prices = prices or {}
        self.calls: list[str] = []

    def set_price(self, symbol: str, price: Decimal) -> None:
        self._prices[symbol] = price

    def get_current_price(self, symbol: str) -> Decimal:
        self.calls.append(symbol)
        if symbol not in self._prices:
            raise KeyError(f"No price for {symbol}")
        return self._prices[symbol]

class FakeOrderExecutor:
    def __init__(self):
        self.orders: list[Order] = []
        self.results: dict[int, ExecutionResult] = {}

    def submit_order(self, order: Order) -> ExecutionResult:
        self.orders.append(order)
        return self.results.get(len(self.orders), ExecutionResult(success=True))
```

**Why fakes over mocks**:
- Type-safe: they must satisfy the protocol
- Refactoring-safe: renaming a method breaks the fake, not a string in a mock spec
- Readable: behavior is explicit in the fake class, not hidden in test setup
- Reusable: shared across many tests

## Fixture Patterns

Use pytest fixtures to inject fakes. Keep fixtures close to the test module:

```python
# tests/unit/test_trading_strategy.py
import pytest
from tests.fakes import FakePriceFeed, FakeRiskEvaluator
from domain.trading import TradingStrategy

@pytest.fixture
def strategy():
    return TradingStrategy(
        price_feed=FakePriceFeed(),
        risk_evaluator=FakeRiskEvaluator(),
    )

def test_generates_signal_when_conditions_met(strategy):
    strategy._price_feed.set_price("AAPL", Decimal("150.00"))
    signal = strategy.generate_signal("AAPL", Portfolio.empty())
    assert signal is not None
```

For shared fixtures, use `conftest.py` at the test directory level.

## Avoiding Test-Induced Design Damage

Do not add production code solely for tests. Instead, design production code that is naturally testable:

**Bad** — production code altered for testability:
```python
class TradeService:
    def __init__(self, executor: OrderExecutor | None = None):
        self._executor = executor or AlpacaExecutor()  # Production default leaked in
```

**Good** — clean separation:
```python
class TradeService:
    def __init__(self, executor: OrderExecutor):
        self._executor = executor

# Composition root handles defaults
service = TradeService(executor=AlpacaExecutor(...))
# Tests pass fake
trade_service = TradeService(executor=FakeOrderExecutor())
```

## Testing Asynchronous Code

For async protocols, make the protocol async and use async fakes:

```python
from typing import Protocol
import asyncio

class PriceFeed(Protocol):
    async def get_current_price(self, symbol: str) -> Decimal: ...

class FakePriceFeed:
    def __init__(self, prices: dict[str, Decimal]):
        self._prices = prices

    async def get_current_price(self, symbol: str) -> Decimal:
        await asyncio.sleep(0)  # Yield control like real async code
        return self._prices[symbol]
```

Test with `pytest.mark.asyncio`:

```python
import pytest

@pytest.mark.asyncio
async def test_async_strategy():
    feed = FakePriceFeed({"AAPL": Decimal("150.00")})
    strategy = AsyncTradingStrategy(price_feed=feed)
    signal = await strategy.generate_signal("AAPL")
    assert signal is not None
```

## Testing with Time

Never call `datetime.now()` directly in domain code. Inject a `Clock` protocol:

```python
from datetime import datetime
from typing import Protocol

class Clock(Protocol):
    def now(self) -> datetime: ...

class SystemClock:
    def now(self) -> datetime:
        return datetime.now()

class FixedClock:
    def __init__(self, fixed_time: datetime):
        self._fixed = fixed_time
    def now(self) -> datetime:
        return self._fixed
```

Tests can freeze time without monkeypatching:

```python
def test_order_expires_after_one_day():
    clock = FixedClock(datetime(2024, 1, 1, 12, 0))
    order = Order(created_at=clock.now(), clock=clock)
    
    clock._fixed = datetime(2024, 1, 2, 12, 1)  # One day + 1 minute later
    assert order.is_expired()
```
