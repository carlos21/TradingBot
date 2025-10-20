from tests.scenario import Scenario, run_scenario
from src.strategies.liquidity_strategy import LineRemovalMode

def test_short_retest_opens():
    csv_path = ''

    s = Scenario(
        name="short line scheduled at row 3",
        csv_path=csv_path,
        pair="NQ",
        strategy_tf="1m",
        min_sl=10.0,
        max_bounce=40.0,
        extra_sl=0.0,
        line_removal=LineRemovalMode.NEVER,
        lines=[["L1", "short", 19560.0, "2024-07-31T11:12:00-04:00"]]
    )

    r = run_scenario(s)

    assert len(r.trade_opens) == 1
    t = r.trade_opens[0]
    assert t["type"] in ("short", "sell")
    # quick sanity on risk/SL/TP relationships
    assert abs(t["stop_loss"] - (t["entry"] + t["risk"])) < 1e-6
    assert abs(t["take_profit"] - (t["entry"] - 4 * t["risk"])) < 1e-6
