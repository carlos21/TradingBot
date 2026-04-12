# SOLID Principles Reference

## Single Responsibility Principle (SRP)

**Definition:** A class should have only one reason to change.

### Signs of Violation
- Class has "and" in its name: `TradeFetcherAndProcessor`
- Multiple imports from different domains
- Methods that don't use the same instance variables
- Large files (>200 lines is a smell)

### Refactoring Steps

```python
# Before: Multiple responsibilities
class TradeManager:
    def fetch_data(self, symbol: str): ...        # Data fetching
    def calculate_indicators(self, bars): ...     # Analysis
    def execute_trade(self, trade): ...           # Execution
    def log_to_database(self, trade): ...         # Persistence
    def send_notification(self, trade): ...       # Notification

# After: Separated concerns
class DataFetcher:
    def fetch(self, symbol: str) -> list[Bar]: ...

class IndicatorCalculator:
    def calculate(self, bars: list[Bar]) -> Signals: ...

class TradeExecutor:
    def execute(self, trade: Trade) -> Result: ...

class TradeRepository:
    def save(self, trade: Trade) -> None: ...

class Notifier:
    def notify(self, trade: Trade) -> None: ...

# Orchestrator with single responsibility: coordinate workflow
class TradeService:
    def __init__(
        self,
        fetcher: DataFetcher,
        calculator: IndicatorCalculator,
        executor: TradeExecutor,
        repository: TradeRepository,
        notifier: Notifier
    ): ...
```

## Open/Closed Principle (OCP)

**Definition:** Software entities should be open for extension, closed for modification.

### Signs of Violation
- Adding a new feature requires modifying existing code
- Long if/elif chains checking types
- `switch` statements with cases growing over time

### Refactoring Steps

```python
# Before: Modify to add strategies
class Strategy:
    def execute(self, bar):
        if self.type == "mean_reversion":
            return self._mean_reversion(bar)
        elif self.type == "momentum":
            return self._momentum(bar)
        elif self.type == "breakout":  # New type = modify class
            return self._breakout(bar)

# After: Extend via inheritance
from abc import ABC, abstractmethod

class TradingStrategy(ABC):
    @abstractmethod
    def execute(self, bar: Bar) -> Signal: ...

class MeanReversionStrategy(TradingStrategy):
    def execute(self, bar: Bar) -> Signal: ...

class MomentumStrategy(TradingStrategy):
    def execute(self, bar: Bar) -> Signal: ...

# New strategy = new class, no existing code modified
class BreakoutStrategy(TradingStrategy):
    def execute(self, bar: Bar) -> Signal: ...

# Usage
strategy: TradingStrategy = load_strategy(config.type)
signal = strategy.execute(bar)
```

## Liskov Substitution Principle (LSP)

**Definition:** Objects of a superclass shall be replaceable with objects of subclasses without affecting correctness.

### Signs of Violation
- Subclass throws `NotImplementedError` for inherited methods
- Subclass has stricter preconditions or weaker postconditions
- `isinstance` checks throughout code
- Client code breaks when using subclass

### Refactoring Steps

```python
# Before: Violation - subclass changes behavior unexpectedly
class Rectangle:
    def __init__(self, width, height):
        self._width = width
        self._height = height
    
    @property
    def width(self): return self._width
    
    @width.setter
    def width(self, value): self._width = value
    
    @property
    def height(self): return self._height
    
    @height.setter
    def height(self, value): self._height = value

class Square(Rectangle):  # Violation!
    @Rectangle.width.setter
    def width(self, value):
        self._width = value
        self._height = value  # Unexpected side effect

# After: Composition over inheritance
class Shape(ABC):
    @abstractmethod
    def area(self) -> float: ...

class Rectangle(Shape):
    def __init__(self, width, height):
        self._width = width
        self._height = height
    
    def area(self) -> float:
        return self._width * self._height

class Square(Shape):  # Proper inheritance - no surprises
    def __init__(self, side):
        self._side = side
    
    def area(self) -> float:
        return self._side * self._side
```

## Interface Segregation Principle (ISP)

