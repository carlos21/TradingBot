# Project Instructions

## Architecture and Code Quality

All code changes in this project must follow **SOLID principles** and **CLEAN architecture**. Prefer dependency inversion, clear separation of concerns, single responsibility for classes and functions, and testable design.

## Testing After Changes

After making any code change, run the test suite that corresponds to the affected layer:

- **C# / NinjaTrader code**: `bin/run_cs_tests.sh`
- **Python backend**: `bin/run_backend_tests.sh`
- **JavaScript frontend**: `bin/run_frontend_tests.sh`
- **MQL5 / MetaTrader connector**: `bin/compile_metatrader.sh` (compiles via MetaEditor through WSL interop and fails on build errors; use `--check` for a faster syntax-only pass)
- **MQL5 unit tests** (`zmq_connectors/metatrader/Tests/`): `bin/run_mql_tests.sh` (compiles `Tests/TestRunnerEA.mq5`, runs it in the MT5 Strategy Tester via a `[Tester]` ini, and fails on any red assert; the terminal must not be running — MetaTrader ignores `/config` while an instance is up)

Do not consider a change complete until the relevant tests have been run and pass.
