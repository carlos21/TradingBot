"""Repository for encrypted credentials."""
from __future__ import annotations

from src.domain.repositories import CredentialRepository as ICredentialRepository
from src.infrastructure.database.database import AppCredential
from src.infrastructure.database.database_protocol import DatabaseProtocol
from src.infrastructure.repositories.base import SQLRepositoryBase


class CredentialRepository(SQLRepositoryBase, ICredentialRepository):
    """CRUD for AppCredential rows."""

    def __init__(self, db: DatabaseProtocol | None = None):
        super().__init__(db)

    def get_credential(self, service: str) -> tuple[str, str] | None:
        with self._session() as session:
            row = session.query(AppCredential).filter_by(service=service).first()
            if row:
                return row.username, row.password_encrypted
            return None

    def list_all(self) -> list[dict]:
        with self._session() as session:
            rows = session.query(AppCredential).all()
            return [
                {"service": r.service, "username": r.username}
                for r in rows
            ]

    def save_credential(self, service: str, username: str, password_encrypted: str) -> None:
        with self._session() as session:
            try:
                row = session.query(AppCredential).filter_by(service=service, username=username).first()
                if row:
                    row.password_encrypted = password_encrypted
                else:
                    row = AppCredential(
                        service=service,
                        username=username,
                        password_encrypted=password_encrypted,
                    )
                    session.add(row)
                session.commit()
            except Exception:
                session.rollback()
                raise

    def delete_credential(self, service: str) -> None:
        with self._session() as session:
            try:
                row = session.query(AppCredential).filter_by(service=service).first()
                if row:
                    session.delete(row)
                    session.commit()
            except Exception:
                session.rollback()
                raise
