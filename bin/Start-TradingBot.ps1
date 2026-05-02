#Requires -Version 5.1
param(
    [string]$WslDistro = "Ubuntu"
)

$ErrorActionPreference = "Stop"

$ts = Get-Date -Format "HH:mm:ss"
Write-Host "[$ts] [INFO]  Starting TradingBot service in WSL ($WslDistro)..."

wsl -d $WslDistro -u root systemctl start tradingbot | Out-Null

$ts = Get-Date -Format "HH:mm:ss"
Write-Host "[$ts] [OK]    TradingBot service start command sent."
Write-Host "[$ts] [INFO]  Check status: wsl -d $WslDistro -u root systemctl status tradingbot"
