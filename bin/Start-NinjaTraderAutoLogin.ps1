#Requires -Version 5.1
<#
.SYNOPSIS
    Launches NinjaTrader and automatically logs in with stored credentials.

.DESCRIPTION
    Reads credentials from the encrypted store created by Set-NTCredential.ps1
    (or the vault created by Setup-TradingBot.ps1), starts NinjaTrader, and
    uses UI Automation to enter username/password and submit the login dialog.
    Falls back to SendKeys if UI Automation cannot locate the input fields.

.PARAMETER CredentialFile
    Path to the credential file created by Set-NTCredential.ps1.
    Default: %LOCALAPPDATA%\TradingBot\NTCredential.dat

.PARAMETER NinjaTraderPath
    Full path to NinjaTrader.exe.
    Default: C:\Program Files\NinjaTrader 8\bin\NinjaTrader.exe

.PARAMETER WaitForExit
    Wait for NinjaTrader to exit before the script completes.

.PARAMETER MaxWaitSeconds
    Maximum seconds to wait for the login dialog to appear. Default: 60.

.EXAMPLE
    .\Start-NinjaTraderAutoLogin.ps1

.EXAMPLE
    .\Start-NinjaTraderAutoLogin.ps1 -NinjaTraderPath "D:\NinjaTrader 8\bin64\NinjaTrader.exe"
#>
[CmdletBinding()]
param(
    [string]$CredentialFile = "$env:LOCALAPPDATA\TradingBot\NTCredential.dat",
    [string]$NinjaTraderPath = "C:\Program Files\NinjaTrader 8\bin\NinjaTrader.exe",
    [switch]$WaitForExit,
    [int]$MaxWaitSeconds = 60
)

$ErrorActionPreference = "Stop"

# =============================================================================
# Prerequisites
# =============================================================================
Add-Type -AssemblyName System.Security
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName System.Windows.Forms

if (-not ("Win32HelperV2" -as [Type])) {
    Add-Type @"
    using System;
    using System.Runtime.InteropServices;
    using System.Text;
    public class Win32HelperV2 {
        public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
        [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);
        [DllImport("user32.dll", SetLastError = true)] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
        [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
        [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
        [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
        public const int SW_RESTORE = 9;
        public const int SW_SHOW = 5;
    }
"@
}

# =============================================================================
# Helpers
# =============================================================================

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
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return $null
}

function Get-CredentialStore {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        $vaultPath = "$env:LOCALAPPDATA\TradingBot\NTCredential.vault.json"
        if (Test-Path $vaultPath) {
            $vault = Get-Content -Path $vaultPath -Raw | ConvertFrom-Json
            if ($vault -isnot [array]) { $vault = @($vault) }
            if ($vault.Count -eq 1) {
                return $vault[0]
            } elseif ($vault.Count -gt 1) {
                Write-Host ""
                Write-Host "Multiple accounts found in vault:" -ForegroundColor Cyan
                for ($i = 0; $i -lt $vault.Count; $i++) {
                    Write-Host "  [$($i+1)] $($vault[$i].Username)" -ForegroundColor White
                }
                $choice = Read-Host "Select account [1-$($vault.Count)]"
                $idx = 0
                if ([int]::TryParse($choice, [ref]$idx) -and $idx -ge 1 -and $idx -le $vault.Count) {
                    return $vault[$idx - 1]
                }
                throw "Invalid vault account selection."
            }
        }
        throw "Credential file not found: $Path`nRun Set-NTCredential.ps1 or Setup-TradingBot.ps1 first."
    }

    $store = Get-Content -Path $Path -Raw | ConvertFrom-Json
    return $store
}

function Decrypt-Password {
    param([string]$Base64Encrypted)

    $encryptedBytes = [Convert]::FromBase64String($Base64Encrypted)
    $decryptedBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
        $encryptedBytes,
        $null,
        [System.Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    return [System.Text.Encoding]::UTF8.GetString($decryptedBytes)
}

function Find-WindowByProcessId {
    param([int]$ProcessId)

    $script:_foundHwnd = [IntPtr]::Zero
    $wrappedCallback = [Win32HelperV2+EnumWindowsProc] {
        param([IntPtr]$hWnd, [IntPtr]$lParam)
        $winPid = 0
        [void][Win32HelperV2]::GetWindowThreadProcessId($hWnd, [ref]$winPid)
        if ($winPid -eq $ProcessId -and [Win32HelperV2]::IsWindowVisible($hWnd)) {
            $sb = New-Object System.Text.StringBuilder 256
            [void][Win32HelperV2]::GetWindowText($hWnd, $sb, 256)
            $title = $sb.ToString()
            if ($title -match "NinjaTrader|Login|Control Center" -or $script:_foundHwnd -eq [IntPtr]::Zero) {
                if ($title -match "NinjaTrader|Login|Control Center") {
                    $script:_foundHwnd = $hWnd
                    return $false
                }
                if ($script:_foundHwnd -eq [IntPtr]::Zero) {
                    $script:_foundHwnd = $hWnd
                }
            }
        }
        return $true
    }

    [void][Win32HelperV2]::EnumWindows($wrappedCallback, [IntPtr]::Zero)
    return $script:_foundHwnd
}

function Get-NinjaTraderWindow {
    param(
        [System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds = 60
    )

    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        $Process.Refresh()

        $hwnd = $Process.MainWindowHandle
        if ($hwnd -ne [IntPtr]::Zero -and [Win32HelperV2]::IsWindowVisible($hwnd)) {
            return $hwnd
        }

        $hwnd = Find-WindowByProcessId -ProcessId $Process.Id
        if ($hwnd -ne [IntPtr]::Zero) {
            return $hwnd
        }

        Start-Sleep -Milliseconds 500
    }

    return [IntPtr]::Zero
}

function Find-WindowWithEditControls {
    param([int]$ProcessId)

    $editCond = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Edit
    )

    $desktop = [System.Windows.Automation.AutomationElement]::RootElement
    $allWindows = $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)

    for ($i = 0; $i -lt $allWindows.Count; $i++) {
        $win = $allWindows[$i]
        $winHandle = [IntPtr]$win.Current.NativeWindowHandle
        if ($winHandle -eq [IntPtr]::Zero) { continue }

        $winPid = 0
        [void][Win32HelperV2]::GetWindowThreadProcessId($winHandle, [ref]$winPid)
        if ($winPid -ne $ProcessId) { continue }

        $edits = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $editCond)
        if ($edits.Count -ge 2) {
            return @{ Element = $win; Hwnd = $winHandle; Edits = $edits }
        }
    }

    return $null
}

