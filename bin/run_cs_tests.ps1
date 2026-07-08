# Run the NinjaTrader C# test suite from Windows PowerShell.

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$csTestDir = Join-Path $repoRoot "zmq_connectors\TradingBot.NinjaTrader.Zmq.Tests"

Write-Host "▶ Running C# tests in: $csTestDir"
Set-Location $csTestDir
dotnet test TradingBot.NinjaTrader.Zmq.Tests.csproj --nologo

Write-Host "✓ C# tests finished"
