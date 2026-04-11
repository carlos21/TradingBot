# ZeroMQ Platform Connectors

This directory contains ZeroMQ connectors for various trading platforms.

## Available Connectors

| Platform | File | Status | Notes |
|----------|------|--------|-------|
| NinjaTrader 8 | `ninjatrader/TradingBotZmqConnector.cs` | ✅ Ready | NetMQ-based |
| MetaTrader 5 | `metatrader/` | 🚧 Planned | MQL5 with ZMQ |
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

Coming soon. Will use MQL5 with ZeroMQ library.

## Protocol

All connectors use the same protocol defined in `src/gateway/protocol.py`.

### Default Ports

| Port | Purpose | Socket Type |
|------|---------|-------------|
| 5555 | Market data | PUB (Platform) → SUB (Python) |
| 5556 | Commands | PUSH (Python) → PULL (Platform) |
| 5557 | Queries | REQ (Python) → REP (Platform) |
| 5558 | Heartbeats | PUB (Bidirectional) |

### Message Format

```json
{
    "msg_type": "tick",
    "timestamp": 1712789432.123,
    "seq_num": 12345,
    "payload": {
        "pair": "NQ",
        "price": 21050.25,
        "volume": 150
    }
}
```

## Building Custom Connectors

To create a connector for a new platform:

1. **Use ZeroMQ library** for your platform
2. **Connect to Python's sockets:**
   - SUB to `tcp://127.0.0.1:5555` for commands
   - PULL from `tcp://127.0.0.1:5556` for market data
   - REP to `tcp://127.0.0.1:5557` for queries
3. **Implement message handlers** for each `msg_type`
4. **Send heartbeats** every 5 seconds

See `ninjatrader/TradingBotZmqConnector.cs` for a complete example.

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
