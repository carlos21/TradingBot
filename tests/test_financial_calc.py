"""Comprehensive tests for src/financial_calc.FinancialCalc — Single Source of Truth."""

from src.domain.types import Direction
from src.financial_calc import FinancialCalc


class TestContracts:
    """Tests for contract calculation."""

    def test_contracts_basic(self):
        """Basic contract calculation."""
        assert FinancialCalc.contracts(risk_budget=1000, risk_per_contract=100) == 10

    def test_contracts_rounds_to_nearest(self):
        """Should round to nearest integer."""
        assert FinancialCalc.contracts(risk_budget=1000, risk_per_contract=150) == 7  # 1000/150 = 6.67

    def test_contracts_half_value_rounds_up(self):
        """Should round half-values up (not banker's rounding)."""
        assert FinancialCalc.contracts(risk_budget=250, risk_per_contract=100) == 3  # 2.5 → 3
        assert FinancialCalc.contracts(risk_budget=450, risk_per_contract=100) == 5  # 4.5 → 5

    def test_contracts_minimum_one(self):
        """Should always return at least 1."""
        assert FinancialCalc.contracts(risk_budget=10, risk_per_contract=100) == 1

    def test_contracts_zero_risk_per_contract(self):
        """Should return 1 if risk_per_contract is zero or negative."""
        assert FinancialCalc.contracts(risk_budget=1000, risk_per_contract=0) == 1
        assert FinancialCalc.contracts(risk_budget=1000, risk_per_contract=-10) == 1

    def test_contracts_large_numbers(self):
        """Should scale contracts proportionally for large accounts with no cap."""
        assert FinancialCalc.contracts(risk_budget=1_000_000, risk_per_contract=100) == 10_000

    def test_contracts_small_budget(self):
        """Should handle very small budgets."""
        assert FinancialCalc.contracts(risk_budget=1, risk_per_contract=100) == 1


class TestFees:
    """Tests for fee calculation."""

    def test_fees_basic(self):
        """Basic fee calculation."""
        assert FinancialCalc.fees(contracts=5, fee_per_rt=1.50) == 7.50

    def test_fees_default(self):
        """Should use default fee per contract."""
        result = FinancialCalc.fees(contracts=2)
        assert result == 3.00  # DEFAULT_FEE_PER_RT = 1.50

    def test_fees_zero_contracts(self):
        """Should return 0 for zero contracts."""
        assert FinancialCalc.fees(contracts=0) == 0

    def test_fees_negative_contracts(self):
        """Should return 0 for negative contracts (safety guard)."""
        assert FinancialCalc.fees(contracts=-5) == 0.0

    def test_fees_custom_fee(self):
        """Should accept custom fee per contract."""
        assert FinancialCalc.fees(contracts=3, fee_per_rt=2.00) == 6.00


class TestPnlUsd:
    """Tests for PnL calculation."""

    def test_pnl_usd_win(self):
        """Calculate PnL for a winning trade."""
        # 2 contracts, 2R win, 10pt SL, $2/pt, $3 fees
        result = FinancialCalc.pnl_usd(
            contracts=2, actual_r=2.0, sl_pts=10.0, point_value=2.0, fees=3.0
        )
        # gross = 2 * 2.0 * 10.0 * 2.0 = 80
        # net = 80 - 3 = 77
        assert result == 77.0

    def test_pnl_usd_loss(self):
        """Calculate PnL for a losing trade (-1R)."""
        result = FinancialCalc.pnl_usd(
            contracts=1, actual_r=-1.0, sl_pts=10.0, point_value=2.0, fees=1.50
        )
        # gross = 1 * -1.0 * 10.0 * 2.0 = -20
        # net = -20 - 1.50 = -21.50
        assert result == -21.50

    def test_pnl_usd_breakeven(self):
        """Calculate PnL for breakeven trade (0R)."""
        result = FinancialCalc.pnl_usd(
            contracts=1, actual_r=0.0, sl_pts=10.0, point_value=2.0, fees=1.50
        )
        # gross = 0
        # net = 0 - 1.50 = -1.50 (just fees)
        assert result == -1.50

    def test_pnl_usd_fractional_r(self):
        """Calculate PnL with fractional R result."""
        result = FinancialCalc.pnl_usd(
            contracts=2, actual_r=1.5, sl_pts=20.0, point_value=2.0, fees=3.0
        )
        # gross = 2 * 1.5 * 20.0 * 2.0 = 120
        # net = 120 - 3 = 117
        assert result == 117.0


