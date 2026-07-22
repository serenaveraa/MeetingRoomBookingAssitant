from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import get_engine, get_session_factory, init_db, reset_engine, seed_odc_room
from app.models import (
    ODC_COMMON_ROOM_NAME,
    Associate,
    Booking,
    BookingStatus,
    Room,
    WaitlistEntry,
)


@pytest.fixture(autouse=True)
def _fresh_sqlite_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield
    reset_engine()
    get_settings.cache_clear()


def test_tables_created_and_room_seeded():
    table_names = set(inspect(get_engine()).get_table_names())
    assert {
        "rooms",
        "associates",
        "bookings",
        "waitlist_entries",
    }.issubset(table_names)

    with get_session_factory()() as session:
        rooms = session.scalars(select(Room)).all()
        assert len(rooms) == 1
        assert rooms[0].name == ODC_COMMON_ROOM_NAME


def test_seed_is_idempotent():
    with get_session_factory()() as session:
        seed_odc_room(session)
        seed_odc_room(session)
        assert len(session.scalars(select(Room)).all()) == 1


def test_booking_check_constraint_rejects_invalid_range():
    with get_session_factory()() as session:
        room = session.scalars(select(Room)).one()
        associate = Associate(name="Ada", email="ada@example.com")
        session.add(associate)
        session.flush()

        start = datetime.now(timezone.utc)
        booking = Booking(
            room_id=room.id,
            associate_id=associate.id,
            purpose="Standup",
            start_at=start,
            end_at=start,
            status=BookingStatus.confirmed,
        )
        session.add(booking)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_booking_and_waitlist_happy_path():
    with get_session_factory()() as session:
        room = session.scalars(select(Room)).one()
        associate = Associate(name="Grace", email="grace@example.com")
        session.add(associate)
        session.flush()

        start = datetime.now(timezone.utc).replace(microsecond=0)
        end = start + timedelta(hours=1)
        booking = Booking(
            room_id=room.id,
            associate_id=associate.id,
            purpose="Client discussion",
            start_at=start,
            end_at=end,
            status=BookingStatus.confirmed,
        )
        wait = WaitlistEntry(
            associate_id=associate.id,
            room_id=room.id,
            desired_start=end,
            desired_end=end + timedelta(hours=1),
        )
        session.add_all([booking, wait])
        session.commit()

        assert len(session.scalars(select(Booking)).all()) == 1
        assert len(session.scalars(select(WaitlistEntry)).all()) == 1
        assert session.scalars(select(Booking)).one().status == BookingStatus.confirmed
