# One-Click TradingBot Launcher

> **DEPRECATED** — The PowerShell-based manager (`bin/TradingBot-Manager.ps1`) is no longer maintained.  
> All configuration, bot lifecycle, and NinjaTrader management is now handled through the **web admin** at `http://localhost:5001/admin`.

Launch everything — WSL Python service + NinjaTrader + ZMQ Connector — with a single double-click.

---

## What It Does

A Windows PowerShell script (`bin/Start-TradingBot.ps1`) that, in order:

1. **Starts the TradingBot systemd service** inside WSL (or verifies it is already running).
2. **Waits for ZMQ ports** (5555-5558) to be reachable from Windows.
3. **Launches NinjaTrader 8**.
4. **Automatically logs in** to NinjaTrader using credentials stored securely with Windows DPAPI encryption.
5. **Waits for the Control Center** to appear.
6. The **TradingBotZmqConnector AddOn** (with `autoConnectOnStartup: true`) automatically opens its status window and connects to Python.
7. **Confirms readiness** and shows a Windows notification.

---

## Files Added / Modified

| File | Purpose |
|------|---------|
| `bin/Start-TradingBot.ps1` | Main one-click launcher |
| `bin/Set-NTCredential.ps1` | Securely stores your NinjaTrader username/password |
| `bin/Create-DesktopShortcut.ps1` | Generates the desktop `.lnk` shortcut |
| `zmq_connectors/ninjatrader/Domain/ValueObjects.cs` | Added `AutoConnectOnStartup` and `AutoShowWindow` to `ZmqConfiguration` |
| `zmq_connectors/ninjatrader/Infrastructure/ConfigLoader.cs` | **New** — loads `TradingBotZmqConfig.json` from NT's `bin\Custom` folder |
| `zmq_connectors/ninjatrader/TradingBotZmqConnector.cs` | Auto-shows and auto-connects on Control Center startup when enabled |

---

## Prerequisites

- Windows 10/11 with WSL2 and systemd enabled (see `docs/wsl_live_service.md`).
- The `tradingbot` systemd service already installed via `sudo ./bin/install_service.sh`.
- NinjaTrader 8 installed and the **TradingBotZmqConnector AddOn** already compiled at least once.
- NetMQ v4 DLLs already in `Documents\NinjaTrader 8\bin\Custom\`.

---

## Step-by-Step Setup

### 1. Store Your NinjaTrader Credentials

Run the credential helper **once** from PowerShell:

```powershell
cd C:\path\to\TradingBot
.\bin\Set-NTCredential.ps1
```

You will be prompted for your NinjaTrader username and password. The password is encrypted with Windows DPAPI (only your Windows account can decrypt it) and stored in:

```
%LOCALAPPDATA%\TradingBot\NTCredential.dat
```

### 2. Update the C# AddOn

Copy the updated source files into NinjaTrader:

```
zmq_connectors/ninjatrader/Domain/ValueObjects.cs        → Documents\NinjaTrader 8\bin\Custom\AddOns\Domain\
zmq_connectors/ninjatrader/Infrastructure/ConfigLoader.cs → Documents\NinjaTrader 8\bin\Custom\AddOns\Infrastructure\
zmq_connectors/ninjatrader/TradingBotZmqConnector.cs      → Documents\NinjaTrader 8\bin\Custom\AddOns\
```

In NinjaTrader: **Tools → Edit NinjaScript → right-click AddOns → Compile (F5)**.

### 3. Create the JSON Config File

Create a new file:

```
Documents\NinjaTrader 8\bin\Custom\TradingBotZmqConfig.json
```

With the following content:

```json
{
  "autoConnectOnStartup": true,
  "autoShowWindow": true
}
```

All other fields are optional and will use hard-coded defaults if omitted. Example with overrides:

```json
{
  "host": "127.0.0.1",
  "marketPort": 5555,
  "commandPort": 5556,
  "queryPort": 5557,
  "heartbeatPort": 5558,
  "autoConnectOnStartup": true,
  "autoShowWindow": true
}
```

> **Note:** The `instrument` field has been removed from this file. Set the instrument in **Admin → Settings**; the Python app sends it to NinjaTrader automatically when the connector starts.

> **Security note:** This JSON file does **not** contain your login credentials. Those are stored separately by `Set-NTCredential.ps1`.

### 4. Create the Desktop Shortcut

```powershell
.\bin\Create-DesktopShortcut.ps1
```

A shortcut named **"Start TradingBot"** appears on your desktop.

### 5. Launch

Double-click **"Start TradingBot"** on your desktop.

A hidden PowerShell window runs the full sequence. When everything is ready, a popup confirms:

> **TradingBot is live.**  
> WSL service: running  
> NinjaTrader: connected  
> ZMQ: active

---

## Customization

### Change NinjaTrader Path

If NinjaTrader is installed in a non-standard location, edit the shortcut or pass the parameter:

```powershell
.\bin\Start-TradingBot.ps1 -NinjaTraderPath "D:\NinjaTrader 8\bin64\NinjaTrader.exe"
```

### Change WSL Distribution

If your WSL distro is not named `Ubuntu`:

```powershell
.\bin\Start-TradingBot.ps1 -WslDistro "Debian"
```

### Debug Login Automation

If the login dialog is not being filled correctly, run with the debug flag:

```powershell
.\bin\Start-TradingBot.ps1 -DebugLogin
```

This prints diagnostic info about the controls found in the login window and pauses briefly so you can inspect it.

---

## Troubleshooting

### "Credential file not found"

Run `Set-NTCredential.ps1` first (see Step 1).

### "TradingBot service did not become active"

Check the service logs inside WSL:

```bash
wsl -d Ubuntu -u root journalctl -u tradingbot -n 50 --no-pager
```

Common causes:
- Poetry not on PATH inside WSL
- Dependencies not installed (`poetry install`)
- Port 5001 already in use

### "ZMQ ports are not all reachable"

Ensure WSL2 is using **mirrored networking** so `localhost` on Windows reaches WSL directly. Add to `%USERPROFILE%\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

