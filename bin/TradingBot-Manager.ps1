#Requires -Version 5.1
<#
.SYNOPSIS
    TradingBot Manager - Windows GUI for starting, configuring and updating the bot.

.DESCRIPTION
    A PowerShell Windows Forms app that provides:
      - Dashboard: start/stop the bot and view live logs with current config summary
      - Settings: edit .env values (pair, instrument, risk, account, ports, etc.)
                  and manage NinjaTrader credentials
      - NinjaTrader: re-copy AddOns, reinstall NetMQ
      - Update: check for updates and pull latest code

    The bot runs natively on Windows (no WSL required).
#>

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Security
Add-Type -AssemblyName Microsoft.VisualBasic

$ErrorActionPreference = "Stop"

# =============================================================================
# State
# =============================================================================
$script:ProjectDir      = (Resolve-Path "$PSScriptRoot\..").Path
$script:CredentialFile  = "$env:LOCALAPPDATA\TradingBot\NTCredential.dat"
$script:VaultFile       = "$env:LOCALAPPDATA\TradingBot\NTCredential.vault.json"
$script:BotProcess      = $null
$script:LogTimer        = $null
$script:LastLogLength   = 0
$script:VenvPython      = $null

# =============================================================================
# Helpers
# =============================================================================

function Write-DebugLog {
    param([string]$Message)
    $ts = Get-Date -Format 'HH:mm:ss.fff'
    $line = "[$ts] $Message"
    Write-Host $line
    $logDir = Join-Path $script:ProjectDir "logs"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    $dbgFile = Join-Path $logDir "manager_debug.log"
    $line | Out-File -FilePath $dbgFile -Append -Encoding utf8
}

function Get-LatestLogFile {
    $logDir = Join-Path $script:ProjectDir "logs"
    if (-not (Test-Path $logDir)) { return $null }
    return Get-ChildItem -Path $logDir -Filter "app_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}

function Read-EnvFile {
    $envPath = Join-Path $script:ProjectDir ".env"
    $dict = @{}
    if (Test-Path $envPath) {
        Get-Content $envPath | ForEach-Object {
            if ($_ -match "^\s*([^#\s=]+)\s*=\s*[`"']?(.+?)[`"']?\s*$") {
                $dict[$Matches[1]] = $Matches[2]
            }
        }
    }
    return $dict
}

