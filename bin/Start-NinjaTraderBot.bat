@echo off
:: Go to the folder where this .bat lives (bin/)
cd /d "%~dp0"

:: Test whether WSL's Windows interop is working by trying to run powershell.exe.
:: If it fails, restart WSL so interop is available for NinjaTrader auto-login.
wsl -e powershell.exe -Command "Get-Host" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN]  WSL Windows interop is not available ^(powershell.exe failed^). Restarting WSL...
    wsl --shutdown
    echo [INFO]  WSL restarted.
    echo.
)

echo [INFO]  Launching TradingBot NinjaTrader instance...
echo [INFO]  Directory: %cd%
echo [INFO]  Press Ctrl+C to stop gracefully, then any key to close this window.
echo.

:: Run the ninjatrader start script inside WSL through an interactive shell
:: so that .zshrc / .bashrc loads and poetry is available in PATH.
:: start_ninjatrader.sh computes its own project root, so it works from bin/
wsl -e zsh -ilc "./start_ninjatrader.sh"

echo.
pause
