"""Repository for application key-value settings."""
from __future__ import annotations

from src.database.database import AppSetting, get_db_session


class SettingsRepository:
    """CRUD for generic AppSetting rows."""

    def get(self, key: str) -> str | None:
        with get_db_session() as session:
            row = session.query(AppSetting).filter_by(key=key).first()
            return row.value if row else None

    def set(self, key: str, value: str, is_sensitive: bool = False) -> None:
        with get_db_session() as session:
            row = session.query(AppSetting).filter_by(key=key).first()
            if row:
                row.value = value
                row.is_sensitive = 1 if is_sensitive else 0
            else:
                row = AppSetting(key=key, value=value, is_sensitive=1 if is_sensitive else 0)
                session.add(row)
            session.commit()

    def get_all(self) -> dict[str, str]:
        with get_db_session() as session:
            rows = session.query(AppSetting).all()
            return {r.key: r.value for r in rows}

    def delete(self, key: str) -> None:
        with get_db_session() as session:
            row = session.query(AppSetting).filter_by(key=key).first()
            if row:
                session.delete(row)
                session.commit()
