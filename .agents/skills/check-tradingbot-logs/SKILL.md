---
name: check-tradingbot-logs
description: "Check TradingBot logs and trade history for a specific date. Use when the user asks to review a trading day, check P&L, find errors, or summarize what happened on a particular date. Triggers: check the logs for DATE, what happened on DATE, P&L for DATE, review trades for DATE, any errors on DATE."
---

# Check TradingBot Logs for a Specific Date

When the user asks to check logs for a date, gather the date and produce a structured summary covering:

1. TradingBot Python application logs for that date.
2. NinjaTrader platform logs for that date (trace + log files).
3. Errors, warnings, or anything unusual.
4. Trade summary from the database (P&L, contracts, SL/TP, result type).
5. Brief narrative of what happened.

---

## 0. Resolve fixed paths

The skill must work no matter which directory the agent starts in. Resolve these once:

```bash
PROJECT_DIR="/mnt/c/Users/dark_/Developer/TradingBot"
NT_DIR="/mnt/c/Users/dark_/Documents/NinjaTrader 8"
```

If either directory does not exist, warn the user and stop.

---

## 1. Identify the date

The user may say "today", "yesterday", or give a specific date. Convert it to `YYYY-MM-DD`.

For today:
```bash
DATE="$(date +%Y-%m-%d)"
```

For yesterday:
```bash
DATE="$(date -d "yesterday" +%Y-%m-%d)"
```

---

## 2. Diagnostic preamble

Show the search scope so the user can verify it:

```bash
echo "Project dir: ${PROJECT_DIR}"
echo "NinjaTrader dir: ${NT_DIR}"
echo "Date: ${DATE}"
echo ""
echo "Available TradingBot log dirs:"
find "${PROJECT_DIR}/logs" -maxdepth 1 -type d 2>/dev/null | sort
echo ""
echo "Most recent TradingBot app logs:"
find "${PROJECT_DIR}/logs" -maxdepth 2 -name 'app_*.log' -type f 2>/dev/null | sort | tail -n 10
echo ""
echo "Most recent NinjaTrader trace files:"
find "${NT_DIR}/trace" -name 'trace.*.txt' -type f 2>/dev/null | sort | tail -n 10
echo ""
echo "Most recent NinjaTrader log files:"
find "${NT_DIR}/log" -name 'log.*.txt' -type f 2>/dev/null | sort | tail -n 10
```

---

## 3. Locate the log files

### TradingBot Python application logs

Daily files are named `app_YYYY-MM-DD.log` and live under `${PROJECT_DIR}/logs/<instance>/` (e.g., `logs/ninja/` for the live NinjaTrader instance, `logs/meta/` for MetaTrader).

```bash
find "${PROJECT_DIR}/logs" -maxdepth 2 -name "app_${DATE}.log" -type f
```

Read the active one(s). The `logs/ninja/` path is usually the live NinjaTrader instance.

If no exact-date file is found, fall back to the most recent files:

```bash
find "${PROJECT_DIR}/logs" -maxdepth 2 -name 'app_*.log' -type f 2>/dev/null | sort | tail -n 5
```

### NinjaTrader platform trace files

NinjaTrader's own internal traces live in `${NT_DIR}/trace/` and are named `trace.YYYYMMDD.XXXXX.txt`.

```bash
find "${NT_DIR}/trace" -name "trace.${DATE//-/}.*.txt" -type f 2>/dev/null | sort
```

If none are found, fall back to the most recent trace files:

```bash
find "${NT_DIR}/trace" -name 'trace.*.txt' -type f 2>/dev/null | sort | tail -n 5
```

### NinjaTrader platform log files

NinjaTrader's own logs live in `${NT_DIR}/log/` and are named `log.YYYYMMDD.XXXXX.txt` (and `log.YYYYMMDD.XXXXX.en.txt`).

```bash
find "${NT_DIR}/log" -name "log.${DATE//-/}.*.txt" -type f 2>/dev/null | sort
```

If none are found, fall back to the most recent log files:

```bash
find "${NT_DIR}/log" -name 'log.*.txt' -type f 2>/dev/null | sort | tail -n 5
```

### Legacy launch logs (informational only)

Older code wrote launch logs to `${PROJECT_DIR}/logs/nt_launch/`, but current launchers do not. Check as a best-effort note, not an error:

```bash
ls "${PROJECT_DIR}/logs/nt_launch/" 2>/dev/null | grep "${DATE//-/}" || echo "No nt_launch log for ${DATE} (current launchers do not write here)."
```

---

## 4. Search for problems

Run these greps against the TradingBot Python log file. Replace `LOG_FILE` with the actual path.

### Errors and critical messages
```bash
grep -iE "ERROR|CRITICAL|FATAL|EXCEPTION" "${LOG_FILE}" | tail -n 50
```

### Warnings and timeouts
```bash
grep -iE "WARNING|timeout|disconnect|rejected|cancelled|failed" "${LOG_FILE}" | tail -n 50
```

### Connection / platform state
```bash
grep -iE "platform_connected|platform_disconnected|gateway_started|gateway_stopped|ZMQDataSource" "${LOG_FILE}" | tail -n 30
```

### History and warmup state
```bash
grep -iE "WARMING_UP|READY|LIVE|History complete|History not ready|REFRESHING|Replay progress" "${LOG_FILE}" | tail -n 30
```

### Trade fills (from log)
```bash
grep -iE "ENTRY FILL|Exit fill|TradeClose|broker_pnl" "${LOG_FILE}"
```

### Entry latency / slippage
```bash
grep -iE "\[LATENCY\] entry" "${LOG_FILE}"
```

Shows, per entry: queue→wire, wire→ACK, ACK→fill, total time, signal price vs fill price, and slippage in points. The big segment is `ack->fill` — that is the broker/NT round trip.

### Command ACK timing
```bash
grep -iE "Command ACK" "${LOG_FILE}"
```

ACK lines show two segments: `py=Xms` (Python queue → actual socket write) and `nt=Yms` (socket write → ACK received, includes NT handler + broker submit).

---

## 5. Pull the trade summary from the database

The live instance stores trades in **PostgreSQL on the Windows host** — `DATABASE_URL` in `${PROJECT_DIR}/.env` points to `localhost:5433` (a Postgres that only exists on Windows). **It is NOT reachable from WSL** (localhost:5433 is refused there, and the Windows host IP times out). Do NOT query the Postgres on WSL port 5432 — that is a stale dev database whose trades end weeks earlier; querying it produces misleading "no trades" results.

### Run the report script on the Windows host via PowerShell interop

The project ships `backend/scripts/check_trading_day.py`, which reads `DATABASE_URL` from `.env` (falling back to a local SQLite file). Run it through Windows interop so `localhost` resolves on Windows:

```bash
/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Set-Location 'C:\Users\dark_\Developer\TradingBot'; poetry run python backend/scripts/check_trading_day.py --date ${DATE}"
```

This prints a full Markdown day report: totals (winners/losers/breakeven, gross/commission/realized P&L) and a per-trade table (entry/slippage/exit/result/P&L), plus the errors/connection sections gathered from the app log.

Notes:
- If interop fails (poetry missing on Windows, connection error), the script exits non-zero with a hint. Fall back to reconstructing the trade summary from the log fills (`TradeManager ENTRY FILL`, `[TradeCloseUseCase] Closed`, `[BrokerFill] Exit fill broker_pnl=...`) and say so in the report.
- The script filters trades by the market timezone (`Etc/GMT+5`, same offset as `America/Lima`), so the date matches the log timestamps.
- WSL fallback for a local SQLite file: `poetry run python backend/scripts/check_trading_day.py --date ${DATE} --db <path-to.db>`

### Key fields
- `pair`: instrument, e.g. `MNQ`.
- `trade_type`: `long` or `short`.
- `entry_price`, `stop_loss`, `take_profit`: prices.
- `risk`: stop distance in points.
- `risk_dollars`: dollar amount risked on the trade.
- `contracts`: number of contracts traded.
- `pnl_usd` / `realized_pnl`: dollar profit/loss.
- `result`: R multiple, e.g. `5.00` or `-1.00`.
- `result_type`: `TP`, `SL`, `BE` (breakeven), `MANUAL`, or `CLOSE`.
- `entry_time`, `exit_time`: trade open/close timestamps (market timezone).
- `original_entry_price` vs `entry_price`: strategy-calculated entry vs real fill (their difference is the entry slippage).

---

