#Requires -RunAsAdministrator
#Requires -Version 5.1

<#
.SYNOPSIS
    Disables automatic reboot from Windows Update for a trading workstation.

.DESCRIPTION
    Configures Windows Update policies to:
    - Prevent automatic restart while a user is logged on.
    - Notify before downloading/installing updates (manual control).
    - Set active hours to 00:00-23:00 to cover the trading day.

    Run this once as Administrator, then reboot.

.EXAMPLE
    .\Disable-WindowsUpdateAutoReboot.ps1
#>

$ErrorActionPreference = "Stop"

function Write-Info {
    param([string]$Message)
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] [INFO]  $Message"
}

function Write-Ok {
    param([string]$Message)
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] [OK]    $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] [WARN]  $Message" -ForegroundColor Yellow
}

Write-Info "Configuring Windows Update to prevent automatic reboots..."

# Ensure the WindowsUpdate/AU policy key exists.
$auPath = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
if (-not (Test-Path $auPath)) {
    Write-Info "Creating Windows Update AU policy key..."
    New-Item -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" -Name "AU" -Force | Out-Null
}

# 1. Do not auto-restart while a user is logged on.
Write-Info "Setting NoAutoRebootWithLoggedOnUsers = 1"
Set-ItemProperty -Path $auPath -Name "NoAutoRebootWithLoggedOnUsers" -Value 1 -Type DWord -Force

# 2. Notify for download and notify for install (manual control over updates).
Write-Info "Setting AUOptions = 2 (notify before download/install)"
Set-ItemProperty -Path $auPath -Name "AUOptions" -Value 2 -Type DWord -Force

# 3. Set active hours to 00:00 - 23:00 to cover the entire trading day.
$settingsPath = "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
if (Test-Path $settingsPath) {
    Write-Info "Setting active hours to 00:00 - 23:00"
    Set-ItemProperty -Path $settingsPath -Name "ActiveHoursStart" -Value 0 -Type DWord -Force
    Set-ItemProperty -Path $settingsPath -Name "ActiveHoursEnd" -Value 23 -Type DWord -Force
}
else {
    Write-Warn "Windows Update UX settings key not found; active hours not set."
}

Write-Ok "Windows Update auto-reboot disabled."
Write-Info "Reboot the computer now for the policy changes to take effect."
Write-Info "After reboot, Windows will notify you about updates but will not restart automatically."
