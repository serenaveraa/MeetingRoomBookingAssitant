from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models import (
    ODC_COMMON_ROOM_NAME,
    Booking,
    BookingStatus,
    Room,
)
from app.services.errors import (
    BookingConflictError,
    BookingNotFoundError,
    InvalidBookingWindowError,
)
from app.services.timeutil import ensure_utc


def _validate_window(start_at: datetime, end_at: datetime) -> None:
    if end_at <= start_at:
        raise InvalidBookingWindowError(start_at, end_at)


def _overlap_query(
    room_id: int,
    start_at: datetime,
    end_at: datetime,
    *,
    exclude_booking_id: int | None = None,
) -> Select[tuple[Booking]]:
    """Confirmed bookings that intersect [start_at, end_at) on the room."""
    stmt = select(Booking).where(
        Booking.room_id == room_id,
        Booking.status == BookingStatus.confirmed,
        Booking.start_at < end_at,
        Booking.end_at > start_at,
    )
    if exclude_booking_id is not None:
        stmt = stmt.where(Booking.id != exclude_booking_id)
    return stmt.order_by(Booking.start_at)


def get_odc_room(session: Session) -> Room:
    room = session.scalar(select(Room).where(Room.name == ODC_COMMON_ROOM_NAME))
    if room is None:
        raise RuntimeError(
            f"ODC room '{ODC_COMMON_ROOM_NAME}' is not seeded; call init_db() first"
        )
    return room


def find_conflicts(
    session: Session,
    room_id: int,
    start_at: datetime,
    end_at: datetime,
    *,
    exclude_booking_id: int | None = None,
    for_update: bool = False,
) -> list[Booking]:
    start_utc = ensure_utc(start_at)
    end_utc = ensure_utc(end_at)
    _validate_window(start_utc, end_utc)
    stmt = _overlap_query(
        room_id,
        start_utc,
        end_utc,
        exclude_booking_id=exclude_booking_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return list(session.scalars(stmt).all())


def _raise_if_conflicts(
    conflicts: list[Booking],
    *,
    start_at: datetime,
    end_at: datetime,
) -> None:
    if not conflicts:
        return
    conflict = conflicts[0]
    raise BookingConflictError(
        start_at=start_at,
        end_at=end_at,
        conflicting_booking_id=conflict.id,
        conflicting_start_at=conflict.start_at,
        conflicting_end_at=conflict.end_at,
        conflicting_associate_id=conflict.associate_id,
    )


def create_booking(
    session: Session,
    *,
    associate_id: int,
    start_at: datetime,
    end_at: datetime,
    purpose: str | None = None,
    room_id: int | None = None,
) -> Booking:
    """Create a confirmed booking or fail closed on overlap."""
    start_utc = ensure_utc(start_at)
    end_utc = ensure_utc(end_at)
    _validate_window(start_utc, end_utc)
    resolved_room_id = room_id if room_id is not None else get_odc_room(session).id

    try:
        conflicts = find_conflicts(
            session,
            resolved_room_id,
            start_utc,
            end_utc,
            for_update=True,
        )
        _raise_if_conflicts(conflicts, start_at=start_utc, end_at=end_utc)

        booking = Booking(
            room_id=resolved_room_id,
            associate_id=associate_id,
            purpose=purpose,
            start_at=start_utc,
            end_at=end_utc,
            status=BookingStatus.confirmed,
        )
        session.add(booking)
        session.commit()
        session.refresh(booking)
        return booking
    except Exception:
        session.rollback()
        raise


def update_booking_window(
    session: Session,
    booking_id: int,
    *,
    start_at: datetime,
    end_at: datetime,
) -> Booking:
    """Move a booking to a new window or fail closed on overlap."""
    start_utc = ensure_utc(start_at)
    end_utc = ensure_utc(end_at)
    _validate_window(start_utc, end_utc)

    try:
        booking = session.get(Booking, booking_id, with_for_update=True)
        if booking is None:
            raise BookingNotFoundError(booking_id)

        conflicts = find_conflicts(
            session,
            booking.room_id,
            start_utc,
            end_utc,
            exclude_booking_id=booking.id,
            for_update=True,
        )
        _raise_if_conflicts(conflicts, start_at=start_utc, end_at=end_utc)

        booking.start_at = start_utc
        booking.end_at = end_utc
        session.commit()
        session.refresh(booking)
        return booking
    except Exception:
        session.rollback()
        raise


def cancel_booking(session: Session, booking_id: int) -> Booking:
    """Mark a booking cancelled inside a transaction."""
    try:
        booking = session.get(Booking, booking_id, with_for_update=True)
        if booking is None:
            raise BookingNotFoundError(booking_id)

        booking.status = BookingStatus.cancelled
        session.commit()
        session.refresh(booking)
        return booking
    except Exception:
        session.rollback()
        raise
