#!/usr/bin/env bash
set -euo pipefail

# Installs the TradingBot systemd service on WSL.
# Run once:  sudo ./bin/install_service.sh
#
# After install:
#   sudo systemctl start tradingbot     — start the server
#   sudo systemctl stop tradingbot      — stop the server
#   sudo systemctl status tradingbot    — check status
#   journalctl -u tradingbot -f         — tail logs
#
# The service auto-starts on WSL boot and restarts on crash.

BOLD='\033[1m'
GREEN='\033[92m'
RED='\033[91m'
RST='\033[0m'

if [ "$(id -u)" -ne 0 ]; then
  echo -e "${RED}Error: run with sudo${RST}"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SERVICE_SRC="$PROJECT_DIR/services/tradingbot.service"

WSL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo $USER)}"
WSL_HOME=$(eval echo "~$WSL_USER")

echo -e "${BOLD}Installing TradingBot service for user: $WSL_USER${RST}"
echo ""

# Generate the actual service file with resolved paths
cat > /etc/systemd/system/tradingbot.service <<EOF
[Unit]
Description=TradingBot Live Server
After=network.target

[Service]
Type=simple
User=$WSL_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/bin/start_live.sh

# Defaults are in bin/start_live.sh — override here only if needed
Environment=HOME=$WSL_HOME
Environment=PATH=$WSL_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin

Restart=on-failure
RestartSec=5

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable tradingbot

echo ""
echo -e "${GREEN}${BOLD}Installed and enabled.${RST}"
echo ""
echo "  Start now:   sudo systemctl start tradingbot"
echo "  Check:       sudo systemctl status tradingbot"
echo "  Logs:        journalctl -u tradingbot -f"
echo "  Stop:        sudo systemctl stop tradingbot"
echo "  Uninstall:   sudo systemctl disable tradingbot && sudo rm /etc/systemd/system/tradingbot.service"
echo ""
