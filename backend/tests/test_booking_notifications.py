from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.db import get_session_factory, init_db, reset_engine
from app.models import Associate, Booking, Room, WaitlistEntry
from app.api.bookings import post_booking
from app.api.schemas import CreateBookingIn
from app.services import booking as booking_module
from app.services.booking import cancel_booking, create_booking, extend_booking
from app.services.notification_dispatch import dispatch_booking_notification


@pytest.fixture(autouse=True)
def _fresh_sqlite_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield
    reset_engine()
    get_settings.cache_clear()


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 16, hour, minute, tzinfo=timezone.utc)


def _associate_id() -> int:
    with get_session_factory()() as session:
        associate = Associate(name="Ada", email="ada@example.com")
        session.add(associate)
        session.commit()
        return associate.id


def test_create_dispatches_confirmation_after_commit(monkeypatch):
    notifier = MagicMock()
    notifier.send_booking_confirmation.return_value = ["brevo"]
    monkeypatch.setattr(
        booking_module,
        "dispatch_booking_notification",
        lambda event, booking, **kwargs: dispatch_booking_notification(
            event, booking, notification_service=notifier, **kwargs
        ),
    )
    with get_session_factory()() as session:
        booking = create_booking(
            session, associate_id=_associate_id(), start_at=_at(10), end_at=_at(11)
        )
        assert session.get(Booking, booking.id) is not None
    notifier.send_booking_confirmation.assert_called_once_with(booking)


def test_extend_dispatches_new_and_previous_end_after_commit(monkeypatch):
    notifier = MagicMock()
    notifier.send_booking_confirmation.return_value = ["brevo"]
    notifier.send_booking_extended.return_value = ["brevo"]
    monkeypatch.setattr(
        booking_module,
        "dispatch_booking_notification",
        lambda event, booking, **kwargs: dispatch_booking_notification(
            event, booking, notification_service=notifier, **kwargs
        ),
    )
    with get_session_factory()() as session:
        booking = create_booking(
            session, associate_id=_associate_id(), start_at=_at(10), end_at=_at(11)
        )
        notifier.reset_mock()
        updated = extend_booking(session, booking.id, minutes=30)
        assert session.get(Booking, booking.id).end_at.replace(tzinfo=timezone.utc) == _at(11, 30)
    notifier.send_booking_extended.assert_called_once_with(
        updated, previous_end_at=_at(11)
    )


def test_cancel_dispatches_cancellation_after_commit(monkeypatch):
    notifier = MagicMock()
    notifier.send_booking_confirmation.return_value = ["brevo"]
    notifier.send_booking_cancelled.return_value = ["brevo"]
    monkeypatch.setattr(
        booking_module,
        "dispatch_booking_notification",
        lambda event, booking, **kwargs: dispatch_booking_notification(
            event, booking, notification_service=notifier, **kwargs
        ),
    )
    with get_session_factory()() as session:
        booking = create_booking(
            session, associate_id=_associate_id(), start_at=_at(10), end_at=_at(11)
        )
        notifier.reset_mock()
        cancelled = cancel_booking(session, booking.id)
        assert session.get(Booking, booking.id).status.value == "cancelled"
    notifier.send_booking_cancelled.assert_called_once_with(cancelled)


def test_notification_failure_is_logged_and_does_not_rollback_create(monkeypatch, caplog):
    notifier = MagicMock()
    notifier.send_booking_confirmation.side_effect = RuntimeError("Brevo timed out")
    monkeypatch.setattr(
        booking_module,
        "dispatch_booking_notification",
        lambda event, booking, **kwargs: dispatch_booking_notification(
            event, booking, notification_service=notifier, **kwargs
        ),
    )
    with get_session_factory()() as session:
        booking = create_booking(
            session, associate_id=_associate_id(), start_at=_at(10), end_at=_at(11)
        )
        assert session.get(Booking, booking.id) is not None

    assert "Booking notification failed" in caplog.text
    assert f"booking_id={booking.id}" in caplog.text
    assert "booking.confirmed" in caplog.text
    assert "ada@example.com" in caplog.text


