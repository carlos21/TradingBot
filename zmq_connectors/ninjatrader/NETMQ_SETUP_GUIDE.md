# NetMQ Setup Guide for NinjaTrader 8

Complete guide for installing and configuring NetMQ v4 (ZeroMQ) in NinjaTrader 8.

## Overview

This guide documents how to install NetMQ v4.0.2.2 in NinjaTrader 8, including all required dependencies and code changes needed from NetMQ v3 to v4.

---

## Prerequisites

- Windows with .NET Framework 4.8 (NinjaTrader 8 requirement)
- NinjaTrader 8 installed
- NuGet command-line tool (`nuget.exe`)

---

## Step 1: Download nuget.exe

If you don't have nuget.exe, download it:

```powershell
cd C:\Users\dark_\Tools
curl -o nuget.exe https://dist.nuget.org/win-x86-commandline/latest/nuget.exe
```

**Location:** `C:\Users\dark_\Tools\nuget.exe`

---

## Step 2: Install NetMQ Package

Open PowerShell or Command Prompt and run:

```powershell
cd C:\Users\dark_\Tools
.\nuget.exe install NetMQ -OutputDirectory .
```

This downloads NetMQ v4.0.2.2 and all dependencies to `C:\Users\dark_\Tools\`.

### Downloaded Packages:
- `NetMQ.4.0.2.2` - Main NetMQ library
- `AsyncIO.0.1.69` - Async I/O dependency
- `System.Memory.4.5.3` - Memory management
- `System.Runtime.CompilerServices.Unsafe.6.1.2` - Unsafe code support
- `Microsoft.Bcl.AsyncInterfaces.9.0.6` - Async interfaces
- `System.Threading.Tasks.Extensions.4.6.3` - Task extensions
- `System.Buffers.4.4.0` - Buffer management
- `System.Numerics.Vectors.4.4.0` - Vector operations
- `NaCl.Net.0.1.13` - Cryptography
- `System.ValueTuple.4.5.0` - Tuple support

---

## Step 3: Download netstandard.dll

NetMQ v4 requires .NET Standard 2.0 support. Download the compatibility library:

```powershell
cd C:\Users\dark_\Tools
.\nuget.exe install NETStandard.Library -Version 2.0.3 -OutputDirectory .
```

---

## Step 4: Copy DLLs to NinjaTrader

Copy these 7 DLLs from `C:\Users\dark_\Tools\` to `C:\Users\dark_\Documents\NinjaTrader 8\bin\Custom\`:

### Source → Destination Mapping:

| Source Path | Destination Path |
|------------|------------------|
| `NetMQ.4.0.2.2\lib\net472\NetMQ.dll` | `Documents\NinjaTrader 8\bin\Custom\NetMQ.dll` |
| `AsyncIO.0.1.69\lib\netstandard2.0\AsyncIO.dll` | `Documents\NinjaTrader 8\bin\Custom\AsyncIO.dll` |
| `System.Memory.4.5.3\lib\netstandard2.0\System.Memory.dll` | `Documents\NinjaTrader 8\bin\Custom\System.Memory.dll` |
| `System.Runtime.CompilerServices.Unsafe.6.1.2\lib\net462\System.Runtime.CompilerServices.Unsafe.dll` | `Documents\NinjaTrader 8\bin\Custom\System.Runtime.CompilerServices.Unsafe.dll` |
| `Microsoft.Bcl.AsyncInterfaces.9.0.6\lib\net462\Microsoft.Bcl.AsyncInterfaces.dll` | `Documents\NinjaTrader 8\bin\Custom\Microsoft.Bcl.AsyncInterfaces.dll` |
| `System.Threading.Tasks.Extensions.4.6.3\lib\net462\System.Threading.Tasks.Extensions.dll` | `Documents\NinjaTrader 8\bin\Custom\System.Threading.Tasks.Extensions.dll` |
| `NETStandard.Library.2.0.3\build\netstandard2.0\ref\netstandard.dll` | `Documents\NinjaTrader 8\bin\Custom\netstandard.dll` |

### Batch Copy Commands (PowerShell):

```powershell
$source = "C:\Users\dark_\Tools"
$dest = "C:\Users\dark_\Documents\NinjaTrader 8\bin\Custom"

