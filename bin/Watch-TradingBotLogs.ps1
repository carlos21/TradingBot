#Requires -Version 5.1
<#
.SYNOPSIS
    Watch live TradingBot logs in the terminal.

.DESCRIPTION
    Tails today's app log file and shows new entries as they are written.
    Similar to 'tail -f' on Linux.

.PARAMETER LogType
    Which log to watch: 'app' (default), 'stdout', 'stderr', or 'launcher'.

.PARAMETER Lines
    Number of existing lines to show before tailing. Default: 50.

.EXAMPLE
    .\Watch-TradingBotLogs.ps1

.EXAMPLE
    .\Watch-TradingBotLogs.ps1 -LogType stderr

.EXAMPLE
    .\Watch-TradingBotLogs.ps1 -Lines 100
#>
[CmdletBinding()]
param(
    [ValidateSet("app", "stdout", "stderr", "launcher")]
    [string]$LogType = "app",
    [int]$Lines = 50
)

$ErrorActionPreference = "Stop"

$projectDir = (Resolve-Path "$PSScriptRoot\..").Path
$logDir = Join-Path $projectDir "logs\ninja"

# Determine which file to watch
switch ($LogType) {
    "app" {
        $today = Get-Date -Format "yyyy-MM-dd"
        $logFile = Join-Path $logDir "app_$today.log"
        $description = "app log"
    }
    "stdout" {
        $logFile = Join-Path $logDir "native_stdout.log"
        $description = "stdout"
    }
    "stderr" {
        $logFile = Join-Path $logDir "native_stderr.log"
        $description = "stderr"
    }
    "launcher" {
        $logFile = Join-Path $logDir "native_launcher.log"
        $description = "launcher log"
    }
}

if (-not (Test-Path $logFile)) {
    Write-Host "Log file not found: $logFile" -ForegroundColor Red
    Write-Host ""
    Write-Host "Available log files:"
    Get-ChildItem $logDir -Filter "*.log" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 10 |
        ForEach-Object { Write-Host "  $($_.Name)  ($($_.LastWriteTime))" }
    exit 1
}

Write-Host "Watching $description`: $logFile" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

# Show existing lines
Get-Content $logFile -Tail $Lines

# Tail new content
$lastLength = (Get-Item $logFile).Length
while ($true) {
    Start-Sleep -Milliseconds 500
    $currentLength = (Get-Item $logFile).Length
    if ($currentLength -gt $lastLength) {
        $fs = [System.IO.FileStream]::new($logFile, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $fs.Position = $lastLength
        $sr = [System.IO.StreamReader]::new($fs)
        try {
            $newContent = $sr.ReadToEnd()
            if ($newContent) {
                Write-Host $newContent -NoNewline
            }
            $lastLength = $fs.Position
        } finally {
            $sr.Close()
            $fs.Close()
        }
    }
}
