# TradingBot Code Categories

This document is a high-level map of the TradingBot codebase. It groups files by functional responsibility so that deep-dive bug-hunt and test-coverage sessions can be focused one category at a time.

The categories are ordered by the recommended **risk-first** review order, but each section can be read independently.

---

## 1. Configuration, Readiness & Orchestration

Startup, dependency wiring, live-readiness state machine, warmup, and configuration loading.

| File | Responsibility |
|------|----------------|
| `app.py` | CLI entry point: loads config, builds app, starts server, handles signals. |
| `app_factory.py` | Main dependency-injection factory: wires strategy, trade manager, controllers, routes, ZMQ handlers, readiness. |
| `src/config/builder.py` | `AppBuilder`: assembles backtest or live app from `AppConfig`. |
| `src/config/builders.py` | Additional builder helpers. |
| `src/config/models.py` | `AppConfig`, `AccountConfig` dataclasses. |
| `src/config/loaders.py` | Env, CLI, DB config loaders and composite loader. |
| `src/application/live_readiness/readiness_monitor.py` | Coordinates history refresh, warmup, live bar buffering, readiness transitions. |
| `src/application/live_readiness/readiness_state_machine.py` | Thread-safe state machine for live readiness. |
| `src/application/live_readiness/readiness_state.py` | `ReadinessState` enum. |
| `src/application/live_readiness/warmup_orchestrator.py` | Replays historical bars through strategy to warm indicators. |
| `src/application/live_readiness/warmup_policy.py` | `MinimumBarsWarmupPolicy`. |
| `src/application/live_readiness/trading_context.py` | Execution context abstraction (always-enabled vs readiness-gated). |
| `src/application/live_readiness/live_bar_buffer.py` | Buffers live bars during warmup. |
| `src/application/ports.py` | Abstract ports: lifecycle service, event publisher, notifier. |
| `src/utils/history_loaded_deduper.py` | Deduplicates `history_loaded` Socket.IO emissions. |
| `src/services/settings_service.py` | Settings business logic. |
| `src/infrastructure/repositories/settings_repository.py` | Settings persistence. |

**Key risk areas:** readiness state-machine race conditions, warmup indicator drift, config loader precedence (env vs DB), account/settings caching, signal handling during startup, `USER_CONTROLLED_SOURCES` flag behavior.

**Test files:** `tests/test_config_*.py`, `tests/test_settings_*.py`, `tests/application/test_readiness*.py`, `tests/application/test_warmup*.py`, `tests/application/test_trading_context.py`.

---

## 2. Broker / Platform Integration & Gateway

ZeroMQ and HTTP communication with NinjaTrader and MetaTrader, plus parity/state checks.

| File | Responsibility |
|------|----------------|
| `src/infrastructure/gateway/gateway.py` | Core ZeroMQ gateway: SUB/PUSH/REQ/REP sockets, message dispatch, heartbeats, command ACKs, E2E test state. |
| `src/infrastructure/gateway/executor.py` | `ZMQTradeExecutor` and `MultiAccountExecutor`; sends open/close/modify commands. |
| `src/infrastructure/gateway/datasource.py` | `ZMQDataSource`: wires gateway events to app callbacks, history batching, refresh, gap detection. |
| `src/infrastructure/gateway/protocol.py` | ZeroMQ message envelope and typed message classes. |
| `src/infrastructure/gateway/integration.py` | Factories for live / multi-account live components. |
| `src/infrastructure/parity_checker.py` | `NinjaTraderParityChecker` implementation. |
| `src/application/parity_service.py` | On-demand parity checks between Python and NinjaTrader state. |
| `src/infrastructure/market_closure_filter.py` | Filters market-closure periods from parity checks. |
| `src/services/nt_manager_service.py` | NinjaTrader manager service. |
| `src/services/mt_manager_service.py` | MetaTrader manager service. |
| `src/services/platform_lifecycle/*.py` | Platform lifecycle services (launch/monitor). |
| `src/services/platform_deploy_service.py` | Platform deployment service. |
| `src/routes/nt_routes.py` | NinjaTrader management endpoints. |
| `src/routes/mt_routes.py` | MetaTrader management endpoints. |
| `zmq_connectors/ninjatrader/**/*.cs` | NinjaTrader C# AddOn source. |
| `zmq_connectors/metatrader/**/*.mq5` | MetaTrader 5 EA source. |

