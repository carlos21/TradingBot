# MQL5 Connector — Unit-Test Coverage Checklist

Scope: every `.mqh` component under `Domain/`, `Application/`, `Commands/`,
`Infrastructure/`. Suites live in `Tests/Suites/`, run by
`Tests/TestRunnerEA.mq5` via `bin/run_mql_tests.sh`.

Legend: ✅ covered by named suite · ⛔ excluded (see Exclusions section).

## Domain/

### Contracts.mqh
Pure interface definitions (ILogger, IMessageSerializer, IRateLimiter,
ICommandHandler, ICommandDispatcher, IOrderTracker, IHistoryProvider,
IZmqNetwork) — no executable logic. Every method is exercised through the
fakes in `Tests/Fakes/` and the SUTs that consume the interfaces.

### MessageTypes.mqh
Protocol string constants — asserted as expected msg_type values in every
handler/streamer/provider suite.

### PlatformApi.mqh
Interface definitions + `TradeResult` struct + `PlatformApis` holder — no
executable logic. Exercised via `MakeFakePlatformApis(...)` in
`Tests/Fakes/Fakes.mqh`.

### TickRateLimiter.mqh
- `TryAllow()` / `Reset()` — TestTickRateLimiter (N-per-second window,
  boundary reset via FakeTimeApi); also exercised indirectly by
  TestMarketStreamer (tick limiter + 1/sec partial limiter).

### ValueObjects.mqh
- `ZmqConfiguration` defaults — TestConfigLoader (defaults on missing keys).
- `MessageEnvelope.MsgType/Timestamp/SeqNum/PayloadString/PayloadDouble/
  PayloadInt/PayloadBool/PayloadHasKey` — TestMessageEnvelope (accessors,
  defaults, type coercion).

### AccountValidator.mqh
- `Validate(...)` — TestAccountValidator (accepts only the terminal login,
  via FakeAccountApi).

## Application/

### BrokerSync.mqh
- `RestoreFromBroker()` — TestBrokerSync (magic filter, comment→trade_id,
  `mt5_<ticket>` fallback, empty-broker silence).
- `ReportPositionsToPython()` — TestBrokerSync (tracked/untracked split,
  per-position fields trade_id/direction/entry_price/SL/TP/quantity/account,
  `source`/`is_source_of_truth`, empty report still sends).
- `ProcessNewDeals()` — TestBrokerSync (entry fill with SL/TP from linked
  order, exit fill SL/TP/CLOSE reason mapping, pnl/commission/exit_time,
  tracker cleanup, foreign-magic skip, processed-ticket dedupe, FIFO
  bound at 1000 tracked deals).

### CommandDispatcher.mqh
- `Register()` / `Dispatch()` — TestCommandDispatcher (first-claim routing,
  result propagation, unknown type, NULL envelope).

### ConnectionWatchdog.mqh
- `OnTimerTick()` — TestConnectionWatchdog (5s ping cadence + counter reset,
  3-failure threshold, recovery = Restart + connect resend + BrokerSync
  resync + subscription re-select, success resets failures, backoff to
  300s after 3 failed cycles, recovery success restores 5s, restart
  failure aborts recovery).

### E2ETestRunner.mqh
- `RunAllScenarios()` — TestE2ETestRunner (3 basic + 4 feature scenarios
  orchestrated as test_start messages, multi_account skip with the
  single-account/login-validated reason, 7/7 completion summary).
- `ValidateSimulationEnvironment()` — TestE2ETestRunner (demo path runs all
  scenarios; non-demo account is refused with a safety-block log, via the
  injected `IAccountApi` seam).

### HistoryProvider.mqh
- `SendHistory()` — TestHistoryProvider (protocol order, batch chunking,
  bar JSON fields, days resolution/override, symbol override, empty and
  partial history, NULL network).