copy "$source\NetMQ.4.0.2.2\lib\net472\NetMQ.dll" $dest
copy "$source\AsyncIO.0.1.69\lib\netstandard2.0\AsyncIO.dll" $dest
copy "$source\System.Memory.4.5.3\lib\netstandard2.0\System.Memory.dll" $dest
copy "$source\System.Runtime.CompilerServices.Unsafe.6.1.2\lib\net462\System.Runtime.CompilerServices.Unsafe.dll" $dest
copy "$source\Microsoft.Bcl.AsyncInterfaces.9.0.6\lib\net462\Microsoft.Bcl.AsyncInterfaces.dll" $dest
copy "$source\System.Threading.Tasks.Extensions.4.6.3\lib\net462\System.Threading.Tasks.Extensions.dll" $dest
copy "$source\NETStandard.Library.2.0.3\build\netstandard2.0\ref\netstandard.dll" $dest
```

---

## Step 5: Add References in NinjaScript

1. Open **NinjaTrader 8**
2. Go to **Tools** → **Edit NinjaScript**
3. In the NinjaScript Editor, right-click on **References**
4. Click **Add...**
5. Add these references from `Documents\NinjaTrader 8\bin\Custom\`:
   - ✅ `NetMQ.dll`
   - ✅ `Newtonsoft.Json.dll` (if not already added)
   - ✅ `netstandard.dll` **← CRITICAL!**

6. Click **OK**

---

## Step 6: Code Migration (NetMQ v3 → v4)

NetMQ v4 removed `NetMQContext`. Update your code as follows:

### v3 API (OLD - Don't use):
```csharp
// OLD - NetMQ v3
using (var context = NetMQContext.Create())
using (var socket = context.CreatePublisherSocket())
{
    socket.Connect("tcp://localhost:5555");
}
```

### v4 API (NEW - Use this):
```csharp
// NEW - NetMQ v4
using (var socket = new PublisherSocket())
{
    socket.Connect("tcp://localhost:5555");
}
```

### Changes Required:

| v3 (Old) | v4 (New) |
|----------|----------|
| `NetMQContext.Create()` | ❌ Remove - no longer needed |
| `context.CreatePublisherSocket()` | `new PublisherSocket()` |
| `context.CreatePullSocket()` | `new PullSocket()` |
| `context.CreateResponseSocket()` | `new ResponseSocket()` |
| `context.CreateRequestSocket()` | `new RequestSocket()` |
| `context.CreateSubscriberSocket()` | `new SubscriberSocket()` |

### Example Class Update:

**Before (v3):**
```csharp
internal class ZmqNetwork : IDisposable
{
    private NetMQContext _context;
    private PublisherSocket _marketPub;
    
    public void Start()
    {
        _context = NetMQContext.Create();
        _marketPub = _context.CreatePublisherSocket();
        _marketPub.Connect("tcp://127.0.0.1:5555");
    }
    
    public void Dispose()
    {
        _marketPub?.Dispose();
        _context?.Dispose();
    }
}
```

**After (v4):**
```csharp
internal class ZmqNetwork : IDisposable
{
    private PublisherSocket _marketPub;
    
    public void Start()
    {
        _marketPub = new PublisherSocket();
        _marketPub.Connect("tcp://127.0.0.1:5555");
    }
    
    public void Dispose()
    {
        _marketPub?.Dispose();
    }
}
```

---

## Step 7: Handling Menu API Changes

NinjaTrader 8's `ControlCenter.MainMenu` is an `ObservableCollection<object>`, not an `ItemsControl`.

### Correct Menu Finding Code:

```csharp
protected override void OnWindowCreated(Window window)
{
    if (!(window is ControlCenter cc)) return;
    
    // Find the New menu - cc.MainMenu is ObservableCollection<object>
    _existingNewMenu = null;
    if (cc.MainMenu != null)
    {
        foreach (var item in cc.MainMenu)
        {
            if (item is MenuItem mi)
            {
                if (mi.Header?.ToString() == "New")
                {
                    _existingNewMenu = mi;
                    break;
                }
                // Check submenus recursively
                _existingNewMenu = FindMenuItem(mi, "New");
                if (_existingNewMenu != null) break;
            }
        }
    }
    if (_existingNewMenu == null) return;
    
    _menuItem = new MenuItem { Header = "TradingBot ZMQ Connector" };
    _menuItem.Click += OnMenuItemClick;
    _existingNewMenu.Items.Add(_menuItem);
}

