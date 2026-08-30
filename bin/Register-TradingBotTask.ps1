#Requires -Version 5.1
<#
.SYNOPSIS
    Register the TradingBot native backend as a Windows Task Scheduler task.

.DESCRIPTION
    Creates a scheduled task that starts the native TradingBot backend at user
    logon and restarts it on failure.  Intended to replace the WSL systemd
    service for live trading.

.PARAMETER ProjectDir
    Optional project root.  Defaults to the parent directory of this script.

.PARAMETER TaskName
    Name of the scheduled task.  Default: TradingBotNative

.EXAMPLE
    .\Register-TradingBotTask.ps1

.EXAMPLE
    .\Register-TradingBotTask.ps1 -TaskName "TradingBotNative"
#>
[CmdletBinding()]
param(
    [string]$ProjectDir = $null,
    [string]$TaskName = "TradingBotNative"
)

$ErrorActionPreference = "Stop"

if (-not $ProjectDir) {
    $ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
}

$launcher = Join-Path $ProjectDir "bin\Start-TradingBot-Native.ps1"
if (-not (Test-Path $launcher)) {
    throw "Launcher not found: $launcher"
}

Write-Host "ProjectDir: $ProjectDir"
Write-Host "Launcher:   $launcher"
Write-Host "TaskName:   $TaskName"

# Remove existing task if present
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed existing task '$TaskName'."
} catch {
    # Task did not exist
}

# Build the task
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$launcher`""

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

# Use Limited run level so the task can be registered without admin elevation.
# The launcher script does not need admin rights.
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "TradingBot native backend (no WSL)" `
    -Force

Write-Host ""
Write-Host "Scheduled task '$TaskName' registered successfully."
Write-Host "It will start at logon and restart on failure."
Write-Host ""
Write-Host "Manage with:"
Write-Host "  Start-ScheduledTask   -TaskName '$TaskName'"
Write-Host "  Stop-ScheduledTask    -TaskName '$TaskName'"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
