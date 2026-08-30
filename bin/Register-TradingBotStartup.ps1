#Requires -Version 5.1
<#
.SYNOPSIS
    Register the TradingBot native backend to start at user logon via the Startup folder.

.DESCRIPTION
    Creates a shortcut in the current user's Startup folder that launches the
    native TradingBot backend at logon.  This method does not require admin
    rights, unlike Task Scheduler.

    The launcher script (Start-TradingBot-Native.ps1) includes an auto-restart
    loop, so the backend will be restarted if it crashes.

.PARAMETER ProjectDir
    Optional project root.  Defaults to the parent directory of this script.

.PARAMETER ShortcutName
    Name of the shortcut file.  Default: TradingBotNative

.EXAMPLE
    .\Register-TradingBotStartup.ps1
#>
[CmdletBinding()]
param(
    [string]$ProjectDir = $null,
    [string]$ShortcutName = "TradingBotNative"
)

$ErrorActionPreference = "Stop"

if (-not $ProjectDir) {
    $ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
}

$launcher = Join-Path $ProjectDir "bin\Start-TradingBot-Native.ps1"
if (-not (Test-Path $launcher)) {
    throw "Launcher not found: $launcher"
}

$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "$ShortcutName.lnk"

Write-Host "ProjectDir:   $ProjectDir"
Write-Host "Launcher:     $launcher"
Write-Host "Startup dir:  $startupDir"
Write-Host "Shortcut:     $shortcutPath"

# Remove existing shortcut if present
if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
    Write-Host "Removed existing shortcut."
}

$Wsh = New-Object -ComObject WScript.Shell
$Sc = $Wsh.CreateShortcut($shortcutPath)
$Sc.TargetPath = "powershell.exe"
$Sc.Arguments = "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$launcher`""
$Sc.WorkingDirectory = $ProjectDir
$Sc.Description = "TradingBot native backend (no WSL)"
$Sc.IconLocation = "powershell.exe,0"
$Sc.Save()

Write-Host ""
Write-Host "Startup shortcut created successfully."
Write-Host "The backend will start at user logon and auto-restart on crash."
Write-Host ""
Write-Host "To remove:"
Write-Host "  Remove-Item '$shortcutPath' -Force"
