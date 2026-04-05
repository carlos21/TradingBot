"""Single source of truth for trade financial calculations (contracts, fees, PNL)."""

from __future__ import annotations


class FinancialCalc:
    DEFAULT_FEE_PER_RT = 1.50  # $ round-trip per contract (Tradovate MNQ)

    @staticmethod
    def contracts(risk_budget: float, risk_per_contract: float) -> int:
        """Number of contracts to trade, matching NinjaTrader's logic."""
        if risk_per_contract <= 0:
            return 1
        return max(1, round(risk_budget / risk_per_contract))

    @staticmethod
    def fees(contracts: int, fee_per_rt: float = DEFAULT_FEE_PER_RT) -> float:
        """Total round-trip fees for a trade."""
        return contracts * fee_per_rt

    @staticmethod
    def pnl_usd(
        contracts: int,
        actual_r: float,
        sl_pts: float,
        point_value: float,
        fees: float,
    ) -> float:
        """Net dollar PNL for a closed trade.

        Unified formula that works for wins, losses, and breakevens:
            gross = contracts * actual_r * sl_pts * point_value
            net   = gross - fees
        """
        return contracts * actual_r * sl_pts * point_value - fees

    @staticmethod
    def risk_budget(
        account_balance: float,
        risk_per_trade: float | None,
        risk_pct_per_trade: float | None,
    ) -> float:
        """Resolve fixed-dollar or %-based risk into a dollar amount."""
        if risk_per_trade is not None:
            return risk_per_trade
        if risk_pct_per_trade is not None:
            return account_balance * risk_pct_per_trade / 100.0
        return 0.0
