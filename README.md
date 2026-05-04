# TradingBot

Automated trading bot with NinjaTrader ZMQ integration.

## Quick Start (Windows Live Mode)

1. **Clone the repo**
   ```powershell
   git clone <private-repo-url>
   cd TradingBot
   ```

2. **Run the installer**
   ```powershell
   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
   .\bin\Install-TradingBot.ps1
   ```
   The installer will:
   - Check Python 3.11+ and Git
   - Install Poetry (Python dependency manager)
   - Install Python dependencies
   - Find NinjaTrader and copy AddOn files
   - Install NetMQ DLLs
   - Prompt for trading settings and save them to `.env`
   - Create a desktop shortcut for **TradingBot Manager**

3. **Compile NinjaScript (one-time manual step)**
   - Open NinjaTrader 8
   - Go to `Tools → Edit NinjaScript`
   - Add references to:
     - `NetMQ.dll` (from `bin\Custom\`)
     - `netstandard.dll` (from `bin\Custom\refs\`)
   - Press `F5` to compile
   - Close NinjaTrader

4. **Launch the TradingBot Manager**
   - Double-click the **TradingBot Manager** shortcut on your desktop
   - Click **Start Bot** in the Dashboard tab
   - Open your browser to `http://localhost:5001`

## TradingBot Manager

The Manager is a Windows GUI for day-to-day operations.

| Tab | Purpose |
|-----|---------|
| **Dashboard** | Start / stop the bot and view live logs |
| **Settings** | Edit trading parameters (pair, risk, account, ports). Changes are saved to `.env`. |
| **NinjaTrader** | Re-copy AddOn files, reinstall NetMQ, or update NinjaTrader credentials |
| **Update** | Check for updates (`git fetch`) or pull the latest code and restart the bot |

## Configuration

All settings are stored in `.env` at the project root. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MODE` | `live` | Run mode: `live` or `backtest` |
| `PAIR` | `MNQ` | Trading instrument |
| `RISK` | `160` | Fixed dollar risk per trade |
| `ACCOUNT_BALANCE` | `100000` | Account balance for position sizing |
| `NT_ACCOUNTS` | — | NinjaTrader account(s). Comma-separated list with optional per-account risk overrides. Example: `Account1:risk=100,Account2:risk_pct=1.5` |
| `FLASK_PORT` | `5001` | Web UI port |
| `ZMQ_HOST` | `127.0.0.1` | ZeroMQ broker host |
| `ZMQ_MARKET_PORT` | `5555` | Market data port |
| `ZMQ_COMMAND_PORT` | `5556` | Trade commands port |

You can edit these in the **Settings** tab of the Manager or by editing `.env` directly.

## Updating

Use the **Update** tab in the TradingBot Manager, or run manually:

```powershell
git pull
poetry install --no-root
```

## Requirements

- Windows 10/11
- PowerShell 5.1+
- Python 3.11+
- Git
- NinjaTrader 8

## Project Structure

```
TradingBot/
├── app.py                      # Entry point
├── app_factory.py              # Flask app factory
├── .env                        # Configuration (committed in private repo)
├── pyproject.toml              # Python dependencies (Poetry)
├── bin/
│   ├── Install-TradingBot.ps1  # One-time installer
│   └── TradingBot-Manager.ps1  # Daily-use GUI
├── src/                        # Strategy, data sources, services
├── zmq_connectors/
│   └── ninjatrader/            # C# AddOn source files
└── tests/                      # Test scenarios
```

## Commands (Advanced)

Start the bot from the command line:
```powershell
poetry run python app.py
```

Override settings via CLI:
```powershell
poetry run python app.py --pair MNQ --risk 160 --mode live
```

## Notes

- This repo is **private** and `.env` is committed for convenience across trusted machines.
- Playwright and scenario tests are not installed on live trading PCs. They remain available in the repo for development machines.
- The bot runs natively on Windows without WSL.