function Set-TextBoxValue {
    param([System.Windows.Automation.AutomationElement]$Element, [string]$Value)

    # 1. Try ValuePattern (direct set, most reliable)
    try {
        $pattern = $Element.GetCurrentPattern([System.Windows.Automation.PatternIdentifiers]::ValuePattern)
        if ($pattern) {
            $pattern.SetValue($Value)
            return $true
        }
    } catch {}

    # 2. Fallback: focus the control and SendKeys into it
    try {
        $Element.SetFocus()
        Start-Sleep -Milliseconds 150
        [System.Windows.Forms.SendKeys]::SendWait("^a")
        Start-Sleep -Milliseconds 50
        $safe = Escape-SendKeys -Text $Value
        [System.Windows.Forms.SendKeys]::SendWait($safe)
        return $true
    } catch {}

    return $false
}

function Invoke-Button {
    param([System.Windows.Automation.AutomationElement]$Element)

    try {
        $pattern = $Element.GetCurrentPattern([System.Windows.Automation.PatternIdentifiers]::InvokePattern)
        if ($pattern) {
            $pattern.Invoke()
            return $true
        }
    } catch {}
    return $false
}

function Find-LoginButton {
    param([System.Windows.Automation.AutomationElement]$Parent)

    $btnCond = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button
    )
    $buttons = $Parent.FindAll([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
    for ($i = 0; $i -lt $buttons.Count; $i++) {
        $btn = $buttons[$i]
        $name = $btn.Current.Name
        if ($name -match "^\s*Login\s*$|^\s*Log in\s*$|^\s*OK\s*$|^\s*Sign in\s*$") {
            return $btn
        }
    }
    return $null
}

function Find-TryItDialog {
    param([int]$ProcessId, [int]$TimeoutSeconds = 30)

    $btnCond = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Button
    )

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        $desktop = [System.Windows.Automation.AutomationElement]::RootElement
        $allWindows = $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)

        for ($i = 0; $i -lt $allWindows.Count; $i++) {
            $win = $allWindows[$i]
            $winHandle = [IntPtr]$win.Current.NativeWindowHandle
            if ($winHandle -eq [IntPtr]::Zero) { continue }

            $winPid = 0
            [void][Win32HelperV2]::GetWindowThreadProcessId($winHandle, [ref]$winPid)
            if ($winPid -ne $ProcessId) { continue }

            $buttons = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
            for ($j = 0; $j -lt $buttons.Count; $j++) {
                $btn = $buttons[$j]
                if ($btn.Current.Name -match "Try it|Try It|TRY IT") {
                    return @{ Button = $btn; Window = $win; Hwnd = $winHandle }
                }
            }
        }

        Start-Sleep -Milliseconds 500
    }

    return $null
}

