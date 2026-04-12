# Clean Architecture Reference

## Layer Structure

```
src/
├── domain/                    # Enterprise business rules (no deps)
│   ├── entities/              # Core business objects
│   ├── value_objects/         # Immutable, validated values
│   ├── services/              # Domain logic that doesn't fit entities
│   └── repositories/          # Repository interfaces (protocols)
│
├── application/               # Application business rules
│   ├── use_cases/             # Single-responsibility operations
│   ├── services/              # Orchestration, workflows
│   ├── dto/                   # Data transfer objects
│   └── interfaces/            # Service interfaces
│
├── infrastructure/            # External concerns
│   ├── persistence/           # DB implementations
│   ├── api/                   # External API clients
│   ├── messaging/             # Message queues
│   └── web/                   # HTTP framework code
│
└── presentation/              # UI layer
    ├── controllers/           # HTTP handlers
    ├── views/                 # Response formatting
    └── middleware/            # Cross-cutting concerns
```

## Dependency Rule

**Source code dependencies must point only inward.**

```
Presentation → Application → Domain
                    ↑
            Infrastructure
```

- **Domain** knows nothing of other layers
- **Application** knows domain, uses interfaces for infrastructure
- **Infrastructure** implements interfaces defined by inner layers
- **Presentation** knows application, formats responses

## Layer Responsibilities

### Domain Layer

Pure business logic. No imports from outer layers.

```python
# src/domain/entities/trade.py
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from enum import Enum

class TradeSide(Enum):
    LONG = "long"
    SHORT = "short"

class TradeStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"

@dataclass(frozen=True)
class Money:
    """Value object - immutable, validated."""
    amount: Decimal
    currency: str = "USD"
    
    def __post_init__(self):
        if self.amount < 0:
            raise ValueError("Money cannot be negative")
    
    def add(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError("Currency mismatch")
        return Money(self.amount + other.amount, self.currency)

@dataclass
class Trade:
    """Entity - has identity, lifecycle."""
    id: str
    symbol: str
    side: TradeSide
    entry_price: Decimal
    size: int
    stop_loss: Decimal
    take_profit: Decimal | None
    status: TradeStatus = TradeStatus.OPEN
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    
    def close(self, exit_price: Decimal, exit_time: datetime) -> "TradeResult":
        """Domain logic - close the trade."""
        if self.status != TradeStatus.OPEN:
            raise InvalidTradeState("Trade already closed")
        
        self.status = TradeStatus.CLOSED
        self.closed_at = exit_time
        
        pnl = self._calculate_pnl(exit_price)
        return TradeResult(trade=self, pnl=pnl, exit_price=exit_price)
    
    def _calculate_pnl(self, exit_price: Decimal) -> Money:
        """Pure calculation - no side effects."""
        multiplier = 1 if self.side == TradeSide.LONG else -1
        ticks = (exit_price - self.entry_price) * multiplier
        return Money(ticks * self.size)

# Repository interface defined in domain
from typing import Protocol

class TradeRepository(Protocol):
    def get(self, trade_id: str) -> Trade | None: ...
    def save(self, trade: Trade) -> None: ...
    def get_open_trades(self) -> list[Trade]: ...
```

### Application Layer

Orchestrates use cases. Defines interfaces for infrastructure.

```python
# src/application/use_cases/open_trade.py
from dataclasses import dataclass
from decimal import Decimal

from src.domain.entities import Trade, TradeSide
from src.domain.repositories import TradeRepository
from src.application.interfaces import RiskEngine, PriceProvider

@dataclass(frozen=True)
class OpenTradeRequest:
    symbol: str
    side: TradeSide
    size: int
    stop_loss: Decimal
    take_profit: Decimal | None

@dataclass(frozen=True)
class OpenTradeResponse:
    trade_id: str
    entry_price: Decimal
    status: str

class OpenTradeUseCase:
    """Single responsibility: handle trade opening workflow."""
    
    def __init__(
        self,
        trade_repo: TradeRepository,
        risk_engine: RiskEngine,
        price_provider: PriceProvider
    ):
        self._repo = trade_repo
        self._risk = risk_engine
        self._prices = price_provider
    
    def execute(self, request: OpenTradeRequest) -> OpenTradeResponse:
        # Check risk limits
        if not self._risk.can_open_trade(request.symbol, request.size):
            raise RiskLimitExceeded("Daily limit reached")
        
        # Get current price
        entry_price = self._prices.get_current_price(request.symbol)
        
        # Create trade
        trade = Trade(
            id=generate_id(),
            symbol=request.symbol,
            side=request.side,
            entry_price=entry_price,
            size=request.size,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit
        )
        
        # Persist
        self._repo.save(trade)
        
        return OpenTradeResponse(
            trade_id=trade.id,
            entry_price=entry_price,
            status="opened"
        )
```