function Save-EnvFile {
    param([hashtable]$Values)
    $envPath = Join-Path $script:ProjectDir ".env"
    $lines = @()
    if (Test-Path $envPath) { $lines = @(Get-Content $envPath) }

    foreach ($key in $Values.Keys) {
        $pattern = "^\s*$key\s*=.*"
        $line = "$key=`"$($Values[$key])`""
        $found = $false
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match $pattern) {
                $lines[$i] = $line
                $found = $true
                break
            }
        }
        if (-not $found) { $lines += $line }
    }
    $lines | Set-Content -Path $envPath -Encoding UTF8
}

function Read-ZmqConfig {
    $ntExe = Find-NinjaTraderExe
    if (-not $ntExe) { return @{ instrument = "MNQ 06-26" } }
    $ntCustom = Get-NinjaTraderCustomDir -NtExePath $ntExe
    $path = Join-Path $ntCustom "TradingBotZmqConfig.json"
    if (Test-Path $path) {
        $obj = Get-Content $path -Raw | ConvertFrom-Json
        $ht = @{}
        $obj.PSObject.Properties | ForEach-Object { $ht[$_.Name] = $_.Value }
        return $ht
    }
    return @{ instrument = "MNQ 06-26" }
}

function Save-ZmqConfig {
    param([hashtable]$Values)
    $ntExe = Find-NinjaTraderExe
    if (-not $ntExe) { return }
    $ntCustom = Get-NinjaTraderCustomDir -NtExePath $ntExe
    $path = Join-Path $ntCustom "TradingBotZmqConfig.json"
    $cfg = @{}
    if (Test-Path $path) { $cfg = Get-Content $path -Raw | ConvertFrom-Json }
    foreach ($key in $Values.Keys) { $cfg.$key = $Values[$key] }
    $cfg | ConvertTo-Json -Depth 3 | Set-Content -Path $path -Encoding UTF8
}

function Get-Vault {
    if (Test-Path $script:VaultFile) {
        try {
            $data = Get-Content -Path $script:VaultFile -Raw | ConvertFrom-Json
            if ($data -is [array]) { $accounts = $data } else { $accounts = @($data) }
            $valid = @($accounts | Where-Object { $_.Username })
            if ($valid.Count -gt 0) { return $valid }
        } catch {}
    }
    return @()
}

function Save-Vault {
    param([array]$Accounts)
    $dir = Split-Path -Parent $script:VaultFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    ConvertTo-Json -InputObject @($Accounts) -Depth 3 | Set-Content -Path $script:VaultFile -Encoding UTF8
}

function Ensure-WslAvailable {
    if (-not (Get-Command wsl -ErrorAction SilentlyContinue)) {
        throw "WSL not found. Install WSL first: wsl --install"
    }
}

function Test-PortOpen {
    param([int]$Port, [int]$TimeoutMs = 500)
    # Check localhost first
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync("localhost", $Port)
        if ($task.Wait($TimeoutMs)) {
            if ($client.Connected) { return $true }
        }
    } finally {
        $client.Close()
    }
    # Check WSL2 IP if localhost failed
    if ($script:WslIp) {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $task = $client.ConnectAsync($script:WslIp, $Port)
            if ($task.Wait($TimeoutMs)) {
                return $client.Connected
            }
        } finally {
            $client.Close()
        }
    }
    return $false
}

function Add-AccountToVault {
    param([string]$Username, [string]$Password)
    $pwBytes = [System.Text.Encoding]::UTF8.GetBytes($Password)
    $encrypted = [System.Security.Cryptography.ProtectedData]::Protect(
        $pwBytes, $null,
        [System.Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    [System.GC]::Collect()
    $vault = Get-Vault
    $vault = $vault | Where-Object { $_.Username -ne $Username }
    $vault += [ordered]@{
        Username       = $Username
        PasswordBase64 = [Convert]::ToBase64String($encrypted)
        Created        = (Get-Date -Format "o")
        Version        = 1
    }
    Save-Vault -Accounts $vault
}

function Find-NinjaTraderExe {
    $regPaths = @(
        "HKLM:\SOFTWARE\NinjaTrader, LLC\NinjaTrader 8",
        "HKLM:\SOFTWARE\WOW6432Node\NinjaTrader, LLC\NinjaTrader 8"
    )
    foreach ($rp in $regPaths) {
        try {
            $props = Get-ItemProperty -Path $rp -ErrorAction SilentlyContinue
            if ($props -and $props.InstallDir) {
                foreach ($sub in @("bin", "bin64")) {
                    $exe = Join-Path $props.InstallDir "$sub\NinjaTrader.exe"
                    if (Test-Path $exe) { return $exe }
                }
            }
        } catch {}
    }
    $candidates = @(
        "C:\Program Files\NinjaTrader 8\bin\NinjaTrader.exe",
        "C:\Program Files\NinjaTrader 8\bin64\NinjaTrader.exe",
        "C:\Program Files (x86)\NinjaTrader 8\bin\NinjaTrader.exe",
        "C:\Program Files (x86)\NinjaTrader 8\bin64\NinjaTrader.exe",
        "$env:USERPROFILE\NinjaTrader 8\bin\NinjaTrader.exe",
        "$env:USERPROFILE\NinjaTrader 8\bin64\NinjaTrader.exe",
        "D:\Program Files\NinjaTrader 8\bin\NinjaTrader.exe",
        "D:\Program Files\NinjaTrader 8\bin64\NinjaTrader.exe",
        "D:\NinjaTrader 8\bin\NinjaTrader.exe",
        "D:\NinjaTrader 8\bin64\NinjaTrader.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    return $null
}

function Get-NinjaTraderCustomDir {
    param([string]$NtExePath)
    $ntRoot = Split-Path (Split-Path $NtExePath)
    $customFromExe = Join-Path $ntRoot "bin\Custom"
    if (Test-Path $customFromExe) {
        try {
            $testFile = Join-Path $customFromExe "_write_test_.tmp"
            [IO.File]::WriteAllText($testFile, "test")
            Remove-Item $testFile -Force
            return $customFromExe
        } catch {}
    }
    $docs = [Environment]::GetFolderPath("MyDocuments")
    $customFromDocs = Join-Path $docs "NinjaTrader 8\bin\Custom"
    if (-not (Test-Path $customFromDocs)) { New-Item -ItemType Directory -Path $customFromDocs -Force | Out-Null }
    return $customFromDocs
}

function Get-GitCommit {
    Push-Location $script:ProjectDir
    try {
        $commit = git rev-parse --short HEAD 2>$null
        $branch = git rev-parse --abbrev-ref HEAD 2>$null
        return "$branch@$commit"
    } catch { return "unknown" }
    finally { Pop-Location }
}

function Update-DashboardSummary {
    param([hashtable]$EnvVals, [hashtable]$ZmqVals)
    $mode = if ($EnvVals["MODE"]) { $EnvVals["MODE"] } else { "live" }
    $pair = if ($EnvVals["PAIR"]) { $EnvVals["PAIR"] } else { "MNQ" }
    $inst = if ($ZmqVals["instrument"]) { $ZmqVals["instrument"] } else { "MNQ 06-26" }
    $acct = if ($EnvVals["NT_ACCOUNT"]) { $EnvVals["NT_ACCOUNT"] } else { "-" }
    $risk = if ($EnvVals["RISK"]) { ('$' + $EnvVals["RISK"]) } elseif ($EnvVals["RISK_PCT"]) { ($EnvVals["RISK_PCT"] + '%') } else { '-' }
    $script:lblConfigSummary.Text = "Mode: $mode  |  Pair: $pair  |  Instrument: $inst  |  Account: $acct  |  Risk: $risk"
}

# =============================================================================
# Build UI
# =============================================================================

$form = New-Object System.Windows.Forms.Form
$form.Text = "TradingBot Manager"
$form.Size = New-Object System.Drawing.Size(900, 700)
$form.StartPosition = "CenterScreen"
$form.Icon = [System.Drawing.SystemIcons]::Application

$tabControl = New-Object System.Windows.Forms.TabControl
$tabControl.Dock = "Fill"
$tabControl.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$form.Controls.Add($tabControl)

# Status timer: polls port + log file
$script:StatusTimer = New-Object System.Windows.Forms.Timer
$script:StatusTimer.Interval = 1000
$script:LastLogLength = 0
$script:StatusTimer.Add_Tick({
    try {
        # Check if our tracked process has died
        if ($script:BotProcess) {
            $exited = $false
            try { $exited = $script:BotProcess.HasExited } catch { $exited = $true }
            if ($exited) {
                $exitCode = "unknown"
                try { $exitCode = $script:BotProcess.ExitCode } catch {}
                $btnToggle.Text = "Start Bot"
                $btnToggle.BackColor = [System.Drawing.Color]::FromArgb(0, 150, 0)
                $lblStatus.Text = "Status: Stopped"
                $lblStatus.ForeColor = [System.Drawing.Color]::Black
                $script:BotProcess = $null
                $script:StatusTimer.Stop()
                $txtLog.AppendText("Bot process exited (exit code: $exitCode).`n")
            }
        }
        
        # Tail log file
        $logFile = Get-LatestLogFile
        if ($logFile) {
            if ($logFile.FullName -ne $script:LastLogFile) {
                $script:LastLogFile = $logFile.FullName
                $script:LastLogPos = 0
            }
            $fs = [System.IO.FileStream]::new($logFile.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
            $sr = [System.IO.StreamReader]::new($fs)
            try {
                if ($script:LastLogPos -gt $fs.Length) { $script:LastLogPos = 0 }
                $fs.Position = $script:LastLogPos
                $newText = $sr.ReadToEnd()
                if ($newText) {
                    $txtLog.AppendText($newText)
                    $txtLog.SelectionStart = $txtLog.Text.Length
                    $txtLog.ScrollToCaret()
                }
                $script:LastLogPos = $fs.Position
            } finally {
                $sr.Close()
            }
        }
    } catch {
        Write-DebugLog "TIMER: ERROR: $_"
        $txtLog.AppendText("[Timer error: $_]`n")
    }
})

