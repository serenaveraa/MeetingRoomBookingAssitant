from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.config import Settings, get_settings
from app.db import get_session_factory, init_db, reset_engine
from app.models import Associate, Booking, BookingStatus, Room
from app.scheduler import create_scheduler
from app.scheduler.vacate_reminders import candidate_bookings, run_vacate_reminder_job


@pytest.fixture(autouse=True)
def _fresh_sqlite_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        reminder_lead_minutes=15,
        reminder_poll_interval_seconds=60,
        reminder_back_to_back_tolerance_minutes=2,
    )


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 16, hour, minute, tzinfo=timezone.utc)


def _booking(session, associate, room, start, end, **kwargs) -> Booking:
    booking = Booking(
        associate_id=associate.id,
        room_id=room.id,
        start_at=start,
        end_at=end,
        status=BookingStatus.confirmed,
        **kwargs,
    )
    session.add(booking)
    session.commit()
    session.refresh(booking)
    return booking


def _setup(session):
    associate = Associate(name="Ada", email="ada@example.com")
    follower = Associate(name="Grace", email="grace@example.com")
    room = session.query(Room).first()
    session.add_all([associate, follower])
    session.commit()
    return associate, follower, room


def test_candidate_query_requires_unreminded_back_to_back_booking(settings):
    with get_session_factory()() as session:
        ada, grace, room = _setup(session)
        adjacent = _booking(session, ada, room, _at(10), _at(11))
        _booking(session, grace, room, _at(11), _at(12))
        other_room = Room(name="Other room")
        third_room = Room(name="Third room")
        session.add_all([other_room, third_room])
        session.commit()
        gapped = _booking(session, ada, other_room, _at(10), _at(11))
        _booking(session, grace, other_room, _at(11, 3), _at(12, 3))
        reminded = _booking(session, ada, third_room, _at(10), _at(11), reminder_sent_at=_at(10, 45))
        _booking(session, grace, third_room, _at(11), _at(12))

        candidates = candidate_bookings(session, now=_at(10, 45), settings=settings)
        assert [booking.id for booking in candidates] == [adjacent.id]
        assert gapped.id not in [booking.id for booking in candidates]
        assert reminded.id not in [booking.id for booking in candidates]


@pytest.mark.parametrize("offset, expected", [(0, True), (2, True), (3, False)])
def test_back_to_back_tolerance_boundaries(settings, offset, expected):
    with get_session_factory()() as session:
        ada, grace, room = _setup(session)
        current = _booking(session, ada, room, _at(10), _at(11))
        _booking(session, grace, room, _at(11) + timedelta(minutes=offset), _at(12) + timedelta(minutes=offset))
        ids = [booking.id for booking in candidate_bookings(session, now=_at(10, 45), settings=settings)]
        assert (current.id in ids) is expected


def test_job_dispatches_once_and_marks_after_success(settings):
    notifier = MagicMock()
    notifier.send_vacate_reminder.return_value = ["brevo", "teams"]
    with get_session_factory()() as session:
        ada, grace, room = _setup(session)
        booking = _booking(session, ada, room, _at(10), _at(11))
        _booking(session, grace, room, _at(11), _at(12))

    assert run_vacate_reminder_job(now=_at(10, 45), settings=settings, notification_service=notifier) == 1
    assert run_vacate_reminder_job(now=_at(10, 46), settings=settings, notification_service=notifier) == 0
    notifier.send_vacate_reminder.assert_called_once()
    with get_session_factory()() as session:
        assert session.get(Booking, booking.id).reminder_sent_at is not None


def test_job_releases_claim_when_delivery_fails(settings):
    notifier = MagicMock()
    notifier.send_vacate_reminder.side_effect = RuntimeError("provider unavailable")
    with get_session_factory()() as session:
        ada, grace, room = _setup(session)
        booking = _booking(session, ada, room, _at(10), _at(11))
        _booking(session, grace, room, _at(11), _at(12))

    assert run_vacate_reminder_job(now=_at(10, 45), settings=settings, notification_service=notifier) == 0
    with get_session_factory()() as session:
        refreshed = session.get(Booking, booking.id)
        assert refreshed.reminder_sent_at is None
        assert refreshed.reminder_claimed_at is None


def test_job_skips_a_booking_owned_by_another_worker(settings):
    notifier = MagicMock()
    with get_session_factory()() as session:
        ada, grace, room = _setup(session)
        booking = _booking(session, ada, room, _at(10), _at(11), reminder_claimed_at=_at(10, 45))
        _booking(session, grace, room, _at(11), _at(12))

    assert run_vacate_reminder_job(now=_at(10, 45), settings=settings, notification_service=notifier) == 0
    notifier.send_vacate_reminder.assert_not_called()
    with get_session_factory()() as session:
        assert session.get(Booking, booking.id).reminder_sent_at is None


def test_scheduler_registers_vacate_job_at_configured_cadence(settings):
    scheduler = create_scheduler(settings)
    scheduler.start(paused=True)
    try:
        job = scheduler.get_job("vacate-reminders")
        assert job is not None
        assert job.trigger.interval == timedelta(seconds=60)
    finally:
        scheduler.shutdown(wait=False)
