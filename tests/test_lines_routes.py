"""Tests for src/routes/lines_routes.py."""

import pytest
from flask import Flask

from src.routes.lines_routes import register_lines_routes
from tests.fakes import FakeLineRepository, FakeLogger


class FakeLinesController:
    def __init__(self):
        self.repo = FakeLineRepository()

    def list_lines(self, pair):
        lines = self.repo.list_lines(pair)
        return {"lines": [l.line_id for l in lines]}

    def add_line(self, pair, price, creation_timestamp=None):
        line = self.repo.insert_line(pair, price)
        return {"id": line.line_id, "pair": pair, "price": price}, 201

    def get_line(self, line_id):
        line = self.repo.get_line(line_id)
        if line:
            return {"id": line.line_id, "price": line.price}, 200
        return {"error": "not found"}, 404

    def update_line(self, line_id, price):
        line = self.repo.update_line(line_id, price)
        return {"id": line.line_id, "price": line.price}, 200

    def delete_line(self, line_id):
        self.repo.delete_line(line_id)
        return "", 204


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    ctrl = FakeLinesController()
    logger = FakeLogger()
    register_lines_routes(app, ctrl, logger)
    return app


class TestLinesRoutes:

    def test_list_lines(self, app):
        with app.test_client() as client:
            resp = client.get("/api/lines?pair=MNQ")
            assert resp.status_code == 200

    def test_add_line(self, app):
        with app.test_client() as client:
            resp = client.post("/api/lines", json={"pair": "MNQ", "price": 5000.0})
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["pair"] == "MNQ"
            assert data["price"] == 5000.0

    def test_add_line_missing_pair(self, app):
        with app.test_client() as client:
            resp = client.post("/api/lines", json={"price": 5000.0})
            assert resp.status_code == 400

    def test_get_line(self, app):
        with app.test_client() as client:
            resp = client.post("/api/lines", json={"pair": "MNQ", "price": 5000.0})
            line_id = resp.get_json()["id"]
            resp = client.get(f"/api/lines/{line_id}")
            assert resp.status_code == 200

    def test_update_line(self, app):
        with app.test_client() as client:
            resp = client.post("/api/lines", json={"pair": "MNQ", "price": 5000.0})
            line_id = resp.get_json()["id"]
            resp = client.put(f"/api/lines/{line_id}", json={"price": 5100.0})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["price"] == 5100.0

    def test_delete_line(self, app):
        with app.test_client() as client:
            resp = client.post("/api/lines", json={"pair": "MNQ", "price": 5000.0})
            line_id = resp.get_json()["id"]
            resp = client.delete(f"/api/lines/{line_id}")
            assert resp.status_code == 204
