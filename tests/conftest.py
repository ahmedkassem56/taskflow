"""Shared fixtures — DESIGN.md §6 tests/.

Every test runs against an isolated temp DB (env TODO_DB -> tmp_path),
never todo.db. `make_client` builds a fresh app instance so the restart /
persistence test can open a second "process" on the same file.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    """Absolute path of this test's isolated SQLite file."""
    return str(tmp_path / "todo.db")


@pytest.fixture
def make_client(db_path, monkeypatch):
    """Factory: enter a fresh app instance pointed at the same temp DB.

    Usage: with make_client() as client: ...  (repeatable for restarts)
    """
    monkeypatch.setenv("TODO_DB", db_path)

    from app.main import create_app

    def _make():
        return TestClient(create_app())

    return _make


@pytest.fixture
def client(make_client):
    with make_client() as c:
        yield c


@pytest.fixture
def sql(db_path):
    """Raw connection to the same temp DB (schema initialized), for crafting
    rows the API cannot (e.g. fixed same-second created_at timestamps)."""
    from app.db import connect, init_schema

    conn = connect(db_path)
    init_schema(conn)
    yield conn
    conn.close()
