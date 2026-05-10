"""Tests for PnL calculation fixes in mode_pnl.py and report_utils.py."""

import pytest

from scripts.mode_pnl import per_trade_sim, per_trade_futures, per_trade_cfd
from scripts.report_utils import compute_trade_pnl


# ---------------------------------------------------------------------------
# per_trade_sim — Bug 1: BE trades with negative R must not lose full risk
# ---------------------------------------------------------------------------

def make_trade(entry=20000.0, sl=19990.0, risk=10.0, contracts=2):
    return {
        "entry": entry,
        "entry_price": entry,
        "stop_loss": sl,
        "orig_sl": sl,
        "risk": risk,
        "contracts": contracts,
    }


def make_close(result, result_type="TP", pnl_usd=None, fees=None):
    close = {"result": result, "result_type": result_type}
    if pnl_usd is not None:
        close["pnl_usd"] = pnl_usd
    if fees is not None:
        close["fees"] = fees
    return close


class TestPerTradeSim:
    """Sim mode: fixed risk per trade."""

    def test_clear_win(self):
        trade = make_trade()
        close = make_close(result=4.0, result_type="TP")
        usd, pct, actual_r, comm, outcome = per_trade_sim(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None
        )
        assert usd == 4_000.0
        assert outcome == "win"

    def test_clear_loss(self):
        trade = make_trade()
        close = make_close(result=-1.0, result_type="SL")
        usd, pct, actual_r, comm, outcome = per_trade_sim(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None
        )
        assert usd == -1_000.0
        assert outcome == "loss"

    def test_breakeven_positive_r(self):
        """BE trade with small positive R should show small positive PnL."""
        trade = make_trade()
        close = make_close(result=0.05, result_type="BE")
        usd, pct, actual_r, comm, outcome = per_trade_sim(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None
        )
        assert usd == pytest.approx(50.0)
        assert outcome == "be"

    def test_breakeven_negative_r(self):
        """BE trade with small negative R should show small negative PnL, NOT full loss."""
        trade = make_trade()
        close = make_close(result=-0.05, result_type="BE")
        usd, pct, actual_r, comm, outcome = per_trade_sim(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None
        )
        assert usd == pytest.approx(-50.0)
        assert outcome == "be"

    def test_breakeven_zero_r(self):
        trade = make_trade()
        close = make_close(result=0.0, result_type="BE")
        usd, pct, actual_r, comm, outcome = per_trade_sim(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None
        )
        assert usd == 0.0
        assert outcome == "be"

    def test_open_trade_returns_zero(self):
        trade = make_trade()
        usd, pct, actual_r, comm, outcome = per_trade_sim(
            trade, close=None, balance=100_000, risk_usd_fix=1_000, risk_pct=None
        )
        assert usd == 0.0
        assert outcome == "open"

    def test_risk_pct_compounding(self):
        """When risk_pct is set, risk scales with balance."""
        trade = make_trade()
        close = make_close(result=2.0, result_type="TP")
        usd, pct, actual_r, comm, outcome = per_trade_sim(
            trade, close, balance=200_000, risk_usd_fix=1_000, risk_pct=1.0
        )
        # 1% of 200k = 2k risk; 2R win = 4k
        assert usd == 4_000.0


# ---------------------------------------------------------------------------
# per_trade_futures — Bug 2: must use stored contracts, not recompute
# ---------------------------------------------------------------------------

class TestPerTradeFutures:
    """Real futures mode: integer contracts + fees."""

    def test_uses_stored_contracts(self):
        """If trade dict has 'contracts', use it even if balance suggests more."""
        trade = make_trade(entry=20000.0, sl=19990.0, risk=10.0, contracts=2)
        close = make_close(result=4.0, result_type="TP")
        # Running balance is huge — would suggest many more contracts if recomputed
        usd, pct, actual_r, comm, outcome = per_trade_futures(
            trade, close, balance=1_000_000, risk_usd_fix=1_000, risk_pct=None,
            nq_pv=2.0, fee_per_rt=1.50,
        )
        # 2 contracts * 4R * 10 pts * $2/pt - $3 fees = 160 - 3 = 157
        assert usd == pytest.approx(157.0)
        assert outcome == "win"

    def test_falls_back_to_computed_contracts_when_none(self):
        trade = make_trade(entry=20000.0, sl=19990.0, risk=10.0)
        del trade["contracts"]  # no stored size
        close = make_close(result=1.0, result_type="TP")
        usd, pct, actual_r, comm, outcome = per_trade_futures(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None,
            nq_pv=2.0, fee_per_rt=1.50,
        )
        # risk_per_contract = 10 * 2 = 20; contracts = 1000 / 20 = 50
        # gross = 50 * 1 * 10 * 2 = 1000; fees = 50 * 1.5 = 75; net = 925
        assert usd == pytest.approx(925.0)
        assert outcome == "win"

    def test_uses_stored_pnl_when_available(self):
        trade = make_trade(contracts=5)
        close = make_close(result=4.0, result_type="TP", pnl_usd=999.0, fees=7.5)
        usd, pct, actual_r, comm, outcome = per_trade_futures(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None,
        )
        assert usd == 999.0
        assert comm == 7.5
        assert outcome == "win"

    def test_be_with_stored_pnl(self):
        trade = make_trade(contracts=3)
        close = make_close(result=-0.02, result_type="BE", pnl_usd=-4.5, fees=4.5)
        usd, pct, actual_r, comm, outcome = per_trade_futures(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None,
        )
        assert usd == -4.5
        assert outcome == "be"

    def test_sp_with_stored_contracts(self):
        trade = make_trade(entry=20000.0, sl=19990.0, risk=10.0, contracts=2)
        close = make_close(result=0.5, result_type="SP")
        usd, pct, actual_r, comm, outcome = per_trade_futures(
            trade, close, balance=1_000_000, risk_usd_fix=1_000, risk_pct=None,
            nq_pv=2.0, fee_per_rt=1.50,
        )
        # 2 contracts * 0.5R * 10 pts * $2/pt - $3 fees = 20 - 3 = 17
        assert usd == pytest.approx(17.0)
        assert outcome == "sp"

    def test_sp_uses_stored_pnl(self):
        trade = make_trade(contracts=2)
        close = make_close(result=0.5, result_type="SP", pnl_usd=42.0, fees=3.0)
        usd, pct, actual_r, comm, outcome = per_trade_futures(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None,
        )
        assert usd == 42.0
        assert comm == 3.0
        assert outcome == "sp"


