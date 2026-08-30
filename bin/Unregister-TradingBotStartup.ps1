#Requires -Version 5.1
<#
.SYNOPSIS
    Remove the TradingBot native backend from Windows startup.

.DESCRIPTION
    Deletes the startup shortcut and optionally the scheduled task so the
    backend no longer starts automatically at logon.

.PARAMETER RemoveScheduledTask
    Also unregister the Task Scheduler task (requires admin if task was
    registered with elevated rights).

.EXAMPLE
    .\Unregister-TradingBotStartup.ps1

.EXAMPLE
    .\Unregister-TradingBotStartup.ps1 -RemoveScheduledTask
#>
[CmdletBinding()]
param(
    [switch]$RemoveScheduledTask
)

$ErrorActionPreference = "Stop"

$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "TradingBotNative.lnk"

Write-Host "Removing TradingBot from startup..."

# Remove startup shortcut
if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
    Write-Host "Removed startup shortcut: $shortcutPath"
} else {
    Write-Host "Startup shortcut not found (already removed?)"
}

# Optionally remove scheduled task
if ($RemoveScheduledTask) {
    $taskName = "TradingBotNative"
    try {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
        Write-Host "Removed scheduled task: $taskName"
    } catch {
        Write-Host "Scheduled task '$taskName' not found or could not be removed."
    }
}

# Kill any running backend processes
$pythonProcs = Get-Process -Name python* -ErrorAction SilentlyContinue
if ($pythonProcs) {
    Write-Host "Stopping $($pythonProcs.Count) running backend process(es)..."
    $pythonProcs | Stop-Process -Force
    Write-Host "Backend stopped."
} else {
    Write-Host "No backend processes running."
}

# Remove PID files
$projectDir = (Resolve-Path "$PSScriptRoot\..").Path
$pidFiles = @(
    (Join-Path $projectDir ".tradingbot_live_native.pid"),
    (Join-Path $projectDir ".tradingbot_live.pid")
)
foreach ($pf in $pidFiles) {
    if (Test-Path $pf) {
        Remove-Item $pf -Force
        Write-Host "Removed PID file: $pf"
    }
}

Write-Host ""
Write-Host "TradingBot will no longer start at logon."
Write-Host "To re-enable, run: .\bin\Register-TradingBotStartup.ps1"
