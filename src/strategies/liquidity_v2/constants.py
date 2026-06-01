"""Single source of truth for StrategyOptions default values.

No other module should define defaults for strategy behaviour.
Callers that need defaults import them from here and pass explicitly.
"""
from src.strategies.liquidity_v2.base_strategy import LineRemovalMode, StrategyOptions

# Individual constants (useful when callers need only one value)
DEFAULT_LINE_REMOVAL_MODE = LineRemovalMode.ON_EVALUATE
DEFAULT_REENTRY_AFTER_SL = False
DEFAULT_REENTRY_THRESHOLD = 60.0
DEFAULT_REENTRY_ONLY = False
DEFAULT_MAX_REENTRY_ATTEMPTS = 1

# Full default instance for dataclasses.replace() in tests
DEFAULT_STRATEGY_OPTIONS = StrategyOptions(
    line_removal_mode=DEFAULT_LINE_REMOVAL_MODE,
    entry_filters=None,
    triggers=None,
    breakeven=None,
    reentry_breakeven=None,
    reentry_after_sl=DEFAULT_REENTRY_AFTER_SL,
    reentry_threshold=DEFAULT_REENTRY_THRESHOLD,
    reentry_only=DEFAULT_REENTRY_ONLY,
    max_reentry_attempts=DEFAULT_MAX_REENTRY_ATTEMPTS,
)