### MarketStreamer.mqh
- `StreamAll()` — TestMarketStreamer (tick for subscribed symbol, time_msc
  dedupe across OnTick/OnTimer, rate limiting, completed bar once on bar
  advance, no bar on first observation, 1/sec partial bars, multi-symbol
  independence, NULL guards).
- `TicksSent()` / `BarsSent()` / `PartialBarsSent()` — TestMarketStreamer
  (stats asserted alongside every scenario).

### SubscriptionManager.mqh
- `Add/Remove/Clear/Contains/Count/Symbol/TickLimiter/PartialLimiter/
  LastBarTime/SetLastBarTime/LastTickMsc/SetLastTickMsc` —
  TestSubscriptionManager; Add-driven symbol re-selection also asserted in
  TestConnectionWatchdog (FakeSymbolApi.SelectCount).

### ZmqNetwork.mqh — ⛔ excluded (transport internals; see Exclusions)

## Commands/

### DisconnectHandler.mqh
- `CanHandle()` / `Handle()` — TestSubscribeUnsubscribeDisconnect.

### OrderCloseHandler.mqh
- `CanHandle()` / `Handle()` — TestOrderCloseHandler (validation matrix,
  simulate fills, live close via tracker/comment fallback, cleanup paths,
  broker failure paths).

### OrderModifyHandler.mqh
- `CanHandle()` / `Handle()` — TestOrderModifyHandler (validation, simulate,
  SL/TP modify via tracked ticket / comment fallback, keep-current
  semantics, failure paths).

### OrderOpenHandler.mqh
- `CanHandle()` / `Handle()` — TestOrderOpenHandler (validation matrix,
  simulate fills, live market entry with dynamic lot sizing, duplicate
  guard, broker paths, `g_e2eTestRunning` simulate override).

### RefreshRequestHandler.mqh
- `CanHandle()` / `Handle()` — TestRefreshRequestHandler (instrument
  validation, history-provider invocation with payload/default days).

### SubscribeHandler.mqh / UnsubscribeHandler.mqh
- `CanHandle()` / `Handle()` — TestSubscribeUnsubscribeDisconnect.

### TestStartHandler.mqh
- `CanHandle()` / `Handle()` — TestE2ETestRunner (NULL/rootless envelope,
  scenario registry: command_ack, duplicate_detection, position_sync,
  order_modify, tp_hit, sl_hit, session_end dispatched with expected
  fill/log/result traffic; multi_account skip result; unknown scenario →
  false + failure test_result; simulate flag set).

## Infrastructure/

### ConfigLoader.mqh
- `Parse(json, sourceName)` — TestConfigLoader (full JSON, defaults,
  string-fallback on malformed JSON, garbage/array input, bool/int
  coercion).
- `ExtractStringValue/ExtractIntValue/ExtractBoolValue` — exercised
  indirectly via the malformed-JSON fallback blocks above.
- `Load(filename)` / `ReadFileAsString(filepath)` — ⛔ file I/O against
  the terminal sandbox (see Exclusions); thin wrapper feeding `Parse`.

### Logger.mqh — ⛔ excluded (see Exclusions)
`MetaTraderLogger` is a one-line `Print()` adapter per level plus a chart
`Comment()` panel (`UpdatePanel`) and UI callback — no branching logic;
the `ILogger` contract itself is asserted everywhere via FakeLogger.

### MqlPlatformApi.mqh — ⛔ excluded (see Exclusions)
Identity passthroughs to the MQL5 terminal API (each method is a single
`*Info*`/history call); needs a live terminal and carries no logic beyond
the seam it implements. `Create()`/`Destroy()` are EA wiring.

### OrderTracking.mqh
- `OrderStateManager`: `TrackEntry/TrackStopLoss/TrackTakeProfit/
  TrackCloseOrder/TryGet*/TryGetTradeIdForTicket/RemoveTrade/
  SetPendingModify/TryGetPendingModify/RemovePendingModify/Clear/
  GetActiveCount/GetActiveTradeIds` — TestOrderTracking; removal and
  enumeration also asserted via TestBrokerSync and TestConnectionWatchdog.

