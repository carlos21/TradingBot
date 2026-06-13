"""Position sizing strategies.

This module provides the Strategy pattern for different position sizing approaches:
- Fixed dollar risk
- Tiered stop loss levels
- Percentage of account risk
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.domain.types import Direction
from src.financial_calc import FinancialCalc


@dataclass
class PositionSize:
    """Result of position size calculation."""
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_points: float
    contracts: float
    risk_dollars: float
    risk_pct: float | None
    rr_ratio: float


class PositionSizer(ABC):
    """Abstract base class for position sizing strategies."""

    def __init__(
        self,
        rr_ratio: float = 5.0,
        point_value: float = 5.0,
        account_balance: float = 100000.0,
        risk_per_trade: float | None = None,
        risk_pct_per_trade: float | None = None,
        use_fractional_lots: bool = False,
    ):
        self.rr_ratio = rr_ratio
        self.point_value = point_value
        self.account_balance = account_balance
        self.risk_per_trade = risk_per_trade
        self.risk_pct_per_trade = risk_pct_per_trade
        self.use_fractional_lots = use_fractional_lots

    @abstractmethod
    def calculate(
        self,
        entry_price: float,
        extreme_price: float,
        direction: Direction,
    ) -> PositionSize:
        """Calculate position size for a trade.

        Args:
            entry_price: Proposed entry price
            extreme_price: Extreme price seen during cross (for SL calculation)
            direction: Direction enum (LONG or SHORT)

        Returns:
            PositionSize with all calculated values
        """

    def _calc_contracts(self, risk_per_contract: float) -> float:
        """Calculate number of contracts/lots based on risk budget."""
        if risk_per_contract <= 0:
            return 1.0 if not self.use_fractional_lots else 0.01

        risk_budget = FinancialCalc.risk_budget(
            self.account_balance,
            self.risk_per_trade,
            self.risk_pct_per_trade,
        )

        if risk_budget <= 0:
            return 1.0 if not self.use_fractional_lots else 0.01

        if self.use_fractional_lots:
            return FinancialCalc.lots(risk_budget, risk_per_contract)
        return FinancialCalc.contracts(risk_budget, risk_per_contract)

    def _calc_risk_fields(self, risk_points: float, contracts: float) -> tuple[float, float | None]:
        """Calculate dollar risk and percentage risk."""
        risk_per_contract = risk_points * self.point_value
        risk_dollars = risk_per_contract * contracts
        risk_pct = (risk_dollars / self.account_balance * 100) if self.account_balance > 0 else None
        return risk_dollars, risk_pct


class FixedRiskPositionSizer(PositionSizer):
    """Position sizer with fixed risk amount plus extra space.

    Uses distance to extreme + extra_sl_space as the stop loss distance.
    Optionally respects a max_stop_loss cap.
    """

    def __init__(
        self,
        min_stop_loss: float,
        extra_sl_space: float = 0.0,
        max_stop_loss: float | None = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.min_stop_loss = min_stop_loss
        self.extra_sl_space = extra_sl_space
        self.max_stop_loss = max_stop_loss

    def calculate(
        self,
        entry_price: float,
        extreme_price: float,
        direction: Direction,
    ) -> PositionSize:
        """Calculate position with dynamic risk based on extreme."""
        # Calculate raw risk distance
        if direction.is_long:
            raw_risk = max(entry_price - extreme_price, self.min_stop_loss)
        else:  # SHORT
            raw_risk = max(extreme_price - entry_price, self.min_stop_loss)

        # Add extra space
        eff_risk = raw_risk + self.extra_sl_space

        # Apply max cap if configured
        if self.max_stop_loss and self.max_stop_loss > 0 and eff_risk > self.max_stop_loss:
                eff_risk = self.max_stop_loss

        # Calculate SL and TP
        if direction.is_long:
            stop_loss = entry_price - eff_risk
            take_profit = entry_price + self.rr_ratio * eff_risk
        else:  # SHORT
            stop_loss = entry_price + eff_risk
            take_profit = entry_price - self.rr_ratio * eff_risk

        # Calculate contracts
        risk_per_contract = eff_risk * self.point_value
        contracts = self._calc_contracts(risk_per_contract)
        risk_dollars, risk_pct = self._calc_risk_fields(eff_risk, contracts)

        return PositionSize(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_points=eff_risk,
            contracts=contracts,
            risk_dollars=risk_dollars,
            risk_pct=risk_pct,
            rr_ratio=self.rr_ratio,
        )


class TieredSLPositionSizer(PositionSizer):
    """Position sizer with tiered stop loss levels.

    Picks the smallest SL tier that covers the distance to extreme
    (within tolerance). Falls back to largest tier.
    """

    def __init__(
        self,
        sl_levels: list[float],
        sl_level_tolerance: float = 5.0,
        min_stop_loss: float = 0.0,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.sl_levels = sorted(sl_levels) if sl_levels else []
        self.sl_level_tolerance = sl_level_tolerance
        self.min_stop_loss = min_stop_loss

    def _select_sl_level(self, distance: float) -> float:
        """Pick the smallest SL tier that covers the distance."""
        for level in self.sl_levels:
            if level + self.sl_level_tolerance >= distance:
                return level
        return self.sl_levels[-1] if self.sl_levels else distance

    def calculate(
        self,
        entry_price: float,
        extreme_price: float,
        direction: Direction,
    ) -> PositionSize:
        """Calculate position with tiered SL selection."""
        # Calculate distance to extreme
        if direction.is_long:
            distance = entry_price - extreme_price
        else:  # SHORT
            distance = extreme_price - entry_price

        # Guard against negative distance (price moved against trade before trigger)
        if distance <= 0:
            distance = self.min_stop_loss if self.min_stop_loss > 0 else 1.0

        # Select appropriate tier
        eff_risk = self._select_sl_level(distance)

        # Calculate SL and TP
        if direction.is_long:
            stop_loss = entry_price - eff_risk
            take_profit = entry_price + self.rr_ratio * eff_risk
        else:  # SHORT
            stop_loss = entry_price + eff_risk
            take_profit = entry_price - self.rr_ratio * eff_risk

        # Calculate contracts
        risk_per_contract = eff_risk * self.point_value
        contracts = self._calc_contracts(risk_per_contract)
        risk_dollars, risk_pct = self._calc_risk_fields(eff_risk, contracts)

        return PositionSize(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_points=eff_risk,
            contracts=contracts,
            risk_dollars=risk_dollars,
            risk_pct=risk_pct,
            rr_ratio=self.rr_ratio,
        )


class PercentageRiskPositionSizer(PositionSizer):
    """Position sizer based on percentage of account risk.

    Calculates position size solely based on risk percentage,
    ignoring extreme price (uses fixed stop loss distance).
    """

    def __init__(
        self,
        fixed_stop_loss: float,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.fixed_stop_loss = fixed_stop_loss

    def calculate(
        self,
        entry_price: float,
        _extreme_price: float,  # Ignored for this sizer
        direction: Direction,
    ) -> PositionSize:
        """Calculate position with fixed SL distance."""
        eff_risk = self.fixed_stop_loss

        # Calculate SL and TP
        if direction.is_long:
            stop_loss = entry_price - eff_risk
            take_profit = entry_price + self.rr_ratio * eff_risk
        else:  # SHORT
            stop_loss = entry_price + eff_risk
            take_profit = entry_price - self.rr_ratio * eff_risk

        # Calculate contracts
        risk_per_contract = eff_risk * self.point_value
        contracts = self._calc_contracts(risk_per_contract)
        risk_dollars, risk_pct = self._calc_risk_fields(eff_risk, contracts)

        return PositionSize(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_points=eff_risk,
            contracts=contracts,
            risk_dollars=risk_dollars,
            risk_pct=risk_pct,
            rr_ratio=self.rr_ratio,
        )