**Key risk areas:** socket lifecycle on reconnect, command ACK timing, heartbeat/thread races, position-sync mismatch, multi-account routing, C#/MQ5 fill detection, history re-send on refresh, no automated tests for C#/MQ5, platform deploy/launch paths uncovered.

**Test files:** `tests/test_gateway*.py`, `tests/test_parity*.py`, `tests/test_nt_manager_service.py`, `tests/test_mt_manager_service.py`, `tests/test_nt_lifecycle_service.py`, `tests/test_mt_lifecycle_service.py`, `tests/test_zmq_gateway.py`, `tests/fake_ninjatrader/*`, `tests/e2e/*`.

---

## 3. Trade Lifecycle / Position Management

Everything that happens after a trade signal is generated: sizing, execution, fills, SL/TP monitoring, closing, PnL, and logging.

| File | Responsibility |
|------|----------------|
| `src/services/trade_manager.py` | In-memory open-trade tracking, SL/TP detection, session-end close, broker fill handling, DB resume. |
| `src/services/trade_executor.py` | `TradeExecutor` + `NoOpExecutor` interfaces. |
| `src/services/trade_close_service.py` | Legacy/helper close service wrapper. |
| `src/services/trade_logger.py` | Writes structured trade lifecycle logs. |
| `src/services/position_sizing/position_sizer.py` | Fixed-risk, tiered-SL, percentage-risk position sizers. |
| `src/application/use_cases/trade_open_use_case.py` | Single source of truth for opening trades: contract sizing, DB insert, executor call, rollback on failure. |
| `src/application/use_cases/trade_close_use_case.py` | Single source of truth for closing trades: PnL/fees, executor, DB persist, event emission. |
| `src/application/use_cases/broker_fill_handler.py` | Updates trade state on broker entry fills (price, SL, TP, contracts). |
| `src/application/services/strategy_trade_service.py` | Bridges strategy signals to trade open + per-account expansion. |
| `src/financial_calc.py` | Single source of truth for contracts, fees, PnL, R-multiple, result-type classification. |
| `src/domain/result_type_classifier.py` | Pluggable SL/TP/BE/SP classifier. |

**Key risk areas:** duplicate PnL counting on broker fills, `_closing_trades` race window, rollback behavior when executor fails, contract rounding for futures/CFD, fee calculations, BE/SP classification edge cases, multi-account trade expansion.

**Test files:** `tests/test_trade_manager*.py`, `tests/test_trade_executor.py`, `tests/test_trade_close_service.py`, `tests/test_trade_logger.py`, `tests/test_pnl_calculations.py`, `tests/test_position_sizer.py`, `tests/test_financial_calc.py`, `tests/application/test_broker_fill_integration.py`, `tests/application/test_strategy_trade_service.py`.

---

## 4. Strategy / Entry Logic

Core decision-making that determines when to enter a trade.

| File | Responsibility |
|------|----------------|
| `src/strategies/strategy_factory.py` | Creates `LiquidityStrategyV2` (default) or `TsiCrossStrategy` by name. |
| `src/strategies/liquidity_v2/strategy.py` | Main production strategy: line latching/removal, multi-timeframe bar aggregation, TSI logging, entry filtering. |
| `src/strategies/liquidity_v2/base_strategy.py` | Shared strategy plumbing: line state, filter pipeline, trade open/close bookkeeping, event-driven trade updates, re-entry logic. |
| `src/strategies/liquidity_v2/triggers.py` | Entry triggers: TSI cross, velocity-adaptive TSI, wick-near-line, 3-candle reversal, double-5m-cross. |
| `src/strategies/liquidity_v2/config.py` | `StrategyNumbers`, `CandleConfig` value objects. |
| `src/strategies/liquidity_v2/prod_config.py` | Production defaults for numbers, candle config, and assembled strategy options. |
| `src/strategies/liquidity_v2/constants.py` | Default `StrategyOptions`, line-removal modes. |
| `src/strategies/liquidity_v2/controllers/lines_controller.py` | HTTP API for adding/updating/removing strategy lines. |
| `src/strategies/liquidity_v2/exits/exit_strategy.py` | Exit-strategy abstractions (SLTP, session end, composite). |
| `src/strategies/entry_context.py` | `EntryContext` plus reusable entry filters (time range, rollover, daily limit, open-trades limit, min cross depth, max bounce). |
| `src/strategies/indicators/tsi.py` | Python TSI(6,13,4) + EMA implementation. |
| `src/strategies/tsi_cross/strategy.py` | Alternative `TsiCrossStrategy`. |
| `src/strategies/tsi_cross/tsi_analyzer.py` | TSI analysis helpers. |
| `src/strategies/tsi_cross/bounce_detector.py` | Bounce detection helpers. |
| `src/strategies/liquidity_m1dual/strategy.py` | Older dual-timeframe M1 liquidity strategy. |
| `ninjatrader/TradingBotTSI.cs` | NinjaScript TSI(6,13,4) indicator matching Python. |