class TestRiskBudget:
    """Tests for risk budget calculation."""

    def test_risk_budget_fixed(self):
        """Should return fixed risk amount when provided."""
        result = FinancialCalc.risk_budget(
            account_balance=100000, risk_per_trade=500, risk_pct_per_trade=None
        )
        assert result == 500

    def test_risk_budget_percentage(self):
        """Should calculate percentage of balance."""
        result = FinancialCalc.risk_budget(
            account_balance=100000, risk_per_trade=None, risk_pct_per_trade=1.0
        )
        assert result == 1000  # 1% of 100000

    def test_risk_budget_fixed_takes_precedence(self):
        """Fixed risk should take precedence over percentage."""
        result = FinancialCalc.risk_budget(
            account_balance=100000, risk_per_trade=500, risk_pct_per_trade=2.0
        )
        assert result == 500  # Fixed wins

    def test_risk_budget_zero(self):
        """Should return 0 when neither is provided."""
        result = FinancialCalc.risk_budget(
            account_balance=100000, risk_per_trade=None, risk_pct_per_trade=None
        )
        assert result == 0.0

    def test_risk_budget_small_percentage(self):
        """Should handle small percentages."""
        result = FinancialCalc.risk_budget(
            account_balance=100000, risk_per_trade=None, risk_pct_per_trade=0.1
        )
        assert result == 100

    def test_risk_budget_negative_balance(self):
        """Should return 0 when account balance is negative or zero."""
        result = FinancialCalc.risk_budget(
            account_balance=-5000, risk_per_trade=None, risk_pct_per_trade=1.0
        )
        assert result == 0.0
        result = FinancialCalc.risk_budget(
            account_balance=0, risk_per_trade=None, risk_pct_per_trade=1.0
        )
        assert result == 0.0


class TestIsBreakeven:
    """Tests for breakeven detection by price."""

    def test_is_breakeven_exact_match(self):
        """Exact match should be breakeven."""
        assert FinancialCalc.is_breakeven(exit_price=100.0, entry_price=100.0) is True

    def test_is_breakeven_within_threshold(self):
        """Exit within threshold points should be breakeven."""
        assert FinancialCalc.is_breakeven(exit_price=101.5, entry_price=100.0) is True
        assert FinancialCalc.is_breakeven(exit_price=98.5, entry_price=100.0) is True

    def test_is_breakeven_at_threshold_boundary(self):
        """Exit at exactly threshold should NOT be breakeven (strict inequality)."""
        # DEFAULT_BE_THRESHOLD_POINTS = 2.0, so 2.0 difference should be False
        assert FinancialCalc.is_breakeven(exit_price=102.0, entry_price=100.0) is False
        assert FinancialCalc.is_breakeven(exit_price=98.0, entry_price=100.0) is False

    def test_is_breakeven_beyond_threshold(self):
        """Exit beyond threshold should not be breakeven."""
        assert FinancialCalc.is_breakeven(exit_price=103.0, entry_price=100.0) is False

    def test_is_breakeven_custom_threshold(self):
        """Should accept custom threshold."""
        assert FinancialCalc.is_breakeven(
            exit_price=100.5, entry_price=100.0, threshold_points=1.0
        ) is True
        assert FinancialCalc.is_breakeven(
            exit_price=102.0, entry_price=100.0, threshold_points=1.0
        ) is False


