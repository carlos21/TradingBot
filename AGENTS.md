# Project Instructions

## Architecture and Code Quality

All code changes in this project must follow **SOLID principles** and **CLEAN architecture**. Prefer dependency inversion, clear separation of concerns, single responsibility for classes and functions, and testable design.

## Testing After Changes

After making any code change, run the test suite that corresponds to the affected layer:

- **C# / NinjaTrader code**: `bin/run_cs_tests.sh`
- **Python backend**: `bin/run_backend_tests.sh`
- **JavaScript frontend**: `bin/run_frontend_tests.sh`

Do not consider a change complete until the relevant tests have been run and pass.
