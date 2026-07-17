"""Repository for application key-value settings."""
from __future__ import annotations

from src.domain.repositories import SettingsRepository as ISettingsRepository
from src.infrastructure.database.database import AppSetting
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.infrastructure.repositories.base import SQLRepositoryBase, retry_on_sqlite_lock


class SettingsRepository(SQLRepositoryBase, ISettingsRepository):
    """CRUD for generic AppSetting rows."""

    def __init__(self, db: DatabaseProtocol | None = None):
        super().__init__(db)

    def get(self, key: str) -> str | None:
        with self._session() as session:
            row = session.query(AppSetting).filter_by(key=key).first()
            return row.value if row else None

    @retry_on_sqlite_lock()
    def set(self, key: str, value: str, is_sensitive: bool = False) -> None:
        with self._session() as session:
            try:
                row = session.query(AppSetting).filter_by(key=key).first()
                if row:
                    row.value = value
                    row.is_sensitive = 1 if is_sensitive else 0
                else:
                    row = AppSetting(key=key, value=value, is_sensitive=1 if is_sensitive else 0)
                    session.add(row)
                session.commit()
            except Exception:
                session.rollback()
                raise

    def get_all(self) -> dict[str, str]:
        with self._session() as session:
            rows = session.query(AppSetting).all()
            return {r.key: r.value for r in rows}

    @retry_on_sqlite_lock()
    def delete(self, key: str) -> None:
        with self._session() as session:
            try:
                row = session.query(AppSetting).filter_by(key=key).first()
                if row:
                    session.delete(row)
                    session.commit()
            except Exception:
                session.rollback()
                raise