## 6. Build the summary

Present the findings in this order:

### Header
- Date reviewed.
- Log file path(s) checked (TradingBot app log, NT trace, NT log).
- Overall status (e.g., "Trading active", "No trades", "Connection errors detected").

### Trade Summary
If trades exist:

| Metric | Value |
|---|---|
| Total trades | N |
| Winners | N |
| Losers | N |
| Breakeven | N |
| Gross P&L | $N |

Then a per-trade table:

| Time | Direction | Entry | SL | TP | Risk (pts) | Risk ($) | Contracts | Result | P&L |
|---|---|---|---|---|---|---|---|---|---|
| 09:18 | Long MNQ | 29333.50 | 29293.50 | 29533.50 | 40.00 | $80 | 1 | TP (+5.00R) | +$400 |
| 00:22 | Short MNQ | 29527.75 | 29547.75 | 29427.75 | 20.00 | $40 | 1 | SL (-1.00R) | -$40 |

Calculate SL distance in points from the log/db: `|entry_price - stop_loss|`.

### Errors / Anomalies
List any ERROR, CRITICAL, rejected/cancelled orders, or unusual disconnects with timestamps. If there are none, say so explicitly.

- A `PLATFORM ERROR ... Order Target_... is Rejected` that arrives **just after** a stop-loss fill is usually the expected OCO teardown (the connector should suppress these now). If it is not immediately followed/suppressed, note it as a residual race worth monitoring.
- Use the `[LATENCY] entry` lines to report per-trade slippage and the broker round-trip time (`ack->fill`).

### Session Narrative
Briefly describe the day:
- When the bot started / connected.
- When it became READY / LIVE.
- How many trades were taken.
- For each trade, include the `[LATENCY] entry` breakdown and the realized slippage.
- Whether it was a winning/losing day.
- Any notable events (data gaps, rejected orders, late starts).

---

## 7. Useful log line reference

| Log line | Meaning |
|---|---|
| `Queued REFRESH request: 2 days, instrument=MNQ 09-26` | Python asked NinjaTrader for N days of historical 1-minute bars. |
| `History complete: 780 bars cached` | NinjaTrader returned 780 bars. If far less than `days * 1440`, the market was likely closed or data was unavailable. |
| `History ends 134945s ago (2026-06-19 17:00:00 UTC)` | Last cached bar is old; large values mean stale history (e.g., weekend). |
| `History not ready: Last bar is 2249m old (need < 1m)` | Bot will not trade until fresh live bars arrive and close the gap. |
| `REFRESHING -> WARMING_UP` | History loaded; indicators are being warmed up by replaying bars. |
| `WARMING_UP -> READY` | Warmup complete; bot is ready to trade once live bars start. |
| `READY -> LIVE` | First live bar received; bot is actively evaluating signals. |
| `platform_connected` / `platform_disconnected` | Socket.IO events reflecting the ZMQ connection to NinjaTrader. |
| `✅ Command ACK: ... (0.06s \| py=12ms nt=48ms)` | Command acknowledged. `py=` is Python queue→socket; `nt=` is socket→ACK (NT handler + broker submit). |
| `[LATENCY] entry {trade_id} (short): queue->wire=5ms wire->ack=55ms ack->fill=1054ms total=1114ms \| signal=29377.25 fill=29362.0 slippage=-15.25pts` | Per-entry latency breakdown + realized slippage. `ack->fill` is the broker/NT round trip; large values explain slippage. |
| `[BrokerFillHandler] ENTRY FILL: ... SL=... TP=...` | Broker filled an entry order. |
| `[TradeCloseUseCase] Closed ... (Result: XR, Type: TP/SL)` | A trade closed with result type and R multiple. |
| `[BrokerFill] Exit fill ... broker_pnl=400.0` | Broker-reported dollar P&L for the closed trade. |
| `PLATFORM ERROR from ninjatrader: [order_state] Order ... is Rejected` | NinjaTrader rejected an order; investigate further. If `Target_`/`Stop_` and right after a fill, it's likely expected OCO teardown. |

---

## 8. Example user prompts

- "Check the logs for 2026-08-11"
- "What happened yesterday?"
- "Did we win or lose today?"
- "Any errors on 2026-08-10?"

For any of these, follow steps 1–6 above.
