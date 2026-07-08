#!/usr/bin/env bash
set -euo pipefail

# Run the NinjaTrader C# test suite from WSL.
# This invokes Windows PowerShell so dotnet can target net48.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CS_TEST_DIR="$REPO_ROOT/zmq_connectors/TradingBot.NinjaTrader.Zmq.Tests"

if ! command -v wslpath &>/dev/null; then
    echo "wslpath not found. This script is intended to run inside WSL."
    exit 1
fi

WIN_CS_TEST_DIR="$(wslpath -w "$CS_TEST_DIR")"

echo "▶ Running C# tests in: $CS_TEST_DIR"
powershell.exe -Command "cd '$WIN_CS_TEST_DIR'; dotnet test TradingBot.NinjaTrader.Zmq.Tests.csproj --nologo"

echo "✓ C# tests finished"