# ---------------------------------------------------------------------------
# DASHBOARD TAB
# ---------------------------------------------------------------------------
$tabDashboard = New-Object System.Windows.Forms.TabPage
$tabDashboard.Text = "Dashboard"
$tabControl.Controls.Add($tabDashboard)

$script:lblConfigSummary = New-Object System.Windows.Forms.Label
$script:lblConfigSummary.Text = "Loading config..."
$script:lblConfigSummary.Location = New-Object System.Drawing.Point(20, 15)
$script:lblConfigSummary.Size = New-Object System.Drawing.Size(840, 24)
$script:lblConfigSummary.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$script:lblConfigSummary.ForeColor = [System.Drawing.Color]::FromArgb(0, 100, 180)
$tabDashboard.Controls.Add($script:lblConfigSummary)

$lblStatus = New-Object System.Windows.Forms.Label
$lblStatus.Text = "Status: Stopped"
$lblStatus.Location = New-Object System.Drawing.Point(20, 44)
$lblStatus.Size = New-Object System.Drawing.Size(400, 28)
$lblStatus.Font = New-Object System.Drawing.Font("Segoe UI", 12, [System.Drawing.FontStyle]::Bold)
$tabDashboard.Controls.Add($lblStatus)

$lblCommit = New-Object System.Windows.Forms.Label
$lblCommit.Text = "Version: $(Get-GitCommit)"
$lblCommit.Location = New-Object System.Drawing.Point(500, 44)
$lblCommit.Size = New-Object System.Drawing.Size(350, 28)
$lblCommit.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$lblCommit.ForeColor = [System.Drawing.Color]::Gray
$tabDashboard.Controls.Add($lblCommit)

$btnToggle = New-Object System.Windows.Forms.Button
$btnToggle.Text = "Start Bot"
$btnToggle.Location = New-Object System.Drawing.Point(20, 80)
$btnToggle.Size = New-Object System.Drawing.Size(120, 36)
$btnToggle.BackColor = [System.Drawing.Color]::FromArgb(0, 150, 0)
$btnToggle.ForeColor = [System.Drawing.Color]::White
$btnToggle.FlatStyle = "Flat"
$tabDashboard.Controls.Add($btnToggle)

$btnClear = New-Object System.Windows.Forms.Button
$btnClear.Text = "Clear Log"
$btnClear.Location = New-Object System.Drawing.Point(160, 80)
$btnClear.Size = New-Object System.Drawing.Size(120, 36)
$tabDashboard.Controls.Add($btnClear)

$btnOpen = New-Object System.Windows.Forms.Button
$btnOpen.Text = "Open Bot"
$btnOpen.Location = New-Object System.Drawing.Point(300, 80)
$btnOpen.Size = New-Object System.Drawing.Size(120, 36)
$btnOpen.BackColor = [System.Drawing.Color]::FromArgb(0, 120, 200)
$btnOpen.ForeColor = [System.Drawing.Color]::White
$btnOpen.FlatStyle = "Flat"
$tabDashboard.Controls.Add($btnOpen)

$txtLog = New-Object System.Windows.Forms.RichTextBox
$txtLog.Location = New-Object System.Drawing.Point(20, 130)
$txtLog.Size = New-Object System.Drawing.Size(840, 460)
$txtLog.Font = New-Object System.Drawing.Font("Consolas", 9)
$txtLog.BackColor = [System.Drawing.Color]::FromArgb(30, 30, 30)
$txtLog.ForeColor = [System.Drawing.Color]::LightGreen
$txtLog.ReadOnly = $true
$txtLog.Multiline = $true
$txtLog.ScrollBars = "Vertical"
$tabDashboard.Controls.Add($txtLog)

