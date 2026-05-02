#Requires -Version 5.1
<#
.SYNOPSIS
    Fully automated TradingBot setup - prompts for minimum info, configures everything else.

.DESCRIPTION
    One script to configure the entire Windows + WSL stack:
      1. Checks prerequisites (PowerShell, WSL, NinjaTrader)
      2. Prompts for credentials, account, and instrument
      3. Downloads & installs NetMQ DLLs into NinjaTrader
      4. Copies C# AddOn source files to NinjaTrader AddOns folder
      5. Creates TradingBotZmqConfig.json (autoConnectOnStartup = true)
      6. Securely stores NinjaTrader credentials via Windows DPAPI
      7. Installs the WSL systemd tradingbot service
      8. Creates a "Start TradingBot" desktop shortcut
      9. Shows a summary and offers to start the service

    The only remaining manual step is compiling the NinjaScript inside
    NinjaTrader (Tools -> Edit NinjaScript -> press F5).

.PARAMETER NinjaTraderPath
    Full path to NinjaTrader.exe. Auto-detected from registry/common paths if omitted.

.PARAMETER WslDistro
    Name of the WSL distribution. Default: Ubuntu

.PARAMETER SkipNetMQ
    Skip NetMQ DLL download/copy (use if already installed).

.PARAMETER SkipWslService
    Skip WSL systemd service installation.

.EXAMPLE
    .\Setup-TradingBot.ps1

.EXAMPLE
    .\Setup-TradingBot.ps1 -NinjaTraderPath "D:\NinjaTrader 8\bin64\NinjaTrader.exe" -WslDistro "Debian"
#>
[CmdletBinding()]
param(
    [string]$NinjaTraderPath,
    [string]$WslDistro = "Ubuntu",
    [switch]$SkipNetMQ,
    [switch]$SkipWslService,
    [switch]$ResetVault
)

$ErrorActionPreference = "Stop"

# -- Load required assemblies --
Add-Type -AssemblyName System.Security
Add-Type -AssemblyName System.Windows.Forms

# -- Script-scoped state --
$script:ProjectDir   = (Resolve-Path "$PSScriptRoot\..").Path
$script:CredentialFile = "$env:LOCALAPPDATA\TradingBot\NTCredential.dat"
$script:VaultFile    = "$env:LOCALAPPDATA\TradingBot\NTCredential.vault.json"
$script:ToolsDir     = "$env:LOCALAPPDATA\TradingBot\Tools"
$script:OkCount      = 0
$script:WarnCount    = 0
$script:ErrCount     = 0

# =============================================================================
# Helpers
# =============================================================================

function Write-Status {
    param([string]$Message, [string]$Level = "Info")
    $ts = Get-Date -Format "HH:mm:ss"
    switch ($Level) {
        "Success" { Write-Host "[$ts] [OK]    $Message" -ForegroundColor Green;  $script:OkCount++ }
        "Warn"    { Write-Host "[$ts] [WARN]  $Message" -ForegroundColor Yellow; $script:WarnCount++ }
        "Error"   { Write-Host "[$ts] [ERR]   $Message" -ForegroundColor Red;    $script:ErrCount++ }
        "Header"  { Write-Host ""; Write-Host "==> $Message" -ForegroundColor Cyan; Write-Host "" }
        default   { Write-Host "[$ts] [INFO]  $Message" }
    }
}

function Write-Divider {
    Write-Host "------------------------------------------------------------" -ForegroundColor DarkGray
}

