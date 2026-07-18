from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.db import get_session_factory, init_db, reset_engine
from app.models import Booking, BookingStatus
from app.services.associates import get_or_create_associate
from app.services.booking import get_odc_room
from app.services.timeutil import get_odc_tz
from app.services.utilization import get_utilization_summary


@pytest.fixture(autouse=True)
def _fresh_sqlite_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("ODC_TIMEZONE", "America/Sao_Paulo")
    reset_engine()
    init_db()
    yield
    reset_engine()


@pytest.fixture
def session():
    with get_session_factory()() as session:
        yield session


def _make_booking(session, *, start: datetime, end: datetime) -> Booking:
    room = get_odc_room(session)
    associate = get_or_create_associate(
        session,
        email=f"{start:%Y%m%d%H%M}@example.com",
        name="Ada",
    )
    booking = Booking(
        room_id=room.id,
        associate_id=associate.id,
        purpose="Standup",
        start_at=start,
        end_at=end,
        status=BookingStatus.confirmed,
    )
    session.add(booking)
    session.commit()
    session.refresh(booking)
    return booking


def test_get_utilization_summary_for_date_range(session):
    tz = get_odc_tz()
    _make_booking(
        session,
        start=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
        end=datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc),
    )
    _make_booking(
        session,
        start=datetime(2026, 7, 15, 13, 0, tzinfo=timezone.utc),
        end=datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc),
    )
    _make_booking(
        session,
        start=datetime(2026, 7, 15, 15, 0, tzinfo=timezone.utc),
        end=datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc),
    )
    _make_booking(
        session,
        start=datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc),
        end=datetime(2026, 7, 15, 17, 0, tzinfo=timezone.utc),
    )
    _make_booking(
        session,
        start=datetime(2026, 7, 16, 11, 0, tzinfo=timezone.utc),
        end=datetime(2026, 7, 16, 12, 30, tzinfo=timezone.utc),
    )

    summary = get_utilization_summary(
        session,
        start_date=date(2026, 7, 15),
        end_date=date(2026, 7, 17),
    )

    assert summary.booking_count == 5
    assert summary.avg_duration_minutes == 66.0
    assert summary.idle_gap_count == 5
    assert [entry["day"] for entry in summary.bookings_per_day] == [
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
    ]
    assert summary.bookings_per_day[0]["booking_count"] == 4
    assert summary.bookings_per_day[1]["booking_count"] == 1
    assert summary.bookings_per_day[2]["booking_count"] == 0