# ---------------------------------------------------------------------------
# SETTINGS TAB
# ---------------------------------------------------------------------------
$tabSettings = New-Object System.Windows.Forms.TabPage
$tabSettings.Text = "Settings"
$tabControl.Controls.Add($tabSettings)

$y = 20
$settingsControls = @{}
$envValues = Read-EnvFile
$zmqValues = Read-ZmqConfig

# --- Mode (read-only, live by default) ---
$lblMode = New-Object System.Windows.Forms.Label
$lblMode.Text = "Mode"
$lblMode.Location = New-Object System.Drawing.Point(20, $y)
$lblMode.Size = New-Object System.Drawing.Size(180, 24)
$tabSettings.Controls.Add($lblMode)

$txtMode = New-Object System.Windows.Forms.TextBox
$txtMode.Text = "live"
$txtMode.Location = New-Object System.Drawing.Point(210, $y)
$txtMode.Size = New-Object System.Drawing.Size(300, 24)
$txtMode.ReadOnly = $true
$txtMode.BackColor = [System.Drawing.Color]::LightGray
$tabSettings.Controls.Add($txtMode)
$settingsControls["MODE"] = $txtMode
$y += 32

# --- Trading settings ---
$settingsMap = @(
    @{ Label = "Pair"; Key = "PAIR"; Default = "MNQ" },
    @{ Label = "Instrument"; Key = "INSTRUMENT"; Default = "MNQ 06-26"; IsZmq = $true },

    @{ Label = "Risk amount ($)"; Key = "RISK"; Default = "" },
    @{ Label = "Risk % of account"; Key = "RISK_PCT"; Default = "" },
    @{ Label = "NT Account name"; Key = "NT_ACCOUNT"; Default = "" },
    @{ Label = "Flask port"; Key = "FLASK_PORT"; Default = "5001" },
    @{ Label = "ZMQ host"; Key = "ZMQ_HOST"; Default = "127.0.0.1" },
    @{ Label = "ZMQ market port"; Key = "ZMQ_MARKET_PORT"; Default = "5555" },
    @{ Label = "ZMQ command port"; Key = "ZMQ_COMMAND_PORT"; Default = "5556" },
    @{ Label = "ZMQ query port"; Key = "ZMQ_QUERY_PORT"; Default = "5557" },
    @{ Label = "ZMQ heartbeat port"; Key = "ZMQ_HEARTBEAT_PORT"; Default = "5558" }
)

foreach ($item in $settingsMap) {
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Text = $item.Label
    $lbl.Location = New-Object System.Drawing.Point(20, $y)
    $lbl.Size = New-Object System.Drawing.Size(180, 24)
    $tabSettings.Controls.Add($lbl)

    $txt = New-Object System.Windows.Forms.TextBox
    $txt.Location = New-Object System.Drawing.Point(210, $y)
    $txt.Size = New-Object System.Drawing.Size(300, 24)

    if ($item.IsZmq) {
        $val = $zmqValues[$item.Key]
    } else {
        $val = $envValues[$item.Key]
    }
    if (-not $val) { $val = $item.Default }
    $txt.Text = $val
    $tabSettings.Controls.Add($txt)
    $settingsControls[$item.Key] = $txt
    $y += 32
}

# --- Risk hint ---
$lblRiskHint = New-Object System.Windows.Forms.Label
$lblRiskHint.Text = "Tip: Set Risk OR Risk %, not both. Risk amount takes precedence."
$lblRiskHint.Location = New-Object System.Drawing.Point(210, $y)
$lblRiskHint.Size = New-Object System.Drawing.Size(500, 20)
$lblRiskHint.Font = New-Object System.Drawing.Font("Segoe UI", 8)
$lblRiskHint.ForeColor = [System.Drawing.Color]::Gray
$tabSettings.Controls.Add($lblRiskHint)
$y += 28

# --- NinjaTrader Credentials ---
$grpCreds = New-Object System.Windows.Forms.GroupBox
$grpCreds.Text = "NinjaTrader Credentials"
$grpCreds.Location = New-Object System.Drawing.Point(20, $y)
$grpCreds.Size = New-Object System.Drawing.Size(500, 110)
$tabSettings.Controls.Add($grpCreds)

$lblNtUser = New-Object System.Windows.Forms.Label
$lblNtUser.Text = "Username"
$lblNtUser.Location = New-Object System.Drawing.Point(10, 24)
$lblNtUser.Size = New-Object System.Drawing.Size(100, 24)
$grpCreds.Controls.Add($lblNtUser)

$cmbNtUser = New-Object System.Windows.Forms.ComboBox
$cmbNtUser.Location = New-Object System.Drawing.Point(110, 22)
$cmbNtUser.Size = New-Object System.Drawing.Size(300, 24)
$cmbNtUser.DropDownStyle = "DropDown"
$vault = Get-Vault
foreach ($acct in $vault) { [void]$cmbNtUser.Items.Add($acct.Username) }
$grpCreds.Controls.Add($cmbNtUser)

$lblNtPass = New-Object System.Windows.Forms.Label
$lblNtPass.Text = "Password"
$lblNtPass.Location = New-Object System.Drawing.Point(10, 54)
$lblNtPass.Size = New-Object System.Drawing.Size(100, 24)
$grpCreds.Controls.Add($lblNtPass)

$txtNtPass = New-Object System.Windows.Forms.TextBox
$txtNtPass.Location = New-Object System.Drawing.Point(110, 52)
$txtNtPass.Size = New-Object System.Drawing.Size(300, 24)
$txtNtPass.PasswordChar = '*'
$grpCreds.Controls.Add($txtNtPass)

