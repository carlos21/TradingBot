# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run the app:**
```bash
poetry run python app.py
```
Starts the Flask-SocketIO server on port 5001.

**Install dependencies:**
```bash
poetry install
```

**Run all test scenarios:**
```bash
./bin/run_scenarios.sh
```

**Run a single test scenario:**
```bash
./bin/run_test_scenario.sh
```

Both scripts invoke `scripts/run_scenarios.py` with Playwright browser automation against `tests/scenarios.yaml` (full suite) or `tests/test_scenario.yaml` (single case). Output is written to `scenarios_out/`.

## Rules

- **Never run `git add` or `git commit`** — I will handle all git operations myself. Only make code changes; do not stage or commit them.

## Architecture

This is a **multi-timeframe liquidity trading strategy backtester** — a Flask web app that replays historical 1m bar data through a trading strategy, visualizes results in a browser, and validates trades via automated scenario testing.

### Data Flow

```
CSV File → CSVDataSource → BarsLoader → LiquidityStrategyV2
                                              ↓
                              TradeManager ← Socket.IO → Frontend
                                    ↓
                              SQLite (via SQLAlchemy)
```

### Key Components

**`app.py`** — Entry point. Calls `build_prod()` which wires all dependencies and starts the server.

**`app_factory.py`** — Flask app factory (`create_app()`). Registers all HTTP routes and Socket.IO event handlers. All dependencies (repos, strategy, data source) are injected here rather than imported globally.

**`src/strategies/liquidity_strategy_v2.py`** — The main strategy. Receives 1m bars, aggregates them into configured timeframes (5m, 15m, etc.), computes TSI on each timeframe, and evaluates whether to enter a trade by running bars through the trigger and filter pipeline. This is the central business logic file.

**`src/strategies/base_liquidity_strategy.py`** — Abstract base for all strategies. Manages lines (support/resistance levels), trade lifecycle, filter/trigger composition, and Socket.IO emissions.

**`src/strategies/triggers.py`** — Entry trigger functions: TSI cross, 3-candle reversal, wick-near-line, double 5m cross. Each returns a boolean given a bar and context.

**`src/strategies/entry_context.py`** — Entry filter functions: open trade limit, max bounce, time range (08:00–14:00 NY), daily trade limit. Filters are composable — all must pass for an entry.

**`src/prod_config.py`** — Production strategy parameters: `StrategyNumbers` (stop loss sizing), `CandleConfig` (pattern ratios), `StrategyOptions` (which triggers and filters are active).

**`src/bars_loader.py`** — Manages the streaming thread, timeframe aggregation, pause/seek/jump controls.

**`src/services/trade_manager.py`** — Runs on every 1m bar to check if any open trade's SL or TP has been hit; closes and persists accordingly.

**`src/repositories/`** — Repository pattern over SQLAlchemy. `SQLLineRepository` stores support/resistance lines; `SQLTradeRepository` stores trades. `tests/fakes.py` has in-memory fake implementations for testing.

### Timezone Handling

The app handles three timezone contexts:
- **FILE_TZ** (`Etc/GMT+3`): timezone of the source CSV bars
- **INPUT_TZ** (`America/New_York`): timezone for user-facing timestamps and trading-hours filters
- Per-pair mapping: NQ → `America/New_York`

Timezone conversion is done centrally when bars are ingested; strategy logic operates in INPUT_TZ.

### Scenario Testing

Tests are YAML-based (`tests/scenarios.yaml`). Each scenario defines: pair, timeframe, date range, support/resistance lines to draw, and expected trade outcomes (entry price, SL, TP, with tolerance). `scripts/run_scenarios.py` starts the Flask app as a subprocess, drives the UI via Playwright, then validates actual trades against expectations.

To add a new scenario, add an entry to `tests/scenarios.yaml` following the existing format.
