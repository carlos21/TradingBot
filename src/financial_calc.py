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

    @staticmethod
    def contracts(risk_budget: float, risk_per_contract: float) -> int:
        """Number of contracts to trade, matching NinjaTrader's logic.

        Uses round-half-up (not Python's banker's rounding) so that
        e.g. 2.5 → 3 and 4.5 → 5, ensuring exact-half budgets don't
        silently under-size.

        Returns 0 when the risk budget or risk-per-contract is not positive,
        so invalid configurations do not open a position.
        """
        if risk_budget <= 0 or risk_per_contract <= 0:
            return 0
        raw = risk_budget / risk_per_contract
        # Round-half-up for positive numbers
        rounded = int(raw + 0.5)
        return max(1, rounded)

    @staticmethod
    def lots(risk_budget: float, risk_per_lot: float) -> float:
        """Fractional lots for CFD mode (no rounding to integer).

        Returns 0 when the risk budget or risk-per-lot is not positive,
        so invalid configurations do not open a position.
        """
        if risk_budget <= 0 or risk_per_lot <= 0:
            return 0.0
        return max(0.01, risk_budget / risk_per_lot)

    @staticmethod
    def fees(contracts: float, fee_per_rt: float = DEFAULT_FEE_PER_RT) -> float:
        """Total round-trip fees for a trade."""
        if contracts < 0:
            return 0.0
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
    def r_multiple_from_pnl(
        pnl_usd: float,
        fees: float,
        risk_points: float,
        contracts: float,
        point_value: float,
    ) -> float:
        """Recover the R-multiple result from a known net PnL.

        Useful when the broker reports the realized net PnL and we still
        want an R-based result for analytics.
            gross = pnl_usd + fees
            r     = gross / (contracts * risk_points * point_value)
        """
        denominator = contracts * risk_points * point_value
        if denominator == 0:
            return 0.0
        return (pnl_usd + fees) / denominator

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
            if account_balance <= 0:
                return 0.0
            return account_balance * risk_pct_per_trade / 100.0
        return 0.0

    @staticmethod
    def risk_fields(
        risk_points: float,
        contracts: float,
        point_value: float,
        account_balance: float,
    ) -> tuple[float, float | None]:
        """Calculate dollar risk and percentage risk for a position.

        Returns:
            Tuple of (risk_dollars, risk_pct). ``risk_pct`` is ``None`` when
            ``account_balance`` is not positive.
        """
        risk_per_contract = risk_points * point_value
        risk_dollars = risk_per_contract * contracts
        risk_pct = (
            (risk_dollars / account_balance * 100)
            if account_balance > 0 else None
        )
        return risk_dollars, risk_pct

    @staticmethod
    def size_position(
        risk_points: float,
        point_value: float,
        account_balance: float,
        risk_per_trade: float | None,
        risk_pct_per_trade: float | None,
        use_fractional_lots: bool = False,
    ) -> tuple[float, float, float | None]:
        """Calculate contracts/lots, dollar risk, and percentage risk.

        This is the single source of truth for sizing a position from a
        risk budget. All higher-level sizers should delegate here.

        Returns:
            Tuple of (contracts, risk_dollars, risk_pct). ``risk_pct`` is
            ``None`` when ``account_balance`` is not positive.
        """
        risk_per_contract = risk_points * point_value
        risk_budget = FinancialCalc.risk_budget(
            account_balance, risk_per_trade, risk_pct_per_trade
        )
        if use_fractional_lots:
            contracts = FinancialCalc.lots(risk_budget, risk_per_contract)
        else:
            contracts = FinancialCalc.contracts(risk_budget, risk_per_contract)
        risk_dollars, risk_pct = FinancialCalc.risk_fields(
            risk_points, contracts, point_value, account_balance
        )
        return contracts, risk_dollars, risk_pct

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
        return abs(exit_price - entry_price) < abs(threshold_points)

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
        return abs(result_r) < abs(threshold_r)

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
        near_entry = FinancialCalc.is_breakeven(
            exit_price, entry_price, be_threshold_points
        )

        # Check SL/TP proximity, but keep BE precedence when the stop/target is
        # right at entry (a breakeven stop). For tight stops outside the BE
        # threshold this prevents misclassifying a true SL/TP as BE.
        hit_sl = stop_loss is not None and abs(exit_price - stop_loss) < sl_tp_tolerance
        hit_tp = take_profit is not None and abs(exit_price - take_profit) < sl_tp_tolerance

        if hit_sl and not near_entry:
            return "SL"
        if hit_tp and not near_entry:
            return "TP"

        # Check BE (SL might be at entry for breakeven trades)
        if near_entry:
            return "BE"

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
        near_entry = FinancialCalc.is_breakeven(
            exit_price, entry_price, be_threshold_points
        )

        if hit_sl and not near_entry:
            return "SL"
        if hit_tp and not near_entry:
            return "TP"

        # Check BE only after confirming it was not an SL/TP hit outside entry.
        if near_entry:
            return "BE"

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
            return 0.0

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
