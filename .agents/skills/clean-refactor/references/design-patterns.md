# Design Patterns Reference

## Creational Patterns

### Factory Method

When to use: Creating objects without specifying exact class.

```python
from typing import Protocol

class DataSource(Protocol):
    def connect(self) -> None: ...
    def fetch(self, symbol: str) -> list[Bar]: ...

def create_datasource(config: Config) -> DataSource:
    """Factory function - centralizes object creation."""
    match config.type:
        case "csv":
            return CSVDataSource(config.path)
        case "websocket":
            return WebSocketDataSource(config.url)
        case "ninjatrader":
            return NinjaTraderDataSource(config.host)
        case _:
            raise ValueError(f"Unknown type: {config.type}")

# Usage - client doesn't know concrete class
datasource = create_datasource(config)
```

### Builder Pattern

When to use: Complex object construction with many optional parameters.

```python
from dataclasses import dataclass
from typing import Self

@dataclass(frozen=True)
class TradeConfig:
    symbol: str
    timeframe: str
    stop_loss: float
    take_profit: float | None = None
    max_trades: int = 5
    risk_percent: float = 1.0

class TradeConfigBuilder:
    def __init__(self, symbol: str, timeframe: str):
        self._symbol = symbol
        self._timeframe = timeframe
        self._stop_loss = 0.0
        self._take_profit = None
        self._max_trades = 5
        self._risk_percent = 1.0
    
    def with_stop_loss(self, sl: float) -> Self:
        self._stop_loss = sl
        return self
    
    def with_take_profit(self, tp: float) -> Self:
        self._take_profit = tp
        return self
    
    def with_max_trades(self, max_trades: int) -> Self:
        self._max_trades = max_trades
        return self
    
    def build(self) -> TradeConfig:
        return TradeConfig(
            symbol=self._symbol,
            timeframe=self._timeframe,
            stop_loss=self._stop_loss,
            take_profit=self._take_profit,
            max_trades=self._max_trades,
            risk_percent=self._risk_percent
        )

# Usage
config = (
    TradeConfigBuilder("NQ", "5m")
    .with_stop_loss(20.0)
    .with_take_profit(40.0)
    .with_max_trades(3)
    .build()
)
```

### Singleton (Use Sparingly)

When to use: Truly single resource (database connection pool, config).

```python
from typing import ClassVar

class AppConfig:
    """Thread-safe singleton using class variable."""
    _instance: ClassVar["AppConfig | None"] = None
    
    def __new__(cls) -> "AppConfig":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self.database_url = ""
        self.api_key = ""
        self._initialized = True

# Prefer: Explicit configuration passed around
# Avoid singletons when possible - they hurt testability
```

## Structural Patterns

### Adapter Pattern

When to use: Making incompatible interfaces work together.

```python
# External library with incompatible interface
class NinjaTraderAPI:
    def place_market_order(self, qty: int, symbol: str) -> dict: ...
    def cancel_order(self, order_id: str) -> bool: ...

# Our domain interface
class OrderExecutor(Protocol):
    def execute(self, order: Order) -> Execution: ...

# Adapter
class NinjaTraderAdapter(OrderExecutor):
    def __init__(self, api: NinjaTraderAPI):
        self._api = api
    
    def execute(self, order: Order) -> Execution:
        result = self._api.place_market_order(
            qty=order.quantity,
            symbol=order.symbol
        )
        return Execution(
            order_id=result["order_id"],
            price=Decimal(result["fill_price"]),
            timestamp=datetime.fromisoformat(result["time"])
        )
```

### Decorator Pattern

When to use: Add behavior dynamically without subclassing.

```python
from typing import Protocol
from functools import wraps
import time

class DataFetcher(Protocol):
    def fetch(self, symbol: str) -> list[Bar]: ...

# Concrete implementation
class APIDataFetcher:
    def fetch(self, symbol: str) -> list[Bar]:
        # API call
        return bars

# Decorator: Caching
class CachedDataFetcher:
    def __init__(self, fetcher: DataFetcher, ttl: int = 60):
        self._fetcher = fetcher
        self._cache: dict[str, tuple[list[Bar], float]] = {}
        self._ttl = ttl
    
    def fetch(self, symbol: str) -> list[Bar]:
        if symbol in self._cache:
            bars, cached_at = self._cache[symbol]
            if time.time() - cached_at < self._ttl:
                return bars
        
        bars = self._fetcher.fetch(symbol)
        self._cache[symbol] = (bars, time.time())
        return bars

# Decorator: Logging
class LoggingDataFetcher:
    def __init__(self, fetcher: DataFetcher, logger: Logger):
        self._fetcher = fetcher
        self._logger = logger
    
    def fetch(self, symbol: str) -> list[Bar]:
        self._logger.info(f"Fetching {symbol}...")
        start = time.time()
        bars = self._fetcher.fetch(symbol)
        elapsed = time.time() - start
        self._logger.info(f"Fetched {len(bars)} bars in {elapsed:.2f}s")
        return bars

# Usage - decorators compose
fetcher = LoggingDataFetcher(
    CachedDataFetcher(APIDataFetcher()),
    logger
)
```