# ---------------------------------------------------------------------------
# per_trade_cfd — Bug 3: must use stored pnl_usd and stored contracts
# ---------------------------------------------------------------------------

class TestPerTradeCfd:
    """Real CFD mode: fractional lots + spread + commission."""

    def test_uses_stored_pnl_when_available(self):
        trade = make_trade(contracts=1.5)
        close = make_close(result=2.0, result_type="TP", pnl_usd=888.0, fees=12.0)
        usd, pct, actual_r, comm, outcome = per_trade_cfd(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None,
            nq_pv=2.0, cfd_spread=1.5, cfd_commission=5.0,
        )
        assert usd == 888.0
        assert comm == 12.0
        assert outcome == "win"

    def test_uses_stored_lots(self):
        """If trade dict has 'contracts', use it as lots."""
        trade = make_trade(entry=20000.0, sl=19990.0, risk=10.0, contracts=2.5)
        close = make_close(result=3.0, result_type="TP")
        usd, pct, actual_r, comm, outcome = per_trade_cfd(
            trade, close, balance=1_000_000, risk_usd_fix=1_000, risk_pct=None,
            nq_pv=2.0, cfd_spread=1.5, cfd_commission=5.0,
        )
        # lots = 2.5, sl_pts = 10, nq_pv = 2
        # gross = 2.5 * 3 * 10 * 2 = 150
        # spread = 2.5 * 1.5 * 2 = 7.5
        # commission = 2.5 * 5 = 12.5
        # total_cost = 20, net = 130
        assert usd == pytest.approx(130.0)
        assert outcome == "win"

    def test_falls_back_to_computed_lots(self):
        trade = make_trade(entry=20000.0, sl=19990.0, risk=10.0)
        del trade["contracts"]
        close = make_close(result=1.0, result_type="TP")
        usd, pct, actual_r, comm, outcome = per_trade_cfd(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None,
            nq_pv=2.0, cfd_spread=1.5, cfd_commission=5.0,
        )
        # risk_per_lot = 10 * 2 = 20; lots = 1000 / 20 = 50
        # gross = 50 * 1 * 10 * 2 = 1000
        # spread = 50 * 1.5 * 2 = 150
        # commission = 50 * 5 = 250
        # total_cost = 400, net = 600
        assert usd == pytest.approx(600.0)

    def test_sp_uses_stored_pnl(self):
        trade = make_trade(contracts=2.0)
        close = make_close(result=0.3, result_type="SP", pnl_usd=55.0, fees=15.0)
        usd, pct, actual_r, comm, outcome = per_trade_cfd(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None,
        )
        assert usd == 55.0
        assert comm == 15.0
        assert outcome == "sp"

    def test_be_uses_stored_pnl(self):
        trade = make_trade(contracts=2.0)
        close = make_close(result=-0.01, result_type="BE", pnl_usd=-10.0, fees=10.0)
        usd, pct, actual_r, comm, outcome = per_trade_cfd(
            trade, close, balance=100_000, risk_usd_fix=1_000, risk_pct=None,
        )
        assert usd == -10.0
        assert comm == 10.0
        assert outcome == "be"


# ---------------------------------------------------------------------------
# report_utils.compute_trade_pnl — Bugs 4 & 5
# ---------------------------------------------------------------------------

