"""Repository for encrypted credentials."""
from __future__ import annotations

from src.database.database import AppCredential, get_db_session


class CredentialRepository:
    """CRUD for AppCredential rows."""

    def get_credential(self, service: str) -> tuple[str, str] | None:
        with get_db_session() as session:
            row = session.query(AppCredential).filter_by(service=service).first()
            if row:
                return row.username, row.password_encrypted
            return None

    def list_all(self) -> list[dict]:
        with get_db_session() as session:
            rows = session.query(AppCredential).all()
            return [
                {"service": r.service, "username": r.username}
                for r in rows
            ]

    def save_credential(self, service: str, username: str, password_encrypted: str) -> None:
        with get_db_session() as session:
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