$btnSaveCreds = New-Object System.Windows.Forms.Button
$btnSaveCreds.Text = "Save Credentials"
$btnSaveCreds.Location = New-Object System.Drawing.Point(110, 80)
$btnSaveCreds.Size = New-Object System.Drawing.Size(140, 28)
$grpCreds.Controls.Add($btnSaveCreds)

$y += 125

# --- Save settings ---
$chkRestart = New-Object System.Windows.Forms.CheckBox
$chkRestart.Text = "Restart bot after saving"
$chkRestart.Location = New-Object System.Drawing.Point(20, $y)
$chkRestart.Size = New-Object System.Drawing.Size(300, 24)
$chkRestart.Checked = $true
$tabSettings.Controls.Add($chkRestart)

$btnSaveSettings = New-Object System.Windows.Forms.Button
$btnSaveSettings.Text = "Save Settings"
$btnSaveSettings.Location = New-Object System.Drawing.Point(20, ($y + 30))
$btnSaveSettings.Size = New-Object System.Drawing.Size(140, 36)
$btnSaveSettings.BackColor = [System.Drawing.Color]::FromArgb(0, 120, 215)
$btnSaveSettings.ForeColor = [System.Drawing.Color]::White
$btnSaveSettings.FlatStyle = "Flat"
$tabSettings.Controls.Add($btnSaveSettings)

# ---------------------------------------------------------------------------
# NINJATRADER TAB
# ---------------------------------------------------------------------------
$tabNT = New-Object System.Windows.Forms.TabPage
$tabNT.Text = "NinjaTrader"
$tabControl.Controls.Add($tabNT)

$lblNtPath = New-Object System.Windows.Forms.Label
$lblNtPath.Text = "Detecting NinjaTrader..."
$lblNtPath.Location = New-Object System.Drawing.Point(20, 20)
$lblNtPath.Size = New-Object System.Drawing.Size(800, 24)
$lblNtPath.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$tabNT.Controls.Add($lblNtPath)

$btnCopyAddOns = New-Object System.Windows.Forms.Button
$btnCopyAddOns.Text = "Re-copy AddOn Files"
$btnCopyAddOns.Location = New-Object System.Drawing.Point(20, 60)
$btnCopyAddOns.Size = New-Object System.Drawing.Size(180, 36)
$tabNT.Controls.Add($btnCopyAddOns)

$btnInstallNetMQ = New-Object System.Windows.Forms.Button
$btnInstallNetMQ.Text = "Re-install NetMQ DLLs"
$btnInstallNetMQ.Location = New-Object System.Drawing.Point(220, 60)
$btnInstallNetMQ.Size = New-Object System.Drawing.Size(180, 36)
$tabNT.Controls.Add($btnInstallNetMQ)

$txtNtOutput = New-Object System.Windows.Forms.TextBox
$txtNtOutput.Location = New-Object System.Drawing.Point(20, 120)
$txtNtOutput.Size = New-Object System.Drawing.Size(840, 400)
$txtNtOutput.Multiline = $true
$txtNtOutput.ScrollBars = "Vertical"
$txtNtOutput.Font = New-Object System.Drawing.Font("Consolas", 9)
$txtNtOutput.ReadOnly = $true
$tabNT.Controls.Add($txtNtOutput)

$ntExe = Find-NinjaTraderExe
if ($ntExe) {
    $ntCustom = Get-NinjaTraderCustomDir -NtExePath $ntExe
    $lblNtPath.Text = "NinjaTrader: $ntExe  |  Custom: $ntCustom"
} else {
    $lblNtPath.Text = "NinjaTrader not found. Run the installer first or enter path manually."
    $lblNtPath.ForeColor = [System.Drawing.Color]::Red
}

# ---------------------------------------------------------------------------
# UPDATE TAB
# ---------------------------------------------------------------------------
$tabUpdate = New-Object System.Windows.Forms.TabPage
$tabUpdate.Text = "Update"
$tabControl.Controls.Add($tabUpdate)

$lblUpdateStatus = New-Object System.Windows.Forms.Label
$lblUpdateStatus.Text = "Current version: $(Get-GitCommit)"
$lblUpdateStatus.Location = New-Object System.Drawing.Point(20, 20)
$lblUpdateStatus.Size = New-Object System.Drawing.Size(800, 24)
$lblUpdateStatus.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$tabUpdate.Controls.Add($lblUpdateStatus)

$btnCheckUpdate = New-Object System.Windows.Forms.Button
$btnCheckUpdate.Text = "Check for Updates"
$btnCheckUpdate.Location = New-Object System.Drawing.Point(20, 60)
$btnCheckUpdate.Size = New-Object System.Drawing.Size(160, 36)
$tabUpdate.Controls.Add($btnCheckUpdate)

$btnPullRestart = New-Object System.Windows.Forms.Button
$btnPullRestart.Text = "Pull & Restart"
$btnPullRestart.Location = New-Object System.Drawing.Point(200, 60)
$btnPullRestart.Size = New-Object System.Drawing.Size(160, 36)
$tabUpdate.Controls.Add($btnPullRestart)

