"""Single source of truth for trade financial calculations (contracts, fees, PNL, result types)."""

from __future__ import annotations

from typing import Literal

from src.domain.types import Direction


class FinancialCalc:
    DEFAULT_FEE_PER_RT = 1.50  # $ round-trip per contract (Tradovate MNQ)
    DEFAULT_BE_THRESHOLD_POINTS = 2.0  # Exit within this many points of entry is BE
    DEFAULT_BE_THRESHOLD_R = 0.1  # Exit with R result below this is BE

    # =========================================================================
    # Basic Financial Calculations
    # =========================================================================

    MAX_CONTRACTS = 100

    @staticmethod
    def contracts(risk_budget: float, risk_per_contract: float) -> int:
        """Number of contracts to trade, matching NinjaTrader's logic."""
        if risk_budget <= 0:
            return 0
        if risk_per_contract <= 0:
            return 1
        return min(FinancialCalc.MAX_CONTRACTS, max(1, round(risk_budget / risk_per_contract)))

    @staticmethod
    def lots(risk_budget: float, risk_per_lot: float) -> float:
        """Fractional lots for CFD mode (no rounding to integer)."""
        if risk_per_lot <= 0:
            return 0.01
        return max(0.01, risk_budget / risk_per_lot)

    @staticmethod
    def fees(contracts: float, fee_per_rt: float = DEFAULT_FEE_PER_RT) -> float:
        """Total round-trip fees for a trade."""
        return contracts * fee_per_rt

    @staticmethod
    def pnl_usd(
        contracts: float,
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

    # =========================================================================
    # Result Type Detection (Single Source of Truth for SL/TP/BE/SP)
    # =========================================================================

    @staticmethod
    def is_breakeven(
        exit_price: float,
        entry_price: float,
        threshold_points: float = DEFAULT_BE_THRESHOLD_POINTS,
    ) -> bool:
        """Check if exit price is within threshold of entry (breakeven).

        Args:
            exit_price: The price at which the trade closed
            entry_price: The entry price of the trade
            threshold_points: Distance in points to consider as breakeven

        Returns:
            True if the exit is within threshold of entry
        """
        return abs(exit_price - entry_price) < threshold_points

    @staticmethod
    def is_breakeven_by_r(
        result_r: float,
        threshold_r: float = DEFAULT_BE_THRESHOLD_R,
    ) -> bool:
        """Check if R result is effectively breakeven (near zero).

        Args:
            result_r: The R-multiple result of the trade
            threshold_r: R threshold below which is considered breakeven

        Returns:
            True if |result_r| < threshold_r
        """
        return abs(result_r) < threshold_r

    @staticmethod
    def determine_result_type(
        exit_price: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        _result_r: float | None = None,
        be_threshold_points: float = DEFAULT_BE_THRESHOLD_POINTS,
        sl_tp_tolerance: float = 0.5,
    ) -> Literal["BE", "SL", "TP", "SP"]:
        """Determine the result type of a closed trade.

        This is the SINGLE SOURCE OF TRUTH for result type classification.
        Order of precedence:
        1. BE - if exit is close to entry price (within threshold)
        2. SL - if exit is close to stop loss (within tolerance)
        3. TP - if exit is close to take profit (within tolerance)
        4. SP - manual/unknown close (session end, etc.)

        Args:
            exit_price: The price at which the trade closed
            entry_price: The entry price of the trade
            stop_loss: The stop loss price
            take_profit: The take profit price
            result_r: Optional R-multiple result (for additional context)
            be_threshold_points: Distance from entry to consider as breakeven
            sl_tp_tolerance: Distance tolerance for SL/TP detection

        Returns:
            One of: "BE" (breakeven), "SL" (stop loss), "TP" (take profit), "SP" (manual/unknown)
        """
        # Check BE first (SL might be at entry for breakeven trades)
        if FinancialCalc.is_breakeven(exit_price, entry_price, be_threshold_points):
            return "BE"

        # Check SL proximity
        if abs(exit_price - stop_loss) < sl_tp_tolerance:
            return "SL"

        # Check TP proximity
        if abs(exit_price - take_profit) < sl_tp_tolerance:
            return "TP"

        # Default: manual close or session end
        return "SP"

    @staticmethod
    def determine_result_type_from_hit(
        hit_sl: bool,
        hit_tp: bool,
        exit_price: float,
        entry_price: float,
        be_threshold_points: float = DEFAULT_BE_THRESHOLD_POINTS,
    ) -> Literal["BE", "SL", "TP", "SP"]:
        """Determine result type when SL/TP hit flags are known.

        Args:
            hit_sl: True if stop loss was hit
            hit_tp: True if take profit was hit
            exit_price: The exit price
            entry_price: The entry price
            be_threshold_points: Distance from entry to consider as breakeven

        Returns:
            One of: "BE", "SL", "TP", "SP"
        """
        # Check BE first (even if SL/TP flags are set)
        if FinancialCalc.is_breakeven(exit_price, entry_price, be_threshold_points):
            return "BE"

        if hit_sl:
            return "SL"
        if hit_tp:
            return "TP"

        return "SP"

    # =========================================================================
    # Trade Close Calculations (Unified)
    # =========================================================================

    @staticmethod
    def calculate_r_multiple(
        direction: Direction,
        entry_price: float,
        exit_price: float,
        risk_points: float,
    ) -> float:
        """Calculate R-multiple result for a trade.

        Args:
            direction: Trade direction (Direction enum)
            entry_price: Entry price
            exit_price: Exit price
            risk_points: Risk in points (distance from entry to original SL)

        Returns:
            R-multiple (pnl_points / risk_points)
        """
        if risk_points <= 0:
            risk_points = 1.0

        if direction.is_long:
            pnl_points = exit_price - entry_price
        else:  # SHORT
            pnl_points = entry_price - exit_price

        return pnl_points / risk_points

    @staticmethod
    def calculate_close_metrics(
        direction: Direction,
        entry_price: float,
        exit_price: float,
        stop_loss: float,
        take_profit: float,
        risk_points: float,
        contracts: float,
        point_value: float,
        fee_per_rt: float = DEFAULT_FEE_PER_RT,
        be_threshold_points: float = DEFAULT_BE_THRESHOLD_POINTS,
        sl_tp_tolerance: float = 0.5,
    ) -> tuple[float, float, float, str]:
        """Calculate all metrics for a trade close in ONE call.

        This is the SINGLE SOURCE OF TRUTH for trade close calculations.
        Returns all values needed to persist a trade close.

        Args:
            direction: Trade direction (Direction enum)
            entry_price: Entry price
            exit_price: Exit price
            stop_loss: Stop loss price (for result type detection)
            take_profit: Take profit price (for result type detection)
            risk_points: Risk in points
            contracts: Number of contracts
            point_value: $ per point
            fee_per_rt: Fee per round-trip per contract
            be_threshold_points: BE detection threshold
            sl_tp_tolerance: SL/TP proximity tolerance

        Returns:
            Tuple of (result_r, fees, pnl_usd, result_type)
        """
        # Calculate R-multiple
        result_r = FinancialCalc.calculate_r_multiple(
            direction, entry_price, exit_price, risk_points
        )

        # Calculate fees
        fees = FinancialCalc.fees(contracts, fee_per_rt)

        # Calculate PnL USD
        pnl_usd = FinancialCalc.pnl_usd(
            contracts, result_r, risk_points, point_value, fees
        )

        # Determine result type
        result_type = FinancialCalc.determine_result_type(
            exit_price, entry_price, stop_loss, take_profit,
            result_r, be_threshold_points, sl_tp_tolerance
        )

        return result_r, fees, pnl_usd, result_type

    @staticmethod
    def calculate_session_end_result_type(
        result_r: float,
        be_threshold_r: float = DEFAULT_BE_THRESHOLD_R,
    ) -> Literal["BE", "SP"]:
        """Determine result type for session-end closes.

        Session ends are special - they're always SP unless very close to breakeven.

        Args:
            result_r: The R-multiple result
            be_threshold_r: R threshold for considering as breakeven

        Returns:
            "BE" if result is near zero, "SP" otherwise
        """
        if FinancialCalc.is_breakeven_by_r(result_r, be_threshold_r):
            return "BE"
        return "SP"
