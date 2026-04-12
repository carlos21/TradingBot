# ZeroMQ Migration Guide

This guide explains how to migrate from HTTP long-polling to ZeroMQ for communication between Python and trading platforms (NinjaTrader, MetaTrader, etc.).

## Overview

### Why ZeroMQ?

| Feature | HTTP Long-Poll | ZeroMQ |
|---------|---------------|--------|
| **Latency** | 5-50ms | 0.05-0.1ms (100-1000x faster) |
| **Throughput** | ~1k msg/s | ~1M msg/s |
| **CPU Usage** | High (constant polling) | Low (event-driven) |
| **Reconnection** | Manual | Automatic |
| **Complexity** | Low | Medium |
| **Universal** | Platform-specific | Works with any platform |

### Architecture

```
┌─────────────┐      ZeroMQ PUB/SUB       ┌─────────────┐
│             │ ◄──────────────────────── │             │
│   Python    │      Ticks/Bars/Fills     │ NinjaTrader │
│  (Strategy) │                           │  (Platform) │
│             │ ◄──────────────────────── │             │
└─────────────┘      ZeroMQ PUSH/PULL      └─────────────┘
                     Commands (Open/Close/Modify)
```

## Quick Start

### 1. Install Dependencies

```bash
# ZeroMQ is now included in pyproject.toml
poetry install

# Or install directly
pip install pyzmq
```

### 2. Python Side (Minimal Change)

```python
# OLD: HTTP-based
from src.data_sources.ninjatrader_datasource import NinjaTraderDataSource
from src.services.trade_executor import NinjaTraderExecutor

data_source = NinjaTraderDataSource(cfg=...)
trade_executor = NinjaTraderExecutor(data_source, risk_usd=500)

# NEW: ZeroMQ-based
from src.gateway import create_live_components

data_source, trade_executor = create_live_components(
    pair="NQ",
    risk_usd=500,
)

# Rest of your code stays the same!
wiring = create_app(
    pair="NQ",
    data_source=data_source,
    trade_executor=trade_executor,
    live_mode=True,
    ...
)

# Start the gateway
data_source.start()
```

### 3. NinjaTrader Side

1. Install NetMQ (ZeroMQ for .NET):
   - In NinjaTrader: `Tools` > `Manage NuGet Packages`
   - Search for `NetMQ` and install it

2. Add the ZMQ connector:
   - Copy `zmq_connectors/ninjatrader/TradingBotZmqConnector.cs` to your NinjaTrader AddOns
   - Compile (F5)

3. Connect:
   - Open Control Center
   - Click `New` > `TradingBot ZMQ Connector`
   - Click `Connect`

## Detailed Migration Steps

### Step 1: Update Your Main Script

**Before (HTTP):**
```python
# main.py
from src.data_sources.ninjatrader_datasource import NinjaTraderDataSource, NinjaTraderConfig
from app_factory import create_app, Repositories

def main():
    # Create data source
    cfg = NinjaTraderConfig(pair="NQ", account="")
    data_source = NinjaTraderDataSource(cfg=cfg)
    
    # Create app
    wiring = create_app(
        pair="NQ",
        data_source=data_source,
        repos=repos,
        numbers=numbers,
        live_mode=True,
        # No explicit trade_executor - uses HTTP routes
    )
    
    # Run
    wiring.socketio.run(wiring.app, port=5001)
```

**After (ZeroMQ):**
```python
# main.py
from src.gateway import create_live_components
from app_factory import create_app, Repositories

def main():
    # Create ZeroMQ components
    data_source, trade_executor = create_live_components(
        pair="NQ",
        risk_usd=500,
    )
    
    # Create app - pass the trade_executor!
    wiring = create_app(
        pair="NQ",
        data_source=data_source,
        trade_executor=trade_executor,  # NEW: Pass the executor
        repos=repos,
        numbers=numbers,
        live_mode=True,
    )
    
    # Start ZeroMQ (before running Flask)
    data_source.start()
    
    try:
        wiring.socketio.run(wiring.app, port=5001)
    finally:
        data_source.stop()  # Clean shutdown
```

