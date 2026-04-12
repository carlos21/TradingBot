# Complete Refactoring Example

## Before: Tightly Coupled Code

```python
# src/trading/strategy.py
import sqlite3
import requests
from datetime import datetime

class TradingStrategy:
    """Before refactoring - violates multiple principles."""
    
    def __init__(self):
        # DIP Violation: Direct instantiation of concrete dependencies
        self.db = sqlite3.connect("trades.db")
        self.api_key = "secret123"
        self.api_url = "https://api.ninjatrader.com"
        self.positions = []
        self.max_positions = 5
        self.trades_today = 0
    
    def on_bar(self, bar):
        """SRP Violation: Does too many things."""
        # 1. Check trade limits
        if self.trades_today >= 10:
            print("Daily limit reached")
            return
        
        # 2. Calculate indicators
        cursor = self.db.execute(
            "SELECT close FROM bars ORDER BY timestamp DESC LIMIT 20"
        )
        closes = [row[0] for row in cursor.fetchall()]
        
        if len(closes) < 20:
            return
        
        sma = sum(closes) / len(closes)
        
        # 3. Check entry conditions
        if bar.close > sma and len(self.positions) < self.max_positions:
            # 4. Calculate position size
            account = self._get_account_value()
            risk = account * 0.01
            size = int(risk / 50)  # $50 risk per contract
            
            # 5. Place order via API
            order = {
                "symbol": bar.symbol,
                "side": "BUY",
                "quantity": size,
                "type": "MARKET"
            }
            
            response = requests.post(
                f"{self.api_url}/orders",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=order
            )
            
            if response.status_code == 200:
                # 6. Update database
                self.db.execute(
                    "INSERT INTO trades VALUES (?, ?, ?, ?)",
                    (bar.timestamp, bar.symbol, size, bar.close)
                )
                self.db.commit()
                
                # 7. Track position
                self.positions.append({
                    "symbol": bar.symbol,
                    "entry": bar.close,
                    "size": size
                })
                self.trades_today += 1
                
                # 8. Send notification
                print(f"Trade opened: {size} {bar.symbol}")
    
    def _get_account_value(self):
        """Hidden dependency - network call."""
        response = requests.get(
            f"{self.api_url}/account",
            headers={"Authorization": f"Bearer {self.api_key}"}
        )
        return response.json()["balance"]

# Problems:
# 1. Untestable - can't mock database or API
# 2. SRP violations - 8 different responsibilities
# 3. DIP violations - hard-coded dependencies
# 4. No error handling
# 5. Global state (trades_today)
# 6. Mixed concerns - business logic + I/O
```

## After: Clean Architecture

### Domain Layer

```python
# src/domain/entities.py
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime
from enum import Enum, auto
from typing import NewType

TradeId = NewType("TradeId", str)
Symbol = NewType("Symbol", str)

class Side(Enum):
    LONG = auto()
    SHORT = auto()

class TradeStatus(Enum):
    PENDING = auto()
    OPEN = auto()
    CLOSED = auto()

@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str = "USD"
    
    def __post_init__(self):
        if self.amount < 0:
            raise ValueError("Money cannot be negative")

@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    symbol: Symbol
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

@dataclass
class Trade:
    id: TradeId
    symbol: Symbol
    side: Side
    entry_price: Decimal
    size: int
    status: TradeStatus = TradeStatus.PENDING
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    exit_price: Decimal | None = None
    
    def open(self, timestamp: datetime) -> None:
        if self.status != TradeStatus.PENDING:
            raise InvalidTradeState("Trade already opened")
        self.status = TradeStatus.OPEN
        self.opened_at = timestamp
    
    def close(self, price: Decimal, timestamp: datetime) -> "TradeResult":
        if self.status != TradeStatus.OPEN:
            raise InvalidTradeState("Trade not open")
        
        self.status = TradeStatus.CLOSED
        self.exit_price = price
        self.closed_at = timestamp
        
        multiplier = 1 if self.side == Side.LONG else -1
        pnl = (price - self.entry_price) * self.size * multiplier
        
        return TradeResult(trade=self, pnl=Money(pnl))

@dataclass(frozen=True)
class TradeResult:
    trade: Trade
    pnl: Money

class InvalidTradeState(Exception):
    pass

# src/domain/indicators.py
def simple_moving_average(values: list[Decimal], period: int) -> Decimal | None:
    """Pure function - no side effects, easily testable."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

# src/domain/repositories.py (interfaces)
from typing import Protocol

class TradeRepository(Protocol):
    def save(self, trade: Trade) -> None: ...
    def get(self, trade_id: TradeId) -> Trade | None: ...
    def get_open_trades(self) -> list[Trade]: ...

class BarRepository(Protocol):
    def get_recent(self, symbol: Symbol, n: int) -> list[Bar]: ...
    def save(self, bar: Bar) -> None: ...
```