### Infrastructure Layer

Implements interfaces defined by inner layers.

```python
# src/infrastructure/persistence/sqlite_trade_repository.py
import sqlite3
from src.domain.entities import Trade, TradeSide, TradeStatus
from src.domain.repositories import TradeRepository

class SQLiteTradeRepository(TradeRepository):
    """Implements domain interface with SQLite."""
    
    def __init__(self, db_path: str):
        self._db_path = db_path
    
    def save(self, trade: Trade) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO trades 
                   (id, symbol, side, entry_price, size, status, opened_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (trade.id, trade.symbol, trade.side.value,
                 str(trade.entry_price), trade.size, trade.status.value,
                 trade.opened_at)
            )
    
    def get(self, trade_id: str) -> Trade | None:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            return self._row_to_trade(row) if row else None
    
    def _row_to_trade(self, row: sqlite3.Row) -> Trade:
        return Trade(
            id=row["id"],
            symbol=row["symbol"],
            side=TradeSide(row["side"]),
            entry_price=Decimal(row["entry_price"]),
            size=row["size"],
            stop_loss=Decimal(row["stop_loss"]),
            take_profit=Decimal(row["take_profit"]) if row["take_profit"] else None,
            status=TradeStatus(row["status"])
        )
```

### Presentation Layer

HTTP handlers, CLI, WebSocket handlers.

```python
# src/presentation/controllers/trade_controller.py
from flask import Blueprint, request, jsonify
from src.application.use_cases import OpenTradeUseCase, OpenTradeRequest
from src.domain.entities import TradeSide

trade_bp = Blueprint("trades", __name__)

class TradeController:
    def __init__(self, open_trade_use_case: OpenTradeUseCase):
        self._open_trade = open_trade_use_case
    
    def register_routes(self, bp: Blueprint):
        bp.route("/trades", methods=["POST"])(self.create_trade)
    
    def create_trade(self):
        """HTTP handler - thin layer."""
        data = request.get_json()
        
        # Map HTTP to application request
        request_dto = OpenTradeRequest(
            symbol=data["symbol"],
            side=TradeSide(data["side"]),
            size=data["size"],
            stop_loss=Decimal(data["stop_loss"]),
            take_profit=Decimal(data["take_profit"]) if "take_profit" in data else None
        )
        
        # Execute use case
        result = self._open_trade.execute(request_dto)
        
        # Format response
        return jsonify({
            "trade_id": result.trade_id,
            "entry_price": str(result.entry_price),
            "status": result.status
        }), 201
```

## Composition Root

Wire everything together at application startup.

```python
# src/composition.py (or app_factory.py)
from src.infrastructure.persistence import SQLiteTradeRepository
from src.infrastructure.api import NinjaTraderAPI, NinjaTraderPriceProvider
from src.infrastructure.risk import ConfigurableRiskEngine
from src.application.use_cases import OpenTradeUseCase, CloseTradeUseCase
from src.presentation.controllers import TradeController

def create_application(config: Config) -> Flask:
    # Infrastructure (outer layer)
    trade_repo = SQLiteTradeRepository(config.db_path)
    nt_api = NinjaTraderAPI(config.nt_host)
    price_provider = NinjaTraderPriceProvider(nt_api)
    risk_engine = ConfigurableRiskEngine(config.max_daily_trades)
    
    # Application layer
    open_trade = OpenTradeUseCase(
        trade_repo=trade_repo,
        risk_engine=risk_engine,
        price_provider=price_provider
    )
    close_trade = CloseTradeUseCase(
        trade_repo=trade_repo,
        price_provider=price_provider
    )
    
    # Presentation layer
    trade_controller = TradeController(open_trade)
    
    # Create app
    app = Flask(__name__)
    trade_controller.register_routes(app)
    
    return app
```

