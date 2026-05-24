---
name: solid-design
description: "Design and write new code using SOLID principles, clean architecture, and dependency inversion from the ground up. Use when creating new modules, services, or features that must be highly testable, loosely coupled, and maintainable. Triggers: write new code SOLID, design a new module with clean architecture, create a testable service, implement dependency injection for new code, build a new feature with proper separation of concerns, design an API with inversion of control."
---

# SOLID Design Skill

Design and write new code using SOLID principles, clean architecture, and dependency inversion. This skill focuses on building testable, maintainable systems from scratch rather than refactoring existing code.

## Design-First Workflow

When asked to build a new module, service, or feature, follow this workflow:

### Step 1: Define the Contract (Interfaces/Protocols)

Start by defining what the system needs to do, not how. Extract the core behaviors as protocols:

```python
from typing import Protocol
from decimal import Decimal

# Domain layer: pure business logic, no external dependencies
class PriceFeed(Protocol):
    def get_current_price(self, symbol: str) -> Decimal: ...

class OrderExecutor(Protocol):
    def submit_order(self, order: Order) -> ExecutionResult: ...

class RiskEvaluator(Protocol):
    def evaluate(self, order: Order, portfolio: Portfolio) -> RiskAssessment: ...
```

**Rule**: Every external dependency (database, API, filesystem) gets a protocol before any implementation is written.

### Step 2: Implement the Domain Layer

Write pure business logic that depends only on other domain objects and the protocols defined in Step 1:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Order:
    symbol: str
    quantity: int
    side: Literal["buy", "sell"]

class TradingStrategy:
    def __init__(
        self,
        price_feed: PriceFeed,
        risk_evaluator: RiskEvaluator,
    ):
        self._price_feed = price_feed
        self._risk = risk_evaluator

    def generate_signal(self, symbol: str, portfolio: Portfolio) -> Signal | None:
        price = self._price_feed.get_current_price(symbol)
        # Pure logic: no side effects, no external calls
        if self._should_enter(price, portfolio):
            return Signal(symbol=symbol, side="buy")
        return None
```

**Rules for the domain layer**:
- No imports from `infrastructure` or `presentation`
- No I/O operations (no HTTP, no DB, no filesystem)
- Use `Protocol` for any behavior that might vary
- Return value objects, not data structures from frameworks

### Step 3: Implement Infrastructure Adapters

Provide concrete implementations of the protocols. These live in the outer layer:

```python
import requests

class AlpacaPriceFeed:
    def __init__(self, api_key: str, base_url: str):
        self._api_key = api_key
        self._base_url = base_url

    def get_current_price(self, symbol: str) -> Decimal:
        response = requests.get(
            f"{self._base_url}/v2/stocks/{symbol}/quotes/latest",
            headers={"APCA-API-KEY-ID": self._api_key},
        )
        response.raise_for_status()
        data = response.json()
        return Decimal(data["quote"]["ap"])
```

### Step 4: Compose at the Edge (Dependency Injection)

Wire everything together at the application boundary. This is the only place concrete classes are instantiated:

```python
# app/composition.py — the only file that knows about concrete implementations
from infrastructure.alpaca import AlpacaPriceFeed, AlpacaExecutor
from infrastructure.sqlite import SqlitePortfolioRepository
from domain.trading import TradingStrategy
from application.trade_service import TradeService

def create_trade_service(config: Config) -> TradeService:
    price_feed = AlpacaPriceFeed(config.api_key, config.base_url)
    executor = AlpacaExecutor(config.api_key, config.base_url)
    portfolio_repo = SqlitePortfolioRepository(config.db_path)
    
    strategy = TradingStrategy(
        price_feed=price_feed,
        risk_evaluator=DefaultRiskEvaluator(max_position_size=config.max_size),
    )
    
    return TradeService(
        strategy=strategy,
        executor=executor,
        portfolio_repo=portfolio_repo,
    )
```

**Rule**: Domain and application layers never instantiate concrete infrastructure classes.

## Type Safety First

All code designed under this skill must be statically type-safe. Type safety is not optional — it is a first-class design constraint.

### Type Safety Rules

1. **Every public method must be fully typed**: All parameters and return types must have explicit annotations.
2. **Never use `Any`**: If a type is unknown, model it with `TypeVar`, `Generic`, or a protocol. `Any` is a type system escape hatch and defeats the purpose of static analysis.
3. **Use `Protocol` for all abstractions**: Structural subtyping via `typing.Protocol` is preferred over `ABC` because it requires no inheritance and is fully type-safe.
4. **Use `NewType` for domain primitives**: Distinguish semantically different strings/numbers:
   ```python
   from typing import NewType
   Symbol = NewType("Symbol", str)
   OrderId = NewType("OrderId", int)
   ```
5. **Use `Literal` for constrained values**: Prefer `Literal["buy", "sell"]` over bare `str` when the domain has a fixed set of valid values.
6. **Use `@dataclass(frozen=True)` for value objects**: Frozen dataclasses provide type-safe, immutable value objects with minimal boilerplate.
7. **Model failures in the type system**: Where possible, use return type unions instead of exceptions:
   ```python
   def parse_order(raw: str) -> Order | ParseError:
       ...
   ```
8. **Static type checking must pass**: All code must pass `mypy --strict` or `pyright` with zero errors.

**Example**: fully typed domain design
```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, NewType, Protocol