class TestIsBreakevenByR:
    """Tests for breakeven detection by R result."""

    def test_is_breakeven_by_r_zero(self):
        """Zero R should be breakeven."""
        assert FinancialCalc.is_breakeven_by_r(result_r=0.0) is True

    def test_is_breakeven_by_r_small_positive(self):
        """Small positive R within threshold should be breakeven."""
        assert FinancialCalc.is_breakeven_by_r(result_r=0.0005) is True

    def test_is_breakeven_by_r_small_negative(self):
        """Small negative R within threshold should be breakeven."""
        assert FinancialCalc.is_breakeven_by_r(result_r=-0.0005) is True

    def test_is_breakeven_by_r_at_threshold(self):
        """R at threshold should NOT be breakeven (strict inequality)."""
        # DEFAULT_BE_THRESHOLD_R = 0.1
        assert FinancialCalc.is_breakeven_by_r(result_r=0.1) is False
        assert FinancialCalc.is_breakeven_by_r(result_r=-0.1) is False

    def test_is_breakeven_by_r_beyond_threshold(self):
        """R beyond threshold should not be breakeven."""
        assert FinancialCalc.is_breakeven_by_r(result_r=0.2) is False
        assert FinancialCalc.is_breakeven_by_r(result_r=-0.2) is False

    def test_is_breakeven_by_r_custom_threshold(self):
        """Should accept custom threshold."""
        assert FinancialCalc.is_breakeven_by_r(
            result_r=0.05, threshold_r=0.1
        ) is True
        assert FinancialCalc.is_breakeven_by_r(
            result_r=0.15, threshold_r=0.1
        ) is False


class TestDetermineResultType:
    """Tests for result type determination (single source of truth)."""

    def test_determine_result_type_breakeven(self):
        """Exit at entry should be BE."""
        result = FinancialCalc.determine_result_type(
            exit_price=100.0, entry_price=100.0,
            stop_loss=90.0, take_profit=110.0
        )
        assert result == "BE"

    def test_determine_result_type_breakeven_near_entry(self):
        """Exit near entry should be BE."""
        result = FinancialCalc.determine_result_type(
            exit_price=101.5, entry_price=100.0,
            stop_loss=90.0, take_profit=110.0
        )
        assert result == "BE"

    def test_determine_result_type_sl_hit(self):
        """Exit at SL should be SL."""
        result = FinancialCalc.determine_result_type(
            exit_price=90.0, entry_price=100.0,
            stop_loss=90.0, take_profit=110.0
        )
        assert result == "SL"

    def test_determine_result_type_sl_near(self):
        """Exit near SL should be SL (within tolerance)."""
        result = FinancialCalc.determine_result_type(
            exit_price=89.7, entry_price=100.0,
            stop_loss=90.0, take_profit=110.0
        )
        assert result == "SL"

    def test_determine_result_type_tp_hit(self):
        """Exit at TP should be TP."""
        result = FinancialCalc.determine_result_type(
            exit_price=110.0, entry_price=100.0,
            stop_loss=90.0, take_profit=110.0
        )
        assert result == "TP"

    def test_determine_result_type_tp_near(self):
        """Exit near TP should be TP (within tolerance)."""
        result = FinancialCalc.determine_result_type(
            exit_price=110.3, entry_price=100.0,
            stop_loss=90.0, take_profit=110.0
        )
        assert result == "TP"

    def test_determine_result_type_sp(self):
        """Exit not near SL or TP should be SP."""
        result = FinancialCalc.determine_result_type(
            exit_price=105.0, entry_price=100.0,
            stop_loss=90.0, take_profit=110.0
        )
        assert result == "SP"

    def test_determine_result_type_be_takes_precedence(self):
        """BE should take precedence over SL (SL moved to entry)."""
        result = FinancialCalc.determine_result_type(
            exit_price=100.0, entry_price=100.0,
            stop_loss=100.0, take_profit=110.0  # SL at entry
        )
        assert result == "BE"

    def test_determine_result_type_short_trade(self):
        """Should work for short trades too."""
        result = FinancialCalc.determine_result_type(
            exit_price=110.0, entry_price=100.0,  # Price went up (loss)
            stop_loss=110.0, take_profit=90.0
        )
        assert result == "SL"

    def test_determine_result_type_custom_tolerance(self):
        """Should accept custom SL/TP tolerance."""
        result = FinancialCalc.determine_result_type(
            exit_price=90.0, entry_price=100.0,
            stop_loss=89.0, take_profit=110.0,
            sl_tp_tolerance=2.0  # Within 2 points counts as SL
        )
        assert result == "SL"