function Escape-SendKeys {
    param([string]$Text)
    $special = @('+', '^', '%', '~', '(', ')', '{', '}', '[', ']')
    $result = New-Object System.Text.StringBuilder
    foreach ($char in $Text.ToCharArray()) {
        if ($special -contains "$char") {
            [void]$result.Append("{$char}")
        } else {
            [void]$result.Append($char)
        }
    }
    return $result.ToString()
}

function Set-ForegroundWindowRobust {
    param([IntPtr]$hWnd)

    $foregroundWindow = [Win32HelperV2]::GetForegroundWindow()
    $targetThread = 0
    [void][Win32HelperV2]::GetWindowThreadProcessId($hWnd, [ref]$targetThread)
    $currentThread = [Win32HelperV2]::GetCurrentThreadId()
    $foregroundThread = 0
    [void][Win32HelperV2]::GetWindowThreadProcessId($foregroundWindow, [ref]$foregroundThread)

    if ($targetThread -ne 0 -and $currentThread -ne $targetThread) {
        if ($foregroundThread -ne 0 -and $currentThread -ne $foregroundThread) {
            [void][Win32HelperV2]::AttachThreadInput($currentThread, $foregroundThread, $true)
        }
        [void][Win32HelperV2]::AttachThreadInput($currentThread, $targetThread, $true)
    }

    [void][Win32HelperV2]::ShowWindow($hWnd, [Win32HelperV2]::SW_RESTORE)
    [void][Win32HelperV2]::SetForegroundWindow($hWnd)

    if ($targetThread -ne 0 -and $currentThread -ne $targetThread) {
        [void][Win32HelperV2]::AttachThreadInput($currentThread, $targetThread, $false)
        if ($foregroundThread -ne 0 -and $currentThread -ne $foregroundThread) {
            [void][Win32HelperV2]::AttachThreadInput($currentThread, $foregroundThread, $false)
        }
    }
}

# =============================================================================
# Main
# =============================================================================