$txtGitOutput = New-Object System.Windows.Forms.TextBox
$txtGitOutput.Location = New-Object System.Drawing.Point(20, 120)
$txtGitOutput.Size = New-Object System.Drawing.Size(840, 400)
$txtGitOutput.Multiline = $true
$txtGitOutput.ScrollBars = "Vertical"
$txtGitOutput.Font = New-Object System.Drawing.Font("Consolas", 9)
$txtGitOutput.ReadOnly = $true
$tabUpdate.Controls.Add($txtGitOutput)

# =============================================================================
# Event Handlers
# =============================================================================

# --- Dashboard ---

function Update-ToggleButton {
    $running = Test-PortOpen -Port $script:FlaskPort

    if ($running) {
        $btnToggle.Text = "Stop Bot"
        $btnToggle.BackColor = [System.Drawing.Color]::FromArgb(180, 0, 0)
        if ($script:BotProcess) {
            $lblStatus.Text = "Status: Running (PID $($script:BotProcess.Id))"
        } else {
            $lblStatus.Text = "Status: Running"
        }
        $lblStatus.ForeColor = [System.Drawing.Color]::FromArgb(0, 150, 0)
    } else {
        $btnToggle.Text = "Start Bot"
        $btnToggle.BackColor = [System.Drawing.Color]::FromArgb(0, 150, 0)
        $lblStatus.Text = "Status: Stopped"
        $lblStatus.ForeColor = [System.Drawing.Color]::Black
        $script:BotProcess = $null
    }
}

$btnToggle.Add_Click({
    if ($btnToggle.Text -eq "Stop Bot") {
        Write-DebugLog "EVENT: Stop button clicked"
        $txtLog.AppendText("$(Get-Date -Format 'HH:mm:ss')  Stopping bot...`n")
        try {
            # Primary: kill tracked wsl.exe process
            if ($script:BotProcess) {
                try { $script:BotProcess.Kill() } catch {}
                try { $script:BotProcess.WaitForExit(3000) } catch {}
            }

            # Fallback: kill any WSL processes running the bot
            $wslProcs = Find-WslBotProcess
            if ($wslProcs) {
                try { $wslProcs.Kill() } catch {}
            }

            # Nuclear fallback: pkill inside WSL
            try {
                & wsl -- pkill -f "app.py" 2>$null
                & wsl -- pkill -f "start_live" 2>$null
            } catch {}

            $txtLog.AppendText("Bot stopped.`n")
        } catch {
            $txtLog.AppendText("Force kill failed: $_`n")
        }
        $script:BotProcess = $null
        $script:StatusTimer.Stop()
        $txtLog.Text = ""
        Update-ToggleButton
    } else {
        Write-DebugLog "EVENT: Start button clicked"
        $txtLog.AppendText("$(Get-Date -Format 'HH:mm:ss')  Starting bot...`n")
        try {
            Ensure-WslAvailable
        } catch {
            $txtLog.SelectionColor = [System.Drawing.Color]::Red
            $txtLog.AppendText("ERROR: $_`n")
            $txtLog.SelectionColor = [System.Drawing.Color]::LightGreen
            return
        }

        try {
            Write-DebugLog "START: Converting path to WSL..."
            $drive = $script:ProjectDir[0].ToString().ToLower()
            $wslPath = "/mnt/$drive" + ($script:ProjectDir.Substring(2) -replace '\\', '/')
            $scriptPath = "$wslPath/bin/start_live.sh"
            Write-DebugLog "START: WSL script path: $scriptPath"

            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = "wsl"
            $psi.Arguments = "bash -l $scriptPath"
            $psi.UseShellExecute = $true
            $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

            $script:BotProcess = New-Object System.Diagnostics.Process
            $script:BotProcess.StartInfo = $psi

            Write-DebugLog "START: Calling Process.Start()..."
            $started = $script:BotProcess.Start()
            if (-not $started) {
                throw "Process.Start() returned false."
            }

            $btnToggle.Text = "Stop Bot"
            $btnToggle.BackColor = [System.Drawing.Color]::FromArgb(180, 0, 0)
            $lblStatus.Text = "Status: Running (PID $($script:BotProcess.Id))"
            $lblStatus.ForeColor = [System.Drawing.Color]::FromArgb(0, 150, 0)
            $txtLog.AppendText("WSL process started (PID $($script:BotProcess.Id)). Tailing logs...`n")
            $script:StatusTimer.Start()
        } catch {
            Write-DebugLog "START: ERROR: $_"
            $txtLog.SelectionColor = [System.Drawing.Color]::Red
            $txtLog.AppendText("ERROR starting bot: $_`n")
            $txtLog.SelectionColor = [System.Drawing.Color]::LightGreen
        }
    }
})

$btnClear.Add_Click({
    $txtLog.Text = ""
})

$btnOpen.Add_Click({
    try {
        $ip = (& wsl hostname -I 2>$null).Trim().Split()[0]
        if (-not $ip) { $ip = "localhost" }
    } catch {
        $ip = "localhost"
    }
    $url = "http://$ip`:$($script:FlaskPort)/"
    Start-Process $url
})

function Load-RecentLogLines {
    param([int]$Lines = 50)
    $logFile = Get-LatestLogFile
    if (-not $logFile) { return }
    $sr = [System.IO.StreamReader]::new($logFile.FullName)
    try {
        $buf = New-Object System.Collections.Generic.Queue[string] $Lines
        while ($null -ne ($line = $sr.ReadLine())) {
            if ($buf.Count -ge $Lines) { $buf.Dequeue() | Out-Null }
            $buf.Enqueue($line)
        }
        $txtLog.Text = ($buf.ToArray() -join "`n") + "`n"
        $txtLog.SelectionStart = $txtLog.Text.Length
        $txtLog.ScrollToCaret()
    } finally {
        $sr.Close()
    }
}