**Key risk areas:** line-latching edge cases, trigger state resets after warmup, TSI precision differences vs C#, multi-timeframe aggregation mismatches, rollover/time-range filters, max-bounce logic, SL/TP sizing in partial fills.

**Test files:** `tests/test_strategy_v2.py`, `tests/test_strategy_base*.py`, `tests/test_triggers.py`, `tests/test_entry_filters.py`, `tests/tsi_cross/*`, `tests/test_liquidity_m1dual_strategy.py`.

---

## 5. Market Data, Bars & Time Aggregation

How price data is ingested, aggregated, buffered, and replayed.

| File | Responsibility |
|------|----------------|
| `src/bars_loader.py` | Loads/replays bars, handles partial bars, timeframe aggregation, gap detection, stream control (start/pause/seek/step). |
| `src/utils/bar_aggregator.py` | Aggregates 1m bars into higher timeframes and merges partial bars. |
| `src/infrastructure/data_sources/bars_datasource.py` | Abstract bars loader interface. |
| `src/infrastructure/data_sources/combined_datasource.py` | Abstract base for data sources. |
| `src/infrastructure/data_sources/csv_datasource.py` | Backtest CSV replay data source. |
| `src/infrastructure/data_sources/csv_replay_datasource.py` | Tick-style CSV replay helper. |
| `src/infrastructure/data_sources/live/live_datasource.py` | Abstract live tick interface. |
| `src/infrastructure/data_sources/live/websocket_live_datasource.py` | WebSocket live data source stub. |
| `src/infrastructure/data_sources/metatrader_datasource.py` | MetaTrader-specific data source. |
| `src/infrastructure/bar_auditor.py` | Compares Python bars vs NinjaTrader bars and detects drift. |
| `fetcher/run.py` | CLI entry point for daily CSV data sync. |
| `fetcher/sync.py` | `DataSyncer`: fetches and merges new bars into CSV. |
| `fetcher/csv_store.py` | CSV persistence with timezone handling. |
| `fetcher/base.py` | Provider abstraction. |
| `fetcher/types.py` | `Bar` typed dict. |
| `fetcher/providers/yfinance_provider.py` | Yahoo Finance provider. |
| `fetcher/providers/polygon_provider.py` | Polygon.io provider. |
| `fetcher/providers/alpaca_provider.py` | Alpaca provider. |
| `fetcher/providers/databento_provider.py` | Databento provider. |

**Key risk areas:** partial-bar merging, gap detection, timezone conversions (FILE_TZ → INPUT_TZ), duplicate history batches, live vs backtest path divergence, aggregator boundary conditions (e.g., 15m bar built from 1m), CSV fetcher timezone/DST handling, missing data in providers.

**Test files:** `tests/test_bars_loader*.py`, `tests/test_bar_aggregator.py`, `tests/test_bar_auditor.py`, `tests/test_combined_datasource.py`, `tests/test_csv_datasource.py`, `tests/application/test_live_bar_buffer.py`, `tests/utils/test_history_loaded_deduper.py`.

---

## 6. Web, Events, Analytics & UI

HTTP/Socket.IO surface, domain events, notifications, analytics, and logging.

