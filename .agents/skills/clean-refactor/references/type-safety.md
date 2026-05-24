# Type Safety Patterns

Advanced patterns for achieving strict static type safety during refactoring.

## Table of Contents
1. Eliminating `Any`
2. Generics and TypeVars
3. NewType for Domain Primitives
4. Literal Types
5. TypedDict vs Dataclass
6. Union Returns for Errors
7. Type Guards

## Eliminating `Any`

`Any` disables the type checker. Replace it with precise types:

```python
from typing import Any

# Before: unsafe
 def process(data: Any) -> Any:
     return data["value"]

# After: typed
from typing import TypedDict

class Payload(TypedDict):
    value: int

def process(data: Payload) -> int:
    return data["value"]
```

If the shape is truly unknown, use `object` and narrow with `isinstance`:

```python
def handle(value: object) -> str:
    if isinstance(value, str):
        return value.upper()
    if isinstance(value, int):
        return str(value)
    raise TypeError(f"Unsupported type: {type(value)}")
```

## Generics and TypeVars

Use `TypeVar` for reusable containers and algorithms:

```python
from typing import TypeVar, Generic, Protocol

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)

class Repository(Protocol, Generic[T]):
    def get(self, id: int) -> T | None: ...
    def save(self, item: T) -> None: ...

class InMemoryRepo(Generic[T]):
    def __init__(self) -> None:
        self._items: dict[int, T] = {}

    def get(self, id: int) -> T | None:
        return self._items.get(id)

    def save(self, item: T) -> None:
        # Requires item to have an `id` attribute
        ...
```

## NewType for Domain Primitives

Distinguish semantically different uses of the same underlying type:

```python
from typing import NewType

Symbol = NewType("Symbol", str)
OrderId = NewType("OrderId", int)
UserId = NewType("UserId", int)

def lookup_symbol(symbol: Symbol) -> Price: ...

def get_user(user_id: UserId) -> User: ...

# These are type errors:
# lookup_symbol("AAPL")          ❌ str is not Symbol
# get_user(OrderId(123))         ❌ OrderId is not UserId
```

## Literal Types

Use `Literal` for constrained values instead of bare strings:

```python
from typing import Literal

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop"]

@dataclass(frozen=True)
class Order:
    side: OrderSide
    type: OrderType
```

## TypedDict vs Dataclass

Use `TypedDict` for parsing external JSON/dict data. Use `@dataclass` for internal domain objects:

```python
from typing import TypedDict

# External API payload — shape comes from outside
class AlpacaQuote(TypedDict):
    symbol: str
    bid_price: float
    ask_price: float

# Internal domain object — controlled by your code
@dataclass(frozen=True)
class Quote:
    symbol: Symbol
    bid: Decimal
    ask: Decimal

    @classmethod
    def from_api(cls, raw: AlpacaQuote) -> "Quote":
        return cls(
            symbol=Symbol(raw["symbol"]),
            bid=Decimal(str(raw["bid_price"])),
            ask=Decimal(str(raw["ask_price"])),
        )
```

## Union Returns for Errors

Model recoverable failures in the return type:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ValidationError:
    field: str
    message: str

@dataclass(frozen=True)
class OrderResult:
    order_id: OrderId
    status: Literal["filled", "pending"]

def place_order(cmd: PlaceOrderCommand) -> OrderResult | ValidationError:
    if cmd.quantity <= 0:
        return ValidationError(field="quantity", message="Must be positive")
    ...
```

## Type Guards

Use `TypeGuard` for custom narrowing functions:

```python
from typing import TypeGuard

def is_valid_order(obj: object) -> TypeGuard[Order]:
    return (
        isinstance(obj, dict)
        and "symbol" in obj
        and "quantity" in obj
        and isinstance(obj["symbol"], str)
        and isinstance(obj["quantity"], int)
    )

def process(raw: object) -> None:
    if is_valid_order(raw):
        # raw is narrowed to `Order` here
        print(raw["symbol"])
```
