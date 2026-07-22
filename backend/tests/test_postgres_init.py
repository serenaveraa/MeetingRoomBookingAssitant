"""Integration checks against Docker Postgres (default DATABASE_URL)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import get_engine, get_session_factory, init_db, reset_engine, seed_odc_room
from app.models import Associate, Booking, BookingStatus, ODC_COMMON_ROOM_NAME, Room
from app.services import booking as booking_service
from app.services.booking import create_booking
from app.services.errors import BookingConflictError


pytestmark = pytest.mark.postgres


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


def _window(hour: int) -> tuple[datetime, datetime]:
    start = datetime(2030, 1, 2, hour, tzinfo=timezone.utc)
    return start, start + timedelta(hours=1)


def _fixtures(session):
    room = Room(name=f"Postgres test room {uuid4()}")
    other_room = Room(name=f"Postgres other room {uuid4()}")
    associate = Associate(name="Postgres Test", email=f"{uuid4()}@example.com")
    session.add_all([room, other_room, associate])
    session.flush()
    return room, other_room, associate


def test_postgres_exclusion_rejects_overlapping_confirmed_booking():
    with get_session_factory()() as session:
        room, _, associate = _fixtures(session)
        start, end = _window(10)
        session.add(
            Booking(
                room_id=room.id,
                associate_id=associate.id,
                start_at=start,
                end_at=end,
                status=BookingStatus.confirmed,
            )
        )
        session.commit()
        session.add(
            Booking(
                room_id=room.id,
                associate_id=associate.id,
                start_at=start + timedelta(minutes=30),
                end_at=end + timedelta(minutes=30),
                status=BookingStatus.confirmed,
            )
        )
        with pytest.raises(IntegrityError) as error:
            session.commit()
        session.rollback()
        assert error.value.orig.diag.constraint_name == "ex_bookings_confirmed_room_time"


def test_postgres_exclusion_scopes_confirmed_and_room():
    with get_session_factory()() as session:
        room, other_room, associate = _fixtures(session)
        start, end = _window(12)
        session.add_all(
            [
                Booking(
                    room_id=room.id,
                    associate_id=associate.id,
                    start_at=start,
                    end_at=end,
                    status=BookingStatus.cancelled,
                ),
                Booking(
                    room_id=room.id,
                    associate_id=associate.id,
                    start_at=start,
                    end_at=end,
                    status=BookingStatus.confirmed,
                ),
                Booking(
                    room_id=other_room.id,
                    associate_id=associate.id,
                    start_at=start,
                    end_at=end,
                    status=BookingStatus.confirmed,
                ),
                Booking(
                    room_id=room.id,
                    associate_id=associate.id,
                    start_at=end,
                    end_at=end + timedelta(hours=1),
                    status=BookingStatus.confirmed,
                ),
            ]
        )
        session.commit()


def test_postgres_exclusion_violation_maps_to_booking_conflict(monkeypatch):
    with get_session_factory()() as session:
        room, _, associate = _fixtures(session)
        start, end = _window(15)
        session.add(
            Booking(
                room_id=room.id,
                associate_id=associate.id,
                start_at=start,
                end_at=end,
                status=BookingStatus.confirmed,
            )
        )
        session.commit()

        monkeypatch.setattr(booking_service, "find_conflicts", lambda *args, **kwargs: [])
        with pytest.raises(BookingConflictError, match="already booked"):
            create_booking(
                session,
                associate_id=associate.id,
                room_id=room.id,
                start_at=start + timedelta(minutes=15),
                end_at=end + timedelta(minutes=15),
            )