### Step 2: Update Configuration (Optional)

Default ports:
- `5555` - Market data (PUB/SUB)
- `5556` - Trade commands (PUSH/PULL)
- `5557` - Sync queries (REQ/REP)
- `5558` - Heartbeats (PUB/SUB)

To customize:
```python
from src.gateway import GatewayConfig, TradingGateway, ZMQDataSource, ZMQTradeExecutor

config = GatewayConfig(
    market_data_pub="tcp://127.0.0.1:5555",
    command_pull="tcp://127.0.0.1:5556",
    query_rep="tcp://127.0.0.1:5557",
    heartbeat_pub="tcp://127.0.0.1:5558",
)

gateway = TradingGateway(config=config, pair="NQ")
data_source = ZMQDataSource(gateway=gateway)
trade_executor = ZMQTradeExecutor(gateway=gateway, risk_usd=500)
```

### Step 3: Verify Connection

**Python logs:**
```
[INFO] TradingGateway started. Listening on:
[INFO]   - Market data: tcp://127.0.0.1:5555
[INFO]   - Commands: tcp://127.0.0.1:5556
[INFO]   - Queries: tcp://127.0.0.1:5557
```

**NinjaTrader logs:**
```
[ZMQ] Connected to Python TradingBot via ZeroMQ
[ZMQ] Command loop started
[ZMQ] Subscribed to market data for MNQ 06-26
```

## Advanced Usage

### Custom Callbacks

```python
from src.gateway import TradingGateway

gateway = TradingGateway(pair="NQ")

# Register callbacks for specific events
gateway.on_tick(lambda tick: print(f"Tick: {tick['price']}"))
gateway.on_entry_fill(lambda fill: print(f"Filled: {fill['entry_price']}"))
gateway.on_exit_fill(lambda fill: print(f"Closed: {fill['exit_price']} ({fill['result_type']})"))

gateway.start()
```

### Manual Command Sending

```python
# Open order
gateway.send_open_order(
    trade_id="trade_123",
    direction="long",
    entry_price=21000,
    stop_loss=20920,
    take_profit=21080,
    risk_points=80,
    rr_ratio=1.0,
)

# Modify SL (Move to breakeven / Trailing stop)
# ✅ FULLY WORKING - Uses _account.ChangeOrder() behind the scenes
gateway.send_modify_order(
    trade_id="trade_123",
    stop_loss=21000,  # Move to breakeven
)

# The connector will:
# 1. Find the cached stop order (or search Account.Orders)
# 2. Validate order state (Working/Accepted)
# 3. Call _account.ChangeOrder() with new stop price
# 4. Send TRADE_LOG confirmation back to Python

# Close order
gateway.send_close_order(
    trade_id="trade_123",
    reason="session_end",
)
```

### Querying Positions

```python
# Synchronous query (blocks until response)
positions = gateway.query_positions(timeout_ms=5000)
print(f"Open positions: {positions}")
```

## Protocol Reference

### Message Format

All messages use JSON with this envelope:

```json
{
    "msg_type": "tick",
    "timestamp": 1712789432.123456,
    "seq_num": 12345,
    "payload": { ... }
}
```

### Message Types

**Market Data (Platform → Python):**
- `tick` - Single price tick
- `bar` - Completed OHLCV bar
- `partial` - In-progress bar (for UI)
- `history_batch` - Batch of historical bars
- `history_end` - End of historical data

**Trade Commands (Python → Platform):**
- `order_open` - Open new position
- `order_close` - Close position  
- `order_modify` - Modify SL/TP ✅ **FULLY WORKING** - Uses `_account.ChangeOrder()` to update stop orders while maintaining ATM strategy attachment

**Events (Platform → Python):**
- `entry_fill` - Position opened
- `exit_fill` - Position closed
- `order_rejected` - Order rejected

