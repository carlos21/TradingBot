# Layered Architecture Guide

Folder structures, module boundaries, and cross-cutting concerns for clean architecture.

## Table of Contents
1. Recommended Folder Structure
2. Layer Responsibilities
3. Cross-Cutting Concerns
4. Module Boundaries

## Recommended Folder Structure

For a medium-sized Python project:

```
my_project/
├── src/
│   ├── domain/                    # Innermost layer — pure business logic
│   │   ├── __init__.py
│   │   ├── models.py              # Entities, value objects
│   │   ├── protocols.py           # All interfaces/Protocol definitions
│   │   ├── services.py            # Domain services (pure logic)
│   │   └── exceptions.py          # Domain-specific exceptions
│   │
│   ├── application/               # Orchestration layer
│   │   ├── __init__.py
│   │   ├── use_cases.py           # One class per use case
│   │   ├── dto.py                 # Data transfer objects
│   │   └── services.py            # Application services
│   │
│   ├── infrastructure/            # External concerns — adapters
│   │   ├── __init__.py
│   │   ├── persistence/
│   │   │   ├── sqlite_repo.py     # Implements domain protocols
│   │   │   └── redis_cache.py
│   │   ├── api/
│   │   │   ├── alpaca_client.py
│   │   │   └── webhook_handler.py
│   │   └── messaging/
│   │       └── event_bus.py
│   │
│   ├── presentation/              # Entry points — thin layer
│   │   ├── __init__.py
│   │   ├── api/
│   │   │   ├── routes.py          # FastAPI/Flask routes
│   │   │   └── schemas.py         # Request/response models
│   │   └── cli/
│   │       └── commands.py
│   │
│   └── composition.py             # Wire everything together
│
├── tests/
│   ├── unit/                      # Domain and application logic
│   ├── integration/               # Infrastructure adapters
│   ├── e2e/                       # Full flows through presentation
│   └── fakes.py                   # Shared fake implementations
```

## Layer Responsibilities

### Domain Layer

- Entities with identity and lifecycle
- Value objects (immutable, compared by value)
- Domain events (things that happened in the business)
- Domain services (logic that doesn't belong to an entity)
- Protocol definitions for anything the domain needs from outside

**Forbidden**: I/O, framework imports, datetime.now() (inject Clock protocol instead).

### Application Layer

- Use cases: `PlaceOrder`, `CancelOrder`, `GenerateReport`
- Application services that coordinate domain objects
- DTOs for crossing layer boundaries
- Transaction/unit-of-work boundaries

**Forbidden**: Direct infrastructure calls. Only through domain protocols.

### Infrastructure Layer

- Database implementations of repositories
- HTTP clients for external APIs
- Message queue publishers/consumers
- File system access
- Caching implementations

**Allowed**: Imports from domain (to implement protocols). No imports from application or presentation.

### Presentation Layer

- HTTP request handling
- CLI argument parsing
- Input validation (structural only — business validation is in domain)
- Response formatting

**Allowed**: Imports from application and composition. No direct domain or infrastructure imports.

## Cross-Cutting Concerns

### Logging

Inject a `Logger` protocol. Avoid module-level loggers that tie you to a framework:

```python
# domain/protocols.py
class Logger(Protocol):
    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...

# infrastructure/logging.py
import structlog

class StructlogLogger:
    def __init__(self, name: str):
        self._logger = structlog.get_logger(name)
    def debug(self, msg: str) -> None:
        self._logger.debug(msg)
```

### Configuration

Pass configuration as a typed object, not a global dict:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    db_path: str
    max_position_size: Decimal
```

### Error Handling

Domain defines what can go wrong. Infrastructure raises domain exceptions. Presentation handles HTTP mapping:

```python
# domain/exceptions.py
class InsufficientFundsError(Exception): ...
class InvalidOrderError(Exception): ...

# presentation/api/routes.py
from fastapi import HTTPException
from domain.exceptions import InsufficientFundsError

@app.post("/orders")
def place_order(...):
    try:
        return service.place_order(dto)
    except InsufficientFundsError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

## Module Boundaries

Keep modules small and focused. A module should:
- Have a single, clear purpose
- Expose a small public interface
- Hide internal structure

If a module has more than ~10 public names, consider splitting it.