# --- Settings ---

$btnSaveSettings.Add_Click({
    $newEnvValues = @{}
    $newZmqValues = @{}
    foreach ($key in $settingsControls.Keys) {
        $val = $settingsControls[$key].Text
        if ($key -eq "INSTRUMENT") {
            $newZmqValues[$key] = $val
        } else {
            $newEnvValues[$key] = $val
        }
    }
    Save-EnvFile -Values $newEnvValues
    Save-ZmqConfig -Values $newZmqValues

    $updatedEnv = Read-EnvFile
    $updatedZmq = Read-ZmqConfig
    Update-DashboardSummary -EnvVals $updatedEnv -ZmqVals $updatedZmq

    [System.Windows.Forms.MessageBox]::Show("Settings saved.", "Saved", "OK", "Information")

    if ($chkRestart.Checked -and $script:BotProcess -and -not $script:BotProcess.HasExited) {
        $btnToggle.PerformClick()
        Start-Sleep -Seconds 2
        $btnToggle.PerformClick()
    }
})

$btnSaveCreds.Add_Click({
    $user = $cmbNtUser.Text
    $pass = $txtNtPass.Text
    if ([string]::IsNullOrWhiteSpace($user) -or [string]::IsNullOrWhiteSpace($pass)) {
        [System.Windows.Forms.MessageBox]::Show("Enter both username and password.", "Missing", "OK", "Warning")
        return
    }
    Add-AccountToVault -Username $user -Password $pass
    if (-not $cmbNtUser.Items.Contains($user)) {
        [void]$cmbNtUser.Items.Add($user)
    }
    [System.Windows.Forms.MessageBox]::Show("Credentials saved securely.", "Saved", "OK", "Information")
    $txtNtPass.Text = ""
})

# --- NinjaTrader ---

$btnCopyAddOns.Add_Click({
    $txtNtOutput.AppendText("$(Get-Date -Format 'HH:mm:ss')  Copying AddOn files...`n")
    try {
        $ntExe = Find-NinjaTraderExe
        if (-not $ntExe) { throw "NinjaTrader not found." }
        $ntCustom = Get-NinjaTraderCustomDir -NtExePath $ntExe
        $source = Join-Path $script:ProjectDir "zmq_connectors\ninjatrader"
        $dest = Join-Path $ntCustom "AddOns\TradingBotZMQ"

        if (Test-Path $dest) { Remove-Item $dest -Force -Recurse -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
        Get-ChildItem -Path $source -Filter "*.cs" -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($source.Length + 1)
            $target = Join-Path $dest $rel
            $targetDir = Split-Path -Parent $target
            if (-not (Test-Path $targetDir)) { New-Item -ItemType Directory -Path $targetDir -Force | Out-Null }
            Copy-Item $_.FullName $target -Force
            $txtNtOutput.AppendText("  Copied $rel`n")
        }
        $txtNtOutput.AppendText("Done.`n`n")
    } catch {
        $txtNtOutput.AppendText("ERROR: $_`n`n")
    }
})

$btnInstallNetMQ.Add_Click({
    $txtNtOutput.AppendText("$(Get-Date -Format 'HH:mm:ss')  Installing NetMQ...`n")
    try {
        $ntExe = Find-NinjaTraderExe
        if (-not $ntExe) { throw "NinjaTrader not found." }
        $ntCustom = Get-NinjaTraderCustomDir -NtExePath $ntExe
        $toolsDir = "$env:LOCALAPPDATA\TradingBot\Tools"
        $nuget = Join-Path $toolsDir "nuget.exe"

        if (-not (Test-Path $nuget)) {
            New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
            Invoke-WebRequest -Uri "https://dist.nuget.org/win-x86-commandline/latest/nuget.exe" -OutFile $nuget
        }

        & $nuget install NetMQ -Version 4.0.1.13 -OutputDirectory $toolsDir | Out-Null
        @(@{Name="AsyncIO";Version="0.1.69"},@{Name="System.Memory";Version="4.5.3"},@{Name="System.Runtime.CompilerServices.Unsafe";Version="6.0.0"},@{Name="Microsoft.Bcl.AsyncInterfaces";Version="9.0.0"},@{Name="System.Threading.Tasks.Extensions";Version="4.5.4"}) | ForEach-Object {
            & $nuget install $_.Name -Version $_.Version -OutputDirectory $toolsDir | Out-Null
        }

        $mappings = @(
            @{ Src = "$toolsDir\NetMQ.4.0.1.13\lib\net47\NetMQ.dll"; Name = "NetMQ.dll"; IsRef = $false },
            @{ Src = "$toolsDir\AsyncIO.0.1.69\lib\netstandard2.0\AsyncIO.dll"; Name = "AsyncIO.dll"; IsRef = $false },
            @{ Src = "$toolsDir\System.Memory.4.5.3\lib\netstandard2.0\System.Memory.dll"; Name = "System.Memory.dll"; IsRef = $false },
            @{ Src = "$toolsDir\System.Runtime.CompilerServices.Unsafe.6.0.0\lib\net461\System.Runtime.CompilerServices.Unsafe.dll"; Name = "System.Runtime.CompilerServices.Unsafe.dll"; IsRef = $false },
            @{ Src = "$toolsDir\Microsoft.Bcl.AsyncInterfaces.9.0.0\lib\net462\Microsoft.Bcl.AsyncInterfaces.dll"; Name = "Microsoft.Bcl.AsyncInterfaces.dll"; IsRef = $false },
            @{ Src = "$toolsDir\System.Threading.Tasks.Extensions.4.5.4\lib\net461\System.Threading.Tasks.Extensions.dll"; Name = "System.Threading.Tasks.Extensions.dll"; IsRef = $false },
            @{ Src = "$env:SystemRoot\Microsoft.NET\Framework\v4.0.30319\netstandard.dll"; Name = "netstandard.dll"; IsRef = $true }
        )
        foreach ($map in $mappings) {
            if (Test-Path $map.Src) {
                if ($map.IsRef) {
                    $refDir = Join-Path $ntCustom "refs"
                    New-Item -ItemType Directory -Path $refDir -Force | Out-Null
                    $dest = Join-Path $refDir $map.Name
                } else {
                    $dest = Join-Path $ntCustom $map.Name
                }
                Copy-Item $map.Src $dest -Force
                $txtNtOutput.AppendText("  Copied $($map.Name)`n")
            } else {
                $txtNtOutput.AppendText("  MISSING $($map.Name)`n")
            }
        }
        $txtNtOutput.AppendText("Done.`n`n")
    } catch {
        $txtNtOutput.AppendText("ERROR: $_`n`n")
    }
})