**Logging (Platform → Python):**
- `trade_log` - Trade lifecycle events
- `error` - Platform errors

**Control:**
- `heartbeat` - Keep-alive
- `connect` - Initial handshake
- `refresh_request` - Request history refresh

### Example Messages

**Tick:**
```json
{
    "msg_type": "tick",
    "timestamp": 1712789432.123,
    "seq_num": 100,
    "payload": {
        "pair": "NQ",
        "price": 21050.25,
        "volume": 150,
        "time": 1712789432
    }
}
```

**Open Order Command:**
```json
{
    "msg_type": "order_open",
    "timestamp": 1712789432.123,
    "seq_num": 50,
    "payload": {
        "trade_id": "trade_123",
        "pair": "NQ",
        "direction": "long",
        "entry_price": 21000,
        "stop_loss": 20920,
        "take_profit": 21080,
        "risk_points": 80,
        "rr_ratio": 1.0
    }
}
```

## Troubleshooting

### "Address already in use"

The ports are already taken. Either:
1. Kill the existing process: `lsof -ti:5555 | xargs kill -9`
2. Use different ports in `GatewayConfig`

### "Connection refused"

NinjaTrader can't connect to Python. Check:
1. Python gateway is started (`data_source.start()`)
2. Firewall allows connections to ports 5555-5558
3. Using correct IP address (127.0.0.1 for local)

### Missing messages

ZeroMQ uses buffering. If Python is slow, messages may be dropped:
- Check `data_source.stats` for drop counts
- Increase `max_queue_size` in `GatewayConfig`
- Use `ZMQ_RCVHWM` socket option for high water mark

### High CPU usage

The command loop polls with 1ms timeout. If you see high CPU:
- This is normal for the command thread
- It's waiting for commands efficiently (not busy-waiting)
- Use `top -H` to verify it's the ZMQ-CommandSender thread

## Platform-Specific Notes

### NinjaTrader

- Requires NetMQ NuGet package
- Runs in AddOn context (no chart required)
- Can use ATM strategies for SL/TP management

### MetaTrader 4/5

- Use MQL ZMQ library (available on GitHub)
- Run as EA (Expert Advisor) on chart
- Socket operations in `OnTick()` with non-blocking check

### cTrader

- Uses cAlgo with NetMQ
- Run as cBot
- Similar architecture to NinjaTrader

## Stop Loss Modification Implementation

The `order_modify` command is **fully implemented and working**. Here's how it works:

### Overview

When you call `gateway.send_modify_order()`, the connector:

1. **Locates the stop order:**
   - First checks `_stopLossOrders[trade_id]` cache
   - If not cached, searches `Account.Orders` for matching stop order
   - Caches the reference for future use

2. **Validates order state:**
   - Must be `OrderState.Working` or `OrderState.Accepted`
   - Must be a stop order (StopMarket or StopLimit)

3. **Modifies via NinjaTrader API:**
   ```csharp
   _account.ChangeOrder(
       order: stopOrder,
       quantity: stopOrder.Quantity,        // Unchanged
       limitPrice: stopOrder.LimitPrice,    // Unchanged
       stopPrice: newSl,                    // NEW VALUE
       atmStrategyId: stopOrder.AtmStrategyId  // Keeps ATM
   );
   ```

4. **Confirms modification:**
   - Sends `TRADE_LOG` message with `event: NT:MODIFY`
   - Logs success to NinjaTrader output window

### Code Changes

**Files modified:**
- `ninjatrader/TradingBotConnector.cs` - HTTP connector
- `zmq_connectors/ninjatrader/TradingBotZmqConnector.cs` - ZMQ connector

