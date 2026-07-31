"""Unit checks for database URL / engine configuration."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.db import get_engine, reset_engine


@pytest.fixture(autouse=True)
def _reset_engine(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    get_settings.cache_clear()
    reset_engine()
    yield
    reset_engine()
    get_settings.cache_clear()


def test_database_url_preserves_sslmode_for_postgres():
    """RDS secrets include ?sslmode=require; SQLAlchemy must not strip it."""
    get_settings.cache_clear()
    reset_engine()
    url = "postgresql+psycopg://user:secret@db.example.com:5432/meeting_room?sslmode=require"
    import os

    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    reset_engine()

    engine = get_engine()
    assert engine.url.drivername.startswith("postgresql")
    assert engine.url.query.get("sslmode") == "require"

    reset_engine()
    get_settings.cache_clear()