| File | Responsibility |
|------|----------------|
| `src/routes/core_routes.py` | Flask core routes (status, health, playback control). |
| `src/routes/trades_routes.py` | Trade REST endpoints. |
| `src/routes/lines_routes.py` | Line REST endpoints. |
| `src/routes/admin_routes.py` | Admin/analytics endpoints. |
| `src/routes/settings_routes.py` | Settings endpoints. |
| `src/routes/nt_routes.py` | NinjaTrader management endpoints. |
| `src/routes/mt_routes.py` | MetaTrader management endpoints. |
| `src/routes/stream_routes.py` | Stream lifecycle endpoints. |
| `src/routes/socketio_handlers.py` | Socket.IO event handlers + token bucket for ticks. |
| `src/strategies/liquidity_v2/routes/debug_routes.py` | Debug/info routes. |
| `src/controllers/admin_controller.py` | Admin page/controller logic. |
| `src/controllers/trades_controller.py` | Trade controller logic. |
| `src/controllers/settings_controller.py` | Settings controller logic. |
| `src/events/event_bus.py` | In-memory domain event bus + `SocketIOBridge`. |
| `src/events/trade_events.py` | Typed trade/line domain event creation. |
| `src/infrastructure/event_publisher.py` | `DomainEventBusPublisher` / `SocketIOEventPublisher`. |
| `src/services/analytics_service.py` | Trade analytics/statistics service. |
| `src/analytics.py` | Analytics reporter abstraction (NoOp / Sentry). |
| `src/trade_analytics.py` | Additional trade statistics helpers. |
| `src/notifier.py` | Telegram / NoOp notifier. |
| `src/utils/app_logger.py` | Console + file logging abstraction. |
| `templates/*.html` | Jinja2 templates. |
| `static/js/*.js`, `static/css/*.css` | Frontend assets. |

**Key risk areas:** Socket.IO emit ordering, tick token-bucket behavior, event-bus delivery guarantees, analytics edge cases with zero/negative values, Telegram notifier silent failures, route error handling.

**Test files:** `tests/test_*_routes.py`, `tests/test_socketio_handlers.py`, `tests/test_event_bus.py`, `tests/test_analytics*.py`, `tests/test_notifier.py`, `tests/test_app_logger.py`.

---

## 7. Utilities, Scripts & Tooling

Helper scripts and the data-fetcher pipeline.

| File | Responsibility |
|------|----------------|
| `scripts/run_scenarios.py` | Scenario-based integration test runner. |
| `scripts/run_integration_tests.py` | HTTP integration test runner. |
| `scripts/scenario_management.py` | Scenario discovery, diffing, YAML formatting. |
| `scripts/add_scenario.py` | Add a new scenario. |
| `scripts/fix_scenario.py` | Fix a single scenario expectation. |
| `scripts/fix_all_scenarios.py` | Bulk scenario fixer. |
| `scripts/compare_configs.py` | Config comparison. |
| `scripts/compare_modes.py` | Mode comparison. |
| `scripts/html_report.py` | HTML report generation. |
| `scripts/html_comparison_report.py` | HTML comparison report. |
| `scripts/html_mode_comparison_report.py` | HTML mode comparison report. |
| `scripts/mode_pnl.py` | Futures vs CFD PnL simulation. |
| `scripts/test_tsi_strategy.py` | TSI strategy backtest runner. |
| `scripts/tune_tsi.py` | Velocity-adaptive trigger tuning. |
| `scripts/debug_liquidity_v2.py` | Liquidity strategy debug plotter. |
| `scripts/debug_3candle_trigger.py` | 3-candle trigger debug plotter. |
| `scripts/code_quality.py` | Linter/pyflakes runner. |
| `scripts/build_prompt.py` | Prompt-building utility. |
| `scripts/extract_csv_range.py` | CSV date-range extraction. |
| `scripts/report_utils.py` | Shared PnL/report helpers. |
| `scripts/test_telegram.py` | Telegram notification smoke test. |

**Key risk areas:** scenario YAML fixers can corrupt data, report scripts assume CSV/DB state, fetcher provider API drift, CSV merge overwrites/lost bars, `extract_csv_range.py` timezone mistakes.

**Test files:** None for scripts or fetcher providers.

---

## How to Use This Document

1. Pick a category.
2. Read every file listed in that category, plus its direct callers and callees.
3. List edge cases: empty collections, boundary values, `None`/`NaN`, timezone transitions, concurrency windows, and failure branches.
4. Write focused tests that reproduce the edge cases.
5. Apply the smallest fix that preserves invariants.
6. Run the full test suite before moving on.

The recommended review order is **1 → 2 → 3 → 4 → 5 → 6 → 7** (risk-first), but any category can be reviewed independently.
