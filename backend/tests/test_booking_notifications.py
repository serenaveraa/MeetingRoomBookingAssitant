from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.config import get_settings
from app.db import get_session_factory, init_db, reset_engine
from app.models import Associate, Booking
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
