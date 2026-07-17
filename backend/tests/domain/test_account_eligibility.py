"""Tests for src/domain/account_eligibility.py."""

import pytest

from src.domain.account_eligibility import (
    filter_eligible_accounts,
    is_account_eligible_for_pair,
)
from src.config.models import AccountConfig


class TestIsAccountEligibleForPair:
    def test_eligible_when_symbol_list_contains_pair(self):
        account = AccountConfig(name="A1", instrument_symbols=["MNQ", "ES"])
        assert is_account_eligible_for_pair(account, "MNQ", live_mode=False) is True
        assert is_account_eligible_for_pair(account, "ES", live_mode=False) is True

    def test_case_insensitive_match(self):
        account = AccountConfig(name="A1", instrument_symbols=["mnq"])
        assert is_account_eligible_for_pair(account, "MNQ", live_mode=False) is True

    def test_not_eligible_when_pair_missing(self):
        account = AccountConfig(name="A1", instrument_symbols=["ES"])
        assert is_account_eligible_for_pair(account, "MNQ", live_mode=False) is False

    def test_not_eligible_when_no_symbols(self):
        account = AccountConfig(name="A1")
        assert is_account_eligible_for_pair(account, "MNQ", live_mode=False) is False

    def test_not_eligible_when_empty_symbol_list(self):
        account = AccountConfig(name="A1", instrument_symbols=[])
        assert is_account_eligible_for_pair(account, "MNQ", live_mode=False) is False

    def test_live_mode_requires_live_enabled(self):
        enabled = AccountConfig(name="A1", live_enabled=True, instrument_symbols=["MNQ"])
        disabled = AccountConfig(name="A2", live_enabled=False, instrument_symbols=["MNQ"])
        assert is_account_eligible_for_pair(enabled, "MNQ", live_mode=True) is True
        assert is_account_eligible_for_pair(disabled, "MNQ", live_mode=True) is False

    def test_non_live_mode_ignores_live_enabled(self):
        disabled = AccountConfig(name="A2", live_enabled=False, instrument_symbols=["MNQ"])
        assert is_account_eligible_for_pair(disabled, "MNQ", live_mode=False) is True


class TestFilterEligibleAccounts:
    def test_returns_only_eligible_accounts(self):
        accounts = [
            AccountConfig(name="A1", instrument_symbols=["MNQ"]),
            AccountConfig(name="A2", instrument_symbols=["ES"]),
            AccountConfig(name="A3"),
        ]
        eligible = filter_eligible_accounts(accounts, "MNQ", live_mode=False)
        assert [a.name for a in eligible] == ["A1"]
