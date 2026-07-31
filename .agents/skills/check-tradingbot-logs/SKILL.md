---
name: check-tradingbot-logs
description: "Check TradingBot logs and trade history for a specific date. Use when the user asks to review a trading day, check P&L, find errors, or summarize what happened on a particular date. Triggers: check the logs for DATE, what happened on DATE, P&L for DATE, review trades for DATE, any errors on DATE."
---

# Check TradingBot Logs for a Specific Date

Run the dedicated report script for the requested date. The script reads the live SQLite database and application logs, then prints a concise Markdown report including slippage, gross/realized PnL, and exact commission.

## Usage

```bash
python3 backend/scripts/check_trading_day.py --date YYYY-MM-DD
```

- Use `today` or `yesterday` by converting to `YYYY-MM-DD` first.
- The script defaults to `ninja.db` (live NinjaTrader instance) and falls back to `database.db`.
- Log files are read from `logs/`, `logs/ninja/`, and `logs/meta/`.

## What the report includes

1. **Day Summary** — total trades, winners, losers, breakeven, gross PnL, commission, realized PnL.
2. **Per-trade table** — calculated entry, real entry (with slippage), exit, result type, gross PnL, commission, realized PnL.
3. **Errors / Anomalies** — ERROR, CRITICAL, rejected/cancelled orders, timeouts, disconnects.
4. **Connection / Platform State** — platform_connected, platform_disconnected, gateway events.
5. **Session Narrative** — brief plain-language summary of the day.

## Example prompts

- "Check the logs for 2026-06-29"
- "What happened yesterday?"
- "Did we win or lose today?"
- "Any errors on 2026-06-28?"

For any of these, run the script with the resolved date and show the resulting Markdown report to the user.
