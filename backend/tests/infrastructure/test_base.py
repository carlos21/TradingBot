"""Tests for src/infrastructure/repositories/base.py."""

import pytest

from sqlalchemy import text

from src.infrastructure.database.database import setup_database
from src.infrastructure.database.database_protocol import SQLiteDatabase
from src.infrastructure.repositories.base import SQLRepositoryBase


class TestSQLRepositoryBase:

    def test_session_with_injected_db(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'base_test.db'}"
        db = SQLiteDatabase(db_url=db_path)
        repo = SQLRepositoryBase(db=db)

        with repo._session() as session:
            # Should be able to use the session against an empty DB
            result = session.execute(text("SELECT 1"))
            assert result.scalar() == 1

    def test_session_without_db_raises_when_global_uninitialized(self):
        from src.infrastructure.database import database as db_module

        original_db = db_module.db
        try:
            db_module.db = None
            repo = SQLRepositoryBase(db=None)
            with pytest.raises(RuntimeError, match="Database not initialized"):
                with repo._session() as session:
                    session.execute("SELECT 1")
        finally:
            db_module.db = original_db

    def test_engine_with_injected_db(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'base_test.db'}"
        db = SQLiteDatabase(db_url=db_path)
        repo = SQLRepositoryBase(db=db)
        engine = repo._engine()
        assert engine is db.get_engine()

    def test_engine_without_db_uses_global_db(self, tmp_path):
        db_path = f"sqlite:///{tmp_path / 'base_test_global.db'}"
        setup_database(db_url=db_path)
        repo = SQLRepositoryBase(db=None)
        engine = repo._engine()
        assert engine is not None

    def test_engine_without_db_raises_when_global_uninitialized(self):
        from src.infrastructure.database import database as db_module

        original_db = db_module.db
        try:
            db_module.db = None
            repo = SQLRepositoryBase(db=None)
            with pytest.raises(RuntimeError, match="Database not initialized"):
                repo._engine()
        finally:
            db_module.db = original_db
