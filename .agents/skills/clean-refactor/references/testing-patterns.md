# Testing Patterns Reference

## Test Pyramid

```
       /\
      /  \
     / E2E \        <- Few tests, slow, expensive
    /--------\
   / Integration \   <- Medium tests, medium speed
  /--------------\
 /    Unit Tests   \ <- Many tests, fast, cheap
/--------------------\
```

Target: 70% unit, 20% integration, 10% E2E

## Testable Code Characteristics

1. **No global state** - All dependencies injected
2. **Pure functions** where possible - Same input → same output
3. **Side effects isolated** - I/O at boundaries
4. **Small units** - Test one thing at a time
5. **Deterministic** - No randomness, no time-based logic without injection

## Test Doubles

### Fake

Working implementation for testing.

```python
class FakeTradeRepository:
    """In-memory repository for testing."""
    def __init__(self):
        self._trades: dict[str, Trade] = {}
        self._next_id = 1
    
    def save(self, trade: Trade) -> None:
        self._trades[trade.id] = trade
    
    def get(self, trade_id: str) -> Trade | None:
        return self._trades.get(trade_id)
    
    def get_open_trades(self) -> list[Trade]:
        return [t for t in self._trades.values() if t.is_open]
```

### Stub

Returns canned responses.

```python
class StubPriceProvider:
    """Returns fixed prices for testing."""
    def __init__(self, prices: dict[str, Decimal]):
        self._prices = prices
    
    def get_current_price(self, symbol: str) -> Decimal:
        return self._prices.get(symbol, Decimal("0"))
```

### Spy

Records calls for verification.

```python
class SpyNotifier:
    """Records notifications for verification."""
    def __init__(self):
        self.notifications: list[tuple[str, str]] = []
    
    def send(self, channel: str, message: str) -> None:
        self.notifications.append((channel, message))
    
    def was_notified(self, channel: str, message: str) -> bool:
        return (channel, message) in self.notifications
```

### Mock

Pre-programmed with expectations.

```python
from unittest.mock import Mock, call

# Using unittest.mock
risk_engine = Mock()
risk_engine.can_trade.return_value = True
risk_engine.calculate_size.return_value = 2

use_case.execute(request)

risk_engine.can_trade.assert_called_once_with("NQ", 2)
```

## Test Structure: Arrange-Act-Assert

```python
def test_close_trade_calculates_pnl_correctly():
    # Arrange
    trade = create_trade(
        entry_price=Decimal("18000.00"),
        size=2,
        side=TradeSide.LONG
    )
    exit_price = Decimal("18050.00")
    
    # Act
    result = trade.close(exit_price, datetime.now())
    
    # Assert
    assert result.pnl == Money(Decimal("100.00"))  # 50 * 2
```

## Given-When-Then (BDD Style)

```python
def test_long_trade_is_profitable_when_price_rises():
    """Given a long trade, when price rises, then profit is positive."""
    # Given
    trade = Trade(
        symbol="NQ",
        side=TradeSide.LONG,
        entry_price=Decimal("18000.00"),
        size=1
    )
    
    # When
    result = trade.close(
        exit_price=Decimal("18100.00"),
        exit_time=datetime.now()
    )
    
    # Then
    assert result.pnl.amount > 0
```

## Parameterized Tests

```python
import pytest

@pytest.mark.parametrize("entry,exit,size,expected", [
    (Decimal("100"), Decimal("110"), 1, Decimal("10")),
    (Decimal("100"), Decimal("90"), 1, Decimal("-10")),
    (Decimal("100"), Decimal("110"), 2, Decimal("20")),
])
def test_pnl_calculation(entry, exit_price, size, expected):
    trade = Trade(
        symbol="TEST",
        side=TradeSide.LONG,
        entry_price=entry,
        size=size
    )
    result = trade.close(exit_price, datetime.now())
    assert result.pnl.amount == expected
```

## Testing Async Code

```python
import pytest
import asyncio

@pytest.mark.asyncio
async def test_async_data_fetch():
    fetcher = FakeAsyncDataFetcher()
    fetcher.set_response([Bar(...), Bar(...)])
    
    bars = await fetcher.fetch("NQ")
    
    assert len(bars) == 2

# Or use pytest-asyncio
```

## Testing with Time

Never use `datetime.now()` directly - inject a clock.

```python
from typing import Protocol
from datetime import datetime

class Clock(Protocol):
    def now(self) -> datetime: ...

class SystemClock:
    def now(self) -> datetime:
        return datetime.now()

class FixedClock:
    def __init__(self, fixed_time: datetime):
        self._time = fixed_time
    
    def now(self) -> datetime:
        return self._time
    
    def advance(self, seconds: int) -> None:
        self._time += timedelta(seconds=seconds)

# Usage in production
trade_manager = TradeManager(clock=SystemClock())

# Usage in tests
clock = FixedClock(datetime(2024, 1, 1, 10, 0, 0))
trade_manager = TradeManager(clock=clock)
clock.advance(60)  # Move time forward
trade_manager.check_expirations()
```

