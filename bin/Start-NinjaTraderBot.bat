@echo off
:: Go to the folder where this .bat lives (bin/)
cd /d "%~dp0"

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