class TestDetermineResultTypeFromHit:
    """Tests for result type determination from hit flags."""

    def test_from_hit_tp(self):
        """TP hit flag should return TP."""
        result = FinancialCalc.determine_result_type_from_hit(
            hit_sl=False, hit_tp=True,
            exit_price=110.0, entry_price=100.0
        )
        assert result == "TP"

    def test_from_hit_sl(self):
        """SL hit flag should return SL."""
        result = FinancialCalc.determine_result_type_from_hit(
            hit_sl=True, hit_tp=False,
            exit_price=90.0, entry_price=100.0
        )
        assert result == "SL"

    def test_from_hit_be_takes_precedence(self):
        """BE should take precedence even if SL flag is set."""
        result = FinancialCalc.determine_result_type_from_hit(
            hit_sl=True, hit_tp=False,
            exit_price=100.0, entry_price=100.0  # At entry (breakeven)
        )
        assert result == "BE"

    def test_from_hit_no_hit(self):
        """No hit flags should return SP."""
        result = FinancialCalc.determine_result_type_from_hit(
            hit_sl=False, hit_tp=False,
            exit_price=105.0, entry_price=100.0
        )
        assert result == "SP"


class TestCalculateRMultiple:
    """Tests for R-multiple calculation."""

    def test_calculate_r_multiple_long_win(self):
        """Long trade with profit."""
        result = FinancialCalc.calculate_r_multiple(
            direction=Direction.LONG, entry_price=100.0, exit_price=110.0, risk_points=10.0
        )
        assert abs(result - 1.0) < 0.001

    def test_calculate_r_multiple_long_loss(self):
        """Long trade with loss."""
        result = FinancialCalc.calculate_r_multiple(
            direction=Direction.LONG, entry_price=100.0, exit_price=90.0, risk_points=10.0
        )
        assert abs(result - (-1.0)) < 0.001

    def test_calculate_r_multiple_short_win(self):
        """Short trade with profit."""
        result = FinancialCalc.calculate_r_multiple(
            direction=Direction.SHORT, entry_price=100.0, exit_price=90.0, risk_points=10.0
        )
        assert abs(result - 1.0) < 0.001

    def test_calculate_r_multiple_short_loss(self):
        """Short trade with loss."""
        result = FinancialCalc.calculate_r_multiple(
            direction=Direction.SHORT, entry_price=100.0, exit_price=110.0, risk_points=10.0
        )
        assert abs(result - (-1.0)) < 0.001

    def test_calculate_r_multiple_from_string(self):
        """Should accept string and convert to Direction."""
        result_long = FinancialCalc.calculate_r_multiple(
            direction=Direction.from_string("long"), entry_price=100.0, exit_price=110.0, risk_points=10.0
        )
        result_short = FinancialCalc.calculate_r_multiple(
            direction=Direction.from_string("short"), entry_price=100.0, exit_price=90.0, risk_points=10.0
        )
        assert abs(result_long - 1.0) < 0.001
        assert abs(result_short - 1.0) < 0.001

    def test_calculate_r_multiple_multiple_r(self):
        """Multiple R win."""
        result = FinancialCalc.calculate_r_multiple(
            direction=Direction.LONG, entry_price=100.0, exit_price=130.0, risk_points=10.0
        )
        assert abs(result - 3.0) < 0.001

    def test_calculate_r_multiple_fractional(self):
        """Fractional R result."""
        result = FinancialCalc.calculate_r_multiple(
            direction=Direction.LONG, entry_price=100.0, exit_price=105.0, risk_points=10.0
        )
        assert abs(result - 0.5) < 0.001

    def test_calculate_r_multiple_zero_risk_returns_zero(self):
        """Zero or negative risk should return 0.0 (invalid risk)."""
        result = FinancialCalc.calculate_r_multiple(
            direction=Direction.LONG, entry_price=100.0, exit_price=110.0, risk_points=0.0
        )
        assert result == 0.0

    def test_calculate_r_multiple_negative_risk_returns_zero(self):
        """Negative risk should return 0.0."""
        result = FinancialCalc.calculate_r_multiple(
            direction=Direction.LONG, entry_price=100.0, exit_price=110.0, risk_points=-5.0
        )
        assert result == 0.0