Then restart WSL:

```powershell
wsl --shutdown
```

### NinjaTrader opens but the ZMQ Connector does not auto-connect

1. Verify the JSON config file exists at the exact path:
   ```
   Documents\NinjaTrader 8\bin\Custom\TradingBotZmqConfig.json
   ```
2. Verify `autoConnectOnStartup` is `true` (boolean, not string).
3. Check NinjaTrader's **Output** window for `[ZMQ]` messages. If you see `[ZMQ] Auto-connect failed: ...`, the AddOn tried but encountered an error.
4. Re-compile the AddOn in NinjaScript Editor (F5).

### Login automation fills the wrong fields

NinjaTrader updates can change dialog control names. Use `-DebugLogin` to inspect the window hierarchy. If UI Automation fails, the script automatically falls back to `SendKeys`, which simply sends **Tab** between the first two text boxes. If your login dialog has extra fields (e.g., a broker selector before username), you may need to adjust the script or enable **Remember Me** in NinjaTrader once to skip the dialog entirely.

### Shortcut does nothing

Right-click the shortcut → **Properties** and ensure the target path points to the correct `Start-TradingBot.ps1` location. Also verify PowerShell execution policy allows scripts:

```powershell
Get-ExecutionPolicy
```

If it returns `Restricted`, you can set it for the current user:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

(The shortcut already passes `-ExecutionPolicy Bypass`, but some corporate GPOs may override it.)

---

## Security Notes

- Credentials are encrypted with **Windows DPAPI** (`CurrentUser` scope). They can only be decrypted by the same Windows user on the same machine.
- The `TradingBotZmqConfig.json` file contains **no passwords**.
- Do not share the `%LOCALAPPDATA%\TradingBot\NTCredential.dat` file with anyone.
- If you change your NinjaTrader password, re-run `Set-NTCredential.ps1`.

---

## Architecture

```
 Windows Desktop
┌─────────────────────────────────────────┐
│  Start TradingBot.lnk                   │
│  └─> powershell Start-TradingBot.ps1    │
│       ├─> wsl systemctl start tradingbot│
│       ├─> Start-Process NinjaTrader.exe │
│       ├─> UI Automation / SendKeys      │
│       │      (login dialog)             │
│       └─> Wait for Control Center       │
│                                         │
│  NinjaTrader 8                          │
│  └─> TradingBotZmqConnector AddOn       │
│       └─> reads TradingBotZmqConfig.json│
│       └─> autoConnectOnStartup=true     │
│       └─> Connect() → ZMQ localhost     │
└─────────────────────────────────────────┘
                   │
        ZMQ 5555-5558
                   │
 WSL2 Linux       ▼
┌─────────────────────────────────────────┐
│  tradingbot systemd service             │
│  └─> Python Flask + ZMQ Gateway         │
│       └─> receives market data from NT  │
│       └─> sends trade commands to NT    │
└─────────────────────────────────────────┘
```
