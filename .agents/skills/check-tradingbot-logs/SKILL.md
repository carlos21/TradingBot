---
name: check-tradingbot-logs
description: "Check TradingBot logs and trade history for a specific date. Use when the user asks to review a trading day, check P&L, find errors, or summarize what happened on a particular date. Triggers: check the logs for DATE, what happened on DATE, P&L for DATE, review trades for DATE, any errors on DATE."
---

# Check TradingBot Logs for a Specific Date

When the user asks to check logs for a date, gather the date and produce a structured summary covering:

1. Python application logs for that date.
2. NinjaTrader launch logs for that date (if any).
3. Errors, warnings, or anything unusual.
4. Trade summary from the database (P&L, contracts, SL/TP, result type).
5. Brief narrative of what happened.

---

## 1. Identify the date

The user may say "today", "yesterday", or give a specific date. Convert it to `YYYY-MM-DD`.

For today:
```bash
date +%Y-%m-%d
```

For yesterday:
```bash
date -d "yesterday" +%Y-%m-%d
```

Store this as `DATE`.

---

## 2. Locate the log files

### Python application logs

Daily files are named `app_YYYY-MM-DD.log` and live under `logs/`, `logs/ninja/`, or `logs/meta/` depending on the instance.

```bash
find logs -name "app_${DATE}.log" -type f
```

Read the active one(s). The deepest path (e.g., `logs/ninja/app_YYYY-MM-DD.log`) is usually the live instance.

### NinjaTrader launch logs

```bash
ls logs/nt_launch/ | grep "${DATE//-/}"
```

(Replace `-` with empty string because NT launch logs use `YYYYMMDD_HHMMSS`.)

---

## 3. Search for problems

Run these greps against the Python log file. Replace `LOG_FILE` with the actual path.

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

---

## 4. Pull the trade summary from the database

The `ninja.db` SQLite database contains the authoritative trade records.

### Total P&L and trade count
```bash
python3 -c "
import sqlite3, datetime
conn = sqlite3.connect('ninja.db')
c = conn.cursor()
date = '${DATE}'
c.execute('''
    SELECT COUNT(*),
           SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END),
           SUM(CASE WHEN pnl_usd < 0 THEN 1 ELSE 0 END),
           SUM(CASE WHEN pnl_usd = 0 THEN 1 ELSE 0 END),
           SUM(pnl_usd)
    FROM trades
    WHERE date(entry_time) = ?
''', (date,))
print(c.fetchone())
"
```

### Per-trade details
```bash
python3 -c "
import sqlite3, json
conn = sqlite3.connect('ninja.db')
c = conn.cursor()
date = '${DATE}'
c.execute('''
    SELECT trade_id, pair, trade_type, entry_price, stop_loss, take_profit,
           risk, risk_dollars, contracts, pnl_usd, result, result_type,
           entry_time, exit_time
    FROM trades
    WHERE date(entry_time) = ?
    ORDER BY entry_time
''', (date,))
for row in c.fetchall():
    print(row)
"
```

### Key fields
- `pair`: instrument, e.g. `MNQ`.
- `trade_type`: `long` or `short`.
- `entry_price`, `stop_loss`, `take_profit`: prices.
- `risk`: stop distance in points.
- `risk_dollars`: dollar amount risked on the trade.
- `contracts`: number of contracts traded.
- `pnl_usd`: dollar profit/loss (excluding fees unless fees are already deducted).
- `result`: R multiple, e.g. `5.00` or `-1.00`.
- `result_type`: `TP`, `SL`, `BE` (breakeven), or `MANUAL`.
- `entry_time`, `exit_time`: trade open/close timestamps.

---

## 5. Build the summary

Present the findings in this order:

### Header
- Date reviewed.
- Log file path(s) checked.
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

### Session Narrative
Briefly describe the day:
- When the bot started / connected.
- When it became READY / LIVE.
- How many trades were taken.
- Whether it was a winning/losing day.
- Any notable events (data gaps, rejected orders, late starts).

---

## 6. Useful log line reference

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
| `[BrokerFillHandler] ENTRY FILL: ... SL=... TP=...` | Broker filled an entry order. |
| `[TradeCloseUseCase] Closed ... (Result: XR, Type: TP/SL)` | A trade closed with result type and R multiple. |
| `[BrokerFill] Exit fill ... broker_pnl=400.0` | Broker-reported dollar P&L for the closed trade. |
| `PLATFORM ERROR from ninjatrader: [order_state] Order ... is Rejected` | NinjaTrader rejected an order; investigate further. |

---

## 7. Example user prompts

- "Check the logs for 2026-06-29"
- "What happened yesterday?"
- "Did we win or lose today?"
- "Any errors on 2026-06-28?"

For any of these, follow steps 1–5 above.
