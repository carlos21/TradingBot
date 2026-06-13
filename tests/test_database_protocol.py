"""Tests for src/database/database_protocol.py."""

import pytest
from sqlalchemy import Column, Integer, String, text

from src.infrastructure.database.database_protocol import (
    Base,
    DatabaseProtocol,
    SQLiteDatabase,
    get_database,
)


class TestDatabaseProtocol:

    def test_is_abstract(self):
        assert hasattr(DatabaseProtocol, "__abstractmethods__")

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            DatabaseProtocol()


class TestSQLiteDatabase:

    def test_init_default_url(self):
        db = SQLiteDatabase()
        assert db.db_url == "sqlite:///./database.db"
        assert db.engine is not None

    def test_init_custom_url(self):
        db = SQLiteDatabase("sqlite:///:memory:")
        assert db.db_url == "sqlite:///:memory:"

    def test_get_engine(self):
        db = SQLiteDatabase("sqlite:///:memory:")
        engine = db.get_engine()
        assert engine is not None

    def test_get_session(self):
        db = SQLiteDatabase("sqlite:///:memory:")
        session = db.get_session()
        assert session is not None
        session.close()

    def test_create_tables(self):
        db = SQLiteDatabase("sqlite:///:memory:")

        class TestModel(Base):
            __tablename__ = "test_table"
            id = Column(Integer, primary_key=True)
            name = Column(String(50))

        db.create_tables(Base)
        # Verify table was created by trying to query it
        session = db.get_session()
        result = session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'"))
        assert result.scalar() == "test_table"
        session.close()


class TestGetDatabase:

    def test_returns_sqlite_database(self):
        db = get_database("sqlite:///:memory:")
        assert isinstance(db, SQLiteDatabase)
