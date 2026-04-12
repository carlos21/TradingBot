# Install-NetMQ.ps1
# Automates NetMQ installation for NinjaTrader 8
# Run as Administrator

param(
    [string]$ToolsPath = "C:\Users\dark_\Tools",
    [string]$NinjaTraderPath = "C:\Users\dark_\Documents\NinjaTrader 8\bin\Custom"
)

Write-Host "=== NetMQ Installer for NinjaTrader 8 ===" -ForegroundColor Cyan
Write-Host ""

# Check if nuget.exe exists
$nuget = Join-Path $ToolsPath "nuget.exe"
if (-not (Test-Path $nuget)) {
    Write-Host "Downloading nuget.exe..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri "https://dist.nuget.org/win-x86-commandline/latest/nuget.exe" -OutFile $nuget
    Write-Host "nuget.exe downloaded successfully" -ForegroundColor Green
}

# Install NetMQ
Write-Host "Installing NetMQ package..." -ForegroundColor Yellow
& $nuget install NetMQ -OutputDirectory $ToolsPath

# Install NETStandard.Library
Write-Host "Installing NETStandard.Library package..." -ForegroundColor Yellow
& $nuget install NETStandard.Library -Version 2.0.3 -OutputDirectory $ToolsPath

Write-Host ""
Write-Host "=== Copying DLLs to NinjaTrader ===" -ForegroundColor Cyan

# Define source and destination mappings
$files = @(
    @{ Source = "$ToolsPath\NetMQ.4.0.2.2\lib\net472\NetMQ.dll"; Dest = "$NinjaTraderPath\NetMQ.dll" },
    @{ Source = "$ToolsPath\AsyncIO.0.1.69\lib\netstandard2.0\AsyncIO.dll"; Dest = "$NinjaTraderPath\AsyncIO.dll" },
    @{ Source = "$ToolsPath\System.Memory.4.5.3\lib\netstandard2.0\System.Memory.dll"; Dest = "$NinjaTraderPath\System.Memory.dll" },
    @{ Source = "$ToolsPath\System.Runtime.CompilerServices.Unsafe.6.1.2\lib\net462\System.Runtime.CompilerServices.Unsafe.dll"; Dest = "$NinjaTraderPath\System.Runtime.CompilerServices.Unsafe.dll" },
    @{ Source = "$ToolsPath\Microsoft.Bcl.AsyncInterfaces.9.0.6\lib\net462\Microsoft.Bcl.AsyncInterfaces.dll"; Dest = "$NinjaTraderPath\Microsoft.Bcl.AsyncInterfaces.dll" },
    @{ Source = "$ToolsPath\System.Threading.Tasks.Extensions.4.6.3\lib\net462\System.Threading.Tasks.Extensions.dll"; Dest = "$NinjaTraderPath\System.Threading.Tasks.Extensions.dll" },
    @{ Source = "$ToolsPath\NETStandard.Library.2.0.3\build\netstandard2.0\ref\netstandard.dll"; Dest = "$NinjaTraderPath\netstandard.dll" }
)

# Copy files
foreach ($file in $files) {
    if (Test-Path $file.Source) {
        try {
            Copy-Item -Path $file.Source -Destination $file.Dest -Force
            Write-Host "✓ Copied: $(Split-Path $file.Source -Leaf)" -ForegroundColor Green
        }
        catch {
            Write-Host "✗ Failed to copy: $(Split-Path $file.Source -Leaf)" -ForegroundColor Red
            Write-Host "  Error: $_" -ForegroundColor Red
        }
    }
    else {
        Write-Host "✗ Source not found: $($file.Source)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== Installation Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Open NinjaTrader 8" -ForegroundColor White
Write-Host "2. Go to Tools > Edit NinjaScript" -ForegroundColor White
Write-Host "3. Right-click References > Add" -ForegroundColor White
Write-Host "4. Add references to:" -ForegroundColor White
Write-Host "   - NetMQ.dll" -ForegroundColor White
Write-Host "   - netstandard.dll" -ForegroundColor White
Write-Host "5. Press F5 to compile" -ForegroundColor White
Write-Host ""
Write-Host "See NETMQ_SETUP_GUIDE.md for detailed instructions." -ForegroundColor Gray