## Testing by Layer

### Domain Tests

Pure unit tests - no mocks needed.

```python
def test_trade_close_calculates_pnl():
    trade = Trade(
        id="123",
        symbol="NQ",
        side=TradeSide.LONG,
        entry_price=Decimal("18000.00"),
        size=2,
        stop_loss=Decimal("17950.00"),
        take_profit=None
    )
    
    result = trade.close(
        exit_price=Decimal("18050.00"),
        exit_time=datetime.now()
    )
    
    assert result.pnl.amount == Decimal("100.00")  # 50 * 2
    assert trade.status == TradeStatus.CLOSED
```

### Application Tests

Use fakes for dependencies.

```python
class FakeTradeRepository:
    def __init__(self):
        self.trades: dict[str, Trade] = {}
    
    def save(self, trade: Trade) -> None:
        self.trades[trade.id] = trade
    
    def get(self, trade_id: str) -> Trade | None:
        return self.trades.get(trade_id)

class FakeRiskEngine:
    def __init__(self, allow: bool = True):
        self._allow = allow
    
    def can_open_trade(self, symbol: str, size: int) -> bool:
        return self._allow

class FakePriceProvider:
    def __init__(self, price: Decimal):
        self._price = price
    
    def get_current_price(self, symbol: str) -> Decimal:
        return self._price

def test_open_trade_use_case_saves_trade():
    repo = FakeTradeRepository()
    use_case = OpenTradeUseCase(
        trade_repo=repo,
        risk_engine=FakeRiskEngine(allow=True),
        price_provider=FakePriceProvider(Decimal("18000.00"))
    )
    
    result = use_case.execute(OpenTradeRequest(
        symbol="NQ",
        side=TradeSide.LONG,
        size=2,
        stop_loss=Decimal("17950.00"),
        take_profit=None
    ))
    
    assert result.trade_id in repo.trades
    assert result.status == "opened"
```

### Infrastructure Tests

Integration tests with real (or test) database/API.

```python
def test_sqlite_repository_round_trip():
    with tempfile.NamedTemporaryFile() as tmp:
        repo = SQLiteTradeRepository(tmp.name)
        
        trade = create_test_trade()
        repo.save(trade)
        
        retrieved = repo.get(trade.id)
        assert retrieved == trade
```

### E2E Tests

Test full HTTP request/response.

```python
def test_create_trade_endpoint(client):
    response = client.post("/trades", json={
        "symbol": "NQ",
        "side": "long",
        "size": 2,
        "stop_loss": "17950.00"
    })
    
    assert response.status_code == 201
    assert "trade_id" in response.json
```

## Anti-Patterns

### Violating Dependency Rule

```python
# WRONG: Domain importing infrastructure
# src/domain/trade.py
from src.infrastructure.database import db  # NEVER DO THIS

class Trade:
    def save(self):
        db.execute(...)  # Domain knows about SQL!
```

### Anemic Domain Model

```python
# WRONG: Entities are just data bags
@dataclass
class Trade:
    id: str
    symbol: str
    status: str

# Logic scattered in services
class TradeService:
    def close_trade(self, trade: Trade, price: Decimal):
        trade.status = "closed"  # State mutation outside entity
        pnl = (price - trade.entry) * trade.size
        db.save(trade)
        return pnl
```

Correct: Put domain logic in entities, use immutable value objects.

### Leaky Abstractions

```python
# WRONG: Interface exposes implementation details
class TradeRepository(Protocol):
    def execute_sql(self, query: str) -> list: ...  # Leaks SQL
    
# CORRECT: Interface expresses domain operations
class TradeRepository(Protocol):
    def get_open_trades(self) -> list[Trade]: ...
    def save(self, trade: Trade) -> None: ...
```
