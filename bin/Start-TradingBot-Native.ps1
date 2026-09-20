#Requires -Version 5.1
<#
.SYNOPSIS
    Launch TradingBot backend natively on Windows (no WSL).

.DESCRIPTION
    Sets required environment variables and runs the Python backend via Poetry
    from the project root.  Intended for live trading on Windows where WSL is
    unreliable.

.PARAMETER ProjectDir
    Optional project root.  Defaults to the parent directory of this script.

.PARAMETER NoLogWindow
    Do not open the live log viewer window.

.EXAMPLE
    .\Start-TradingBot-Native.ps1

.EXAMPLE
    .\Start-TradingBot-Native.ps1 -NoLogWindow
#>
[CmdletBinding()]
param(
    [string]$ProjectDir = $null,
    [switch]$NoLogWindow
)

$ErrorActionPreference = "Stop"

# Resolve project root (parent of bin/)
if (-not $ProjectDir) {
    $ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path
}

$pidFile = Join-Path $ProjectDir ".tradingbot_live_native.pid"
$logDir  = Join-Path $ProjectDir "logs\ninja"
$logFile = Join-Path $logDir "native_launcher.log"

# Ensure log directory exists
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Write-LauncherLog {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"
    $line = "[$ts] $Message"
    Write-Host $line
    $line | Out-File -FilePath $logFile -Append -Encoding utf8
}

function Get-EnvOrDefault {
    param([string]$Name, [string]$Default)
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value
}

Write-LauncherLog "=== TradingBot Native Launcher ==="
Write-LauncherLog "ProjectDir: $ProjectDir"

# Single-instance guard: a second launcher must never start (it would spawn
# duplicate backends and cause port conflicts).  The mutex is released by the
# OS when this process dies, so it can never go stale.
$script:instanceMutex = New-Object System.Threading.Mutex($false, "TradingBotNativeLauncher")
if (-not $script:instanceMutex.WaitOne(0)) {
    Write-LauncherLog "Another launcher instance is already running. Exiting."
    exit 0
}