### Facade Pattern

When to use: Simplify complex subsystem with unified interface.

```python
class TradingFacade:
    """Simplifies complex trading operations."""
    
    def __init__(
        self,
        data_source: DataSource,
        order_manager: OrderManager,
        risk_engine: RiskEngine,
        position_tracker: PositionTracker
    ):
        self._data = data_source
        self._orders = order_manager
        self._risk = risk_engine
        self._positions = position_tracker
    
    def enter_position(self, signal: Signal) -> TradeResult:
        """One method to handle the entire entry workflow."""
        # Check risk
        if not self._risk.can_trade(signal):
            return TradeResult.rejected("Risk limits")
        
        # Get current price
        price = self._data.current_price(signal.symbol)
        
        # Calculate position size
        size = self._risk.calculate_size(signal, price)
        
        # Place order
        order = Order(
            symbol=signal.symbol,
            side=signal.side,
            size=size,
            stop_loss=signal.stop_loss
        )
        
        execution = self._orders.place(order)
        self._positions.add(execution)
        
        return TradeResult.success(execution)
```

## Behavioral Patterns

### Strategy Pattern

When to use: Multiple interchangeable algorithms.

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass(frozen=True)
class SizingParams:
    account_balance: Decimal
    risk_percent: Decimal
    stop_loss_ticks: int
    tick_value: Decimal

class PositionSizingStrategy(ABC):
    @abstractmethod
    def calculate(self, params: SizingParams) -> int: ...

class FixedFractionalSizing(PositionSizingStrategy):
    """Risk fixed percent of account per trade."""
    def calculate(self, params: SizingParams) -> int:
        risk_amount = params.account_balance * params.risk_percent
        risk_per_contract = params.stop_loss_ticks * params.tick_value
        return int(risk_amount / risk_per_contract)

class FixedRatioSizing(PositionSizingStrategy):
    """Ryan Jones' Fixed Ratio method."""
    def __init__(self, delta: Decimal):
        self._delta = delta
    
    def calculate(self, params: SizingParams) -> int:
        # Fixed ratio formula
        ...

class KellyCriterionSizing(PositionSizingStrategy):
    """Optimal f / Kelly Criterion."""
    def calculate(self, params: SizingParams) -> int:
        # Kelly formula
        ...

# Usage
class PositionSizer:
    def __init__(self, strategy: PositionSizingStrategy):
        self._strategy = strategy
    
    def size(self, params: SizingParams) -> int:
        return self._strategy.calculate(params)

# Easily switch strategies
sizer = PositionSizer(FixedFractionalSizing())
sizer = PositionSizer(KellyCriterionSizing())
```

### Observer Pattern

When to use: Notify multiple objects about state changes.

```python
from typing import Protocol
from dataclasses import dataclass, field

class TradeObserver(Protocol):
    def on_trade_opened(self, trade: Trade) -> None: ...
    def on_trade_closed(self, trade: Trade, pnl: Decimal) -> None: ...

@dataclass
class TradeManager:
    _observers: list[TradeObserver] = field(default_factory=list)
    _trades: list[Trade] = field(default_factory=list)
    
    def subscribe(self, observer: TradeObserver) -> None:
        self._observers.append(observer)
    
    def unsubscribe(self, observer: TradeObserver) -> None:
        self._observers.remove(observer)
    
    def open_trade(self, trade: Trade) -> None:
        self._trades.append(trade)
        for observer in self._observers:
            observer.on_trade_opened(trade)
    
    def close_trade(self, trade: Trade, pnl: Decimal) -> None:
        trade.close()
        for observer in self._observers:
            observer.on_trade_closed(trade, pnl)

# Concrete observers
class DatabaseTradeLogger:
    def __init__(self, repository: TradeRepository):
        self._repo = repository
    
    def on_trade_opened(self, trade: Trade) -> None:
        self._repo.save(trade)
    
    def on_trade_closed(self, trade: Trade, pnl: Decimal) -> None:
        self._repo.update(trade)

class TradeNotifier:
    def __init__(self, notifier: Notifier):
        self._notifier = notifier
    
    def on_trade_opened(self, trade: Trade) -> None:
        self._notifier.send(f"Opened: {trade}")
    
    def on_trade_closed(self, trade: Trade, pnl: Decimal) -> None:
        emoji = "🟢" if pnl > 0 else "🔴"
        self._notifier.send(f"{emoji} Closed: P&L ${pnl}")

class AnalyticsCollector:
    def __init__(self, analytics: AnalyticsService):
        self._analytics = analytics
    
    def on_trade_opened(self, trade: Trade) -> None:
        self._analytics.record_entry(trade)
    
    def on_trade_closed(self, trade: Trade, pnl: Decimal) -> None:
        self._analytics.record_exit(trade, pnl)