class TestCalculateCloseMetrics:
    """Tests for unified close metrics calculation."""

    def test_calculate_close_metrics_tp_hit(self):
        """Full close metrics for TP hit."""
        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.LONG,
            entry_price=100.0,
            exit_price=110.0,
            stop_loss=90.0,
            take_profit=110.0,
            risk_points=10.0,
            contracts=2,
            point_value=2.0,
        )
        assert abs(result_r - 1.0) < 0.001
        assert fees == 3.00  # 2 contracts * $1.50
        assert pnl_usd == 37.00  # 2 * 1.0 * 10 * 2 - 3
        assert result_type == "TP"

    def test_calculate_close_metrics_sl_hit(self):
        """Full close metrics for SL hit."""
        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.LONG,
            entry_price=100.0,
            exit_price=90.0,
            stop_loss=90.0,
            take_profit=110.0,
            risk_points=10.0,
            contracts=1,
            point_value=2.0,
        )
        assert abs(result_r - (-1.0)) < 0.001
        assert fees == 1.50
        assert pnl_usd == -21.50  # 1 * -1.0 * 10 * 2 - 1.50
        assert result_type == "SL"

    def test_calculate_close_metrics_breakeven(self):
        """Full close metrics for breakeven (SL at entry)."""
        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.LONG,
            entry_price=100.0,
            exit_price=100.0,  # Exit at entry
            stop_loss=100.0,   # SL at entry
            take_profit=110.0,
            risk_points=10.0,
            contracts=1,
            point_value=2.0,
        )
        assert abs(result_r - 0.0) < 0.001
        assert result_type == "BE"
        assert pnl_usd == -1.50  # Just fees

    def test_calculate_close_metrics_short_trade(self):
        """Full close metrics for short trade."""
        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.SHORT,
            entry_price=100.0,
            exit_price=90.0,  # Price went down (win for short)
            stop_loss=110.0,
            take_profit=90.0,
            risk_points=10.0,
            contracts=2,
            point_value=2.0,
        )
        assert abs(result_r - 1.0) < 0.001
        assert result_type == "TP"

    def test_calculate_close_metrics_session_close(self):
        """Full close metrics for manual/unknown close."""
        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.LONG,
            entry_price=100.0,
            exit_price=105.0,  # In the middle
            stop_loss=90.0,
            take_profit=110.0,
            risk_points=10.0,
            contracts=1,
            point_value=2.0,
        )
        assert abs(result_r - 0.5) < 0.001
        assert result_type == "SP"

    def test_calculate_close_metrics_custom_fees(self):
        """Should accept custom fee per contract."""
        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.LONG,
            entry_price=100.0,
            exit_price=110.0,
            stop_loss=90.0,
            take_profit=110.0,
            risk_points=10.0,
            contracts=2,
            point_value=2.0,
            fee_per_rt=2.00,  # Custom fee
        )
        assert fees == 4.00  # 2 * $2.00


class TestCalculateSessionEndResultType:
    """Tests for session end result type determination."""

    def test_session_end_breakeven_zero(self):
        """Zero R should be BE at session end."""
        result = FinancialCalc.calculate_session_end_result_type(result_r=0.0)
        assert result == "BE"

    def test_session_end_breakeven_small_positive(self):
        """Small positive R should be BE."""
        result = FinancialCalc.calculate_session_end_result_type(result_r=0.0005)
        assert result == "BE"

    def test_session_end_breakeven_small_negative(self):
        """Small negative R should be BE."""
        result = FinancialCalc.calculate_session_end_result_type(result_r=-0.0005)
        assert result == "BE"

    def test_session_end_sp_positive(self):
        """Positive R beyond threshold should be SP."""
        result = FinancialCalc.calculate_session_end_result_type(result_r=0.5)
        assert result == "SP"

    def test_session_end_sp_negative(self):
        """Negative R beyond threshold should be SP."""
        result = FinancialCalc.calculate_session_end_result_type(result_r=-0.5)
        assert result == "SP"

    def test_session_end_custom_threshold(self):
        """Should accept custom threshold."""
        result = FinancialCalc.calculate_session_end_result_type(
            result_r=0.05, be_threshold_r=0.1
        )
        assert result == "BE"


