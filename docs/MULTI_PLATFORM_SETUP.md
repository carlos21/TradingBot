# Multi-Platform Setup: NinjaTrader + MetaTrader on Same PC

This guide explains how to run the TradingBot with **both NinjaTrader and MetaTrader simultaneously** on the same machine.

## Why Two App Instances?

The TradingBot is architected as a **single-instance** application: one Flask server, one database, one log file, one strategy, and one trade manager per process. Supporting multiple platforms in a single process would require rewriting the core architecture, web UI, and database schema.

The cleanest, safest solution is to run **two fully-isolated app instances** — one for each platform. Each instance gets its own:

- **ZMQ port range** (no collisions)
- **SQLite database** (no mixed trades/lines)
- **Log directory** (no mixed logs)
- **Flask port** (no binding conflicts)
- **Web UI** (separate dashboards)

## Quick Start

### 1. Start Both Instances

```bash
# Terminal 1 — NinjaTrader instance (default ports 5555-5558, Flask 5001)
./bin/start_ninjatrader.sh

# Terminal 2 — MetaTrader instance (default ports 5565-5568, Flask 5002)
./bin/start_metatrader.sh
```

Or start both in the background with one command:

```bash
./bin/start_multi.sh
```

Stop both later:

```bash
./bin/start_multi.sh stop
```

### 2. Verify Both Are Running

```bash
# Check processes
ps aux | grep "python app.py"

# Check ports
lsof -i :5001    # NinjaTrader UI
lsof -i :5002    # MetaTrader UI
lsof -i :5555    # NT ZMQ market data
lsof -i :5565    # MT ZMQ market data
```

### 3. Open Both Web UIs

- **NinjaTrader instance**: http://localhost:5001
- **MetaTrader instance**: http://localhost:5002

Each dashboard shows only its own trades, lines, and chart data.

## Default Configuration

| Setting | NinjaTrader | MetaTrader |
|---------|-------------|------------|
| Instance name | `ninja` | `meta` |
| ZMQ Market Port | `5555` | `5565` |
| ZMQ Command Port | `5556` | `5566` |
| ZMQ Query Port | `5557` | `5567` |
| ZMQ Heartbeat Port | `5558` | `5568` |
| Flask Port | `5001` | `5002` |
| Database | `./ninja.db` | `./meta.db` |
| Log Directory | `logs/ninja/` | `logs/meta/` |

## Customizing Per Instance

### Environment Variables

You can override any setting via environment variables before launching:

```bash
# NinjaTrader instance with custom pair
export PAIR=ES
export DB_PATH="sqlite:///./es_ninja.db"
export LOG_DIR="logs/es_ninja"
export FLASK_PORT=5003
./bin/start_ninjatrader.sh
```

```bash
# MetaTrader instance with custom pair
export PAIR=GBPUSD
export DB_PATH="sqlite:///./gbp_meta.db"
export LOG_DIR="logs/gbp_meta"
export FLASK_PORT=5004
export ZMQ_MARKET_PORT=5575
export ZMQ_COMMAND_PORT=5576
export ZMQ_QUERY_PORT=5567
export ZMQ_HEARTBEAT_PORT=5578
./bin/start_metatrader.sh
```

### CLI Arguments

You can also pass overrides directly:

```bash
python app.py --mode live --pair MNQ --db-path sqlite:///./ninja.db --log-dir logs/ninja --flask-port 5001 --instance-name ninja
python app.py --mode live --pair EURUSD --db-path sqlite:///./meta.db --log-dir logs/meta --flask-port 5002 --instance-name meta --zmq-market-port 5565 --zmq-command-port 5566 --zmq-query-port 5567 --zmq-heartbeat-port 5568
```

## MetaTrader 5 Setup

### 1. Install ZeroMQ for MQL5

Download the ZeroMQ library for MQL5 (commonly called `Zmq` or `ZeroMQ`) and place it in:

```
MetaTrader 5/MQL5/Include/Zmq/
```

### 2. Install the EA

Copy all connector files to your MetaTrader 5:

```bash
# Create directory
mkdir -p "~/MetaTrader 5/MQL5/Experts/TradingBot"

# Copy main EA and all include files
cp zmq_connectors/metatrader/TradingBotZmqEA.mq5 \
   zmq_connectors/metatrader/**/*.mqh \
   "~/MetaTrader 5/MQL5/Experts/TradingBot/"
```

### 3. Create Config File (optional)

Copy the example config and customize it:

```bash
cp zmq_connectors/metatrader/TradingBotZmqConfig.example.json \
   "~/MetaTrader 5/MQL5/Files/TradingBotZmqConfig.json"
```

Edit `TradingBotZmqConfig.json` to set ports, pair, history days, etc. If the config file is missing, the EA uses its input parameters as fallback.

### 4. Compile the EA

From WSL, run the terminal compile script (wraps MetaEditor via WSL interop and
parses the build log):

```bash
./bin/compile_metatrader.sh          # full compile, emits TradingBotZmqEA.ex5
./bin/compile_metatrader.sh --check  # syntax check only
```

Alternatively, open MetaEditor, load `TradingBotZmqEA.mq5`, and press **F7** to compile.

