from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.db import get_session_factory, init_db, reset_engine
from app.models import Associate, BookingStatus
from app.services import (
    BookingConflictError,
    BookingNotFoundError,
    BookingOwnershipError,
    InvalidBookingWindowError,
    cancel_booking,
    create_booking,
    extend_booking,
    update_booking_window,
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


@pytest.fixture
def associate_id() -> int:
    with get_session_factory()() as session:
        associate = Associate(name="Ada", email="ada@example.com")
        session.add(associate)
        session.commit()
        session.refresh(associate)
        return associate.id


@pytest.fixture
def other_associate_id() -> int:
    with get_session_factory()() as session:
        associate = Associate(name="Grace", email="grace@example.com")
        session.add(associate)
        session.commit()
        session.refresh(associate)
        return associate.id


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 16, hour, minute, tzinfo=timezone.utc)


def test_adjacent_bookings_allowed(associate_id: int, other_associate_id: int):
    with get_session_factory()() as session:
        first = create_booking(
            session,
            associate_id=associate_id,
            start_at=_at(10),
            end_at=_at(11),
            purpose="Morning",
        )
        second = create_booking(
            session,
            associate_id=other_associate_id,
            start_at=_at(11),
            end_at=_at(12),
            purpose="Noon",
        )
        assert first.end_at == second.start_at
        assert first.status == BookingStatus.confirmed
        assert second.status == BookingStatus.confirmed


def test_partial_overlap_rejected(associate_id: int, other_associate_id: int):
    with get_session_factory()() as session:
        create_booking(
            session,
            associate_id=associate_id,
            start_at=_at(10),
            end_at=_at(12),
        )
        with pytest.raises(BookingConflictError) as exc:
            create_booking(
                session,
                associate_id=other_associate_id,
                start_at=_at(11),
                end_at=_at(13),
            )
        assert exc.value.conflicting_associate_id == associate_id


def test_contained_identical_and_touching_start_rejected(
    associate_id: int, other_associate_id: int
):
    with get_session_factory()() as session:
        create_booking(
            session,
            associate_id=associate_id,
            start_at=_at(10),
            end_at=_at(12),
        )

        with pytest.raises(BookingConflictError):
            create_booking(
                session,
                associate_id=other_associate_id,
                start_at=_at(10, 30),
                end_at=_at(11, 30),
            )

        with pytest.raises(BookingConflictError):
            create_booking(
                session,
                associate_id=other_associate_id,
                start_at=_at(10),
                end_at=_at(12),
            )

        with pytest.raises(BookingConflictError):
            create_booking(
                session,
                associate_id=other_associate_id,
                start_at=_at(9),
                end_at=_at(10, 30),
            )


def test_cancel_frees_slot_for_new_booking(
    associate_id: int, other_associate_id: int
):
    with get_session_factory()() as session:
        existing = create_booking(
            session,
            associate_id=associate_id,
            start_at=_at(10),
            end_at=_at(11),
        )
        cancelled = cancel_booking(session, existing.id)
        assert cancelled.status == BookingStatus.cancelled

        replacement = create_booking(
            session,
            associate_id=other_associate_id,
            start_at=_at(10),
            end_at=_at(11),
            purpose="Rebooked",
        )
        assert replacement.status == BookingStatus.confirmed


def test_cancel_requires_booking_owner(
    associate_id: int, other_associate_id: int
):
    with get_session_factory()() as session:
        existing = create_booking(
            session,
            associate_id=associate_id,
            start_at=_at(10),
            end_at=_at(11),
        )
        with pytest.raises(BookingOwnershipError):
            cancel_booking(session, existing.id, associate_id=other_associate_id)


def test_extend_requires_booking_owner(
    associate_id: int, other_associate_id: int
):
    with get_session_factory()() as session:
        existing = create_booking(
            session,
            associate_id=associate_id,
            start_at=_at(10),
            end_at=_at(11),
        )
        with pytest.raises(BookingOwnershipError):
            extend_booking(
                session,
                existing.id,
                minutes=15,
                associate_id=other_associate_id,
            )


def test_update_window_conflict_and_success(
    associate_id: int, other_associate_id: int
):
    with get_session_factory()() as session:
        first = create_booking(
            session,
            associate_id=associate_id,
            start_at=_at(10),
            end_at=_at(11),
        )
        second = create_booking(
            session,
            associate_id=other_associate_id,
            start_at=_at(12),
            end_at=_at(13),
        )

        with pytest.raises(BookingConflictError):
            update_booking_window(
                session,
                second.id,
                start_at=_at(10, 30),
                end_at=_at(11, 30),
            )

        moved = update_booking_window(
            session,
            second.id,
            start_at=_at(14),
            end_at=_at(15),
        )
        assert moved.start_at.replace(tzinfo=timezone.utc) == _at(14)
        assert moved.end_at.replace(tzinfo=timezone.utc) == _at(15)
        assert first.id != moved.id


def test_invalid_window_and_missing_booking(associate_id: int):
    with get_session_factory()() as session:
        start = _at(10)
        with pytest.raises(InvalidBookingWindowError):
            create_booking(
                session,
                associate_id=associate_id,
                start_at=start,
                end_at=start,
            )

        with pytest.raises(InvalidBookingWindowError):
            create_booking(
                session,
                associate_id=associate_id,
                start_at=start,
                end_at=start - timedelta(minutes=30),
            )

        with pytest.raises(BookingNotFoundError):
            cancel_booking(session, booking_id=99999)