Symbol = NewType("Symbol", str)
OrderId = NewType("OrderId", int)

@dataclass(frozen=True)
class Order:
    id: OrderId
    symbol: Symbol
    quantity: int
    side: Literal["buy", "sell"]
    price: Decimal

class OrderValidator(Protocol):
    def validate(self, order: Order) -> ValidationResult: ...

class ValidationResult:
    pass  # Typed success/error union
```

## SOLID Quick Reference

Apply these at design time:

| Principle | Design Question | Red Flag |
|-----------|-----------------|----------|
| **S**ingle Responsibility | "What is the one reason this class would change?" | Class has more than one area of concern |
| **O**pen/Closed | "Can I extend this behavior without editing the file?" | Adding a feature requires `if/else` chains |
| **L**iskov Substitution | "Can I swap this subclass without breaking callers?" | Overridden methods change pre/post conditions |
| **I**nterface Segregation | "Do all implementers use every method?" | Protocols with 5+ methods |
| **D**ependency Inversion | "Does my domain depend on abstractions?" | Domain imports concrete infrastructure |

See [references/solid-deep-dive.md](references/solid-deep-dive.md) for detailed guidance on each principle.

## Dependency Inversion Patterns

### Constructor Injection (Preferred)

Inject all dependencies via `__init__`. Required dependencies have no defaults. Optional ones use `None` with sentinel pattern:

```python
class TradeService:
    def __init__(
        self,
        executor: OrderExecutor,
        notifier: Notifier | None = None,
    ):
        self._executor = executor
        self._notifier = notifier or NullNotifier()
```

### Protocol over ABC

Use `typing.Protocol` for flexibility. Callers do not need to inherit:

```python
from typing import Protocol

class Logger(Protocol):
    def info(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...

# Any object with `info` and `error` methods works — no inheritance needed
class StdoutLogger: ...  # Implicitly satisfies Logger
class StructuredLogger: ...  # Also satisfies Logger
```

### The `Null Object` Pattern

Provide do-nothing implementations to avoid `if x is not None` checks:

```python
class NullNotifier(Notifier):
    def notify(self, event: Event) -> None:
        pass
```

See [references/dependency-inversion.md](references/dependency-inversion.md) for more patterns (factories, context providers, decorator injection).

## Clean Architecture Layers

```
┌─────────────────────────────────────────────┐
│  Presentation  │  HTTP handlers, CLI, WS    │
│  (Controllers) │  Thin layer, no logic      │
├─────────────────────────────────────────────┤
│  Application   │  Use cases, DTOs, services │
│  (Orchestrate) │  Coordinates domain calls  │
├─────────────────────────────────────────────┤
│  Domain        │  Entities, protocols, pure │
│  (Core rules)  │  logic, value objects      │
├─────────────────────────────────────────────┤
│ Infrastructure │  DB, APIs, files, queues   │
│  (Adapters)    │  Implements protocols      │
└─────────────────────────────────────────────┘
```

**Dependency Rule**: Arrows point inward. Domain has zero external dependencies.

See [references/layered-architecture.md](references/layered-architecture.md) for folder structures, module boundaries, and cross-cutting concerns.

## Testability by Design

Every component you design should be unit-testable without mocking frameworks when possible. Use fakes:

```python
# tests/fakes.py — simple, fast, no magic
class FakePriceFeed:
    def __init__(self, prices: dict[str, Decimal]):
        self._prices = prices
        self.calls: list[str] = []

    def get_current_price(self, symbol: str) -> Decimal:
        self.calls.append(symbol)
        return self._prices[symbol]
```

Write tests against the protocol, not the implementation:

```python
def test_strategy_generates_buy_signal():
    feed = FakePriceFeed({"AAPL": Decimal("150.00")})
    strategy = TradingStrategy(price_feed=feed, risk_evaluator=FakeRiskEvaluator())
    
    signal = strategy.generate_signal("AAPL", Portfolio.empty())
    
    assert signal is not None
    assert signal.side == "buy"
```

See [references/testable-design.md](references/testable-design.md) for test pyramid strategies, fixture patterns, and avoiding test-induced design damage.

## Module Design Checklist

Before declaring a new module complete:

- [ ] All external dependencies have protocols
- [ ] Domain layer has zero infrastructure imports
- [ ] All classes receive dependencies via constructor
- [ ] No global state or singletons (except explicit composition root)
- [ ] Every public method has a test using fakes
- [ ] No `if/else` chains for behavior variation — use strategy/protocol
- [ ] Value objects are immutable (`frozen=True`)
- [ ] Failures are modeled as return values, not exceptions where possible
- [ ] All public methods have complete type hints (no `Any`, no untyped parameters)
- [ ] Static type checker passes with zero errors
- [ ] Domain primitives use `NewType` where semantically distinct
- [ ] Constrained strings use `Literal` instead of raw `str`

## Anti-Patterns to Reject During Design

1. **Service Locator** — `get_service("logger")` hides dependencies. Use explicit constructor injection.
2. **Ambient Context** — `config.get_current()` or `logger.get_logger()` are global state. Pass instances explicitly.
3. **Anemic Domain Model** — Entities with only getters/setters and no behavior. Put business logic in domain objects.
4. **Leaky Abstractions** — Protocols that expose framework types (e.g., SQLAlchemy `Session`, `HttpResponse`).
5. **Premature Abstraction** — Extracting a protocol for a single concrete implementation that will never vary. Wait for the second use case.