### Application Layer

```python
# src/application/interfaces.py
from typing import Protocol
from decimal import Decimal
from src.domain.entities import Symbol, Money, Trade

class OrderGateway(Protocol):
    def place_market_order(self, symbol: Symbol, side: Side, size: int) -> Execution: ...

class AccountInfoProvider(Protocol):
    def get_balance(self) -> Money: ...

class Notifier(Protocol):
    def notify(self, message: str) -> None: ...

class RiskManager(Protocol):
    def can_trade(self) -> bool: ...
    def record_trade(self) -> None: ...

# src/application/signals.py
from dataclasses import dataclass
from src.domain.entities import Bar, Side

@dataclass(frozen=True)
class Signal:
    side: Side
    symbol: str
    confidence: float

class SignalGenerator:
    """Pure logic - generates signals based on indicators."""
    
    def __init__(self, period: int = 20):
        self._period = period
    
    def generate(self, bar: Bar, history: list[Bar]) -> Signal | None:
        from src.domain.indicators import simple_moving_average
        
        closes = [b.close for b in history] + [bar.close]
        sma = simple_moving_average(closes, self._period)
        
        if sma is None:
            return None
        
        if bar.close > sma:
            return Signal(side=Side.LONG, symbol=bar.symbol, confidence=0.7)
        elif bar.close < sma:
            return Signal(side=Side.SHORT, symbol=bar.symbol, confidence=0.7)
        
        return None

# src/application/position_sizing.py
from decimal import Decimal
from src.domain.entities import Money

class PositionSizer:
    """Strategy pattern for position sizing."""
    
    def __init__(self, risk_percent: Decimal, risk_per_contract: Money):
        self._risk_percent = risk_percent
        self._risk_per_contract = risk_per_contract
    
    def calculate_size(self, account_balance: Money) -> int:
        risk_amount = account_balance.amount * self._risk_percent
        return int(risk_amount / self._risk_per_contract.amount)

# src/application/use_cases/enter_trade.py
from dataclasses import dataclass
from datetime import datetime
from src.domain.entities import Bar, Trade, TradeId, TradeStatus
from src.domain.repositories import TradeRepository
from src.application.interfaces import (
    OrderGateway, AccountInfoProvider, 
    Notifier, RiskManager
)
from src.application.signals import SignalGenerator
from src.application.position_sizing import PositionSizer

@dataclass(frozen=True)
class EnterTradeRequest:
    bar: Bar
    history: list[Bar]

@dataclass(frozen=True)
class EnterTradeResult:
    success: bool
    trade_id: TradeId | None = None
    message: str = ""

class EnterTradeUseCase:
    """Single responsibility: handle trade entry workflow."""
    
    def __init__(
        self,
        signal_generator: SignalGenerator,
        position_sizer: PositionSizer,
        trade_repo: TradeRepository,
        order_gateway: OrderGateway,
        account_info: AccountInfoProvider,
        risk_manager: RiskManager,
        notifier: Notifier
    ):
        self._signal_gen = signal_generator
        self._position_sizer = position_sizer
        self._trade_repo = trade_repo
        self._order_gateway = order_gateway
        self._account_info = account_info
        self._risk_manager = risk_manager
        self._notifier = notifier
    
    def execute(self, request: EnterTradeRequest) -> EnterTradeResult:
        # Check risk limits
        if not self._risk_manager.can_trade():
            return EnterTradeResult(success=False, message="Risk limits exceeded")
        
        # Generate signal
        signal = self._signal_gen.generate(request.bar, request.history)
        if signal is None:
            return EnterTradeResult(success=False, message="No signal")
        
        # Calculate position size
        balance = self._account_info.get_balance()
        size = self._position_sizer.calculate_size(balance)
        
        if size <= 0:
            return EnterTradeResult(success=False, message="Position size too small")
        
        # Create domain entity
        trade = Trade(
            id=TradeId(generate_id()),
            symbol=request.bar.symbol,
            side=signal.side,
            entry_price=request.bar.close,
            size=size
        )
        
        # Execute order via gateway
        try:
            execution = self._order_gateway.place_market_order(
                symbol=trade.symbol,
                side=trade.side,
                size=trade.size
            )
        except OrderError as e:
            return EnterTradeResult(success=False, message=str(e))
        
        # Record trade
        trade.open(datetime.now())
        self._trade_repo.save(trade)
        self._risk_manager.record_trade()
        
        # Notify
        self._notifier.notify(f"Opened {size} {trade.symbol} @ {trade.entry_price}")
        
        return EnterTradeResult(
            success=True,
            trade_id=trade.id,
            message="Trade entered"
        )
```