**Definition:** Clients should not be forced to depend on methods they don't use.

### Signs of Violation
- Interface with many methods
- Implementations throw `NotImplementedError`
- Empty method implementations
- Classes implementing "fat" interfaces

### Refactoring Steps

```python
# Before: Fat interface
class DataSource(ABC):
    @abstractmethod
    def connect(self): ...
    @abstractmethod
    def fetch_historical(self, start, end): ...
    @abstractmethod
    def stream_live(self): ...
    @abstractmethod
    def disconnect(self): ...

class CSVDataSource(DataSource):  # Doesn't need connect/disconnect/stream
    def connect(self): pass
    def disconnect(self): pass
    def stream_live(self): raise NotImplementedError
    def fetch_historical(self, start, end): ...

# After: Segregated interfaces
class HistoricalDataSource(Protocol):
    def fetch_historical(self, start: datetime, end: datetime) -> list[Bar]: ...

class LiveDataSource(Protocol):
    def connect(self) -> None: ...
    def stream_live(self) -> Iterator[Bar]: ...
    def disconnect(self) -> None: ...

class CSVDataSource(HistoricalDataSource):  # Clean!
    def fetch_historical(self, start, end): ...

class WebSocketDataSource(HistoricalDataSource, LiveDataSource):  # Clean!
    def fetch_historical(self, start, end): ...
    def connect(self): ...
    def stream_live(self): ...
    def disconnect(self): ...
```

## Dependency Inversion Principle (DIP)

**Definition:** High-level modules should not depend on low-level modules. Both should depend on abstractions.

### Signs of Violation
- Direct instantiation: `self.db = Database()`
- Imports from concrete implementation modules
- Cannot unit test without real database/API
- Global imports used throughout

### Refactoring Steps

```python
# Before: High-level depends on low-level
from database.sqlite import SQLiteDatabase  # Concrete import
from api.ninjatrader import NinjaTraderAPI   # Concrete import

class TradingStrategy:
    def __init__(self):
        self.db = SQLiteDatabase("trades.db")  # Tight coupling
        self.api = NinjaTraderAPI()            # Tight coupling
    
    def on_bar(self, bar):
        self.db.save_bar(bar)
        if self.should_enter(bar):
            self.api.place_order(Order(...))

# After: Both depend on abstractions
from typing import Protocol

# Define abstractions in the domain layer
class BarRepository(Protocol):
    def save(self, bar: Bar) -> None: ...
    def get_recent(self, n: int) -> list[Bar]: ...

class OrderExecutor(Protocol):
    def place_order(self, order: Order) -> Execution: ...

# High-level module depends on abstractions only
class TradingStrategy:
    def __init__(
        self,
        bar_repo: BarRepository,
        executor: OrderExecutor
    ):
        self._repo = bar_repo
        self._executor = executor
    
    def on_bar(self, bar: Bar) -> None:
        self._repo.save(bar)
        if self._should_enter(bar):
            self._executor.place_order(Order(...))

# Low-level implementations in infrastructure layer
class SQLiteBarRepository:
    def __init__(self, connection_string: str): ...
    def save(self, bar: Bar) -> None: ...
    def get_recent(self, n: int) -> list[Bar]: ...

class NinjaTraderExecutor:
    def __init__(self, config: NTConfig): ...
    def place_order(self, order: Order) -> Execution: ...

# Wiring at composition root
strategy = TradingStrategy(
    bar_repo=SQLiteBarRepository("trades.db"),
    executor=NinjaTraderExecutor(config)
)
```

## Detecting Violations with Code Metrics

Use these heuristics to find violations:

| Metric | SRP | OCP | LSP | ISP | DIP |
|--------|-----|-----|-----|-----|-----|
| Lines of code per class | >200 | - | - | - | - |
| Number of imports | >10 | - | - | - | concrete imports |
| Methods per class | - | growing | overridden throwing | >10 | - |
| Cyclomatic complexity | high in one method | high in dispatch | - | - | - |
| Test setup complexity | - | - | - | mocking unused methods | cannot mock |