private MenuItem FindMenuItem(MenuItem parent, string header)
{
    if (parent.Header?.ToString() == header)
        return parent;
    
    foreach (var item in parent.Items)
    {
        if (item is MenuItem mi)
        {
            if (mi.Header?.ToString() == header)
                return mi;
            var found = FindMenuItem(mi, header);
            if (found != null) return found;
        }
    }
    return null;
}
```

---

## Step 8: Compile

1. In NinjaScript Editor, press **F5** or click **Compile**
2. If you get errors about missing references, make sure you added `netstandard.dll`
3. If you get `NetMQContext` errors, check that you removed all v3 API calls

---

## Troubleshooting

### Error: "The type 'NetMQContext' could not be found"
**Cause:** Code still uses NetMQ v3 API  
**Fix:** Remove `NetMQContext.Create()` and `context.CreateXxxSocket()` calls, use `new XxxSocket()` instead

### Error: "type defined in an assembly that is not referenced" (netstandard)
**Cause:** Missing `netstandard.dll` reference  
**Fix:** Add reference to `netstandard.dll` from `bin\Custom\`

### Error: "cannot convert from 'ObservableCollection<object>' to 'ItemsControl'"
**Cause:** Trying to cast `cc.MainMenu` to wrong type  
**Fix:** Use `foreach (var item in cc.MainMenu)` directly

### Error: "Input/output error" when copying DLLs
**Cause:** NinjaTrader is running and locking files  
**Fix:** Close NinjaTrader before copying DLLs

---

## File Locations Summary

### Downloaded Tools:
- **nuget.exe:** `C:\Users\dark_\Tools\nuget.exe`
- **Package cache:** `C:\Users\dark_\Tools\`

### NinjaTrader Custom Folder:
- **Path:** `C:\Users\dark_\Documents\NinjaTrader 8\bin\Custom\`
- **AddOns:** `C:\Users\dark_\Documents\NinjaTrader 8\bin\Custom\AddOns\`

### Your AddOn Code:
- **Symlink:** `C:\Users\dark_\Documents\NinjaTrader 8\bin\Custom\AddOns\TradingBotZMQ`
- **Actual:** `C:\Users\dark_\Developer\TradingBot\zmq_connectors\ninjatrader\`

---

## Quick Reference Commands

### Install NetMQ:
```powershell
cd C:\Users\dark_\Tools
.\nuget.exe install NetMQ -OutputDirectory .
.\nuget.exe install NETStandard.Library -Version 2.0.3 -OutputDirectory .
```

### Copy DLLs:
```powershell
$src = "C:\Users\dark_\Tools"
$dst = "C:\Users\dark_\Documents\NinjaTrader 8\bin\Custom"
copy "$src\NetMQ.4.0.2.2\lib\net472\NetMQ.dll" $dst
copy "$src\AsyncIO.0.1.69\lib\netstandard2.0\AsyncIO.dll" $dst
copy "$src\System.Memory.4.5.3\lib\netstandard2.0\System.Memory.dll" $dst
copy "$src\System.Runtime.CompilerServices.Unsafe.6.1.2\lib\net462\System.Runtime.CompilerServices.Unsafe.dll" $dst
copy "$src\Microsoft.Bcl.AsyncInterfaces.9.0.6\lib\net462\Microsoft.Bcl.AsyncInterfaces.dll" $dst
copy "$src\System.Threading.Tasks.Extensions.4.6.3\lib\net462\System.Threading.Tasks.Extensions.dll" $dst
copy "$src\NETStandard.Library.2.0.3\build\netstandard2.0\ref\netstandard.dll" $dst
```

---

## Version Information

- **NinjaTrader:** 8.1.x (.NET Framework 4.8)
- **NetMQ:** 4.0.2.2
- **AsyncIO:** 0.1.69
- **Target Framework:** net472 / netstandard2.0 compatible

---

## See Also

- [NetMQ Documentation](https://netmq.readthedocs.io/)
- [NetMQ GitHub](https://github.com/zeromq/netmq)
- [ZeroMQ Guide](https://zguide.zeromq.org/)