# Load .env so DATABASE_URL / DB_PATH and other settings are available.
# python-dotenv will not override already-exported env vars, so we read it here
# to mirror the behavior of the shell launchers.
$envPath = Join-Path $ProjectDir ".env"
if (Test-Path $envPath) {
    Get-Content $envPath | ForEach-Object {
        if ($_ -match "^\s*([^#\s=]+)\s*=\s*[`"']?(.+?)[`"']?\s*$") {
            $name  = $Matches[1]
            $value = $Matches[2]
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
    Write-LauncherLog "Loaded .env"
} else {
    Write-LauncherLog "WARNING: .env not found at $envPath"
}

# Defaults matching the WSL ninja launcher, overridden for native Windows
$env:MODE          = Get-EnvOrDefault "MODE"          "live"
$env:INSTANCE_NAME = Get-EnvOrDefault "INSTANCE_NAME" "ninja"
$env:PLATFORM_TYPE = Get-EnvOrDefault "PLATFORM_TYPE" "ninjatrader"
$env:PAIR          = Get-EnvOrDefault "PAIR"          "MNQ"
$env:FLASK_PORT    = Get-EnvOrDefault "FLASK_PORT"    "5001"
$env:LOG_DIR       = Get-EnvOrDefault "LOG_DIR"       "logs/ninja"

# Native Windows defaults to the cp1252 codepage - force UTF-8 so log lines
# containing emoji/unicode never raise UnicodeEncodeError.
$env:PYTHONUTF8        = "1"
$env:PYTHONIOENCODING  = "utf-8"

# ZMQ must bind to localhost so NinjaTrader (same machine) can reach it
$env:ZMQ_HOST            = Get-EnvOrDefault "ZMQ_HOST"            "127.0.0.1"
$env:ZMQ_MARKET_PORT     = Get-EnvOrDefault "ZMQ_MARKET_PORT"     "5555"
$env:ZMQ_COMMAND_PORT    = Get-EnvOrDefault "ZMQ_COMMAND_PORT"    "5556"
$env:ZMQ_QUERY_PORT      = Get-EnvOrDefault "ZMQ_QUERY_PORT"      "5557"
$env:ZMQ_HEARTBEAT_PORT  = Get-EnvOrDefault "ZMQ_HEARTBEAT_PORT"  "5558"

# Database URL: prefer DATABASE_URL from .env, fall back to local SQLite
if (-not $env:DATABASE_URL) {
    $env:DB_PATH = Get-EnvOrDefault "DB_PATH" "sqlite:///./ninja.db"
}

Write-LauncherLog "Mode: $env:MODE | Pair: $env:PAIR | Instance: $env:INSTANCE_NAME"
Write-LauncherLog "Flask port: $env:FLASK_PORT"
Write-LauncherLog "ZMQ: $env:ZMQ_MARKET_PORT/$env:ZMQ_COMMAND_PORT/$env:ZMQ_QUERY_PORT/$env:ZMQ_HEARTBEAT_PORT"
if ($env:DATABASE_URL) {
    $maskedDb = $env:DATABASE_URL -replace "://([^:]+):[^@]+@", "://`$1:***@"
    Write-LauncherLog "DB: $maskedDb"
} else {
    Write-LauncherLog "DB: $env:DB_PATH"
}

# Kill any existing bot process tracked by the native PID file
if (Test-Path $pidFile) {
    $oldPid = Get-Content $pidFile -ErrorAction SilentlyContinue | ForEach-Object { $_.Trim() }
    if ($oldPid -match '^\d+$') {
        try {
            $oldProc = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
            if ($oldProc) {
                Write-LauncherLog "Killing existing bot process PID $oldPid"
                Stop-Process -Id ([int]$oldPid) -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
            }
        } catch {}
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# Verify Poetry and Python are available
$poetryCmd = Get-Command poetry -ErrorAction SilentlyContinue
if (-not $poetryCmd) {
    Write-LauncherLog "ERROR: poetry not found in PATH. Run .\bin\Setup-TradingBot.ps1 first."
    exit 1
}
Write-LauncherLog "Poetry: $($poetryCmd.Source)"

# Resolve the virtualenv Python so we can run it directly (poetry run spawns a
# wrapper process that exits immediately, which breaks the restart loop).
$venvPath = & poetry env info --path 2>$null
if (-not $venvPath -or -not (Test-Path $venvPath)) {
    Write-LauncherLog "ERROR: Could not find Poetry virtualenv. Run 'poetry install' first."
    exit 1
}
$pythonExe = Join-Path $venvPath "Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-LauncherLog "ERROR: Python not found in virtualenv at $venvPath"
    exit 1
}
Write-LauncherLog "Python: $pythonExe"

# Write our own PID before starting
$PID | Out-File -FilePath $pidFile -Encoding utf8
Write-LauncherLog "Launcher PID: $PID"

# Start the backend with auto-restart on crash
$maxRestarts = 5
$restartDelaySeconds = 10
$restartCount = 0

Push-Location $ProjectDir
try {
    while ($true) {
        Write-LauncherLog "Starting backend (attempt $($restartCount + 1))..."
        $process = Start-Process -FilePath $pythonExe `
            -ArgumentList @("backend/run.py") `
            -WorkingDirectory $ProjectDir `
            -NoNewWindow `
            -RedirectStandardOutput (Join-Path $logDir "native_stdout.log") `
            -RedirectStandardError  (Join-Path $logDir "native_stderr.log") `
            -PassThru

        Write-LauncherLog "Backend started with PID $($process.Id)"
        Write-LauncherLog "Stdout: $(Join-Path $logDir 'native_stdout.log')"
        Write-LauncherLog "Stderr: $(Join-Path $logDir 'native_stderr.log')"

        # Update PID file to the actual backend process
        $process.Id | Out-File -FilePath $pidFile -Encoding utf8

        # Open a live log viewer window unless disabled (first attempt only,
        # so restarts do not pile up viewer windows)
        if (-not $NoLogWindow -and $restartCount -eq 0) {
            $watchScript = Join-Path $ProjectDir "bin\Watch-TradingBotLogs.ps1"
            if (Test-Path $watchScript) {
                Start-Process -FilePath "powershell.exe" `
                    -ArgumentList "-ExecutionPolicy Bypass -NoProfile -File `"$watchScript`" -Lines 20" `
                    -WindowStyle Normal
                Write-LauncherLog "Opened log viewer window"
            }
        }

        # Wait for the backend and surface exit code
        $process.WaitForExit()
        $exitCode = $process.ExitCode
        Write-LauncherLog "Backend exited with code $exitCode"

        if ($exitCode -eq 0) {
            Write-LauncherLog "Backend exited cleanly. Stopping."
            break
        }

        $restartCount++
        if ($restartCount -gt $maxRestarts) {
            Write-LauncherLog "ERROR: Backend crashed $restartCount times. Giving up."
            exit $exitCode
        }

        Write-LauncherLog "Backend crashed. Restarting in $restartDelaySeconds seconds..."
        Start-Sleep -Seconds $restartDelaySeconds
    }
} finally {
    Pop-Location
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}