class TestComputeTradePnl:
    """HTML-report PnL calculator."""

    def test_sim_be_negative_r(self):
        """SP trade in sim mode with small negative R should not lose full risk."""
        trade = make_trade()
        close = make_close(result=-0.05, result_type="SP")
        result = compute_trade_pnl(
            trade, close, account=100_000, risk=1_000,
            mode="sim", nq_pv=2.0, fee_per_rt=1.50, be_threshold=0.1,
        )
        assert result["usd"] == pytest.approx(-50.0)
        assert result["outcome"] == "sp"

    def test_sim_be_positive_r(self):
        trade = make_trade()
        close = make_close(result=0.05, result_type="BE")
        result = compute_trade_pnl(
            trade, close, account=100_000, risk=1_000,
            mode="sim", nq_pv=2.0, fee_per_rt=1.50, be_threshold=0.1,
        )
        assert result["usd"] == pytest.approx(50.0)
        assert result["outcome"] == "be"

    def test_outcome_classifies_negative_r_within_be_as_be(self):
        """Bug 5: actual_r = -0.05 must be 'be', not 'loss'."""
        trade = make_trade()
        close = make_close(result=-0.05, result_type="TP")
        result = compute_trade_pnl(
            trade, close, account=100_000, risk=1_000,
            mode="sim", nq_pv=2.0, fee_per_rt=1.50, be_threshold=0.1,
        )
        assert result["outcome"] == "be"

    def test_uses_stored_contracts(self):
        trade = make_trade(entry=20000.0, sl=19990.0, risk=10.0, contracts=2)
        close = make_close(result=4.0, result_type="TP")
        result = compute_trade_pnl(
            trade, close, account=100_000, risk=1_000,
            mode="real_futures", nq_pv=2.0, fee_per_rt=1.50, be_threshold=0.1,
            balance=1_000_000,  # huge balance would suggest more contracts
        )
        # 2 contracts * 4R * 10 pts * $2 - $3 = 157
        assert result["usd"] == pytest.approx(157.0)

    def test_uses_stored_pnl(self):
        trade = make_trade(contracts=5)
        close = make_close(result=4.0, result_type="TP", pnl_usd=1234.0, fees=7.5)
        result = compute_trade_pnl(
            trade, close, account=100_000, risk=1_000,
            mode="real_futures", nq_pv=2.0, fee_per_rt=1.50, be_threshold=0.1,
        )
        assert result["usd"] == 1234.0
        assert result["commission"] == 7.5

    def test_risk_pct_adjusts_risk(self):
        trade = make_trade()
        close = make_close(result=2.0, result_type="TP")
        result = compute_trade_pnl(
            trade, close, account=100_000, risk=1_000,
            mode="sim", nq_pv=2.0, fee_per_rt=1.50, be_threshold=0.1,
            risk_pct=2.0, balance=200_000,
        )
        # risk = 2% of 200k = 4k; 2R win = 8k
        assert result["usd"] == 8_000.0

    def test_real_partial_loss_scales_by_actual_r(self):
        """Bug: real-mode fallback hard-coded full loss for any actual_r <= 0.
        A -0.5R loss should lose half the risk, not full risk."""
        trade = make_trade(entry=20000.0, sl=19985.0, risk=15.0, contracts=10)
        close = make_close(result=-0.5, result_type="SL")
        result = compute_trade_pnl(
            trade, close, account=100_000, risk=1_000,
            mode="real_futures", nq_pv=2.0, fee_per_rt=1.50, be_threshold=0.1,
        )
        # 10 contracts * -0.5R * 15 pts * $2 - $15 fees = -150 - 15 = -165
        assert result["usd"] == pytest.approx(-165.0)
        assert result["outcome"] == "loss"

    def test_real_be_without_stored_pnl_is_small_loss_not_full_loss(self):
        """Bug: BE trades without stored_pnl fell into the full-loss branch.
        A breakeven at -0.02R should be a tiny loss, not -1R."""
        trade = make_trade(entry=20000.0, sl=19985.0, risk=15.0, contracts=10)
        close = make_close(result=-0.02, result_type="BE")
        result = compute_trade_pnl(
            trade, close, account=100_000, risk=1_000,
            mode="real_futures", nq_pv=2.0, fee_per_rt=1.50, be_threshold=0.1,
        )
        # 10 contracts * -0.02R * 15 pts * $2 - $15 fees = -6 - 15 = -21
        assert result["usd"] == pytest.approx(-21.0)
        assert result["outcome"] == "be"

    def test_contracts_fallback_uses_round_half_up(self):
        """Bug: fallback contract calculation used banker's rounding.
        With risk=300, sl_pts_price=100, nq_pv=2  =>  300/200 = 1.5 → should be 2."""
        trade = {"entry": 20000.0, "stop_loss": 19900.0, "risk": 100.0}
        close = make_close(result=1.0, result_type="TP")
        result = compute_trade_pnl(
            trade, close, account=100_000, risk=300,
            mode="real_futures", nq_pv=2.0, fee_per_rt=1.50, be_threshold=0.1,
        )
        # 2 contracts * 1R * 100 pts * $2 - $3 = 397
        assert result["usd"] == pytest.approx(397.0)
