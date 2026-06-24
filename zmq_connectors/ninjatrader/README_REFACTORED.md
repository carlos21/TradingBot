# Refactored NinjaTrader ZMQ Connector

This directory now contains a clean-architecture refactor of the NinjaTrader ZMQ connector. The goal is full testability: the Domain and Application layers have **no dependencies on NinjaTrader assemblies or NetMQ**, so they can be unit-tested and integration-tested in isolation.

## Project Structure

```
zmq_connectors/ninjatrader/
├── TradingBot.NinjaTrader.Zmq/           # Class library (netstandard2.0)
│   ├── Domain/                           # Contracts + value objects (no external deps)
│   │   ├── ILogger.cs, IZmqNetwork.cs, IOrderTracker.cs, ...
│   │   ├── BrokerOrder.cs, BrokerAccount.cs, BrokerInstrument.cs, Bar.cs, ...
│   │   ├── MessageEnvelope.cs, MessageType.cs, ZmqConfiguration.cs, ...
│   └── Application/                      # Business logic (no NinjaTrader deps)
│       ├── ConnectorService.cs           # Headless orchestrator
│       ├── CommandDispatcher.cs
│       ├── ZmqE2ETestRunner.cs
│       └── Handlers/                     # Command handlers
│           ├── OrderOpenHandler.cs
│           ├── OrderCloseHandler.cs
│           ├── OrderModifyHandler.cs
│           ├── SubscribeHandler.cs
│           ├── RefreshRequestHandler.cs
│           ├── AuditRequestHandler.cs
│           └── TestStartHandler.cs
├── TradingBot.NinjaTrader.AddOn/         # NinjaTrader-specific AddOn (net48)
│   ├── Infrastructure/                   # NT + NetMQ implementations
│   │   ├── ZmqNetwork.cs
│   │   ├── JsonMessageSerializer.cs
│   │   ├── NtAccountProvider.cs
│   │   ├── NtInstrumentProvider.cs
│   │   ├── NtOrderExecutionService.cs
│   │   ├── NtOrderTracker.cs
│   │   ├── NtBarHistoryService.cs
│   │   ├── NtStreamingCoordinator.cs
│   │   ├── NtPnLCalculator.cs
│   │   ├── NtTradeIdExtractor.cs
│   │   ├── NtConnectorClock.cs
│   │   ├── BrokerOrderMapper.cs
│   │   └── Loggers (NinjatraderLogger, FileLogger, CompositeLogger)
│   └── Presentation/
│       ├── TradingBotZmqConnector.cs     # Thin AddOnBase adapter
│       ├── ZmqConnectorWindow.cs
│       └── NinjaTraderCompositionRoot.cs # Wires everything together
└── TradingBot.NinjaTrader.sln
```

## Dependency Rule

Dependencies point inward only:

- **Domain** knows nothing about Application, Infrastructure, or NinjaTrader.
- **Application** knows only Domain.
- **Infrastructure** knows Domain/Application and external APIs (NinjaTrader, NetMQ).
- **Presentation** knows Application and Infrastructure.

## Key Abstractions

| NinjaTrader Concept | Domain Abstraction | Implementation |
|---|---|---|
| `Account.All` | `IAccountProvider` | `NtAccountProvider` |
| `Instrument.GetInstrument` | `IInstrumentProvider` | `NtInstrumentProvider` |
| `Account.CreateOrder/Submit/Cancel` | `IOrderExecutionService` | `NtOrderExecutionService` |
| `Order` object | `BrokerOrder` value object | mapped by `BrokerOrderMapper` |
| `BarsRequest` history | `IBarHistoryService` | `NtBarHistoryService` |
| Live streaming | `IStreamingCoordinator` | `NtStreamingCoordinator` |
| PnL calculation | `IPnLCalculator` | `NtPnLCalculator` |
| Time | `IConnectorClock` | `NtConnectorClock` |
| ZMQ network | `IZmqNetwork` | `ZmqNetwork` |
| Serialization | `IMessageSerializer` | `JsonMessageSerializer` |

## How to Build

### On Windows (recommended)

1. Open `TradingBot.NinjaTrader.sln` in Visual Studio or JetBrains Rider.
2. Update the NinjaTrader assembly references in `TradingBot.NinjaTrader.AddOn.csproj` to point to your `Documents\NinjaTrader 8\bin\Custom\` DLLs.
3. Build the solution.
4. Copy the compiled `TradingBot.NinjaTrader.AddOn.dll` (and its dependencies) to `Documents\NinjaTrader 8\bin\Custom\`, or use NinjaTrader's NinjaScript editor to compile the source directly.

### Using NinjaScript Editor Only

If you prefer to keep using NinjaTrader's built-in compiler:

1. Copy the contents of `TradingBot.NinjaTrader.Zmq/Domain/` and `TradingBot.NinjaTrader.Zmq/Application/` into a NinjaScript AddOn folder.
2. Copy the contents of `TradingBot.NinjaTrader.AddOn/Infrastructure/` and `TradingBot.NinjaTrader.AddOn/Presentation/` into the same folder.
3. Ensure the namespace `TradingBot.NinjaTrader.Zmq.Domain` / `TradingBot.NinjaTrader.AddOn.Infrastructure` etc. are preserved.
4. Add references to `NetMQ.dll`, `Newtonsoft.Json.dll`, and the required NinjaTrader assemblies.
5. Compile.

> **Note:** The old single-file `TradingBotZmqConnector.cs` and the old `Application/`, `Commands/`, `Domain/`, `Infrastructure/`, `UI/` folders are the legacy implementation. They are kept for reference but should not be compiled together with the new code.

## Migration from Legacy Code

The legacy connector mixed AddOn lifecycle, UI, threading, business logic, and NinjaTrader API calls in one large class. The refactor extracts all business logic into `ConnectorService`, so:

- `TradingBotZmqConnector` is now a thin adapter that:
  - Handles AddOn lifecycle (`OnWindowCreated` / `OnWindowDestroyed`).
  - Creates the WPF window.
  - Wires NinjaTrader account/order/execution events to `ConnectorService`.
  - Calls `ConnectorService.Connect()` / `Disconnect()`.
- `ConnectorService` contains the command loop, heartbeat loop, duplicate detection, position sync, order-fill handling, and bracket creation — all using domain abstractions.
- Command handlers are small, single-responsibility classes injected into `CommandDispatcher`.

## Known Simplifications / TODO

1. **Gap-fill logic:** The original `SendHistoryAsync` had sophisticated gap-fill (external and internal gaps). The refactored `RefreshRequestHandler` currently delegates a single history request to `IBarHistoryService`. If gap-fill is required, implement it as a decorator around `IBarHistoryService` or extend `NtBarHistoryService`.
2. **ConnectorService size:** `ConnectorService` still handles order lifecycle + connection orchestration. For even better testability, consider splitting it into `ConnectionService`, `CommandLoopService`, `HeartbeatService`, and `OrderLifecycleService`.
3. **Stats UI updates:** The original updated stats in the UI periodically. The new AddOn does not yet poll `ConnectorService.GetStats()` for UI display.
4. **Compilation verification:** This refactor was authored without access to NinjaTrader assemblies. It must be compiled in a Windows/NinjaTrader environment and adjusted for any API mismatches.
