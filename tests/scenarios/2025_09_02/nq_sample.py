from pathlib import Path
from tests.scenario import Scenario

SCENARIO = Scenario(
    name="NQ — retest + wick reversal",
    csv_path=Path(__file__).with_name("data.csv"),
    strategy_tf="5m",  # optional; defaults to 5m
    lines=[
        ["L1", "short",  18258.25, "2024-05-07 07:05:00"]
    ],
)