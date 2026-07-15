"""Tests for src.controllers.settings_controller."""
import pytest
from flask import Flask
from werkzeug.exceptions import HTTPException

from src.controllers.settings_controller import SettingsController


class FakeSettingsService:
    def __init__(self, settings=None, accounts=None, raise_on_save=False, raise_value_error=False):
        self._settings = settings or {"trading": {"pair": "MNQ"}, "accounts": accounts or []}
        self.raise_on_save = raise_on_save
        self.raise_value_error = raise_value_error
        self.saved_payloads = []
        self.saved_accounts = []
        self.deleted_accounts = []

    def get_full_settings(self):
        return self._settings

    def save_full_settings(self, payload):
        if self.raise_on_save:
            raise RuntimeError("service failure")
        self.saved_payloads.append(payload)

    def save_account(self, payload):
        if self.raise_value_error:
            raise ValueError("bad account")
        if self.raise_on_save:
            raise RuntimeError("service failure")
        self.saved_accounts.append(payload)

    def delete_account(self, name):
        if self.raise_on_save:
            raise RuntimeError("service failure")
        self.deleted_accounts.append(name)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


class TestSettingsController:
    def test_get_settings(self, app, client):
        svc = FakeSettingsService(settings={"trading": {"pair": "ES"}, "accounts": []})
        ctrl = SettingsController(svc)

        with app.test_request_context():
            resp = ctrl.get_settings()

        data, status = resp
        assert status == 200
        assert data.get_json() == {"trading": {"pair": "ES"}, "accounts": []}

    def test_save_settings_success(self, app, client):
        svc = FakeSettingsService()
        ctrl = SettingsController(svc)

        with app.test_request_context(json={"trading": {"pair": "ES"}}):
            resp = ctrl.save_settings()

        data, status = resp
        assert status == 200
        assert data.get_json() == {"success": True}
        assert svc.saved_payloads == [{"trading": {"pair": "ES"}}]

    def test_save_settings_error(self, app, client):
        svc = FakeSettingsService(raise_on_save=True)
        ctrl = SettingsController(svc)

        with app.test_request_context(json={"trading": {"pair": "ES"}}):
            with pytest.raises(HTTPException) as exc_info:
                ctrl.save_settings()

        assert exc_info.value.code == 500
        assert "service failure" in exc_info.value.description

    def test_get_accounts(self, app, client):
        svc = FakeSettingsService(accounts=[{"name": "A1"}])
        ctrl = SettingsController(svc)

        with app.test_request_context():
            resp = ctrl.get_accounts()

        data, status = resp
        assert status == 200
        assert data.get_json() == [{"name": "A1"}]

    def test_save_account_success(self, app, client):
        svc = FakeSettingsService()
        ctrl = SettingsController(svc)

        with app.test_request_context(json={"name": "A1", "risk_usd": 100.0}):
            resp = ctrl.save_account()

        data, status = resp
        assert status == 200
        assert data.get_json() == {"name": "A1"}
        assert svc.saved_accounts == [{"name": "A1", "risk_usd": 100.0}]

    def test_save_account_value_error(self, app, client):
        svc = FakeSettingsService(raise_value_error=True)
        ctrl = SettingsController(svc)

        with app.test_request_context(json={"name": ""}):
            with pytest.raises(HTTPException) as exc_info:
                ctrl.save_account()

        assert exc_info.value.code == 400
        assert "bad account" in exc_info.value.description

    def test_save_account_server_error(self, app, client):
        svc = FakeSettingsService(raise_on_save=True)
        ctrl = SettingsController(svc)

        with app.test_request_context(json={"name": "A1"}):
            with pytest.raises(HTTPException) as exc_info:
                ctrl.save_account()

        assert exc_info.value.code == 500
        assert "service failure" in exc_info.value.description

    def test_delete_account_success(self, app, client):
        svc = FakeSettingsService()
        ctrl = SettingsController(svc)

        with app.test_request_context():
            resp = ctrl.delete_account("A1")

        data, status = resp
        assert status == 204
        assert data == ""
        assert svc.deleted_accounts == ["A1"]

    def test_delete_account_error(self, app, client):
        svc = FakeSettingsService(raise_on_save=True)
        ctrl = SettingsController(svc)

        with app.test_request_context():
            with pytest.raises(HTTPException) as exc_info:
                ctrl.delete_account("A1")

        assert exc_info.value.code == 500
        assert "service failure" in exc_info.value.description
