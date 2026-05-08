"""Tests for src/controllers/lines_controller.py."""

import pytest
from flask import Flask

from src.controllers.lines_controller import LinesController
from tests.fakes import FakeDataSource, FakeLineRepository, FakeLogger


class FakeStrategy:
    def __init__(self):
        self.lines = {}
        self.updated = []
        self.removed = []

    def add_strategy_line(self, line_id, price, creation_timestamp=None):
        self.lines[line_id] = price

    def update_strategy_line(self, line_id, price):
        self.updated.append((line_id, price))

    def remove_strategy_line(self, line_id):
        self.removed.append(line_id)


class FakeBarsLoader:
    def __init__(self, pair="MNQ"):
        self.data_source = FakeDataSource(pair=pair)
        self._last_played_ts = 0
        self.socketio = None


@pytest.fixture
def app_context():
    app = Flask(__name__)
    with app.app_context():
        yield


@pytest.fixture
def controller(app_context):
    line_repo = FakeLineRepository()
    bars_loader = FakeBarsLoader(pair="MNQ")
    strategy = FakeStrategy()
    logger = FakeLogger()
    ctrl = LinesController(
        line_repository=line_repo,
        bars_loader=bars_loader,
        liquidity_strategy=strategy,
        logger=logger,
    )
    return ctrl, line_repo, strategy


class TestLinesControllerListLines:

    def test_empty_list(self, controller):
        ctrl, _, _ = controller
        resp = ctrl.list_lines("MNQ")
        data = resp.get_json()
        assert data == []

    def test_returns_lines(self, controller):
        ctrl, repo, _ = controller
        repo.insert_line("MNQ", 5000.0)
        repo.insert_line("MNQ", 5100.0)
        resp = ctrl.list_lines("MNQ")
        data = resp.get_json()
        assert len(data) == 2
        assert data[0]["pair"] == "MNQ"
        assert data[0]["price"] == 5000.0


class TestLinesControllerAddLine:

    def test_add_line_with_timestamp(self, controller):
        ctrl, repo, strategy = controller
        resp, status = ctrl.add_line("MNQ", 5000.0, creation_timestamp=1700000000.0)
        assert status == 201
        data = resp.get_json()
        assert data["pair"] == "MNQ"
        assert data["price"] == 5000.0
        assert len(repo._store) == 1
        assert len(strategy.lines) == 1

    def test_add_line_without_timestamp(self, controller):
        ctrl, repo, strategy = controller
        resp, status = ctrl.add_line("MNQ", 5000.0)
        assert status == 201
        data = resp.get_json()
        assert data["price"] == 5000.0

    def test_add_line_wrong_pair_aborts(self, controller):
        ctrl, _, _ = controller
        with pytest.raises(Exception):  # abort() raises HTTPException
            ctrl.add_line("ES", 5000.0)


class TestLinesControllerGetLine:

    def test_get_existing(self, controller):
        ctrl, repo, _ = controller
        line = repo.insert_line("MNQ", 5000.0)
        resp, status = ctrl.get_line(line.line_id)
        assert status == 200
        data = resp.get_json()
        assert data["id"] == line.line_id
        assert data["price"] == 5000.0

    def test_get_missing_aborts(self, controller):
        ctrl, _, _ = controller
        with pytest.raises(Exception):
            ctrl.get_line("NONEXISTENT")


class TestLinesControllerUpdateLine:

    def test_update_existing(self, controller):
        ctrl, repo, strategy = controller
        line = repo.insert_line("MNQ", 5000.0)
        resp, status = ctrl.update_line(line.line_id, 5200.0)
        assert status == 200
        data = resp.get_json()
        assert data["price"] == 5200.0
        assert len(strategy.updated) == 1

    def test_update_missing_aborts(self, controller):
        ctrl, _, _ = controller
        with pytest.raises(Exception):
            ctrl.update_line("NONEXISTENT", 5200.0)


class TestLinesControllerDeleteLine:

    def test_delete_existing(self, controller):
        ctrl, repo, strategy = controller
        line = repo.insert_line("MNQ", 5000.0)
        resp, status = ctrl.delete_line(line.line_id)
        assert status == 204
        assert repo.get_line(line.line_id) is None
        assert len(strategy.removed) == 1

    def test_delete_missing_returns_empty(self, controller):
        ctrl, repo, strategy = controller
        # FakeLineRepository.delete_line doesn't raise on missing,
        # so controller won't abort
        resp, status = ctrl.delete_line("NONEXISTENT")
        assert status == 204