def test_create_endpoint_commits_when_notification_provider_fails(monkeypatch):
    notifier = MagicMock()
    notifier.send_booking_confirmation.side_effect = RuntimeError("provider timeout")
    monkeypatch.setattr(
        booking_module,
        "dispatch_booking_notification",
        lambda event, booking, **kwargs: dispatch_booking_notification(
            event, booking, notification_service=notifier, **kwargs
        ),
    )
    with get_session_factory()() as session:
        response = post_booking(
            CreateBookingIn(
                associate_email="ada@example.com",
                associate_name="Ada",
                start_at=_at(10),
                end_at=_at(11),
            ),
            db=session,
        )
        assert session.get(Booking, response.id) is not None


def test_cancel_notifies_all_fully_contained_waitlist_entries_and_deduplicates(monkeypatch):
    notifier = MagicMock()
    notifier.send_booking_confirmation.return_value = ["brevo"]
    notifier.send_booking_cancelled.return_value = ["brevo"]
    notifier.send_waitlist_slot_available.return_value = ["brevo"]
    monkeypatch.setattr(
        booking_module,
        "dispatch_booking_notification",
        lambda event, booking, **kwargs: dispatch_booking_notification(
            event, booking, notification_service=notifier, **kwargs
        ),
    )
    with get_session_factory()() as session:
        room = session.query(Room).one()
        first = Associate(name="Grace", email="grace@example.com")
        second = Associate(name="Lin", email="lin@example.com")
        third = Associate(name="Nope", email="nope@example.com")
        session.add_all([first, second, third])
        session.flush()
        booking = create_booking(
            session, associate_id=_associate_id(), start_at=_at(10), end_at=_at(12), room_id=room.id
        )
        session.add_all([
            WaitlistEntry(associate_id=first.id, room_id=room.id, desired_start=_at(10), desired_end=_at(12)),
            WaitlistEntry(associate_id=second.id, room_id=room.id, desired_start=_at(10, 30), desired_end=_at(11, 30)),
            WaitlistEntry(associate_id=third.id, room_id=room.id, desired_start=_at(9), desired_end=_at(11)),
        ])
        session.commit()
        notifier.reset_mock()

        cancel_booking(session, booking.id)
        entries = session.query(WaitlistEntry).order_by(WaitlistEntry.id).all()
        assert entries[0].notified_at is not None
        assert entries[1].notified_at is not None
        assert entries[2].notified_at is None
        assert notifier.send_waitlist_slot_available.call_count == 2

        notifier.reset_mock()
        dispatch_booking_notification(
            "booking.cancelled", booking, notification_service=notifier
        )
        notifier.send_waitlist_slot_available.assert_not_called()


def test_waitlist_notification_failure_does_not_change_cancelled_booking(monkeypatch, caplog):
    notifier = MagicMock()
    notifier.send_booking_confirmation.return_value = ["brevo"]
    notifier.send_booking_cancelled.return_value = ["brevo"]
    notifier.send_waitlist_slot_available.side_effect = RuntimeError("Brevo down")
    monkeypatch.setattr(
        booking_module,
        "dispatch_booking_notification",
        lambda event, booking, **kwargs: dispatch_booking_notification(
            event, booking, notification_service=notifier, **kwargs
        ),
    )
    with get_session_factory()() as session:
        associate_id = _associate_id()
        booking = create_booking(session, associate_id=associate_id, start_at=_at(10), end_at=_at(11))
        room = session.query(Room).one()
        wait = WaitlistEntry(
            associate_id=associate_id,
            room_id=room.id,
            desired_start=_at(10),
            desired_end=_at(11),
        )
        session.add(wait)
        session.commit()
        cancel_booking(session, booking.id)
        assert session.get(Booking, booking.id).status.value == "cancelled"
        assert session.get(WaitlistEntry, wait.id).notified_at is None
    assert "Waitlist notification failed" in caplog.text
