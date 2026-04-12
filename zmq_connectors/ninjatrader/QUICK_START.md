# NetMQ Quick Start for NinjaTrader 8

## ⚡ One-Line Summary

Install NetMQ v4, copy 7 DLLs, add 2 references, update code to remove `NetMQContext`.

---

## 📋 Checklist

- [ ] Download `nuget.exe` to `C:\Users\dark_\Tools\`
- [ ] Run: `nuget install NetMQ -OutputDirectory .`
- [ ] Run: `nuget install NETStandard.Library -Version 2.0.3 -OutputDirectory .`
- [ ] Copy 7 DLLs to `Documents\NinjaTrader 8\bin\Custom\`
- [ ] Add references in NinjaScript: `NetMQ.dll` + `netstandard.dll`
- [ ] Update code: Replace `context.CreateXxxSocket()` with `new XxxSocket()`
- [ ] Compile (F5)

---

## 📁 Required DLLs (7 files)

| # | File | Source Path |
|---|------|-------------|
| 1 | `NetMQ.dll` | `NetMQ.4.0.2.2\lib\net472\` |
| 2 | `AsyncIO.dll` | `AsyncIO.0.1.69\lib\netstandard2.0\` |
| 3 | `System.Memory.dll` | `System.Memory.4.5.3\lib\netstandard2.0\` |
| 4 | `System.Runtime.CompilerServices.Unsafe.dll` | `System.Runtime.CompilerServices.Unsafe.6.1.2\lib\net462\` |
| 5 | `Microsoft.Bcl.AsyncInterfaces.dll` | `Microsoft.Bcl.AsyncInterfaces.9.0.6\lib\net462\` |
| 6 | `System.Threading.Tasks.Extensions.dll` | `System.Threading.Tasks.Extensions.4.6.3\lib\net462\` |
| 7 | `netstandard.dll` | `NETStandard.Library.2.0.3\build\netstandard2.0\ref\` |

**Destination:** `C:\Users\dark_\Documents\NinjaTrader 8\bin\Custom\`

---

## 🔧 Code Changes (v3 → v4)

### Remove These:
```csharp
// DELETE these lines
private NetMQContext _context;
_context = NetMQContext.Create();
_context.Dispose();
```

### Replace These:
```csharp
// OLD: _marketPub = _context.CreatePublisherSocket();
// NEW:
_marketPub = new PublisherSocket();

// OLD: _commandPull = _context.CreatePullSocket();
// NEW:
_commandPull = new PullSocket();

// OLD: _queryRep = _context.CreateResponseSocket();
// NEW:
_queryRep = new ResponseSocket();
```

---

## 🚨 Common Errors

| Error | Solution |
|-------|----------|
| `NetMQContext could not be found` | Remove `NetMQContext`, use `new XxxSocket()` |
| `netstandard not referenced` | Add `netstandard.dll` reference |
| `ObservableCollection to ItemsControl` | Iterate with `foreach (var item in cc.MainMenu)` |
| `Input/output error copying DLLs` | Close NinjaTrader first |

---

## 📂 Folder Structure

```
C:\Users\dark_\Tools\                           # Downloaded packages
├── nuget.exe
├── NetMQ.4.0.2.2\
├── AsyncIO.0.1.69\
├── System.Memory.4.5.3\
├── NETStandard.Library.2.0.3\
└── ...

C:\Users\dark_\Documents\NinjaTrader 8\bin\Custom\   # NinjaTrader DLLs
├── NetMQ.dll
├── AsyncIO.dll
├── System.Memory.dll
├── System.Runtime.CompilerServices.Unsafe.dll
├── Microsoft.Bcl.AsyncInterfaces.dll
├── System.Threading.Tasks.Extensions.dll
├── netstandard.dll
└── AddOns\
    └── TradingBotZMQ\      # ← Your symlink here
        └── TradingBotZmqConnector.cs
```

---

## 🔗 Quick Links

- Full Guide: `NETMQ_SETUP_GUIDE.md`
- Install Script: `Install-NetMQ.ps1`
- Your Code: `TradingBotZmqConnector.cs`

---

## 📞 Emergency Commands

```powershell
# Download everything
cd C:\Users\dark_\Tools
.\nuget.exe install NetMQ -OutputDirectory .
.\nuget.exe install NETStandard.Library -Version 2.0.3 -OutputDirectory .

# Copy all DLLs (PowerShell)
$src="C:\Users\dark_\Tools"; $dst="C:\Users\dark_\Documents\NinjaTrader 8\bin\Custom"
copy "$src\NetMQ.4.0.2.2\lib\net472\NetMQ.dll" $dst
copy "$src\AsyncIO.0.1.69\lib\netstandard2.0\AsyncIO.dll" $dst
copy "$src\System.Memory.4.5.3\lib\netstandard2.0\System.Memory.dll" $dst
copy "$src\System.Runtime.CompilerServices.Unsafe.6.1.2\lib\net462\System.Runtime.CompilerServices.Unsafe.dll" $dst
copy "$src\Microsoft.Bcl.AsyncInterfaces.9.0.6\lib\net462\Microsoft.Bcl.AsyncInterfaces.dll" $dst
copy "$src\System.Threading.Tasks.Extensions.4.6.3\lib\net462\System.Threading.Tasks.Extensions.dll" $dst
copy "$src\NETStandard.Library.2.0.3\build\netstandard2.0\ref\netstandard.dll" $dst
```
