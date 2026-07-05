"""Tests for scripts.scenario_management."""

from pathlib import Path

import yaml

from scripts.scenario_management import (
    DiscoveryResultParser,
    FileScenarioRepository,
    ScenarioDiffService,
    ScenarioYamlFormatter,
    derive_name,
    find_insert_position,
)


class TestDiscoveryResultParser:
    def test_no_trade_returns_none_expect(self):
        raw = {"results": [{"trade_pairs": []}]}
        result = DiscoveryResultParser.parse(raw)
        assert result.had_trade is False
        assert result.expect == {"none": True}

    def test_first_trade_parsed(self):
        raw = {
            "results": [
                {
                    "trade_pairs": [
                        [
                            {
                                "entry": 5000.0,
                                "orig_sl": 4990.0,
                                "take_profit": 5050.0,
                                "tf": "5m",
                                "type": "long",
                            }
                        ]
                    ]
                }
            ]
        }
        result = DiscoveryResultParser.parse(raw)
        assert result.had_trade is True
        assert result.expect == {"entry": 5000.0, "sl": 4990.0, "tp": 5050.0}
        assert result.tf == "5m"


class TestScenarioDiffService:
    def test_detects_tf_change(self):
        changes = ScenarioDiffService.compute_diff({}, {"entry": 1}, "5m", "15m")
        assert any("tf:" in c for c in changes)

    def test_detects_entry_change(self):
        old = {"entry": 1.0, "sl": 2.0, "tp": 3.0}
        new = {"entry": 1.5, "sl": 2.0, "tp": 3.0}
        changes = ScenarioDiffService.compute_diff(old, new, "5m", "5m")
        assert any("entry:" in c for c in changes)


class TestScenarioYamlFormatter:
    def test_format_lines_block(self):
        lines = [{"price": 5000.0, "at": "2024-01-01 10:00:00"}]
        block = ScenarioYamlFormatter.format_lines_block(lines)
        assert "price: 5000.00" in block
        assert 'at: "2024-01-01 10:00:00"' in block

    def test_expect_none(self):
        block = ScenarioYamlFormatter.format_expect_block({"none": True})
        assert "none: true" in block

    def test_name_escaping(self):
        sc = {
            "name": 'MNQ - "special" day',
            "tf": "5m",
            "start": "2024-01-01 06:00:00Z",
            "end": "2024-01-01 16:00:00Z",
            "lines": [],
            "expect": {"none": True},
        }
        block = ScenarioYamlFormatter.format_scenario_block(sc)
        assert 'MNQ - \\"special\\" day' in block


class TestPureHelpers:
    def test_derive_name_unique(self):
        assert derive_name("2024-01-01 06:00:00Z", []) == "MNQ - 2024-01-01"

    def test_derive_name_duplicate(self):
        existing = ["MNQ - 2024-01-01"]
        assert derive_name("2024-01-01 06:00:00Z", existing) == "MNQ - 2024-01-01 - 2"

    def test_find_insert_position_sorted(self):
        scenarios = [
            {"start": "2024-01-02 06:00:00Z"},
            {"start": "2024-01-05 06:00:00Z"},
        ]
        assert find_insert_position(scenarios, "2024-01-03 06:00:00Z") == 1
        assert find_insert_position(scenarios, "2024-01-06 06:00:00Z") == 2


class TestFileScenarioRepository:
    def test_insert_at_end(self, tmp_path: Path):
        path = tmp_path / "scenarios.yaml"
        path.write_text("scenarios:\n")
        repo = FileScenarioRepository(path)
        scenario = {
            "name": "MNQ - 2024-01-01",
            "tf": "5m",
            "start": "2024-01-01 06:00:00Z",
            "end": "2024-01-01 16:00:00Z",
            "lines": [{"price": 5000.0, "at": "2024-01-01 10:00:00"}],
            "expect": {"none": True},
        }
        repo.insert(scenario, 0, [])
        doc = yaml.safe_load(path.read_text())
        assert len(doc["scenarios"]) == 1

    def test_replace_existing(self, tmp_path: Path):
        path = tmp_path / "scenarios.yaml"
        original = (
            "scenarios:\n"
            '  - name: "MNQ - 2024-01-01"\n'
            '    pair: "MNQ"\n'
            '    tf: "5m"\n'
            '    start: "2024-01-01 06:00:00Z"\n'
            '    end:   "2024-01-01 16:00:00Z"\n'
            "    lines:\n"
            '      - { price: 5000.00, at: "2024-01-01 10:00:00" }\n'
            "    expect:\n"
            "      none: true\n"
        )
        path.write_text(original)
        repo = FileScenarioRepository(path)
        updated = {
            "name": "MNQ - 2024-01-01",
            "tf": "15m",
            "start": "2024-01-01 06:00:00Z",
            "end": "2024-01-01 16:00:00Z",
            "lines": [{"price": 5000.0, "at": "2024-01-01 10:00:00"}],
            "expect": {"entry": 1.0, "sl": 2.0, "tp": 3.0},
        }
        assert repo.replace("MNQ - 2024-01-01", updated) is True
        doc = yaml.safe_load(path.read_text())
        assert doc["scenarios"][0]["tf"] == "15m"

    def test_replace_missing_returns_false(self, tmp_path: Path):
        path = tmp_path / "scenarios.yaml"
        path.write_text("scenarios:\n")
        repo = FileScenarioRepository(path)
        assert repo.replace("missing", {"name": "missing"}) is False
