#Requires -Version 5.1
<#
.SYNOPSIS
    Force restart the TradingBot native backend, leaving no weird state behind.

.DESCRIPTION
    Guarantees a single clean instance afterwards:

      1. Kills EVERY TradingBot-related process: the Python backend
         (backend/run.py), launcher instances (Start-TradingBot-Native.ps1)
         and log viewers (Watch-TradingBotLogs.ps1) - matched by command line,
         so it also finds orphans whose PID file was lost.
      2. Waits until every one of them is actually dead.
      3. Frees ports 5001 and 5555-5558 if an orphan still holds them.
      4. Deletes stale PID files.
      5. Starts exactly one new launcher (hidden window).
      6. Verifies the backend answers on http://localhost:5001 and reports
         the result.

    Safe to run repeatedly, safe to run while the bot is running, safe to
    run when nothing is running at all.

.PARAMETER NoLogWindow
    Do not open the live log viewer window.

.EXAMPLE
    .\Restart-TradingBot-Native.ps1

.EXAMPLE
    .\Restart-TradingBot-Native.ps1 -NoLogWindow
#>
[CmdletBinding()]
param(
    [switch]$NoLogWindow
)

$ErrorActionPreference = "Stop"
$projectDir = (Resolve-Path "$PSScriptRoot\..").Path

Write-Host "=== TradingBot Force Restart ===" -ForegroundColor Cyan

# --- Helpers ---------------------------------------------------------------

function Get-TradingBotProcesses {
    <#
    .SYNOPSIS
        All processes belonging to the bot: backend python, launcher and log
        viewer powershell instances.  Matches on CommandLine so orphans
        without a PID file are found too.  Never returns this script itself.
    #>
    $patterns = @(
        "*backend/run.py*",
        "*backend\run.py*",
        "*Start-TradingBot-Native*",
        "*Watch-TradingBotLogs*"
    )
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessId -ne $PID } |
        Where-Object {
            $cmd = $_.CommandLine
            if ([string]::IsNullOrEmpty($cmd)) { return $false }
            foreach ($p in $patterns) {
                if ($cmd -like $p) { return $true }
            }
            return $false
        }
}

function Wait-AllDead {
    param([int]$TimeoutSeconds = 15)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $remaining = @(Get-TradingBotProcesses)
        if ($remaining.Count -eq 0) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return ($false)
}

function Free-Port {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $owner = $c.OwningProcess
        if ($owner -and $owner -ne $PID -and $owner -ne 0) {
            Write-Host "  Port $Port still held by PID $owner - killing it." -ForegroundColor Yellow
            Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
        }
    }
}

# --- 1. Kill everything ----------------------------------------------------

$targets = @(Get-TradingBotProcesses)
if ($targets.Count -gt 0) {
    Write-Host "Killing $($targets.Count) TradingBot process(es):" -ForegroundColor Yellow
    foreach ($t in $targets) {
        $desc = if ($t.CommandLine.Length -gt 80) { $t.CommandLine.Substring(0, 80) + "..." } else { $t.CommandLine }
        Write-Host "  PID $($t.ProcessId)  $($t.Name)  $desc" -ForegroundColor Gray
        Stop-Process -Id $t.ProcessId -Force -ErrorAction SilentlyContinue
    }

    if (-not (Wait-AllDead -TimeoutSeconds 15)) {
        Write-Host "Some processes refused to die - killing leftovers again." -ForegroundColor Red
        Get-TradingBotProcesses | ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
        [void](Wait-AllDead -TimeoutSeconds 5)
    }
} else {
    Write-Host "No TradingBot processes found." -ForegroundColor Gray
}

# --- 2. Make sure the ports are free ---------------------------------------

$ports = @(5001, 5555, 5556, 5557, 5558)
foreach ($p in $ports) { Free-Port -Port $p }
Start-Sleep -Seconds 1

# --- 3. Remove stale PID files ----------------------------------------------

foreach ($pf in @(".tradingbot_live_native.pid", ".tradingbot_live.pid")) {
    $full = Join-Path $projectDir $pf
    if (Test-Path $full) {
        Remove-Item $full -Force
        Write-Host "Removed stale PID file: $pf" -ForegroundColor Gray
    }
}

# --- 4. Start exactly one launcher ------------------------------------------

Write-Host ""
Write-Host "Starting backend..." -ForegroundColor Green

$launcher = Join-Path $projectDir "bin\Start-TradingBot-Native.ps1"
$launcherArgs = @("-ExecutionPolicy", "Bypass", "-NoProfile", "-File", "`"$launcher`"")
if ($NoLogWindow) {
    $launcherArgs += "-NoLogWindow"
}
Start-Process -FilePath "powershell.exe" -ArgumentList $launcherArgs -WindowStyle Hidden

# --- 5. Verify the backend actually comes up ---------------------------------

$deadline = (Get-Date).AddSeconds(45)
$ok = $false
while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:5001" -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -eq 200) { $ok = $true; break }
    } catch {
        Start-Sleep -Seconds 2
    }
}

Write-Host ""
if ($ok) {
    $backendPid = Get-Content (Join-Path $projectDir ".tradingbot_live_native.pid") -ErrorAction SilentlyContinue
    Write-Host "OK - backend responding on http://localhost:5001 (PID $backendPid)" -ForegroundColor Green
    exit 0
} else {
    Write-Host "FAILED - backend did not respond within 45s." -ForegroundColor Red
    Write-Host "Check logs\ninja\native_stderr.log and logs\ninja\native_launcher.log" -ForegroundColor Red
    exit 1
}
