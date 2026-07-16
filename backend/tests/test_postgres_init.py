"""Integration checks against Docker Postgres (default DATABASE_URL)."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select, text

from app.config import get_settings
from app.db import get_engine, get_session_factory, init_db, reset_engine, seed_odc_room
from app.models import ODC_COMMON_ROOM_NAME, Room


@pytest.fixture(autouse=True)
def _postgres_db(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://odc:odc@localhost:5432/meeting_room",
    )
    get_settings.cache_clear()
    reset_engine()
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        reset_engine()
        get_settings.cache_clear()
        pytest.skip(f"Docker Postgres not reachable: {exc}")

    init_db()
    yield
    reset_engine()
    get_settings.cache_clear()


def test_postgres_tables_and_seed():
    table_names = set(inspect(get_engine()).get_table_names())
    assert {
        "rooms",
        "associates",
        "bookings",
        "waitlist_entries",
    }.issubset(table_names)

    with get_session_factory()() as session:
        rooms = session.scalars(select(Room)).all()
        assert len(rooms) >= 1
        assert any(r.name == ODC_COMMON_ROOM_NAME for r in rooms)


def test_postgres_seed_idempotent():
    with get_session_factory()() as session:
        seed_odc_room(session)
        seed_odc_room(session)
        count = len(
            session.scalars(
                select(Room).where(Room.name == ODC_COMMON_ROOM_NAME)
            ).all()
        )
        assert count == 1
