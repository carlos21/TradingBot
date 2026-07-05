"""Tests for src/database/database.py."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from src.infrastructure.database.database import (
    AppCredential,
    AppSetting,
    DecisionLog,
    Line,
    LineTriggerState,
    NtAccount,
    Trade,
    get_db_session,
    setup_database,
)


@pytest.fixture
def test_db(tmp_path):
    db_path = f"sqlite:///{tmp_path / 'test.db'}"
    setup_database(db_url=db_path)
    return db_path


class TestSetupDatabase:

    def test_creates_tables(self, test_db):
        with get_db_session() as session:
            tables = session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
            table_names = {t[0] for t in tables}
            assert "lines" in table_names
            assert "trades" in table_names
            assert "line_trigger_state" in table_names
            assert "decision_logs" in table_names
            assert "app_settings" in table_names
            assert "nt_accounts" in table_names
            assert "app_credentials" in table_names


class TestLineModel:

    def test_create_line(self, test_db):
        with get_db_session() as session:
            line = Line(line_id="L1", pair="MNQ", price=5000.0, creation_date=datetime.now(timezone.utc))
            session.add(line)
            session.commit()
            result = session.query(Line).filter_by(line_id="L1").first()
            assert result.pair == "MNQ"
            assert result.price == 5000.0


class TestTradeModel:

    def test_create_trade(self, test_db):
        with get_db_session() as session:
            trade = Trade(
                trade_id="T1",
                pair="MNQ",
                trade_type="long",
                entry_price=100.0,
                stop_loss=90.0,
                take_profit=130.0,
                risk=10.0,
                entry_time=datetime.now(timezone.utc),
            )
            session.add(trade)
            session.commit()
            result = session.query(Trade).filter_by(trade_id="T1").first()
            assert result.trade_type == "long"
            assert result.entry_price == 100.0

    def test_trade_with_optional_fields(self, test_db):
        with get_db_session() as session:
            trade = Trade(
                trade_id="T1",
                pair="MNQ",
                trade_type="long",
                entry_price=100.0,
                stop_loss=90.0,
                take_profit=130.0,
                risk=10.0,
                entry_time=datetime.now(timezone.utc),
                risk_dollars=200.0,
                risk_pct=0.2,
                contracts=2.0,
                fees=4.0,
                pnl_usd=396.0,
            )
            session.add(trade)
            session.commit()
            result = session.query(Trade).filter_by(trade_id="T1").first()
            assert result.risk_dollars == 200.0
            assert result.contracts == 2.0


class TestAppSettingModel:

    def test_create_setting(self, test_db):
        with get_db_session() as session:
            setting = AppSetting(key="pair", value="MNQ")
            session.add(setting)
            session.commit()
            result = session.query(AppSetting).filter_by(key="pair").first()
            assert result.value == "MNQ"
            assert result.is_sensitive == 0


class TestNtAccountModel:

    def test_create_account(self, test_db):
        with get_db_session() as session:
            acct = NtAccount(name="TestAccount", risk_usd=100.0, risk_pct=1.0)
            session.add(acct)
            session.commit()
            result = session.query(NtAccount).filter_by(name="TestAccount").first()
            assert result.risk_usd == 100.0
            assert result.risk_pct == 1.0


class TestAppCredentialModel:

    def test_create_credential(self, test_db):
        with get_db_session() as session:
            cred = AppCredential(service="ninjatrader", username="user1", password_encrypted="enc_pass")
            session.add(cred)
            session.commit()
            result = session.query(AppCredential).filter_by(service="ninjatrader").first()
            assert result.username == "user1"


class TestLineTriggerStateModel:

    def test_create_state(self, test_db):
        with get_db_session() as session:
            state = LineTriggerState(
                line_id="L1",
                pair="MNQ",
                state_json={"crosses": 2},
                updated_at=datetime.now(timezone.utc),
            )
            session.add(state)
            session.commit()
            result = session.query(LineTriggerState).filter_by(line_id="L1").first()
            assert result.state_json == {"crosses": 2}


class TestDecisionLogModel:

    def test_create_log(self, test_db):
        with get_db_session() as session:
            log = DecisionLog(
                bar_time=1700000000.0,
                pair="MNQ",
                event="LATCH",
                direction="long",
            )
            session.add(log)
            session.commit()
            result = session.query(DecisionLog).filter_by(event="LATCH").first()
            assert result.pair == "MNQ"
            assert result.direction == "long"