# Wiring
manager = TradeManager()
manager.subscribe(DatabaseTradeLogger(repo))
manager.subscribe(TradeNotifier(notifier))
manager.subscribe(AnalyticsCollector(analytics))
```

### Command Pattern

When to use: Encapsulate requests as objects for queuing, logging, undo.

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable
from datetime import datetime

class Command(ABC):
    @abstractmethod
    def execute(self) -> None: ...
    
    @abstractmethod
    def undo(self) -> None: ...

@dataclass
class PlaceOrderCommand(Command):
    _order: Order
    _executor: OrderExecutor
    _execution_id: str | None = None
    
    def execute(self) -> None:
        result = self._executor.place(self._order)
        self._execution_id = result.id
    
    def undo(self) -> None:
        if self._execution_id:
            self._executor.cancel(self._execution_id)

class CommandHistory:
    def __init__(self):
        self._history: list[tuple[datetime, Command]] = []
    
    def push(self, command: Command) -> None:
        self._history.append((datetime.now(), command))
    
    def undo_last(self) -> None:
        if self._history:
            _, command = self._history.pop()
            command.undo()

# For simple cases, use callable
class SimpleCommandBus:
    def __init__(self):
        self._handlers: dict[type, Callable] = {}
    
    def register(self, command_type: type, handler: Callable) -> None:
        self._handlers[command_type] = handler
    
    def execute(self, command) -> None:
        handler = self._handlers.get(type(command))
        if handler:
            handler(command)
```

### Template Method Pattern

When to use: Common algorithm with varying steps.

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class AnalysisResult:
    signal: Signal
    confidence: float
    metadata: dict

class StrategyAnalyzer(ABC):
    """Template for strategy analysis workflow."""
    
    def analyze(self, bar: Bar, context: Context) -> AnalysisResult:
        """Template method - defines the algorithm."""
        # Step 1: Validate preconditions (common)
        if not self._can_analyze(bar, context):
            return AnalysisResult(Signal.NONE, 0.0, {})
        
        # Step 2: Calculate indicators (customizable)
        indicators = self._calculate_indicators(bar, context)
        
        # Step 3: Check entry conditions (customizable)
        if self._should_enter(indicators, bar, context):
            return self._create_entry_signal(indicators, bar, context)
        
        # Step 4: Check exit conditions (customizable)
        if self._should_exit(indicators, bar, context):
            return self._create_exit_signal(indicators, bar, context)
        
        return AnalysisResult(Signal.NONE, 0.0, indicators)
    
    def _can_analyze(self, bar: Bar, context: Context) -> bool:
        """Hook - common implementation."""
        return context.has_sufficient_data()
    
    @abstractmethod
    def _calculate_indicators(self, bar: Bar, context: Context) -> dict: ...
    
    @abstractmethod
    def _should_enter(self, indicators: dict, bar: Bar, context: Context) -> bool: ...
    
    @abstractmethod
    def _should_exit(self, indicators: dict, bar: Bar, context: Context) -> bool: ...
    
    @abstractmethod
    def _create_entry_signal(self, indicators: dict, bar: Bar, context: Context) -> AnalysisResult: ...
    
    @abstractmethod
    def _create_exit_signal(self, indicators: dict, bar: Bar, context: Context) -> AnalysisResult: ...

# Concrete implementation
class MeanReversionAnalyzer(StrategyAnalyzer):
    def _calculate_indicators(self, bar: Bar, context: Context) -> dict:
        return {
            "rsi": calculate_rsi(context.bars, 14),
            "bb": calculate_bollinger_bands(context.bars, 20)
        }
    
    def _should_enter(self, indicators: dict, bar: Bar, context: Context) -> bool:
        return indicators["rsi"] < 30 and bar.close < indicators["bb"].lower
    
    def _should_exit(self, indicators: dict, bar: Bar, context: Context) -> bool:
        return indicators["rsi"] > 70 or bar.close > indicators["bb"].upper
    
    def _create_entry_signal(self, indicators: dict, bar: Bar, context: Context) -> AnalysisResult:
        return AnalysisResult(
            Signal.LONG, 
            confidence=0.8,
            metadata=indicators
        )
    
    def _create_exit_signal(self, indicators: dict, bar: Bar, context: Context) -> AnalysisResult:
        return AnalysisResult(
            Signal.EXIT,
            confidence=0.9,
            metadata=indicators
        )
```

## Choosing the Right Pattern

| Problem | Pattern | Why |
|---------|---------|-----|
| Object creation complex | Factory/Builder | Centralize, make flexible |
| Multiple algorithms | Strategy | Interchangeable, testable |
| Add behavior dynamically | Decorator | Compose behaviors |
| Complex subsystem | Facade | Simplify interface |
| Incompatible interfaces | Adapter | Bridge gaps |
| State changes need notification | Observer | Loose coupling |
| Need to queue/log operations | Command | Encapsulate requests |
| Algorithm with varying steps | Template Method | Reuse structure |
| Tree structure operations | Composite | Uniform treatment |
| One instance only | Singleton | Rarely needed |