# --- Update ---

$btnCheckUpdate.Add_Click({
    $txtGitOutput.Text = ""
    Push-Location $script:ProjectDir
    try {
        $fetch = git fetch 2>&1
        $status = git status -uno 2>&1
        $txtGitOutput.AppendText("$fetch`n`n$status`n")
    } catch {
        $txtGitOutput.AppendText("ERROR: $_`n")
    }
    Pop-Location
})

$btnPullRestart.Add_Click({
    $txtGitOutput.Text = ""
    $wasRunning = $false
    if ($script:BotProcess -and -not $script:BotProcess.HasExited) {
        $wasRunning = $true
        $txtGitOutput.AppendText("Stopping bot for update...`n")
        $btnToggle.PerformClick()
        Start-Sleep -Seconds 3
    }

    Push-Location $script:ProjectDir
    try {
        $pull = git pull 2>&1
        $txtGitOutput.AppendText("$pull`n")
        $lblCommit.Text = "Version: $(Get-GitCommit)"
        $lblUpdateStatus.Text = "Current version: $(Get-GitCommit)"
    } catch {
        $txtGitOutput.AppendText("ERROR: $_`n")
    }
    Pop-Location

    if ($wasRunning) {
        Start-Sleep -Seconds 1
        $txtGitOutput.AppendText("`nRestarting bot...`n")
        $btnToggle.PerformClick()
    }
})

# =============================================================================
# Init
# =============================================================================

Write-DebugLog "INIT: Starting..."

Write-DebugLog "INIT: Reading .env..."
$initEnv = Read-EnvFile

Write-DebugLog "INIT: Reading ZMQ config..."
$initZmq = Read-ZmqConfig

Write-DebugLog "INIT: Updating dashboard..."
Update-DashboardSummary -EnvVals $initEnv -ZmqVals $initZmq

Write-DebugLog "INIT: Detecting bot..."
$script:FlaskPort = if ($initEnv["FLASK_PORT"]) { [int]$initEnv["FLASK_PORT"] } else { 5001 }
$portInUse = Test-PortOpen -Port $script:FlaskPort
Write-DebugLog "INIT: Port check done. In use=$portInUse"

if ($portInUse) {
    $lblStatus.Text = "Status: Running (detected on port $($script:FlaskPort))"
    $lblStatus.ForeColor = [System.Drawing.Color]::FromArgb(0, 150, 0)
    $btnToggle.Text = "Stop Bot"
    $btnToggle.BackColor = [System.Drawing.Color]::FromArgb(180, 0, 0)
    $script:StatusTimer.Start()
    Write-DebugLog "INIT: Loading recent logs..."
    Load-RecentLogLines -Lines 50
} else {
    $txtLog.Text = ""
    Update-ToggleButton
}

Write-DebugLog "INIT: Loading vault..."
$vault = Get-Vault
foreach ($acct in $vault) {
    [void]$cmbNtUser.Items.Add($acct.Username)
}
if ($vault.Count -gt 0) {
    $cmbNtUser.Text = $vault[0].Username
}

# =============================================================================
# Cleanup on close
# =============================================================================

$form.Add_FormClosing({
    if ($script:BotProcess -and -not $script:BotProcess.HasExited) {
        try { $script:BotProcess.Kill() } catch {}
    }
    # Also try to clean up any orphaned WSL bot processes
    try {
        & wsl -- pkill -f "app.py" 2>$null
        & wsl -- pkill -f "start_live" 2>$null
    } catch {}
})

# =============================================================================
# Run
# =============================================================================

Write-DebugLog "INIT: Showing dialog..."
try {
    [void]$form.ShowDialog()
} catch {
    [System.Windows.Forms.MessageBox]::Show(
        "Fatal error starting TradingBot Manager:`n`n$($_.Exception.Message)`n`n$($_.ScriptStackTrace)",
        "TradingBot Manager Error",
        "OK",
        "Error"
    )
}
