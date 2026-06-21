---
name: check-tradingbot-logs
description: "Check Python application logs and NinjaTrader-related logs for the TradingBot project. Use when the user asks about log entries, connection issues, history/warmup problems, errors, or why the bot is not trading. Triggers: check the logs, what does this log mean, why is it waiting, why no bars, connection error, warmup issue."
---

# Check TradingBot Logs

This skill tells you where the logs are and what to grep for when diagnosing TradingBot issues.

## 1. Log Locations

### Python application logs (live mode)

The Python side writes one log file per calendar day using `FileAndConsoleLogger`. The directory depends on how the instance was started:

| Instance | Log directory | How it is selected |
|----------|---------------|--------------------|
| Default / single | `logs/` | Default `--log-dir logs` |
| NinjaTrader | `logs/ninja/` | `start_ninjatrader.sh` sets `LOG_DIR=logs/ninja` |
| MetaTrader | `logs/meta/` | `start_metatrader.sh` sets `LOG_DIR=logs/meta` |

Daily file name: `app_YYYY-MM-DD.log`

Today's active file: `app_$(date +%Y-%m-%d).log`

Backtest mode uses `ConsoleLogger` (stdout only) unless a custom file logger was injected.

### NinjaTrader launch logs

- Directory: `logs/nt_launch/`
- File pattern: `nt_launch_YYYYMMDD_HHMMSS.log`
- Content: PowerShell auto-login / launcher output. Use these when NinjaTrader fails to start or login fails.

### NinjaTrader ZMQ connector logs

- Managed by the C# connector (`zmq_connectors/ninjatrader/`).
- Log directory is configured in `TradingBotZmqConfig.json` under `logDirectory`.
- Separate from Python logs; usually only needed for low-level ZMQ/connector debugging.

## 2. Find the Active Log File

Run this to locate today's Python log:

```bash
find logs -name "app_$(date +%Y-%m-%d).log" -type f
```

The deepest match (e.g., `logs/ninja/app_YYYY-MM-DD.log`) is usually the active instance.

To see which file is still being written:

```bash
find logs -name "app_$(date +%Y-%m-%d).log" -mmin -5 -type f
```

## 3. Essential Grep Patterns

Replace `logs/ninja/app_YYYY-MM-DD.log` with the actual path found above.

### Connection / platform state

```bash
grep -iE "platform_connected|platform_disconnected|gateway_started|gateway_stopped|connection" logs/ninja/app_YYYY-MM-DD.log
```

### History refresh and bar counts

```bash
grep -iE "REFRESH request|history_batch|History complete|BarsRequest returned|Trimmed|HISTORY SCAN|GAP DETECTED" logs/ninja/app_YYYY-MM-DD.log
```

### Warmup / readiness

```bash
grep -iE "WARMING_UP|READY|Warmup|Replay progress|MinimumBarsWarmup|History not ready" logs/ninja/app_YYYY-MM-DD.log
```

### Errors and warnings

```bash
grep -iE "ERROR|CRITICAL|failed|exception|timeout|disconnect" logs/ninja/app_YYYY-MM-DD.log | tail -n 50
```

### Trades

```bash
grep -iE "TRADE_OPEN|TRADE_CLOSED|ENTRY|EXIT|BrokerFill|order" logs/ninja/app_YYYY-MM-DD.log | tail -n 50
```

## 4. How to Interpret Common Log Lines

| Log line | Meaning |
|----------|---------|
| `Queued REFRESH request: 2 days, instrument=MNQ 09-26` | Python asked NinjaTrader for N days of historical 1-minute bars. |
| `History complete: 780 bars cached` | NinjaTrader returned 780 bars. If far less than `days * 1440`, the market was likely closed or data was unavailable. |
| `History ends 134945s ago (2026-06-19 17:00:00 UTC)` | Last cached bar is old; large values mean stale history (e.g., weekend). |
| `History not ready: Last bar is 2249m old (need < 1m)` | Bot will not trade until fresh live bars arrive and close the gap. |
| `REFRESHING -> WARMING_UP` | History loaded; indicators are being warmed up by replaying bars. |
| `WARMING_UP -> READY` | Warmup complete; bot is ready to trade once live bars start. |
| `READY -> LIVE` | First live bar received; bot is actively evaluating signals. |
| `platform_connected` / `platform_disconnected` | Socket.IO events reflecting the ZMQ connection to NinjaTrader. |
| `[Warmup] Replay progress: 780/780 bars` | Warmup replay finished; `780` is the total bar count from the history load. |
| `RECV: history_batch seq=... pair=MNQ \| bars=500` | Batch of historical bars arriving from NinjaTrader. |
| `RECV: history_end seq=...` | End-of-history marker; no more historical bars for this refresh. |

## 5. Diagnostic Workflow

When the user asks to check logs:

1. **Find the active log file** with `find logs -name "app_$(date +%Y-%m-%d).log" -type f`.
2. **Identify the symptom category**: connection, history, warmup, errors, trades.
3. **Run the matching grep pattern** from section 3.
4. **Look for the lines in section 4** to determine root cause.
5. **Check `logs/nt_launch/` only if NinjaTrader itself failed to start or login failed.**
6. **Summarize findings** with timestamps, bar counts, and the last cached bar time when relevant.