class TestIntegrationScenarios:
    """Integration tests simulating real trade scenarios."""

    def test_long_trade_full_lifecycle_tp(self):
        """Simulate a long trade that hits TP."""
        entry = 21000.0
        sl = 20900.0  # 100 points risk
        tp = 21300.0  # 3R TP (300 points)
        risk = 100.0

        # Trade closes at TP
        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.LONG,
            entry_price=entry,
            exit_price=tp,
            stop_loss=sl,
            take_profit=tp,
            risk_points=risk,
            contracts=2,
            point_value=2.0,  # MNQ
        )

        assert result_type == "TP"
        assert abs(result_r - 3.0) < 0.001  # 3R
        # gross = 2 * 3.0 * 100 * 2 = 1200
        # fees = 2 * 1.5 = 3
        # net = 1200 - 3 = 1197
        assert pnl_usd == 1197.00

    def test_long_trade_full_lifecycle_sl(self):
        """Simulate a long trade that hits SL."""
        entry = 21000.0
        sl = 20900.0
        tp = 21300.0
        risk = 100.0

        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.LONG,
            entry_price=entry,
            exit_price=sl,
            stop_loss=sl,
            take_profit=tp,
            risk_points=risk,
            contracts=2,
            point_value=2.0,
        )

        assert result_type == "SL"
        assert abs(result_r - (-1.0)) < 0.001  # -1R
        # gross = 2 * -1.0 * 100 * 2 = -400
        # fees = 3
        # net = -400 - 3 = -403
        assert pnl_usd == -403.00

    def test_long_trade_full_lifecycle_be(self):
        """Simulate a long trade that moves SL to entry and gets stopped at BE."""
        entry = 21000.0
        sl = 21000.0  # SL moved to entry (breakeven)
        tp = 21300.0
        risk = 100.0

        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.LONG,
            entry_price=entry,
            exit_price=entry,  # Stopped at entry
            stop_loss=sl,
            take_profit=tp,
            risk_points=risk,
            contracts=2,
            point_value=2.0,
        )

        assert result_type == "BE"
        assert abs(result_r - 0.0) < 0.001
        assert pnl_usd == -3.00  # Just fees

    def test_short_trade_full_lifecycle_tp(self):
        """Simulate a short trade that hits TP."""
        entry = 21000.0
        sl = 21100.0  # 100 points risk
        tp = 20700.0  # 3R TP (300 points down)
        risk = 100.0

        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.SHORT,
            entry_price=entry,
            exit_price=tp,
            stop_loss=sl,
            take_profit=tp,
            risk_points=risk,
            contracts=2,
            point_value=2.0,
        )

        assert result_type == "TP"
        assert abs(result_r - 3.0) < 0.001  # 3R
        assert pnl_usd == 1197.00

    def test_short_trade_full_lifecycle_sl(self):
        """Simulate a short trade that hits SL."""
        entry = 21000.0
        sl = 21100.0
        tp = 20700.0
        risk = 100.0

        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.SHORT,
            entry_price=entry,
            exit_price=sl,
            stop_loss=sl,
            take_profit=tp,
            risk_points=risk,
            contracts=2,
            point_value=2.0,
        )

        assert result_type == "SL"
        assert abs(result_r - (-1.0)) < 0.001
        assert pnl_usd == -403.00


class TestConstants:
    """Tests for module constants."""

    def test_default_fee_constant(self):
        """Default fee per RT should be 1.50."""
        assert FinancialCalc.DEFAULT_FEE_PER_RT == 1.50

    def test_default_be_threshold_points(self):
        """Default BE threshold in points should be 2.0."""
        assert FinancialCalc.DEFAULT_BE_THRESHOLD_POINTS == 2.0

    def test_default_be_threshold_r(self):
        """Default BE threshold in R should be 0.1."""
        assert FinancialCalc.DEFAULT_BE_THRESHOLD_R == 0.1


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_very_large_r_result(self):
        """Should handle very large R results."""
        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.LONG,
            entry_price=100.0,
            exit_price=200.0,  # 10R win
            stop_loss=90.0,
            take_profit=110.0,  # TP at 1R, but went past
            risk_points=10.0,
            contracts=1,
            point_value=2.0,
        )
        assert abs(result_r - 10.0) < 0.001
        assert result_type == "SP"  # Not at SL or TP

    def test_very_small_numbers(self):
        """Should handle very small price movements."""
        result_r, fees, pnl_usd, result_type = FinancialCalc.calculate_close_metrics(
            direction=Direction.LONG,
            entry_price=0.0001,
            exit_price=0.00011,
            stop_loss=0.00009,
            take_profit=0.00012,
            risk_points=0.00001,
            contracts=1,
            point_value=1.0,
        )
        assert abs(result_r - 1.0) < 0.001

    def test_extreme_contract_count(self):
        """Should handle extreme contract counts."""
        fees = FinancialCalc.fees(contracts=1000)
        assert fees == 1500.00  # 1000 * 1.50
