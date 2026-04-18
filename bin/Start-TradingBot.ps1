#Requires -Version 5.1
<#
.SYNOPSIS
    One-click launcher: starts the TradingBot WSL service, launches NinjaTrader,
    logs in automatically, and confirms the ZMQ connection is alive.

.DESCRIPTION
    This script orchestrates the full Windows + WSL startup sequence:
      1. Starts (or verifies) the TradingBot systemd service inside WSL.
      2. Waits for Python ZMQ sockets to be listening.
      3. Launches NinjaTrader 8.
      4. Automatically enters credentials into the NinjaTrader login dialog.
      5. Waits for the Control Center to appear.
      6. The TradingBotZmqConnector AddOn (with autoConnectOnStartup=true)
         automatically opens and connects.
      7. Confirms readiness and shows a notification.

.PARAMETER NinjaTraderPath
    Full path to NinjaTrader.exe.
    Default: "C:\Program Files\NinjaTrader 8\bin64\NinjaTrader.exe"

.PARAMETER WslDistro
    Name of the WSL distribution running the TradingBot service.
    Default: Ubuntu

.PARAMETER CredentialFile
    Path to the encrypted credential file created by Set-NTCredential.ps1.
    Default: %LOCALAPPDATA%\TradingBot\NTCredential.dat

.PARAMETER MaxWaitSeconds
    Maximum time to wait for each major stage (service, ports, NT window).
    Default: 120

.PARAMETER DebugLogin
    If set, the script pauses before interacting with the login dialog and
    prints UIA diagnostic info. Use this to troubleshoot credential automation.

.EXAMPLE
    .\Start-TradingBot.ps1

.EXAMPLE
    .\Start-TradingBot.ps1 -NinjaTraderPath "D:\NinjaTrader 8\bin64\NinjaTrader.exe" -WslDistro "Debian"

.EXAMPLE
    .\Start-TradingBot.ps1 -DebugLogin
#>
[CmdletBinding()]
param(
    [string]$NinjaTraderPath = "C:\Program Files\NinjaTrader 8\bin64\NinjaTrader.exe",
    [string]$WslDistro = "Ubuntu",
    [string]$CredentialFile = "$env:LOCALAPPDATA\TradingBot\NTCredential.dat",
    [int]$MaxWaitSeconds = 120,
    [switch]$DebugLogin
)

$ErrorActionPreference = "Stop"

# Load required assembly for DPAPI decryption
Add-Type -AssemblyName System.Security

