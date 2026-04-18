#Requires -Version 5.1
<#
.SYNOPSIS
    Securely stores NinjaTrader credentials for the one-click launcher.

.DESCRIPTION
    Prompts for username and password, encrypts the password using Windows
    Data Protection (DPAPI), and saves both to a local JSON file.
    Only the current Windows user can decrypt the password.

.PARAMETER CredentialFile
    Path where the encrypted credential store is written.
    Default: %LOCALAPPDATA%\TradingBot\NTCredential.dat

.EXAMPLE
    .\Set-NTCredential.ps1
    .\Set-NTCredential.ps1 -CredentialFile "C:\Tools\ntcreds.json"
#>
[CmdletBinding()]
param(
    [string]$CredentialFile = "$env:LOCALAPPDATA\TradingBot\NTCredential.dat"
)

$ErrorActionPreference = "Stop"

# ─────────────────────────────────────────────────────────────────────────────
# Load required assembly for DPAPI encryption
# ─────────────────────────────────────────────────────────────────────────────
Add-Type -AssemblyName System.Security

# ─────────────────────────────────────────────────────────────────────────────
# Prompt for credentials
# ─────────────────────────────────────────────────────────────────────────────
$username = Read-Host -Prompt "Enter your NinjaTrader username"
if ([string]::IsNullOrWhiteSpace($username)) {
    Write-Error "Username cannot be empty."
    exit 1
}

$securePassword = Read-Host -Prompt "Enter your NinjaTrader password" -AsSecureString
if ($securePassword.Length -eq 0) {
    Write-Error "Password cannot be empty."
    exit 1
}

# Convert SecureString to plain text (in memory only) so we can encrypt it with DPAPI
$BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
    $plainPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
} finally {
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR)
}

# ─────────────────────────────────────────────────────────────────────────────
# Encrypt password with DPAPI (CurrentUser scope)
# ─────────────────────────────────────────────────────────────────────────────
$pwBytes   = [System.Text.Encoding]::UTF8.GetBytes($plainPassword)
$encrypted = [System.Security.Cryptography.ProtectedData]::Protect(
    $pwBytes,
    $null,  # no optional entropy
    [System.Security.Cryptography.DataProtectionScope]::CurrentUser
)

# Clear plaintext from memory as best we can
[System.GC]::Collect()

# ─────────────────────────────────────────────────────────────────────────────
# Write credential store
# ─────────────────────────────────────────────────────────────────────────────
$storeDir = Split-Path -Parent $CredentialFile
if (-not (Test-Path $storeDir)) {
    New-Item -ItemType Directory -Path $storeDir -Force | Out-Null
}

$store = [ordered]@{
    Username       = $username
    PasswordBase64 = [Convert]::ToBase64String($encrypted)
    Created        = (Get-Date -Format "o")
    Version        = 1
} | ConvertTo-Json -Depth 3

$store | Set-Content -Path $CredentialFile -Encoding UTF8

Write-Host ""
Write-Host "Credentials saved successfully." -ForegroundColor Green
Write-Host "  File: $CredentialFile"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Compile the updated TradingBotZmqConnector AddOn in NinjaTrader."
Write-Host "  2. Create the TradingBotZmqConfig.json file (see docs)."
Write-Host "  3. Run Start-TradingBot.ps1 or double-click the desktop shortcut."
Write-Host ""
