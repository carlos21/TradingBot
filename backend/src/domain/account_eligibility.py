"""Account eligibility rules.

Centralises the decision of whether a given account may trade a specific
instrument.  Both TSI Cross and Liquidity V2 strategies use these helpers so
that the rule lives in one place.
"""

from __future__ import annotations

from typing import Protocol


class AccountEligibilityInfo(Protocol):
    """Structural view of the account data eligibility checks require.

    Satisfied by both ``src.config.models.AccountConfig`` and
    ``src.domain.repositories.AccountConfig``.
    """

    name: str
    live_enabled: bool
    instrument_symbols: list[str]


def is_account_eligible_for_pair(
    account: AccountEligibilityInfo, pair: str, live_mode: bool
) -> bool:
    """Return True if *account* is allowed to trade *pair*.

    Rules:
      1. In live mode the account must be live-enabled.
      2. The account must have a non-empty instrument_symbols list.
      3. The requested pair must be one of those symbols.
    """
    if live_mode and not account.live_enabled:
        return False

    symbols = account.instrument_symbols
    if not symbols:
        return False

    pair_norm = str(pair).upper()
    return any(str(symbol).upper() == pair_norm for symbol in symbols)


def filter_eligible_accounts(
    accounts: list[AccountEligibilityInfo], pair: str, live_mode: bool
) -> list[AccountEligibilityInfo]:
    """Return only the accounts eligible to trade *pair*."""
    return [a for a in accounts if is_account_eligible_for_pair(a, pair, live_mode)]