### Serializers.mqh
- `JsonMessageSerializer.Serialize()` — exercised indirectly: FakeZmqNetwork
  `SendPositionSync` serializes the same payload tree through the same
  vendor `JSONValue.Serialize()` and the JSON shape is asserted in
  TestBrokerSync / TestE2ETestRunner.
- `JsonMessageSerializer.Deserialize()` — exercised indirectly: every
  suite builds envelopes with `JSONParser::Parse(...)` (the same call the
  serializer wraps); parse results asserted via MessageEnvelope accessors
  and handler behavior. (The class is a 2-call wrapper over the vendor
  parser with a NULL guard; no dedicated suite — noted as a soft spot.)

## Exclusions

| Component | Reason |
|---|---|
| `TradingBotZmqEA.mq5` (main EA) | Wiring/global glue (OnInit/OnTick/OnTimer, `g_e2eTestRunning`, `g_disconnectRequestTick` deliberate-disconnect flag is EA-side); no testable units beyond what the extracted classes cover. |
| `Application/ZmqNetwork.mqh` | Real ØMQ socket transport over the vendor DLL — DLL imports cannot load in the Strategy Tester; every public method is exercised through `FakeZmqNetwork` instead. |
| `UI/ConnectorDialog.mqh` | Chart-object UI (buttons/labels); requires an interactive chart, no assertable logic. |
| `vendor/include/JSON`, `vendor/include/Zmq` | Third-party libraries. |
| `Infrastructure/MqlPlatformApi.mqh` | One-line terminal-API passthroughs; the interfaces they implement are fully covered via fakes. |
| `Infrastructure/Logger.mqh` | `Print()`/`Comment()` adapter; no logic. |
| `ConfigLoader::Load/ReadFileAsString` | Terminal-sandbox file I/O; the parsing core (`Parse`) is fully covered. |
| Account-mismatch NACK / subscribe-ACK E2E scenarios | Python-driven by design (noted in `E2ETestRunner.mqh` / `TestStartHandler.mqh` comments); coverage lives on the Python E2E side. |

## Connector bugs found by these tests (FIXED)

1. `Application/ZmqNetwork.mqh` — `payload["count"] = new JSONValue(count)`
   resolved to the `JSONValue(JSON_ENUM)` ctor (int→enum), so
   `position_sync.count` serialized as a JSON *type tag* (count=0 → `null`,
   1 → `{}`, 2 → `[]`). Fixed by casting to `long`; TestBrokerSync now
   asserts the numeric form.
2. `Application/BrokerSync.mqh` `ProcessNewDeals()` — once the processed-deals
   FIFO overflowed, eviction cascaded into re-reporting the entire deal set.
   Fixed: a deal-time high-water mark now bounds the scan (older-than-watermark
   stops the newest→oldest iteration; same-second ties are deduped via the
   ticket array, cap raised 1000→10000).
3. `Application/E2ETestRunner.mqh` `ValidateSimulationEnvironment()` bypassed
   the `IAccountApi` seam (raw `AccountInfoInteger`). Fixed: the runner takes
   an injected `IAccountApi`; `MqlAccountApi::IsDemo()` covers demo AND
   contest accounts; the non-demo refusal path is now tested.

## Remaining known quirks (reported, not fixed)

- `OrderOpenHandler`/`OrderCloseHandler` send fills directly on live success
  AND `BrokerSync.ProcessNewDeals()` reports the same deals from history —
  duplicate `entry_fill`/`exit_fill` messages. Python dedupes via
  `status != "open"` guards, so this is noise (double logs), not corruption.
- `ConfigLoader` string-fallback could previously overwrite `pair`/
  `platformVersion` defaults with `""` — fixed (defaults preserved).