## Testing Strategies

### Property-Based Testing

```python
from hypothesis import given, strategies as st

@given(
    entry=st.decimals(min_value=1, max_value=100000),
    exit_price=st.decimals(min_value=1, max_value=100000),
    size=st.integers(min_value=1, max_value=100)
)
def test_pnl_properties(entry, exit_price, size):
    """Property: P&L magnitude increases with size."""
    trade = Trade(
        symbol="TEST",
        side=TradeSide.LONG,
        entry_price=entry,
        size=size
    )
    result = trade.close(exit_price, datetime.now())
    
    # Property: P&L scales linearly with size
    assert result.pnl.amount == (exit_price - entry) * size
```

### Mutation Testing

Check test quality by introducing bugs:

```bash
pip install mutmut
mutmut run --paths-to-mutate=src/domain
mutmut results
```

## Test Coverage Guidelines

### What to Test (A-TRIP)

- **A**utomated: Tests run without human intervention
- **T**horough: Cover happy path, edge cases, error conditions
- **R**epeatable: Same input → same output
- **I**ndependent: Tests don't depend on each other
- **P**rofessional: Tests are clean code too

### Coverage Targets by Layer

| Layer | Target | Notes |
|-------|--------|-------|
| Domain | 95%+ | Pure logic, easy to test |
| Application | 85%+ | Use fakes for dependencies |
| Infrastructure | 70%+ | Integration tests |
| Presentation | 60%+ | Controller tests, E2E |

### Measuring Coverage

```bash
# pytest with coverage
pytest --cov=src --cov-report=html --cov-report=term-missing

# View report
open htmlcov/index.html
```

## Common Testing Mistakes

### 1. Testing Implementation Details

```python
# WRONG: Testing internal state
def test_counter_increments():
    counter = Counter()
    counter.increment()
    assert counter._count == 1  # Testing private state!

# CORRECT: Testing behavior
def test_counter_returns_incremented_value():
    counter = Counter()
    result = counter.increment()
    assert result == 1
```

### 2. Brittle Tests

```python
# WRONG: Exact string matching
def test_error_message():
    with pytest.raises(ValueError) as exc:
        trade.close(-100)
    assert str(exc.value) == "Price cannot be negative: -100"  # Brittle!

# CORRECT: Meaningful assertions
def test_error_includes_price_info():
    with pytest.raises(ValueError) as exc:
        trade.close(-100)
    assert "-100" in str(exc.value)  # Flexible
```

### 3. Tests that Pass Together, Fail Alone

```python
# WRONG: Shared mutable state
class TestTradeManager:
    def setup_method(self):
        self.manager = TradeManager()  # Shared state
    
    def test_first(self):
        self.manager.open_trade(trade1)
        # ... relies on manager state
    
    def test_second(self):
        # Fails if run alone - depends on test_first!
        assert len(self.manager.trades) == 1

# CORRECT: Clean state per test
def test_trade_manager():
    manager = TradeManager()
    manager.open_trade(create_trade())
    assert len(manager.get_open_trades()) == 1
```

### 4. Testing Multiple Things

```python
# WRONG: Multiple assertions, multiple concepts
def test_trade():
    trade = Trade(...)
    assert trade.symbol == "NQ"
    assert trade.size == 2
    result = trade.close(100, now())
    assert result.pnl > 0
    assert trade.status == TradeStatus.CLOSED

# CORRECT: Separate tests
def test_trade_has_correct_symbol():
    trade = Trade(symbol="NQ", ...)
    assert trade.symbol == "NQ"

def test_trade_closes_with_correct_pnl():
    trade = create_long_trade(entry=100)
    result = trade.close(110, now())
    assert result.pnl == Money(10)
```

## Test Organization

```
tests/
├── unit/                      # Fast, isolated tests
│   ├── domain/
│   │   ├── test_trade.py
│   │   └── test_money.py
│   └── application/
│       └── test_open_trade.py
├── integration/               # Tests with real dependencies
│   ├── test_sqlite_repository.py
│   └── test_nt_api.py
├── e2e/                       # Full flow tests
│   └── test_trading_scenario.py
├── fakes.py                   # Reusable fakes
├── fixtures.py                # pytest fixtures
└── conftest.py               # Shared pytest config
```

## Fixtures Pattern

```python
# conftest.py
import pytest
from tests.fakes import FakeTradeRepository, FakeClock

@pytest.fixture
def trade_repo():
    return FakeTradeRepository()

@pytest.fixture
def clock():
    return FakeClock(datetime(2024, 1, 1, 10, 0, 0))

@pytest.fixture
def trade_manager(trade_repo, clock):
    return TradeManager(
        repository=trade_repo,
        clock=clock
    )

# Usage
def test_manager_opens_trade(trade_manager, trade_repo):
    trade_manager.open_trade(create_trade())
    assert len(trade_repo.get_open_trades()) == 1
```
