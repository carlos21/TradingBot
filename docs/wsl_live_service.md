# WSL Live Service

> **Note** — Settings and bot management are now handled through the **web admin** at `http://localhost:5001/admin`.  
> The legacy PowerShell manager (`bin/TradingBot-Manager.ps1`) is deprecated.

Run the TradingBot in **live trading mode** as a background service on WSL. The Python Flask server starts automatically when WSL boots, restarts on crash, and streams logs to journald.

---

## Architecture Overview

```
 Windows                             WSL (Linux)
┌──────────────────┐   HTTP    ┌──────────────────────────┐
│   NinjaTrader    │ ───────── │  TradingBot (port 5001)  │
│                  │  :5001   │  systemd service          │
│                  │ ◄──────── │                          │
│  Sends bars/ticks│           │  Returns trade commands   │
│  Reports fills   │           │  (place/modify/close)     │
└──────────────────┘           └──────────────────────────┘
```

NinjaTrader on Windows pushes market data to the Flask server running inside WSL. The server processes bars through the strategy and sends trade commands back via long-poll.

**Data flow:**

1. NinjaTrader POSTs historical bars to `/api/nt/bars`
2. NinjaTrader signals history complete via `/api/nt/history_end`
3. NinjaTrader streams live bars to `/api/nt/bar` and ticks to `/api/nt/tick`
4. NinjaTrader long-polls `/api/nt/await_command` for trade commands
5. NinjaTrader reports fills back to `/api/nt/entry_fill` and `/api/nt/fill`

---

## Files

| File | Description |
|------|-------------|
| `bin/start_live.sh` | Non-interactive launcher script. Sets `MODE=live` and default env vars, then starts the app. |
| `bin/install_service.sh` | Installs the systemd service with resolved paths for the current user. |
| `services/tradingbot.service` | Reference systemd unit template (the install script generates the actual file). |

---

## Prerequisites

### 1. Enable systemd in WSL

Edit `/etc/wsl.conf` (create it if it doesn't exist):

```ini
[boot]
systemd=true
```

Then restart WSL from PowerShell:

```powershell
wsl --shutdown
```

### 2. Install dependencies

```bash
cd ~/TradingBot
poetry install
```

### 3. Verify the app runs manually first

```bash
./bin/start_live.sh
```

You should see:

```
[tradingbot] Starting live — pair=MNQ accounts=FNFTCHCARLOSDUCLOS74105
```

and the Flask server listening on port 5001. Press `Ctrl+C` to stop.

---

## Installation

Run the install script once:

```bash
sudo ./bin/install_service.sh
```

This will:
- Generate `/etc/systemd/system/tradingbot.service` with your actual user and project paths
- Reload systemd and enable the service to start on boot

---

## Usage

### Start / Stop / Restart

```bash
sudo systemctl start tradingbot
sudo systemctl stop tradingbot
sudo systemctl restart tradingbot
```

### Check Status

```bash
sudo systemctl status tradingbot
```

### View Logs

```bash
# Follow logs in real-time
journalctl -u tradingbot -f

# Last 100 lines
journalctl -u tradingbot -n 100

# Logs since last boot
journalctl -u tradingbot -b
```

### Uninstall

```bash
sudo systemctl stop tradingbot
sudo systemctl disable tradingbot
sudo rm /etc/systemd/system/tradingbot.service
sudo systemctl daemon-reload
```

---

## Configuration

### Environment Variables

The service uses these environment variables (with defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `PAIR` | `MNQ` | Trading pair (e.g., `MNQ`) |

> **Note:** Account names, risk, and R:R ratio are now configured via the **Settings page** (`/admin`) and stored in the database. They are no longer set via environment variables.

### Changing defaults

**Option A — Override at the service level:**

Edit the installed service file:

```bash
sudo systemctl edit tradingbot
```

This opens an override file. Add:

```ini
[Service]
Environment=PAIR=MNQ
```

Then reload:

```bash
sudo systemctl daemon-reload
sudo systemctl restart tradingbot
```

**Option B — Override in the launcher script:**

Edit `bin/live.sh` and change the defaults:

```bash
export PAIR="${PAIR:-MNQ}"
```

Then restart:

```bash
sudo systemctl restart tradingbot
```

### Strategy Parameters

The strategy configuration is in `src/prod_config.py`. Key parameters:

- **Stop loss levels:** `[15, 20, 30, 40]` points with 3pt tolerance
- **R:R ratio:** 3.3 (default)
- **Entry filters:** max 1 open trade, max 90pt bounce, trading hours 08:00-17:00 NY, max 1 trade/day
- **Trigger:** velocity-adaptive TSI with dual-timeframe cross conditions
- **Breakeven:** moves SL to entry+0.05R at 2R profit
- **Re-entry:** enabled after SL hit, cancels if price moves 90pts past line

These are shared between backtest and live mode. Changes require a service restart.

---

## Running Manually (without systemd)

For quick testing or debugging, run the launcher directly:

```bash
# With defaults
./bin/start_live.sh

# With overrides
PAIR=MNQ ./bin/start_live.sh
```

---

## Troubleshooting

### Service won't start

```bash
# Check for errors
sudo systemctl status tradingbot
journalctl -u tradingbot --no-pager -n 50
```

Common causes:
- Poetry not installed or not on PATH — make sure `poetry` is available for your WSL user
- Dependencies not installed — run `poetry install`
- Port 5001 already in use — check with `ss -tlnp | grep 5001`

### NinjaTrader can't connect

- Confirm the Flask server is listening: `ss -tlnp | grep 5001`
- From Windows, try: `curl http://localhost:5001/` — WSL2 should forward the port automatically
- If not, check WSL networking mode. With mirrored networking (`networkingMode=mirrored` in `.wslconfig`), `localhost` works directly. Otherwise you may need the WSL IP (`hostname -I` inside WSL)

### Service keeps restarting

The service restarts on failure with a 5-second delay. Check logs to find the root cause:

```bash
journalctl -u tradingbot -b --no-pager
```
