#Requires -Version 5.1
<#
.SYNOPSIS
    Creates a Windows desktop shortcut that runs the TradingBot one-click launcher.

.DESCRIPTION
    Generates a .lnk file on the user's desktop pointing to Start-TradingBot.ps1.
    The shortcut runs PowerShell with -ExecutionPolicy Bypass so the user is not
    prompted every time.

.PARAMETER TradingBotRoot
    Root directory of the TradingBot repository.
    Default: parent directory of the folder containing this script.

.PARAMETER ShortcutPath
    Full path where the .lnk file should be created.
    Default: %USERPROFILE%\Desktop\Start TradingBot.lnk

.EXAMPLE
    .\Create-DesktopShortcut.ps1

.EXAMPLE
    .\Create-DesktopShortcut.ps1 -ShortcutPath "C:\Users\Carlos\Desktop\TradingBot.lnk"
#>
[CmdletBinding()]
param(
    [string]$TradingBotRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$ShortcutPath = "$env:USERPROFILE\Desktop\Start TradingBot.lnk"
)

$ErrorActionPreference = "Stop"

$launcher = Join-Path $TradingBotRoot "bin\Start-TradingBot.ps1"
if (-not (Test-Path $launcher)) {
    Write-Error "Launcher script not found: $launcher"
    exit 1
}

Write-Host "Creating desktop shortcut..."
Write-Host "  Source: $launcher"
Write-Host "  Target: $ShortcutPath"

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "powershell.exe"
$Shortcut.Arguments = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
$Shortcut.WorkingDirectory = $TradingBotRoot
$Shortcut.Description = "Launch TradingBot WSL service + NinjaTrader + ZMQ Connector"
$Shortcut.IconLocation = "powershell.exe,0"
$Shortcut.Save()

Write-Host ""
Write-Host "Shortcut created successfully!" -ForegroundColor Green
Write-Host "  $ShortcutPath"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Run Set-NTCredential.ps1 to store your NinjaTrader password."
Write-Host "  2. Compile the updated TradingBotZmqConnector AddOn in NinjaTrader."
Write-Host "  3. Create Documents\NinjaTrader 8\bin\Custom\TradingBotZmqConfig.json"
Write-Host "     with: { `"autoConnectOnStartup`": true }"
Write-Host "  4. Double-click the shortcut on your desktop."
Write-Host ""