### Infrastructure Layer

```python
# src/infrastructure/persistence/sqlite_repositories.py
import sqlite3
from src.domain.entities import Trade, Bar, TradeId
from src.domain.repositories import TradeRepository, BarRepository

class SQLiteTradeRepository(TradeRepository):
    def __init__(self, db_path: str):
        self._db_path = db_path
    
    def save(self, trade: Trade) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO trades 
                   (id, symbol, side, entry_price, size, status, opened_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (trade.id, trade.symbol, trade.side.name,
                 str(trade.entry_price), trade.size, trade.status.name,
                 trade.opened_at)
            )
    
    def get(self, trade_id: TradeId) -> Trade | None:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            return self._row_to_trade(row) if row else None
    
    def get_open_trades(self) -> list[Trade]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = ?", ("OPEN",)
            ).fetchall()
            return [self._row_to_trade(row) for row in rows]
    
    def _row_to_trade(self, row: sqlite3.Row) -> Trade:
        return Trade(
            id=TradeId(row["id"]),
            symbol=row["symbol"],
            side=Side[row["side"]],
            entry_price=Decimal(row["entry_price"]),
            size=row["size"],
            status=TradeStatus[row["status"]]
        )

# src/infrastructure/api/ninjatrader_gateway.py
import requests
from src.application.interfaces import OrderGateway
from src.domain.entities import Execution

class NinjaTraderOrderGateway(OrderGateway):
    """Adapter pattern - adapts NT API to our interface."""
    
    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url
        self._api_key = api_key
    
    def place_market_order(self, symbol, side, size):
        response = requests.post(
            f"{self._base_url}/orders",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "symbol": symbol,
                "side": "BUY" if side == Side.LONG else "SELL",
                "quantity": size,
                "type": "MARKET"
            }
        )
        response.raise_for_status()
        data = response.json()
        return Execution(
            order_id=data["order_id"],
            fill_price=Decimal(data["fill_price"]),
            timestamp=datetime.fromisoformat(data["timestamp"])
        )

# src/infrastructure/services.py
from datetime import datetime, date

class DailyTradeLimiter:
    """Concrete implementation of RiskManager."""
    
    def __init__(self, max_trades: int, trade_repo: TradeRepository):
        self._max_trades = max_trades
        self._trade_repo = trade_repo
        self._trades_today = 0
        self._last_reset = date.today()
    
    def can_trade(self) -> bool:
        self._reset_if_new_day()
        return self._trades_today < self._max_trades
    
    def record_trade(self) -> None:
        self._reset_if_new_day()
        self._trades_today += 1
    
    def _reset_if_new_day(self) -> None:
        if date.today() != self._last_reset:
            self._trades_today = 0
            self._last_reset = date.today()
```

### Presentation Layer

```python
# src/presentation/strategy_controller.py
from src.application.use_cases.enter_trade import EnterTradeRequest

class StrategyController:
    """Thin adapter between HTTP and application."""
    
    def __init__(self, enter_trade_use_case):
        self._enter_trade = enter_trade_use_case
    
    def on_bar(self, bar_data: dict) -> dict:
        bar = Bar(
            timestamp=datetime.fromisoformat(bar_data["timestamp"]),
            symbol=Symbol(bar_data["symbol"]),
            open=Decimal(bar_data["open"]),
            high=Decimal(bar_data["high"]),
            low=Decimal(bar_data["low"]),
            close=Decimal(bar_data["close"]),
            volume=bar_data["volume"]
        )
        
        # In real implementation, get history from repo
        history = []
        
        result = self._enter_trade.execute(EnterTradeRequest(bar, history))
        
        return {
            "success": result.success,
            "trade_id": result.trade_id,
            "message": result.message
        }
```

