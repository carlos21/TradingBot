# SOLID Principles Deep Dive

Concrete guidance for applying each SOLID principle during design.

## Single Responsibility Principle (SRP)

**Definition**: A class should have only one reason to change.

**Design Test**: Ask "What business requirement change would force me to edit this class?" If you can list more than one area (e.g., "pricing rules change" and "database schema changes"), split the class.

**Example**:

```python
# Violation: class changes for trade logic AND database reasons
class TradeManager:
    def calculate_profit(self, trade: Trade) -> Decimal: ...
    def save_to_db(self, trade: Trade) -> None: ...

# Correct: separate concerns
class ProfitCalculator:
    def calculate(self, trade: Trade) -> Decimal: ...

class TradeRepository:
    def save(self, trade: Trade) -> None: ...
```

**Rule of thumb**: If a class name contains "And" or "Manager", it likely violates SRP.

## Open/Closed Principle (OCP)

**Definition**: Modules should be open for extension, closed for modification.

**Design Test**: "Can I add a new variant of this behavior without editing existing code?"

**Example**:

```python
# Violation: adding a new strategy requires editing this method
class TradingEngine:
    def execute(self, strategy_type: str):
        if strategy_type == "mean_reversion":
            ...
        elif strategy_type == "momentum":
            ...

# Correct: extend via new implementations
class TradingStrategy(Protocol):
    def execute(self, data: MarketData) -> Signal: ...

class MeanReversionStrategy: ...
class MomentumStrategy: ...

class TradingEngine:
    def __init__(self, strategy: TradingStrategy):
        self._strategy = strategy
    def run(self, data: MarketData) -> Signal:
        return self._strategy.execute(data)
```

## Liskov Substitution Principle (LSP)

**Definition**: Subtypes must be substitutable for their base types without altering correctness.

**Design Test**: "Can I replace any implementation with another without the caller knowing?"

**Common Violations**:
- Overriding a method to raise `NotImplementedError`
- Strengthening preconditions (e.g., subclass requires extra validation)
- Weakening postconditions (e.g., subclass returns `None` where base always returns a value)

**Example**:

```python
# Violation: ReadOnlyRepository breaks callers expecting save to work
class Repository(Protocol):
    def get(self, id: int) -> Item: ...
    def save(self, item: Item) -> None: ...

class ReadOnlyRepository:
    def get(self, id: int) -> Item: ...
    def save(self, item: Item) -> None:
        raise NotImplementedError("Read-only!")  # LSP violation

# Correct: separate protocols
class Readable(Protocol):
    def get(self, id: int) -> Item: ...

class Writable(Protocol):
    def save(self, item: Item) -> None: ...
```

## Interface Segregation Principle (ISP)

**Definition**: Clients should not be forced to depend on methods they do not use.

**Design Test**: "Does every implementer of this protocol use every method?"

**Example**:

```python
# Violation: Logger forces implementers to provide trace/debug
class Logger(Protocol):
    def trace(self, msg: str) -> None: ...
    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warn(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...
    def fatal(self, msg: str) -> None: ...

# Correct: segregate by need
class MinimalLogger(Protocol):
    def info(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...

class DebugLogger(Protocol):
    def debug(self, msg: str) -> None: ...

# Implement only what you need
class ProductionLogger:  # Implements MinimalLogger
    def info(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...
```

## Dependency Inversion Principle (DIP)

**Definition**:
1. High-level modules should not depend on low-level modules. Both should depend on abstractions.
2. Abstractions should not depend on details. Details should depend on abstractions.

**Design Test**: "Does my domain/business logic import any concrete infrastructure class?"

**Example**:

```python
# Violation: domain depends on concrete infrastructure
from infrastructure.alpaca import AlpacaClient  # ❌ Domain imports infrastructure

class TradingStrategy:
    def __init__(self):
        self._client = AlpacaClient()  # ❌ Direct instantiation

# Correct: domain defines what it needs, infrastructure provides it
from typing import Protocol
from decimal import Decimal

class PriceFeed(Protocol):
    def get_price(self, symbol: str) -> Decimal: ...

class TradingStrategy:
    def __init__(self, price_feed: PriceFeed):  # ✅ Depends on abstraction
        self._feed = price_feed
```

**The Golden Rule**: The `domain/` directory's import graph should contain only:
- Standard library modules
- Other `domain/` modules
- `typing` and `abc` for protocols

No framework imports. No database imports. No HTTP client imports.
