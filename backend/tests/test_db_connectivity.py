from __future__ import annotations

from app.db.session import check_database_connection


def test_database_connection_helper_returns_status() -> None:
    status, message = check_database_connection()
    assert isinstance(status, bool)
    assert isinstance(message, str)