### Composition Root

```python
# src/composition.py
from src.infrastructure.persistence import SQLiteTradeRepository
from src.infrastructure.api import NinjaTraderOrderGateway
from src.infrastructure.services import DailyTradeLimiter
from src.application.use_cases import EnterTradeUseCase
from src.application.signals import SignalGenerator
from src.application.position_sizing import PositionSizer
from src.presentation import StrategyController

def create_strategy_controller(config: Config) -> StrategyController:
    # Infrastructure
    trade_repo = SQLiteTradeRepository(config.db_path)
    order_gateway = NinjaTraderOrderGateway(config.nt_url, config.nt_key)
    
    # Application services
    signal_generator = SignalGenerator(period=20)
    position_sizer = PositionSizer(
        risk_percent=Decimal("0.01"),
        risk_per_contract=Money(Decimal("50"))
    )
    
    # Use case with all dependencies injected
    enter_trade = EnterTradeUseCase(
        signal_generator=signal_generator,
        position_sizer=position_sizer,
        trade_repo=trade_repo,
        order_gateway=order_gateway,
        account_info=order_gateway,  # NT gateway provides both
        risk_manager=DailyTradeLimiter(config.max_trades, trade_repo),
        notifier=ConsoleNotifier()
    )
    
    return StrategyController(enter_trade)
```

## Test Comparison

### Before: Difficult to Test

```python
def test_strategy():
    # Can't easily test - requires real database and API
    strategy = TradingStrategy()
    # ... what do we test?
```

### After: Fully Testable

```python
# tests/unit/test_signal_generator.py
def test_generates_long_signal_when_price_above_sma():
    generator = SignalGenerator(period=3)
    
    history = [
        Bar(timestamp=now(), symbol="NQ", open=100, high=110, low=95, close=100, volume=100),
        Bar(timestamp=now(), symbol="NQ", open=100, high=110, low=95, close=102, volume=100),
    ]
    current = Bar(timestamp=now(), symbol="NQ", open=100, high=110, low=95, close=110, volume=100)
    
    signal = generator.generate(current, history)
    
    assert signal is not None
    assert signal.side == Side.LONG
    assert signal.confidence == 0.7

def test_returns_none_when_not_enough_history():
    generator = SignalGenerator(period=20)
    
    signal = generator.generate(create_bar(), [create_bar()])
    
    assert signal is None

# tests/unit/test_enter_trade_use_case.py
def test_executes_trade_when_signal_generated():
    # Arrange
    use_case = EnterTradeUseCase(
        signal_generator=StubSignalGenerator(signal=Signal(Side.LONG, "NQ", 0.8)),
        position_sizer=FixedPositionSizer(size=2),
        trade_repo=FakeTradeRepository(),
        order_gateway=MockOrderGateway(),
        account_info=StubAccountInfo(balance=Money(Decimal("100000"))),
        risk_manager=FakeRiskManager(can_trade=True),
        notifier=SpyNotifier()
    )
    
    # Act
    result = use_case.execute(EnterTradeRequest(create_bar(), []))
    
    # Assert
    assert result.success is True
    assert result.trade_id is not None

# tests/integration/test_sqlite_repository.py
def test_round_trip_save_and_load():
    with tempfile.NamedTemporaryFile() as tmp:
        repo = SQLiteTradeRepository(tmp.name)
        trade = create_test_trade()
        
        repo.save(trade)
        loaded = repo.get(trade.id)
        
        assert loaded.symbol == trade.symbol
        assert loaded.size == trade.size
```

## Summary of Improvements

| Aspect | Before | After |
|--------|--------|-------|
| **SRP** | 8 responsibilities | Each class has 1 responsibility |
| **OCP** | Modify class to add features | Extend via new strategies |
| **LSP** | N/A | Proper inheritance hierarchy |
| **ISP** | N/A | Segregated interfaces |
| **DIP** | Direct instantiation | All dependencies injected |
| **Testability** | Cannot unit test | 95%+ coverage possible |
| **Coupling** | High - everything connected | Low - depends on interfaces |
| **Cohesion** | Low - mixed concerns | High - focused classes |