**Key additions:**
```csharp
// Dictionary to cache stop orders
private readonly Dictionary<string, Order> _stopLossOrders = 
    new Dictionary<string, Order>();

// Track orders as they appear
private void OnOrderUpdate(object sender, OrderEventArgs e)
{
    if ((order.Name == "Stop" || order.Name.Contains("Stop")) && 
        (order.OrderType == OrderType.StopMarket || order.OrderType == OrderType.StopLimit))
    {
        var tradeId = FindTradeIdByAtmOrder(order);
        if (tradeId != null)
        {
            _stopLossOrders[tradeId] = order;
            _log($"TRACKING SL order for {tradeId}");
        }
    }
}

// Modify the order
internal void HandleModifyOrder(string body)
{
    // ... validation ...
    
    _account.ChangeOrder(
        stopOrder,
        stopOrder.Quantity,
        stopOrder.LimitPrice,
        newSl,
        stopOrder.AtmStrategyId
    );
    
    _log($"SUCCESS: Modified SL for {tradeId} to {newSl}");
    LogToPython(trade_id, "NT:MODIFY", $"Stop loss changed to {newSl}");
}
```

### Python Example

```python
from src.gateway import TradingGateway

gateway = TradingGateway(logger, config)
gateway.start()

# Register callback for modification confirmation
def on_trade_log(payload):
    event = payload.get('event')
    msg = payload.get('message')
    if event == 'NT:MODIFY':
        print(f"✅ SL Modified: {msg}")
    elif event == 'NT:ERROR':
        print(f"❌ Error: {msg}")

gateway.on_trade_log(on_trade_log)

# After entry fill received...
gateway.send_modify_order(
    trade_id="trade_abc123",
    stop_loss=21050  # Move SL to breakeven
)
```

### Error Handling

| Scenario | Response |
|----------|----------|
| Order not found | Searches Account.Orders, errors if still not found |
| Order filled/cancelled | Error: "Stop order not modifiable" |
| ChangeOrder fails | Exception logged + TRADE_LOG error sent |
| Account disconnected | Error: "No account available" |

## Performance Tuning

### For Ultra-Low Latency

1. **Use IPC instead of TCP** (same machine only):
```python
config = GatewayConfig(
    market_data_pub="ipc:///tmp/trading_market",
    command_pull="ipc:///tmp/trading_commands",
    ...
)
```

2. **Disable Nagle's algorithm** (already done in code)

3. **Use dedicated CPU cores**:
```bash
taskset -c 2,3 python main.py
```

### For High Throughput

1. **Increase batch size** for historical data
2. **Use PUB/SUB for ticks** (fire-and-forget)
3. **Separate threads** for different message types

## Rollback Plan

If you need to switch back to HTTP:

```python
# Simply don't pass trade_executor to create_app
# The HTTP routes will handle everything

wiring = create_app(
    pair="NQ",
    data_source=data_source,  # Can still use ZMQ for data
    # trade_executor=executor,  # Comment out - uses HTTP
    ...
)
```

Or use the hybrid mode:
```python
from src.gateway.integration import HybridDataSource

hybrid = HybridDataSource(
    primary=zmq_data_source,
    fallback=http_data_source,
)

# Switch protocols at runtime
hybrid.switch_to_fallback()  # Use HTTP
hybrid.switch_to_primary()   # Use ZeroMQ
```

## FAQ

**Q: Can I use both HTTP and ZeroMQ at the same time?**
A: Yes, but not recommended. Pick one for production.

**Q: Does ZeroMQ work over the network?**
A: Yes, change `127.0.0.1` to the target IP. Be aware of security implications.

**Q: What if NinjaTrader crashes?**
A: ZeroMQ will automatically reconnect when NinjaTrader restarts. No action needed.

**Q: How do I debug messages?**
A: Use `tcpdump` or Wireshark to capture ZeroMQ traffic on ports 5555-5558.

**Q: Can I use this with multiple NinjaTrader instances?**
A: Yes, use different port ranges for each instance:
- Instance 1: 5555-5558
- Instance 2: 5565-5568

## Next Steps

1. Run the example: `python examples/zmq_live_trading.py`
2. Test in simulation mode first
3. Monitor latency with the stats output
4. Gradually move from HTTP to ZeroMQ

For questions or issues, check the logs in `logs/` directory.
