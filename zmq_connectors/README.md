# ZeroMQ Platform Connectors

This directory contains ZeroMQ connectors for various trading platforms.

## Available Connectors

| Platform | File | Status | Notes |
|----------|------|--------|-------|
| NinjaTrader 8 | `ninjatrader/TradingBotZmqConnector.cs` | ✅ Ready | NetMQ-based, SOLID architecture |
| MetaTrader 5 | `metatrader/TradingBotZmqEA.mq5` | ✅ Ready | MQL5 with ZMQ, SOLID architecture |
| cTrader | - | 🚧 Planned | cAlgo with NetMQ |

## Quick Start

### NinjaTrader 8

1. **Install NetMQ:**
   - In NinjaTrader: `Tools` > `Manage NuGet Packages`
   - Search for `NetMQ` and install

2. **Install the Connector:**
   ```
   NinjaTrader 8/bin/Custom/AddOns/TradingBotZmqConnector.cs
   ```

3. **Compile:** Press F5 in NinjaScript Editor

4. **Connect:**
   - Open Control Center
   - `New` > `TradingBot ZMQ Connector`
   - Click `Connect`

### MetaTrader 5

1. **Install ZeroMQ for MQL5:**
   - Download the MQL5 ZMQ library (commonly `Zmq.mqh`)
   - Place it in: `MetaTrader 5/MQL5/Include/Zmq/`

2. **Copy the Connector Files:**
   ```bash
   cp zmq_connectors/metatrader/*.mq5 \
      zmq_connectors/metatrader/**/*.mqh \
      "~/MetaTrader 5/MQL5/Experts/TradingBot/"
   ```

3. **Create Config File (optional):**
   ```bash
   cp zmq_connectors/metatrader/TradingBotZmqConfig.example.json \
      "~/MetaTrader 5/MQL5/Files/TradingBotZmqConfig.json"
   ```
   Edit the JSON to customize ports, pair, risk settings, etc.

4. **Compile in MetaEditor:**
   - Open MetaEditor
   - Load `TradingBotZmqEA.mq5`
   - Press **F7** to compile

5. **Attach to Chart:**
   - Open a chart for your symbol (e.g., EURUSD)
   - Drag `TradingBotZmqEA` onto the chart
   - In the **Inputs** tab, verify ports match your Python instance (default: 5565-5568)
   - Click **OK**

The EA auto-connects on startup. Check the `Experts` tab for connection logs.

## Protocol

All connectors use the same protocol defined in `backend/src/infrastructure/gateway/protocol.py`.

### Default Ports

| Port | Purpose | Socket Type | NinjaTrader | MetaTrader 5 |
|------|---------|-------------|-------------|--------------|
| 5555 | Market data | PUB (Platform) → SUB (Python) | ✅ | - |
| 5556 | Commands | PUSH (Python) → PULL (Platform) | ✅ | - |
| 5557 | Queries | REQ (Python) → REP (Platform) | ✅ | - |
| 5558 | Heartbeats | PUB (Platform) → SUB (Python) | ✅ | - |
| 5565 | Market data | PUB (Platform) → SUB (Python) | - | ✅ |
| 5566 | Commands | PUSH (Python) → PULL (Platform) | - | ✅ |
| 5567 | Queries | REQ (Python) → REP (Platform) | - | ✅ |
| 5568 | Heartbeats | PUB (Platform) → SUB (Python) | - | ✅ |

### Message Format

```json
{
    "msg_type": "tick",
    "timestamp": 1712789432.123,
    "seq_num": 12345,
    "payload": {
        "pair": "MNQ",
        "price": 21050.25,
        "volume": 150
    }
}
```

## Architecture

Both connectors follow the same **SOLID** layered architecture:

```
Presentation (Main controller)
        ↓
Application (ZmqNetwork, CommandDispatcher, E2ETestRunner)
        ↓
Commands (OrderOpen, OrderClose, OrderModify, Refresh, Test)
        ↓
Domain (Contracts, ValueObjects, MessageTypes, RateLimiter)
        ↑
Infrastructure (ConfigLoader, Logger, Serializer, OrderTracking)
```

**Features (both platforms):**
- 4-socket ZMQ topology (PUB market, PULL commands, REQ queries, PUB heartbeat)
- JSON envelope protocol with `msg_type`, `timestamp`, `seq_num`, `payload`
- External JSON config file (no recompile to change settings)
- Command acknowledgments (`command_ack`)
- Duplicate `seq_num` detection (bounded, 1000 entries)
- Tick rate limiting (configurable)
- Partial bar streaming (1/sec rate limit)
- Heartbeat timer loop
- Subscribe-driven per-instrument streaming (`subscribe`/`unsubscribe`)
- Per-command account routing (validated against the platform account, no silent fallback)
- Position sync on connect (crash recovery)
- Dynamic lot sizing from `risk_usd` / `risk_points`
- Trade log messages (`trade_log`)
- Structured error messages (`error`)
- E2E test runner

## Building Custom Connectors

To create a connector for a new platform:

1. **Use ZeroMQ library** for your platform
2. **Connect to Python's sockets:**
   - PUB to `tcp://127.0.0.1:5555` (or `5565` for MT5) for market data
   - PULL from `tcp://127.0.0.1:5556` (or `5566` for MT5) for commands
   - REQ to `tcp://127.0.0.1:5557` (or `5567` for MT5) for queries
3. **Implement message handlers** for each `msg_type`
4. **Send heartbeats** every 5 seconds

See `ninjatrader/TradingBotZmqConnector.cs` or `metatrader/TradingBotZmqEA.mq5` for a complete example.

## Testing

Test your connector with:

```bash
python examples/zmq_live_trading.py
```

Then connect your platform and verify:
- Historical bars are received
- Ticks flow in real-time
- Commands are executed

## Troubleshooting

### Connection Refused
- Check Python gateway is running
- Verify ports are not blocked by firewall
- Ensure using correct IP address

### Messages Not Received
- Check SUB socket subscription (must subscribe to "")
- Verify message format matches protocol
- Check sequence numbers for gaps

### High Latency
- Use IPC transport for same-machine: `ipc:///tmp/...`
- Disable Nagle's algorithm
- Use dedicated CPU cores
