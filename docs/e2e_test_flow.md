# E2E Test Flow Diagram

## Overview

```
+------------------+                              +------------------+
|   NinjaTrader    |                              |     Python       |
|   (C# AddOn)     |                              |   (Flask App)    |
+------------------+                              +------------------+
        |                                                  |
        |  "Run E2E Tests" button click                    |
        |  or POST /api/nt/run_e2e_test                    |
        |                                                  |
```

## Scenario: TP Hit

```
    NinjaTrader                                        Python
    ───────────                                        ──────
        │                                                │
        │         POST /api/nt/run_e2e_test              │
        │  ─────────────────────────────────────────>    │
        │         {"scenario": "tp_hit"}                 │
        │                                                │
        │                                    ┌───────────┤
        │                                    │ 1. Insert trade in DB
        │                                    │    (entry=21000, SL=20920, TP=21080)
        │                                    │ 2. Add to open_trades
        │                                    │ 3. Enqueue place_order
        │                                    │    (test=true, scenario=tp_hit)
        │                                    └───────────┤
        │                                                │
        │  ◄─── GET /api/nt/await_command ──────────     │
        │       (long-poll returns instantly)             │
        │       {command: "place_order", test: true,     │
        │        trade_id, entry=21000, SL=20920,        │
        │        TP=21080}                               │
        │                                                │
   ┌────┤  HandleTestCommand (no real orders)            │
   │    │                                                │
   │    │  POST /api/nt/trade_log ──────────────────>    │  log: NT:ORDER
   │    │  {"event": "NT:ORDER"}                         │
   │    │                                                │
   │    │  POST /api/nt/trade_log ──────────────────>    │  log: NT:FILL (entry)
   │    │  {"event": "NT:FILL"}                          │
   │    │                                                │
   │    │  POST /api/nt/entry_fill ─────────────────>    │
   │    │  {"trade_id", "entry_price": 21000}            │
   └────┤                                                │
        │                                    ┌───────────┤
        │                                    │ 4. handle_broker_entry_fill
        │                                    │    → log: NT_ENTRY_FILL
        │                                    │ 5. E2E hook: move SL → breakeven
        │                                    │    → update DB: SL = 21000
        │                                    │    → log: SL_UPDATE, CMD_SENT
        │                                    │ 6. Enqueue modify_order
        │                                    │    (SL=21000, scenario=tp_hit)
        │                                    └───────────┤
        │                                                │
        │  ◄─── GET /api/nt/await_command ──────────     │
        │       {command: "modify_order", test: true,    │
        │        stop_loss: 21000, scenario: "tp_hit",   │
        │        entry_price: 21000, tp: 21080}          │
        │                                                │
   ┌────┤  HandleTestCommand                             │
   │    │                                                │
   │    │  POST /api/nt/trade_log ──────────────────>    │  log: NT:MODIFY
   │    │  {"event": "NT:MODIFY"}                        │
   │    │                                                │
   │    │  (scenario=tp_hit → simulate TP exit)          │
   │    │                                                │
   │    │  POST /api/nt/trade_log ──────────────────>    │  log: NT:FILL (TP)
   │    │  {"event": "NT:FILL"}                          │
   │    │                                                │
   │    │  POST /api/nt/fill ───────────────────────>    │
   │    │  {"trade_id", "exit_price": 21080,             │
   │    │   "result_type": "TP"}                         │
   └────┤                                                │
        │                                    ┌───────────┤
        │                                    │ 7. handle_broker_fill
        │                                    │    → result = 1.00R
        │                                    │    → set exit_time, exit_price in DB
        │                                    │    → log: NT_FILL, CLOSE
        │                                    │ 8. Remove from open_trades
        │                                    │ 9. Clean up _test_sequences
        │                                    └───────────┤
        │                                                │
        │  GET /api/nt/test_result/{id} ────────────>    │
        │                                                │  Check:
        │  ◄────────────────────────────────────────     │  - trade.exit_time != None ✓
        │  {"passed": true,                              │  - NT:ORDER in logs ✓
        │   "trade_closed": true,                        │  - NT_ENTRY_FILL in logs ✓
        │   "events_found": [...],                       │  - NT:MODIFY in logs ✓
        │   "formatted_logs": "..."}                     │  - No ERROR events ✓
        │                                                │
        ▼                                                ▼
   "tp_hit: PASSED"                              Trade persisted in DB
                                                 with full lifecycle logs
```

## Scenario Differences

```
                    TP Hit              SL Hit              Session End
                    ──────              ──────              ───────────
Entry price:        21000               21000               21000
SL after breakeven: 21000               21000               21000
TP:                 21080               21080               21080

After modify_order:
                    NT simulates        NT simulates        Python enqueues
                    TP fill @ 21080     SL fill @ 21000     close_order
                                                            │
                                                            ▼
                                                        NT receives close_order
                                                        NT simulates
                                                        CLOSE fill @ 21005

Result:             +1.00R (profit)     0.00R (breakeven)   +0.06R (small profit)
Result type:        TP                  SL                  CLOSE
```

## Command Routing

```
                    ┌─────────────────────────────┐
                    │     NT Command Loop          │
                    │  (GET /api/nt/await_command)  │
                    └─────────────┬───────────────┘
                                  │
                    ┌─────────────┴───────────────┐
                    │  body contains "test"?       │
                    └──────┬──────────────┬───────┘
                           │              │
                      YES  │              │  NO
                           ▼              ▼
                  ┌────────────┐  ┌──────────────────┐
                  │ HandleTest │  │ HandlePlaceOrder  │
                  │ Command    │  │ HandleModifyOrder │
                  │            │  │ HandleCloseOrder  │
                  │ Simulates  │  │                   │
                  │ fills via  │  │ Real NinjaTrader  │
                  │ HTTP POST  │  │ orders via        │
                  │ (no real   │  │ Account.Create    │
                  │  orders)   │  │ Account.Change    │
                  └────────────┘  └──────────────────┘
```

## Trigger Points

```
From NinjaTrader UI:
  [Test Connection]  →  POST /api/nt/test_connection  →  ping/pong (200 OK)
  [Run E2E Tests]    →  Runs tp_hit, sl_hit, session_end sequentially
                        Each: POST /api/nt/run_e2e_test → poll test_result

From shell script (deprecated):
  Option 1: Run all  →  Same as above via curl (deprecated script removed)
  Option 2: Single   →  Pick one scenario
  Option 3: Check    →  GET /api/nt/test_result/{trade_id}
  Option 4: Ping     →  POST /api/nt/test_connection
```
