"""Tests for src/dbexception.py."""

from src.dbexception import DBException, DBNotFoundException


class TestDBExceptions:

    def test_db_exception_is_exception(self):
        with pytest.raises(DBException):
            raise DBException("test error")

    def test_db_not_found_is_exception(self):
        ex = DBNotFoundException("not found")
        assert isinstance(ex, Exception)
        assert str(ex) == "not found"

    def test_message_preserved(self):
        ex = DBException("hello")
        assert str(ex) == "hello"


import pytest
