#!/usr/bin/env bash
set -euo pipefail

echo "=== Liquid Setup ==="

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="mac"
elif grep -qi microsoft /proc/version 2>/dev/null; then
    OS="wsl"
else
    OS="linux"
fi
echo "Detected OS: $OS"

# 1. Install Poetry if missing
if ! command -v poetry &>/dev/null; then
    echo "Installing Poetry..."
    curl -sSL https://install.python-poetry.org | python3 -
fi

# 2. Install Python dependencies
echo "Installing Python dependencies..."
poetry install --no-root

# 3. Install Playwright browsers
echo "Installing Playwright browsers..."
poetry run playwright install chromium

# 4. Install Playwright system dependencies (needs sudo on Linux/WSL)
if [[ "$OS" == "wsl" || "$OS" == "linux" ]]; then
    echo "Installing Playwright system dependencies (requires sudo)..."
    sudo poetry run playwright install-deps chromium
elif [[ "$OS" == "mac" ]]; then
    echo "macOS: Playwright system deps are bundled with the browser download."
fi

# 5. Fix line endings on shell scripts (in case of Windows checkout)
if command -v sed &>/dev/null; then
    echo "Fixing line endings in bin/*.sh..."
    sed -i'' -e 's/\r$//' bin/*.sh 2>/dev/null || true
fi

echo ""
echo "=== Setup complete! ==="
echo "Run the app:        poetry run python app.py"
echo "Run scenarios:      ./bin/run_scenarios.sh"