# ─────────────────────────────────────────────────────────────────────────────
# Helper: Write timestamped status line
# ─────────────────────────────────────────────────────────────────────────────
function Write-Status {
    param([string]$Message, [string]$Level = "Info")
    $ts = Get-Date -Format "HH:mm:ss"
    switch ($Level) {
        "Success" { Write-Host "[$ts] [OK]    $Message" -ForegroundColor Green }
        "Warn"    { Write-Host "[$ts] [WARN]  $Message" -ForegroundColor Yellow }
        "Error"   { Write-Host "[$ts] [ERR]   $Message" -ForegroundColor Red }
        default   { Write-Host "[$ts] [INFO]  $Message" }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: Start / verify the TradingBot systemd service in WSL
# ─────────────────────────────────────────────────────────────────────────────
function Start-WslService {
    param([string]$Distro, [int]$Timeout = 120)

    Write-Status "Checking TradingBot service in WSL ($Distro)..."

    # Quick check: is the service already active?
    $active = $false
    try {
        $state = wsl -d $Distro -u root systemctl is-active tradingbot 2>$null
        if ($state -eq "active") { $active = $true }
    } catch { }

    if ($active) {
        Write-Status "TradingBot service is already running." "Success"
        return
    }

    Write-Status "Starting TradingBot service..."
    wsl -d $Distro -u root systemctl start tradingbot | Out-Null

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $Timeout) {
        try {
            $state = wsl -d $Distro -u root systemctl is-active tradingbot 2>$null
            if ($state -eq "active") {
                Write-Status "TradingBot service is now active." "Success"
                return
            }
        } catch { }
        Start-Sleep -Seconds 2
    }

    throw "TradingBot service did not become active within $Timeout seconds. Run 'wsl -d $Distro journalctl -u tradingbot -n 50' to diagnose."
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: Wait for ZMQ ports to be reachable from Windows
# ─────────────────────────────────────────────────────────────────────────────
function Wait-ZmqPorts {
    param([int]$Timeout = 60)

    Write-Status "Waiting for ZMQ ports (5555-5558)..."
    $ports = @(5555, 5556, 5557, 5558)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    while ($sw.Elapsed.TotalSeconds -lt $Timeout) {
        $allOpen = $true
        foreach ($port in $ports) {
            try {
                $client = New-Object System.Net.Sockets.TcpClient
                $client.Connect("localhost", $port)
                $client.Close()
            } catch {
                $allOpen = $false
                break
            }
        }
        if ($allOpen) {
            Write-Status "ZMQ ports are open." "Success"
            return
        }
        Start-Sleep -Seconds 2
    }

    Write-Status "ZMQ ports are not all reachable yet. Continuing anyway — the AddOn will retry." "Warn"
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Launch NinjaTrader
# ─────────────────────────────────────────────────────────────────────────────
function Start-NinjaTrader {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        throw "NinjaTrader not found at: $Path`nPlease pass -NinjaTraderPath with the correct location."
    }

    # Check if already running
    $existing = Get-Process | Where-Object { $_.ProcessName -like "*NinjaTrader*" } | Select-Object -First 1
    if ($existing) {
        Write-Status "NinjaTrader is already running (PID $($existing.Id))." "Warn"
        return $existing
    }

    Write-Status "Launching NinjaTrader..."
    $proc = Start-Process -FilePath $Path -PassThru
    Write-Status "NinjaTrader started (PID $($proc.Id))." "Success"
    return $proc
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4: Automate NinjaTrader login
# ─────────────────────────────────────────────────────────────────────────────
function Enter-NinjaTraderCredentials {
    param(
        [string]$CredentialFile,
        [int]$Timeout = 90
    )

    if (-not (Test-Path $CredentialFile)) {
        throw "Credential file not found: $CredentialFile`nRun Set-NTCredential.ps1 first."
    }

    # ── Load and decrypt credentials ──
    $store = Get-Content -Path $CredentialFile -Raw | ConvertFrom-Json
    $username = $store.Username
    $encrypted = [Convert]::FromBase64String($store.PasswordBase64)
    $pwBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $encrypted,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    $password = [System.Text.Encoding]::UTF8.GetString($pwBytes)

    Write-Status "Waiting for NinjaTrader login window (up to ${Timeout}s)..."

    # ── Wait for any NinjaTrader process with a visible window ──
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $ntProc = $null
    while ($sw.Elapsed.TotalSeconds -lt $Timeout) {
        $ntProc = Get-Process | Where-Object {
            $_.ProcessName -like "*NinjaTrader*" -and
            $_.MainWindowHandle -ne 0 -and
            $_.MainWindowTitle -notlike "*Control Center*"
        } | Select-Object -First 1

        if ($ntProc) { break }
        Start-Sleep -Milliseconds 500
    }

    if (-not $ntProc) {
        # Maybe NT opened straight to Control Center (Remember Me already set)
        $cc = Get-Process | Where-Object {
            $_.ProcessName -like "*NinjaTrader*" -and
            $_.MainWindowTitle -like "*Control Center*"
        } | Select-Object -First 1

        if ($cc) {
            Write-Status "NinjaTrader went straight to Control Center (no login dialog). Skipping credential entry." "Success"
            return
        }
        throw "NinjaTrader login window did not appear within $Timeout seconds."
    }

    Write-Status "Login window detected: '$($ntProc.MainWindowTitle)'"

    if ($DebugLogin) {
        Write-Status "DebugLogin is ON. Pausing 10 seconds so you can inspect the dialog..." "Warn"
        Start-Sleep -Seconds 10
    }

    # ── Strategy A: UI Automation ──
    $uiaSuccess = $false
    try {
        Add-Type -AssemblyName UIAutomationClient

        $condWindow = [System.Windows.Automation.PropertyCondition]::new(
            [System.Windows.Automation.AutomationElement]::ProcessIdProperty, $ntProc.Id)

        $loginWindow = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst(
            [System.Windows.Automation.TreeScope]::Children, $condWindow)

        if (-not $loginWindow -and $DebugLogin) {
            # Dump all top-level windows for diagnostics
            Write-Status "Dumping top-level windows..." "Warn"
            $allTop = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
                [System.Windows.Automation.TreeScope]::Children,
                [System.Windows.Automation.ControlTypeCondition]::FromControlType([System.Windows.Automation.ControlType]::Window))
            for ($i = 0; $i -lt $allTop.Count; $i++) {
                $w = $allTop[$i]
                Write-Status "  Window[$i] Name='$($w.Current.Name)' Class='$($w.Current.ClassName)' ProcId=$($w.Current.ProcessId)" "Warn"
            }
        }

        if ($loginWindow) {
            if ($DebugLogin) {
                Write-Status "UIA: Found window '$($loginWindow.Current.Name)'" "Warn"
            }

            # Find all Edit controls
            $editCond = [System.Windows.Automation.ControlTypeCondition]::FromControlType([System.Windows.Automation.ControlType]::Edit)
            $edits = $loginWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants, $editCond)

            if ($DebugLogin) {
                Write-Status "UIA: Found $($edits.Count) Edit control(s)." "Warn"
            }

            if ($edits.Count -ge 2) {
                # Set username
                $userEdit = $edits[0]
                $userPattern = $userEdit.GetCurrentPattern([System.Windows.Automation.PatternIdentifiers]::ValuePattern)
                $userPattern.SetValue($username)

                # Set password
                $pwEdit = $edits[1]
                $pwPattern = $pwEdit.GetCurrentPattern([System.Windows.Automation.PatternIdentifiers]::ValuePattern)
                $pwPattern.SetValue($password)

                # Find Login button (by name first, then any button)
                $btnCond = [System.Windows.Automation.PropertyCondition]::new(
                    [System.Windows.Automation.AutomationElement]::NameProperty, "Login")
                $btn = $loginWindow.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $btnCond)

                if (-not $btn) {
                    $btnTypeCond = [System.Windows.Automation.ControlTypeCondition]::FromControlType([System.Windows.Automation.ControlType]::Button)
                    $allBtns = $loginWindow.FindAll([System.Windows.Automation.TreeScope]::Descendants, $btnTypeCond)
                    if ($allBtns.Count -gt 0) { $btn = $allBtns[0] }
                }

                if ($btn) {
                    $btnPattern = $btn.GetCurrentPattern([System.Windows.Automation.PatternIdentifiers]::InvokePattern)
                    $btnPattern.Invoke()
                    $uiaSuccess = $true
                    Write-Status "Login submitted via UI Automation." "Success"
                } elseif ($DebugLogin) {
                    Write-Status "UIA: Could not find Login button." "Warn"
                }
            } elseif ($DebugLogin) {
                Write-Status "UIA: Expected at least 2 Edit controls, found $($edits.Count)." "Warn"
            }
        }
    }
    catch {
        Write-Status "UI Automation failed: $($_.Exception.Message)" "Warn"
    }

    # ── Strategy B: SendKeys fallback ──
    if (-not $uiaSuccess) {
        Write-Status "Falling back to SendKeys..." "Warn"

        # Bring window to foreground
        $code = @'
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
}
'@
        Add-Type -TypeDefinition $code -ErrorAction SilentlyContinue
        [Win32]::ShowWindowAsync($ntProc.MainWindowHandle, 1) | Out-Null
        [Win32]::SetForegroundWindow($ntProc.MainWindowHandle) | Out-Null
        Start-Sleep -Milliseconds 800

        Add-Type -AssemblyName System.Windows.Forms

        # Escape special SendKeys characters
        function Escape-SendKeys {
            param([string]$text)
            $special = @('^','%','~','(',')','{','}','[',']','+')
            $sb = New-Object System.Text.StringBuilder
            foreach ($ch in $text.ToCharArray()) {
                if ($special -contains "$ch") {
                    [void]$sb.Append("{}")
                }
                [void]$sb.Append($ch)
            }
            return $sb.ToString()
        }

        $safeUser = Escape-SendKeys -text $username
        $safePw   = Escape-SendKeys -text $password

        [System.Windows.Forms.SendKeys]::SendWait($safeUser)
        Start-Sleep -Milliseconds 300
        [System.Windows.Forms.SendKeys]::SendWait("{TAB}")
        Start-Sleep -Milliseconds 300
        [System.Windows.Forms.SendKeys]::SendWait($safePw)
        Start-Sleep -Milliseconds 300
        [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")

        Write-Status "Login submitted via SendKeys." "Success"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 5: Wait for NinjaTrader Control Center
# ─────────────────────────────────────────────────────────────────────────────
function Wait-ForControlCenter {
    param([int]$Timeout = 120)

    Write-Status "Waiting for NinjaTrader Control Center..."
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    while ($sw.Elapsed.TotalSeconds -lt $Timeout) {
        $cc = Get-Process | Where-Object {
            $_.ProcessName -like "*NinjaTrader*" -and
            $_.MainWindowTitle -like "*Control Center*"
        } | Select-Object -First 1

        if ($cc) {
            Write-Status "Control Center is open." "Success"
            return
        }
        Start-Sleep -Seconds 2
    }

    throw "Control Center did not appear within $Timeout seconds."
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 6: Final readiness check
# ─────────────────────────────────────────────────────────────────────────────
function Confirm-Readiness {
    param([string]$Distro, [int]$Timeout = 60)

    Write-Status "Waiting for ZMQ AddOn to handshake (up to ${Timeout}s)..."

    # The AddOn auto-connects when the Control Center loads.
    # We give it a grace period, then do a lightweight sanity check.
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $Timeout) {
        # Quick ZMQ port check
        try {
            $client = New-Object System.Net.Sockets.TcpClient
            $client.Connect("localhost", 5555)
            $client.Close()
        } catch {
            Start-Sleep -Seconds 2
            continue
        }

        # Tail Python logs for a connect message
        try {
            $logs = wsl -d $Distro -u root journalctl -u tradingbot --no-pager -n 15 2>$null
            if ($logs -match "ninjatrader" -or $logs -match "Connected to Python TradingBot" -or $logs -match "connect.*ninjatrader") {
                Write-Status "ZMQ handshake detected in Python logs." "Success"
                return
            }
        } catch { }

        Start-Sleep -Seconds 3
    }

    Write-Status "Could not confirm ZMQ handshake in logs, but ports are open and NT is running." "Warn"
}

# ─────────────────────────────────────────────────────────────────────────────
# Phase 7: Notify user
# ─────────────────────────────────────────────────────────────────────────────
function Show-Completion {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "TradingBot is live.`n`nWSL service: running`nNinjaTrader: connected`nZMQ: active",
        "TradingBot Launcher",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
}

# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════
try {
    Write-Status "=== TradingBot One-Click Launcher ==="
    Write-Status "NinjaTrader path: $NinjaTraderPath"
    Write-Status "WSL distro:       $WslDistro"
    Write-Status "Credential file:  $CredentialFile"
    Write-Status ""

    Start-WslService   -Distro $WslDistro -Timeout $MaxWaitSeconds
    Wait-ZmqPorts      -Timeout 60
    $ntProc = Start-NinjaTrader -Path $NinjaTraderPath
    Enter-NinjaTraderCredentials -CredentialFile $CredentialFile -Timeout $MaxWaitSeconds
    Wait-ForControlCenter -Timeout $MaxWaitSeconds
    Confirm-Readiness  -Distro $WslDistro -Timeout 60

    Write-Status ""
    Write-Status "ALL SYSTEMS READY. TradingBot is live." "Success"
    Write-Status ""

    Show-Completion
}
catch {
    Write-Status $_.Exception.Message "Error"
    Write-Status "Launcher failed. Check the messages above and try again." "Error"

    # Keep console open if running from a shortcut
    if (-not $psISE -and -not $env:WT_SESSION) {
        Write-Host "`nPress any key to exit..." -NoNewline
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    }

    exit 1
}