try {
    # -- Resolve NinjaTrader path --
    if (-not (Test-Path $NinjaTraderPath)) {
        Write-Warning "NinjaTrader not found at default path: $NinjaTraderPath"
        Write-Host "Attempting auto-detection..." -ForegroundColor Yellow
        $NinjaTraderPath = Find-NinjaTraderExe
    }
    if (-not $NinjaTraderPath -or -not (Test-Path $NinjaTraderPath)) {
        throw "NinjaTrader.exe not found. Specify -NinjaTraderPath."
    }

    # -- Load credentials --
    $store = Get-CredentialStore -Path $CredentialFile
    $username = $store.Username
    $password = Decrypt-Password -Base64Encrypted $store.PasswordBase64

    if ([string]::IsNullOrWhiteSpace($username)) {
        throw "Username is empty in credential store."
    }

    Write-Host ""
    Write-Host "Starting NinjaTrader with auto-login for: $username" -ForegroundColor Cyan

    # -- Check if already running --
    $existingNt = Get-Process | Where-Object { $_.ProcessName -like "*NinjaTrader*" } | Select-Object -First 1
    if ($existingNt) {
        Write-Warning "NinjaTrader is already running (PID $($existingNt.Id)). This script works best when started from a closed state."
        $continue = Read-Host "Continue anyway? (y/N)"
        if ($continue -notmatch '^[Yy]') { exit 0 }
    }

    # -- Launch NinjaTrader (capture the process) --
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $NinjaTraderPath
    $psi.WorkingDirectory = Split-Path -Parent $NinjaTraderPath
    $psi.UseShellExecute = $false
    $ntProcess = [System.Diagnostics.Process]::Start($psi)

    if (-not $ntProcess) {
        throw "Failed to start NinjaTrader process."
    }

    Write-Host "Waiting for NinjaTrader window to appear (up to $MaxWaitSeconds seconds)..." -ForegroundColor Yellow

    $hwnd = Get-NinjaTraderWindow -Process $ntProcess -TimeoutSeconds $MaxWaitSeconds
    if ($hwnd -eq [IntPtr]::Zero) {
        throw "NinjaTrader window not found within $MaxWaitSeconds seconds.`nIf NinjaTrader is already logged in, no action is needed."
    }

    Set-ForegroundWindowRobust -hWnd $hwnd
    Start-Sleep -Milliseconds 800

    Write-Host "Window found. Entering credentials..." -ForegroundColor Green

    # -- Try UI Automation across ALL top-level windows from this process --
    $uiaSuccess = $false
    $foundDialog = Find-WindowWithEditControls -ProcessId $ntProcess.Id

    if ($foundDialog) {
        Write-Host "Found login dialog with $($foundDialog.Edits.Count) input field(s) via UI Automation." -ForegroundColor Green

        # Bring the dialog window to the foreground
        Set-ForegroundWindowRobust -hWnd $foundDialog.Hwnd
        Start-Sleep -Milliseconds 300

        $uiaSuccess = Set-TextBoxValue -Element $foundDialog.Edits[0] -Value $username
        if ($uiaSuccess) {
            Start-Sleep -Milliseconds 200
            $uiaSuccess = Set-TextBoxValue -Element $foundDialog.Edits[1] -Value $password
        }

        if ($uiaSuccess) {
            Start-Sleep -Milliseconds 200
            $loginBtn = Find-LoginButton -Parent $foundDialog.Element
            if ($loginBtn) {
                Invoke-Button -Element $loginBtn | Out-Null
            }
            # Always send Enter as well — it is the most reliable way to submit a login dialog
            Start-Sleep -Milliseconds 200
            [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
            Write-Host "Login submitted via UI Automation." -ForegroundColor Green
        }
    }

    # -- Fallback: SendKeys on the active window --
    if (-not $uiaSuccess) {
        Write-Host "Falling back to SendKeys..." -ForegroundColor Yellow

        # If we found the dialog, use its handle; otherwise use the main window
        $targetHwnd = if ($foundDialog) { $foundDialog.Hwnd } else { $hwnd }
        Set-ForegroundWindowRobust -hWnd $targetHwnd
        Start-Sleep -Milliseconds 800

        $safeUser = Escape-SendKeys -Text $username
        $safePass = Escape-SendKeys -Text $password

        [System.Windows.Forms.SendKeys]::SendWait($safeUser)
        Start-Sleep -Milliseconds 300
        [System.Windows.Forms.SendKeys]::SendWait("{TAB}")
        Start-Sleep -Milliseconds 300
        [System.Windows.Forms.SendKeys]::SendWait("{TAB}")
        Start-Sleep -Milliseconds 300
        [System.Windows.Forms.SendKeys]::SendWait($safePass)
        Start-Sleep -Milliseconds 300
        [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")

        Write-Host "Login submitted via SendKeys." -ForegroundColor Green
    }

    # -- Dismiss "Try it" post-login dialog if it appears --
    Write-Host "Checking for post-login dialogs..." -ForegroundColor Yellow
    $tryIt = Find-TryItDialog -ProcessId $ntProcess.Id -TimeoutSeconds 30
    if ($tryIt) {
        Write-Host "Found 'Try it' dialog. Clicking it..." -ForegroundColor Green
        Set-ForegroundWindowRobust -hWnd $tryIt.Hwnd
        Start-Sleep -Milliseconds 800

        # Focus the button itself so Enter will trigger it
        try { $tryIt.Button.SetFocus() } catch {}
        Start-Sleep -Milliseconds 300
        [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")

        Write-Host "'Try it' dialog dismissed." -ForegroundColor Green
    }

    # -- Wait for exit if requested --
    if ($WaitForExit) {
        Write-Host "Waiting for NinjaTrader to exit..." -ForegroundColor Yellow
        $ntProcess.WaitForExit()
        Write-Host "NinjaTrader has exited." -ForegroundColor Green
    } else {
        Write-Host "NinjaTrader is running. You may close this window." -ForegroundColor Green
    }

} catch {
    Write-Host ""
    Write-Host "ERROR: $_" -ForegroundColor Red
    exit 1
}