function Find-NinjaTraderExe {
    # 1. Registry
    $regPaths = @(
        "HKLM:\SOFTWARE\NinjaTrader, LLC\NinjaTrader 8",
        "HKLM:\SOFTWARE\WOW6432Node\NinjaTrader, LLC\NinjaTrader 8"
    )
    foreach ($rp in $regPaths) {
        try {
            $props = Get-ItemProperty -Path $rp -ErrorAction SilentlyContinue
            if ($props -and $props.InstallDir) {
                # Try both bin and bin64 locations
                foreach ($sub in @("bin", "bin64")) {
                    $exe = Join-Path $props.InstallDir "$sub\NinjaTrader.exe"
                    if (Test-Path $exe) { return $exe }
                }
            }
        } catch {}
    }

    # 2. Common filesystem locations
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

function Get-NinjaTraderCustomDir {
    param([string]$NtExePath)
    # Derive Custom dir from exe location (go up from bin or bin64)
    $ntRoot = Split-Path (Split-Path $NtExePath)
    $customFromExe = Join-Path $ntRoot "bin\Custom"

    # If it exists and we can write to it, use it
    if (Test-Path $customFromExe) {
        try {
            $testFile = Join-Path $customFromExe "_write_test_.tmp"
            [IO.File]::WriteAllText($testFile, "test")
            Remove-Item $testFile -Force
            return $customFromExe
        } catch {
            # Not writable (e.g. Program Files) - silently fall back to Documents
        }
    }

    # Fallback to Documents
    $docs = [Environment]::GetFolderPath("MyDocuments")
    $customFromDocs = Join-Path $docs "NinjaTrader 8\bin\Custom"
    if (-not (Test-Path $customFromDocs)) {
        New-Item -ItemType Directory -Path $customFromDocs -Force | Out-Null
    }
    return $customFromDocs
}

function ConvertTo-WslPath {
    param([string]$WindowsPath)
    ($WindowsPath -replace '^([A-Za-z]):', '/mnt/$1' -replace '\\', '/').ToLower()
}

function Get-Vault {
    if (Test-Path $script:VaultFile) {
        try {
            $data = Get-Content -Path $script:VaultFile -Raw | ConvertFrom-Json
            # Ensure we always return an array (ConvertFrom-Json returns a single object for 1-item JSON)
            if ($data -is [array]) {
                $accounts = $data
            } else {
                $accounts = @($data)
            }
            # Validate: each entry must have a Username
            # Wrap in @() because Where-Object on a 1-element array returns the object itself in PS 5.1
            $valid = @($accounts | Where-Object { $_.Username })
            if ($valid.Count -eq 0) {
                Write-Status "Vault file is corrupted (no valid accounts). Resetting." "Warn"
                Remove-Item $script:VaultFile -Force -ErrorAction SilentlyContinue
            } else {
                return $valid
            }
        } catch {
            Write-Status "Vault file is corrupted. Resetting." "Warn"
            Remove-Item $script:VaultFile -Force -ErrorAction SilentlyContinue
        }
    }
    # Migrate old single-account credential file to vault
    if (Test-Path $script:CredentialFile) {
        try {
            $old = Get-Content -Path $script:CredentialFile -Raw | ConvertFrom-Json
            $vault = @( [ordered]@{
                Username       = $old.Username
                PasswordBase64 = $old.PasswordBase64
                Created        = $old.Created
                Version        = 1
            } )
            Save-Vault -Accounts $vault
            Write-Status "Migrated existing credentials to vault." "Success"
            return $vault
        } catch {
            Write-Status "Could not migrate old credential file. Starting fresh vault." "Warn"
        }
    }
    return @()
}

function Save-Vault {
    param([array]$Accounts)
    $dir = Split-Path -Parent $script:VaultFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    # Use InputObject (not pipeline) so hashtables are serialized as objects, not enumerated
    ConvertTo-Json -InputObject @($Accounts) -Depth 3 | Set-Content -Path $script:VaultFile -Encoding UTF8
}

function Add-AccountToVault {
    param([string]$Username, [string]$Password)

    $pwBytes   = [System.Text.Encoding]::UTF8.GetBytes($Password)
    $encrypted = [System.Security.Cryptography.ProtectedData]::Protect(
        $pwBytes, $null,
        [System.Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    [System.GC]::Collect()

    $vault = Get-Vault
    # Remove existing entry with same username (overwrite)
    $vault = $vault | Where-Object { $_.Username -ne $Username }
    $vault += [ordered]@{
        Username       = $Username
        PasswordBase64 = [Convert]::ToBase64String($encrypted)
        Created        = (Get-Date -Format "o")
        Version        = 1
    }
    Save-Vault -Accounts $vault
    Write-Status "Account '$Username' saved to vault." "Success"
}

function Select-AccountFromVault {
    $vault = @(Get-Vault)

    if ($vault.Count -eq 0) {
        return $null
    }

    Write-Host ""
    Write-Host "Saved NinjaTrader accounts:" -ForegroundColor Cyan
    for ($i = 0; $i -lt $vault.Count; $i++) {
        Write-Host "  [$($i + 1)] $($vault[$i].Username)" -ForegroundColor White
    }
    Write-Host "  [N] New account" -ForegroundColor White
    Write-Host ""

    $choice = Read-Host "Select an account [1-$($vault.Count) or N]"
    if ($choice -match '^[Nn]') {
        return $null
    }

    $idx = 0
    if ([int]::TryParse($choice, [ref]$idx) -and $idx -ge 1 -and $idx -le $vault.Count) {
        return $vault[$idx - 1]
    }

    Write-Status "Invalid selection. Prompting for new account." "Warn"
    return $null
}

function Save-ActiveCredential {
    param([psobject]$Account, [string]$OutFile)

    $store = [ordered]@{
        Username       = $Account.Username
        PasswordBase64 = $Account.PasswordBase64
        Created        = $Account.Created
        Version        = 1
    } | ConvertTo-Json -Depth 3

    $store | Set-Content -Path $OutFile -Encoding UTF8
    Write-Status "Active credential set to: $($Account.Username)" "Success"
}

function Install-NetMQDlls {
    param([string]$CustomDir)

    $nuget = Join-Path $script:ToolsDir "nuget.exe"

    # Download nuget if missing
    if (-not (Test-Path $nuget)) {
        Write-Status "nuget.exe not found - downloading..."
        New-Item -ItemType Directory -Path $script:ToolsDir -Force | Out-Null
        Invoke-WebRequest -Uri "https://dist.nuget.org/win-x86-commandline/latest/nuget.exe" -OutFile $nuget
        Write-Status "nuget.exe downloaded." "Success"
    }

    # Install exact NetMQ version that works with .NET Framework 4.8
    # DO NOT use latest — newer versions pull in .NET 10 dependencies that break NinjaTrader
    $hasNetMQ = @(Get-ChildItem -Path $script:ToolsDir -Directory -Filter "NetMQ.4.0.1.13").Count -gt 0
    if (-not $hasNetMQ) {
        Write-Status "Downloading NetMQ 4.0.1.13 (compatible with .NET 4.8)..."
        & $nuget install NetMQ -Version 4.0.1.13 -OutputDirectory $script:ToolsDir | Out-Null
        Write-Status "NetMQ downloaded." "Success"
    } else {
        Write-Status "NetMQ already present - skipping download." "Warn"
    }

    # Install dependency packages at exact versions
    $deps = @(
        @{Name="AsyncIO"; Version="0.1.69"},
        @{Name="System.Memory"; Version="4.5.3"},
        @{Name="System.Runtime.CompilerServices.Unsafe"; Version="6.0.0"},
        @{Name="Microsoft.Bcl.AsyncInterfaces"; Version="9.0.0"},
        @{Name="System.Threading.Tasks.Extensions"; Version="4.5.4"}
    )
    foreach ($dep in $deps) {
        $hasDep = @(Get-ChildItem -Path $script:ToolsDir -Directory -Filter "$($dep.Name).$($dep.Version)").Count -gt 0
        if (-not $hasDep) {
            Write-Status "Downloading $($dep.Name) $($dep.Version)..."
            & $nuget install $dep.Name -Version $dep.Version -OutputDirectory $script:ToolsDir | Out-Null
            Write-Status "$($dep.Name) downloaded." "Success"
        }
    }

    # Copy DLLs using exact paths for versions that work with .NET Framework 4.8
    # NOTE: netstandard.dll is copied to a 'refs' subfolder, NOT bin\Custom\ directly.
    # We use the .NET Framework 4.8 FACADE (from C:\Windows\Microsoft.NET\Framework\v4.0.30319)
    # which is ~100 KB and type-forwards to mscorlib. The NuGet NETStandard.Library package
    # contains a ~1.3 MB REFERENCE assembly with full type definitions that causes CS0433
    # duplicate-type conflicts when loaded alongside mscorlib.
    $mappings = @(
        @{ Src = "$script:ToolsDir\NetMQ.4.0.1.13\lib\net47\NetMQ.dll";                            Name = "NetMQ.dll";                           IsRef = $false },
        @{ Src = "$script:ToolsDir\AsyncIO.0.1.69\lib\netstandard2.0\AsyncIO.dll";                  Name = "AsyncIO.dll";                         IsRef = $false },
        @{ Src = "$script:ToolsDir\System.Memory.4.5.3\lib\netstandard2.0\System.Memory.dll";      Name = "System.Memory.dll";                   IsRef = $false },
        @{ Src = "$script:ToolsDir\System.Runtime.CompilerServices.Unsafe.6.0.0\lib\net461\System.Runtime.CompilerServices.Unsafe.dll"; Name = "System.Runtime.CompilerServices.Unsafe.dll"; IsRef = $false },
        @{ Src = "$script:ToolsDir\Microsoft.Bcl.AsyncInterfaces.9.0.0\lib\net462\Microsoft.Bcl.AsyncInterfaces.dll"; Name = "Microsoft.Bcl.AsyncInterfaces.dll"; IsRef = $false },
        @{ Src = "$script:ToolsDir\System.Threading.Tasks.Extensions.4.5.4\lib\net461\System.Threading.Tasks.Extensions.dll"; Name = "System.Threading.Tasks.Extensions.dll"; IsRef = $false },
        @{ Src = "$env:SystemRoot\Microsoft.NET\Framework\v4.0.30319\netstandard.dll";              Name = "netstandard.dll";                     IsRef = $true }
    )

    foreach ($map in $mappings) {
        if (Test-Path $map.Src) {
            if ($map.IsRef) {
                $refDir = Join-Path $CustomDir "refs"
                New-Item -ItemType Directory -Path $refDir -Force | Out-Null
                $dest = Join-Path $refDir $map.Name
            } else {
                $dest = Join-Path $CustomDir $map.Name
            }
            Copy-Item -Path $map.Src -Destination $dest -Force
            Write-Status "Copied $($map.Name)" "Success"
        } else {
            Write-Status "Could not find DLL: $($map.Src)" "Error"
        }
    }
}

function Copy-AddOnFiles {
    param([string]$AddOnsDir)

    $source = Join-Path $script:ProjectDir "zmq_connectors\ninjatrader"
    $dest   = Join-Path $AddOnsDir "TradingBotZMQ"

    if (-not (Test-Path $source)) {
        Write-Status "AddOn source not found: $source" "Error"
        return
    }

    # If a symlink/junction already exists, verify it points to our source and skip copying
    if (Test-Path $dest) {
        $item = Get-Item $dest -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            $linkTarget = $item.Target
            if ($linkTarget -and $linkTarget.TrimEnd('\') -eq $source.TrimEnd('\')) {
                Write-Status "Symlink already exists: $dest -> $source" "Success"
                return
            } else {
                Write-Status "Existing symlink points elsewhere. Removing and re-creating..." "Warn"
                Remove-Item $dest -Force -Recurse
            }
        }
    }

    New-Item -ItemType Directory -Path $dest -Force | Out-Null

    $files = Get-ChildItem -Path $source -Filter "*.cs" -Recurse -File
    $failed = @()
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($source.Length + 1)
        $target = Join-Path $dest $rel
        $targetDir = Split-Path -Parent $target
        if (-not (Test-Path $targetDir)) { New-Item -ItemType Directory -Path $targetDir -Force | Out-Null }

        $copied = $false
        for ($retry = 0; $retry -lt 3; $retry++) {
            try {
                Copy-Item -Path $f.FullName -Destination $target -Force
                $copied = $true
                break
            } catch [System.IO.IOException] {
                if ($retry -lt 2) {
                    Start-Sleep -Milliseconds 500
                }
            }
        }

        if ($copied) {
            Write-Status "AddOn  -> $rel" "Success"
        } else {
            Write-Status "AddOn  -> $rel (locked, skipped)" "Warn"
            $failed += $rel
        }
    }

    if ($failed.Count -gt 0) {
        Write-Status "Some files were locked and skipped. Close any apps using them and re-run." "Warn"
    }
    Write-Status "AddOn files copied to:`n       $dest" "Success"
}

function New-ZmqConfig {
    param([string]$CustomDir, [string]$Instrument)

    $path = Join-Path $CustomDir "TradingBotZmqConfig.json"
    $json = @{
        host                 = "127.0.0.1"
        marketPort           = 5555
        commandPort          = 5556
        queryPort            = 5557
        heartbeatPort        = 5558
        instrument           = $Instrument
        historyDays          = 30
        batchSize            = 500
        maxTicksPerSecond    = 10
        autoConnectOnStartup = $true
        autoShowWindow       = $true
    } | ConvertTo-Json -Depth 3

    $json | Set-Content -Path $path -Encoding UTF8
    Write-Status "Created TradingBotZmqConfig.json" "Success"
}

function Install-WslDependencies {
    param([string]$Distro, [string]$LinuxProjectPath)

    Write-Status "Checking Python dependencies in WSL ($Distro)..."

    # Check if poetry is installed (add ~/.local/bin to PATH in case it's not there yet)
    $hasPoetry = wsl -d $Distro -e bash -c 'export PATH="$HOME/.local/bin:$PATH" && which poetry' 2>$null
    if ($hasPoetry) { $hasPoetry = $hasPoetry.Trim() }
    if ([string]::IsNullOrWhiteSpace($hasPoetry)) {
        Write-Status "Poetry not found. Installing..."
        $poetryInstall = wsl -d $Distro -e bash -c "curl -sSL https://install.python-poetry.org | python3 -" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Status "Poetry installation failed." "Error"
            Write-Status "Output: $poetryInstall" "Warn"
            return
        }
        # Re-check now that it's installed (add ~/.local/bin to PATH)
        $hasPoetry = wsl -d $Distro -e bash -c 'export PATH="$HOME/.local/bin:$PATH" && which poetry' 2>$null
        if ($hasPoetry) { $hasPoetry = $hasPoetry.Trim() }
        if ([string]::IsNullOrWhiteSpace($hasPoetry)) {
            # Fallback to user's home directory
            $hasPoetry = wsl -d $Distro -e bash -c 'echo "$HOME/.local/bin/poetry"' 2>$null
            if ($hasPoetry) { $hasPoetry = $hasPoetry.Trim() }
        }
        Write-Status "Poetry installed at: $hasPoetry" "Success"
    } else {
        Write-Status "Poetry found: $hasPoetry" "Success"
    }

    # Run poetry install (install dependencies only, skip the root package)
    Write-Status "Running 'poetry install' (this may take a few minutes)..."
    $poetryInstall = wsl -d $Distro -e bash -c "export PATH=`"`$HOME/.local/bin:`$PATH`" && cd '$LinuxProjectPath' && poetry install --no-interaction --no-root" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Status "poetry install failed." "Error"
        Write-Status "Output: $poetryInstall" "Warn"
    } else {
        Write-Status "Python dependencies installed." "Success"
    }
}

function Install-WslService {
    param([string]$Distro, [string]$LinuxProjectPath)

    Write-Status "Running install_service.sh in WSL ($Distro)..."

    # Detect the default WSL user (so the service runs as the right user, not root)
    $wslUser = (wsl -d $Distro whoami 2>$null).Trim()
    if ([string]::IsNullOrWhiteSpace($wslUser)) {
        $wslUser = $env:USERNAME
        Write-Status "Could not detect WSL default user, falling back to '$wslUser'." "Warn"
    }

    # Run as root (-u root) to avoid sudo password prompt, but set SUDO_USER
    # so install_service.sh knows which user's home/project paths to use
    $output = wsl -d $Distro -u root -e bash -c "export SUDO_USER='$wslUser' && cd '$LinuxProjectPath' && ./bin/install_service.sh" 2>&1
    $exitCode = $LASTEXITCODE

    # wsl sometimes returns bogus non-zero exit codes even on success.
    # Check output for actual success indicators.
    $looksOk = ($output -match "Installed and enabled") -or ($output -match "Created symlink")

    if ($exitCode -eq 0 -or $looksOk) {
        Write-Status "WSL systemd service installed for user '$wslUser'." "Success"
    } else {
        Write-Status "WSL service install failed (exit $exitCode)." "Error"
        Write-Status "Output: $output" "Warn"
    }
}

function New-DesktopShortcut {
    $launcher = Join-Path $script:ProjectDir "bin\Start-TradingBot.ps1"
    if (-not (Test-Path $launcher)) {
        Write-Status "Launcher script not found: $launcher" "Error"
        return
    }

    $shortcutPath = "$env:USERPROFILE\Desktop\Start TradingBot.lnk"
    $Wsh = New-Object -ComObject WScript.Shell
    $Sc  = $Wsh.CreateShortcut($shortcutPath)
    $Sc.TargetPath       = "powershell.exe"
    $Sc.Arguments        = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
    $Sc.WorkingDirectory = $script:ProjectDir
    $Sc.Description      = "Launch TradingBot WSL service + NinjaTrader + ZMQ Connector"
    $Sc.IconLocation     = "powershell.exe,0"
    $Sc.Save()

    Write-Status "Desktop shortcut created: Start TradingBot.lnk" "Success"
}

# =============================================================================
# MAIN
# =============================================================================

if ($ResetVault) {
    Write-Status "Resetting credential vault..." "Header"
    if (Test-Path $script:VaultFile) {
        Remove-Item $script:VaultFile -Force
        Write-Status "Deleted vault: $script:VaultFile" "Success"
    }
    if (Test-Path $script:CredentialFile) {
        Remove-Item $script:CredentialFile -Force
        Write-Status "Deleted credential: $script:CredentialFile" "Success"
    }
    Write-Status "Vault reset complete. Run again without -ResetVault to set up fresh credentials." "Success"
    exit 0
}

try {
    Write-Host ""
    Write-Status "TradingBot Fully Automated Setup" "Header"
    Write-Status "Project directory: $script:ProjectDir"
    Write-Divider

    # -- Phase 1: Prerequisites --
    Write-Status "Phase 1: Checking prerequisites..." "Header"

    if ($PSVersionTable.PSVersion.Major -lt 5) {
        throw "PowerShell 5.1 or later is required. You have $($PSVersionTable.PSVersion)."
    }
    Write-Status "PowerShell version OK." "Success"

    $wslOutput = wsl -l -v 2>&1 | Out-String
    if ([string]::IsNullOrWhiteSpace($wslOutput)) {
        throw "WSL is not installed or not available. Enable WSL first."
    }
    # Use -Quiet list for reliable distro name matching (avoids header/formatting issues)
    $distroList = wsl -l --quiet 2>$null
    $distroNames = $distroList | ForEach-Object { $_.Trim() }
    $found = $distroNames | Where-Object { $_ -eq $WslDistro }
    if (-not $found) {
        # Fallback: try case-insensitive match on the verbose output
        $found = $wslOutput -imatch [regex]::Escape($WslDistro)
    }
    if (-not $found) {
        throw "WSL distribution '$WslDistro' not found. Available distributions:`n$wslOutput"
    }
    Write-Status "WSL distribution '$WslDistro' is available." "Success"

    # NinjaTrader path
    if (-not $NinjaTraderPath) {
        $NinjaTraderPath = Find-NinjaTraderExe
    }
    if (-not $NinjaTraderPath -or -not (Test-Path $NinjaTraderPath)) {
        $NinjaTraderPath = Read-Host "NinjaTrader.exe not found. Enter full path (e.g. C:\Program Files\NinjaTrader 8\bin64\NinjaTrader.exe)"
        if (-not (Test-Path $NinjaTraderPath)) {
            throw "NinjaTrader not found at: $NinjaTraderPath"
        }
    }
    $ntCustomDir = Get-NinjaTraderCustomDir -NtExePath $NinjaTraderPath
    if (-not $ntCustomDir) {
        throw "Could not locate NinjaTrader 'bin\Custom' directory."
    }
    Write-Status "NinjaTrader found: $NinjaTraderPath" "Success"
    Write-Status "Custom folder:     $ntCustomDir" "Success"

    $ntRunning = Get-Process | Where-Object { $_.ProcessName -like "*NinjaTrader*" } | Select-Object -First 1
    if ($ntRunning) {
        Write-Status "NinjaTrader is currently RUNNING. Close it before proceeding or DLL/file copy may fail." "Warn"
        $confirm = Read-Host "Continue anyway? (y/N)"
        if ($confirm -notmatch '^[Yy]') { exit 0 }
    }

    Write-Divider

    # -- Phase 2: Prompts --
    Write-Status "Phase 2: Configuration prompts..." "Header"

    # Credentials - vault selection
    $selectedAccount = Select-AccountFromVault
    if ($selectedAccount) {
        Write-Status "Selected account: $($selectedAccount.Username)" "Success"
    } else {
        # Prompt for new account
        $newUser = Read-Host "Enter your NinjaTrader username"
        if ([string]::IsNullOrWhiteSpace($newUser)) { throw "Username is required." }

        $securePw = Read-Host "Enter your NinjaTrader password" -AsSecureString
        if ($securePw.Length -eq 0) { throw "Password is required." }
        $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePw)
        try { $newPw = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR) }
        finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR) }

        Add-AccountToVault -Username $newUser -Password $newPw
        $selectedAccount = Get-Vault | Where-Object { $_.Username -eq $newUser } | Select-Object -First 1
    }

    # Trading settings
    $defaultAccount = "FNFTCHCARLOSDUCLOS42006"
    $accountInput   = Read-Host "NinjaTrader account name [default: $defaultAccount]"
    $accountName    = if ($accountInput) { $accountInput } else { $defaultAccount }

    $pairInput = Read-Host "Trading pair [default: MNQ]"
    $pair      = if ($pairInput) { $pairInput } else { "MNQ" }

    $riskInput = Read-Host "Risk per trade (USD) [default: 160]"
    $risk      = if ($riskInput) { $riskInput } else { "160" }

    $instInput = Read-Host "Instrument [default: MNQ 06-26]"
    $instrument = if ($instInput) { $instInput } else { "MNQ 06-26" }

    Write-Status "Configuration collected." "Success"
    Write-Divider

    # -- Phase 3: Windows-side setup --
    Write-Status "Phase 3: Configuring Windows side..." "Header"

    # 3a. Credentials
    Save-ActiveCredential -Account $selectedAccount -OutFile $script:CredentialFile

    # 3b. NetMQ
    if (-not $SkipNetMQ) {
        Install-NetMQDlls -CustomDir $ntCustomDir
    } else {
        Write-Status "Skipping NetMQ installation (--SkipNetMQ)." "Warn"
    }

    # 3c. C# AddOn files
    Copy-AddOnFiles -AddOnsDir (Join-Path $ntCustomDir "AddOns")

    # 3d. ZMQ JSON config
    New-ZmqConfig -CustomDir $ntCustomDir -Instrument $instrument

    # 3e. Desktop shortcut
    New-DesktopShortcut

    Write-Divider

    # -- Phase 4: WSL-side setup --
    if (-not $SkipWslService) {
        Write-Status "Phase 4: Configuring WSL side..." "Header"
        $linuxPath = ConvertTo-WslPath -WindowsPath $script:ProjectDir
        Install-WslDependencies -Distro $WslDistro -LinuxProjectPath $linuxPath
        Install-WslService -Distro $WslDistro -LinuxProjectPath $linuxPath
    } else {
        Write-Status "Skipping WSL service install (--SkipWslService)." "Warn"
    }

    Write-Divider

    # -- Phase 5: Summary --
    Write-Status "Setup Summary" "Header"
    Write-Status "OK     : $script:OkCount"   "Success"
    if ($script:WarnCount -gt 0) { Write-Status "Warnings: $script:WarnCount" "Warn" }
    if ($script:ErrCount  -gt 0) { Write-Status "Errors  : $script:ErrCount"  "Error" }

    Write-Host ""
    Write-Host "Next step (manual - cannot be automated):" -ForegroundColor Yellow
    Write-Host "  1. Open NinjaTrader 8" -ForegroundColor White
    Write-Host "  2. Go to Tools -> Edit NinjaScript" -ForegroundColor White
    Write-Host "  3. In the References section, add references to:" -ForegroundColor White
    Write-Host "       * NetMQ.dll  (from bin\Custom\)" -ForegroundColor White
    Write-Host "       * netstandard.dll  (from bin\Custom\refs\)" -ForegroundColor White
    Write-Host "  4. Press F5 to compile" -ForegroundColor White
    Write-Host "  5. Close NinjaTrader" -ForegroundColor White
    Write-Host ""
    Write-Host "After that, launch everything with:" -ForegroundColor Yellow
    Write-Host "  * Double-click 'Start TradingBot' on your desktop" -ForegroundColor White
    Write-Host "  * Or run: .\bin\Start-TradingBot.ps1" -ForegroundColor White
    Write-Host ""
    Write-Host "Your settings:" -ForegroundColor Yellow
    Write-Host "  Account : $accountName" -ForegroundColor White
    Write-Host "  Pair    : $pair" -ForegroundColor White
    Write-Host "  Risk    : $risk" -ForegroundColor White
    Write-Host "  Instrument: $instrument" -ForegroundColor White
    Write-Host ""
    Write-Host "To override these when starting manually:" -ForegroundColor DarkGray
    Write-Host "  PAIR=$pair NT_ACCOUNT=$accountName RISK=$risk .\bin\start_live.sh" -ForegroundColor DarkGray
    Write-Host ""

    if (-not $SkipWslService) {
        $startNow = Read-Host "Start the WSL tradingbot service now? (y/N)"
        if ($startNow -match '^[Yy]') {
            wsl -d $WslDistro -u root systemctl start tradingbot
            Write-Status "Service start command sent." "Success"
            Write-Status "Check status: wsl -d $WslDistro -u root systemctl status tradingbot" "Info"
        }
    }

    Write-Host ""
    Write-Status "Setup finished." "Success"

} catch {
    Write-Status $_.Exception.Message "Error"
    Write-Status "Setup aborted. Fix the issue above and re-run the script." "Error"
    exit 1
}
