---
name: clean-refactor
description: "Refactor existing code using SOLID principles, design patterns, and clean architecture. Use when refactoring legacy code, improving code maintainability, achieving high testability, applying dependency injection, extracting interfaces, or decoupling tightly-coupled components. Triggers: refactor this code, make it SOLID, improve testability, apply clean architecture, reduce coupling, extract interface, dependency injection."
---

# Clean Refactoring Skill

Refactor code using SOLID principles, design patterns, and clean architecture to achieve highly testable, maintainable code.

## Quick Start

1. **Analyze** the current code for violations (see [references/solid-principles.md](references/solid-principles.md))
2. **Plan** refactoring steps - identify interfaces to extract, dependencies to invert
3. **Apply** patterns incrementally - one principle at a time
4. **Test** thoroughly after each change

## Core Workflow

### Step 1: Identify Violations

Read the target code and identify:
- **S**ingle Responsibility violations: classes/functions doing too much
- **O**pen/Closed violations: modifying code to add features
- **L**iskov Substitution violations: inheritance breaking substitutability
- **I**nterface Segregation violations: fat interfaces with unused methods
- **D**ependency Inversion violations: direct instantiation, tight coupling

### Step 2: Extract Interfaces

Create protocols/interfaces for external dependencies:

```python
from typing import Protocol

# Before: direct dependency
class OrderService:
    def __init__(self):
        self.db = Database()  # Hard-coded dependency
        self.api = PaymentAPI()  # Hard-coded dependency

# After: dependency injection via protocol
class Database(Protocol):
    def get(self, id: int) -> Order: ...
    def save(self, order: Order) -> None: ...

class PaymentGateway(Protocol):
    def charge(self, amount: Decimal) -> PaymentResult: ...

class OrderService:
    def __init__(self, db: Database, payment: PaymentGateway):
        self._db = db
        self._payment = payment
```

### Step 3: Apply Design Patterns

Choose patterns based on the problem:

| Problem | Pattern | Usage |
|---------|---------|-------|
| Object creation | Factory, Builder | `create_order()` instead of `Order()` |
| One instance | Singleton (use sparingly) | Config, Logger |
| Add behavior dynamically | Decorator | `@cached`, `@validated` |
| Different algorithms | Strategy | `PricingStrategy` interface |
| Complex object construction | Builder | `OrderBuilder` |
| Notify observers | Observer | Event bus, pub/sub |
| Simplify complex subsystems | Facade | `TradingFacade` |

See [references/design-patterns.md](references/design-patterns.md) for detailed patterns.

### Step 4: Structure by Layer (Clean Architecture)

```
┌─────────────────────────────────────┐
│           Presentation              │  ← HTTP handlers, CLI, WebSocket
│        (Controllers/Routes)         │    Thin layer, delegates to use cases
├─────────────────────────────────────┤
│            Application              │  ← Use cases, services, DTOs
│         (Business Logic)            │    Orchestrates domain operations
├─────────────────────────────────────┤
│             Domain                  │  ← Entities, value objects, domain services
│       (Core Business Rules)         │    Pure business logic, no dependencies
├─────────────────────────────────────┤
│         Infrastructure              │  ← DB, HTTP, external APIs
│       (I/O and External)            │    Implements interfaces defined in domain
└─────────────────────────────────────┘
```

Dependencies point INWARD. Domain knows nothing of infrastructure.

See [references/clean-architecture.md](references/clean-architecture.md) for folder structure and examples.

### Step 5: Make It Testable

Ensure every component can be unit tested:

```python
# Before: hard to test
class TradeManager:
    def __init__(self):
        self.db = SQLiteDatabase("trades.db")  # Cannot mock
        self.api = NinjaTraderAPI()  # Network dependency

# After: fully testable
class TradeManager:
    def __init__(
        self,
        repository: TradeRepository,  # Interface
        executor: TradeExecutor,       # Interface
        notifier: Notifier,            # Interface
    ):
        self._repo = repository
        self._exec = executor
        self._notifier = notifier
```

Test with fakes/mocks:

```python
# tests/fakes.py
class FakeTradeRepository(TradeRepository):
    def __init__(self):
        self.trades: list[Trade] = []
    
    def save(self, trade: Trade) -> None:
        self.trades.append(trade)
    
    def get(self, id: int) -> Trade | None:
        return next((t for t in self.trades if t.id == id), None)
```

See [references/testing-patterns.md](references/testing-patterns.md) for test strategies.

## Refactoring Checklist

Before starting:
- [ ] Code has existing tests (or write characterization tests first)
- [ ] Understand current behavior
- [ ] Identify dependencies to break

During refactoring:
- [ ] Extract one interface at a time
- [ ] Update one caller at a time
- [ ] Run tests after each change
- [ ] Commit after each successful refactor

After refactoring:
- [ ] All tests pass
- [ ] No imports from outer layers (domain → infrastructure)
- [ ] All external dependencies injected
- [ ] High test coverage (>80%)
- [ ] No global state mutations

## Anti-Patterns to Avoid

1. **God Object** - Class that knows/does everything → Split into cohesive classes
2. **Feature Envy** - Method uses more data from another class → Move method
3. **Shotgun Surgery** - Change requires many small edits → Consolidate behavior
4. **Primitive Obsession** - Using primitives for domain concepts → Create value objects
5. **Law of Demeter violations** - Chained method calls → Encapsulate traversal

## Example Transformation

See [references/before-after-example.md](references/before-after-example.md) for a complete refactoring example from a real codebase.

## Language-Specific Notes

### Python
- Use `typing.Protocol` for structural subtyping
- Use `@dataclass` for value objects
- Use dependency injection containers sparingly (prefer explicit)
- Use `pytest` fixtures for test dependencies

### TypeScript
- Use interfaces for dependency contracts
- Use `readonly` for immutability
- Use discriminated unions for type-safe states

### Other Languages
- Adapt patterns to idiomatic style of the language
- Focus on dependency inversion and interface segregation