### 5. Attach to Chart

1. Open a chart for your desired symbol (e.g., EURUSD)
2. Drag `TradingBotZmqEA` onto the chart
3. In the inputs tab, set the ZMQ ports to match your Python instance:
   - `InpMarketPort` = `5565`
   - `InpCommandPort` = `5566`
   - `InpQueryPort` = `5567`
   - `InpHeartbeatPort` = `5568`
4. Click **OK**

The EA auto-connects on startup. Check the `Experts` tab for connection logs.

### 6. Account Configuration

The EA does **not** store the account name in its JSON config. The account is configured in the bot's Settings page (stored in the app's SQLite database). For MetaTrader, set the account `name` to the broker login ID shown in MT5 (e.g. `12345678`). The EA validates every order command against this login ID and rejects commands for any other account.

### 5. Verify Connection

Check the Python logs (`logs/meta/app_YYYY-MM-DD.log`) for:

```
[meta] [INFO] Platform connected: metatrader5 v2.0 | Pair: EURUSD | Account: ...
[meta] [INFO] History complete: N bars cached, switching to STREAMING mode
```

## Systemd Service (Linux/WSL)

If you run the bot as a systemd service, install both services:

```bash
# Copy service files
sudo cp services/tradingbot.service /etc/systemd/system/tradingbot@.service
sudo cp services/tradingbot-mt.service /etc/systemd/system/tradingbot-mt@.service

# Reload systemd
sudo systemctl daemon-reload

# Start both services (replace 'youruser' with your username)
sudo systemctl start tradingbot@youruser
sudo systemctl start tradingbot-mt@youruser

# Enable auto-start on boot
sudo systemctl enable tradingbot@youruser
sudo systemctl enable tradingbot-mt@youruser

# View logs
sudo journalctl -u tradingbot@youruser -f
sudo journalctl -u tradingbot-mt@youruser -f
```

## Log Management

Each instance writes to its own log directory:

```bash
# Tail NinjaTrader logs
tail -f logs/ninja/app_$(date +%Y-%m-%d).log

# Tail MetaTrader logs
tail -f logs/meta/app_$(date +%Y-%m-%d).log

# Search both for errors
grep -r "ERROR" logs/
```

## Database Management

Each instance uses its own SQLite database:

```bash
# List trades from NinjaTrader instance
sqlite3 ninja.db "SELECT trade_id, pair, entry_price, result_type FROM trades;"

# List trades from MetaTrader instance
sqlite3 meta.db "SELECT trade_id, pair, entry_price, result_type FROM trades;"
```

## Troubleshooting

### "Address already in use" Error

One of the ports is already taken. Check which process is using it:

```bash
lsof -i :5001   # or 5002, 5555, 5565, etc.
```

Then either kill the conflicting process or change the port via environment variable.

### Mixed Logs

If you see both platforms' logs in the same file, you forgot to set a different `LOG_DIR` for one of the instances. Each instance **must** have a unique log directory.

### Database Isolation Issues

If trades from one platform appear in the other's dashboard, you forgot to set a different `DB_PATH`. Each instance **must** have a unique database file.

### MetaTrader EA Not Connecting

1. Ensure the Python instance is running first (Python binds the ZMQ sockets, MT connects)
2. Check that the ports in the EA inputs match the Python instance ports
3. Check the MT `Experts` tab for compilation errors
4. Verify the ZeroMQ MQL5 library is installed correctly

### Telegram Notification Spam

Both instances send notifications to the same Telegram chat by default. This is usually fine (you want to know about trades from both platforms), but if it becomes noisy, you can disable Telegram on one instance by not setting `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Your PC (Windows / WSL)                  │
│                                                             │
│  ┌──────────────┐        ZMQ 5555-5558        ┌─────────┐  │
│  │              │ ◄──────────────────────────► │         │  │
│  │  Python      │  Ticks / Bars / Commands    │ Ninja-  │  │
│  │  Instance 1  │                             │ Trader  │  │
│  │  (port 5001) │                             │         │  │
│  │  DB: ninja.db│                             └─────────┘  │
│  │  Logs: ninja/│                                          │
│  └──────────────┘                                          │
│                                                             │
│  ┌──────────────┐        ZMQ 5565-5568        ┌─────────┐  │
│  │              │ ◄──────────────────────────► │ Meta-   │  │
│  │  Python      │  Ticks / Bars / Commands    │ Trader  │  │
│  │  Instance 2  │                             │   5     │  │
│  │  (port 5002) │                             └─────────┘  │
│  │  DB: meta.db │                                          │
│  │  Logs: meta/ │                                          │
│  └──────────────┘                                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Summary

| Question | Answer |
|----------|--------|
| Do I need to run the app twice? | **Yes** — one instance per platform |
| Will logs mix? | **No** — each instance has its own `LOG_DIR` |
| Will databases mix? | **No** — each instance has its own `DB_PATH` |
| Will ports conflict? | **No** — each instance has its own ZMQ + Flask ports |
| Can I share lines between platforms? | **No** — instances are fully isolated |
| Can I use different pairs per platform? | **Yes** — each instance configures its own `PAIR` |
