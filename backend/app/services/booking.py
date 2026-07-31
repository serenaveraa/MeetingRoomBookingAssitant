from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models import (
    ODC_COMMON_ROOM_NAME,
    Booking,
    BookingStatus,
    Room,
)
from app.services.errors import (
    BookingConflictError,
    BookingNotFoundError,
    BookingOwnershipError,
    InvalidBookingWindowError,
    MyMeetingNotFoundError,
)
from app.services.notification_dispatch import dispatch_booking_notification
from app.services.schedule import assert_no_weekend_booking
from app.services.timeutil import as_utc, ensure_utc

logger = logging.getLogger(__name__)


def _validate_window(start_at: datetime, end_at: datetime) -> None:
    if end_at <= start_at:
        raise InvalidBookingWindowError(start_at, end_at)
    assert_no_weekend_booking(start_at, end_at)


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
        # Lock only bookings rows — joinedload + FOR UPDATE breaks on Postgres
        # (outer join to associates).
        stmt = stmt.with_for_update(of=Booking)
    stmt = stmt.options(joinedload(Booking.associate))
    return list(session.scalars(stmt).unique().all())


def _raise_if_conflicts(
    conflicts: list[Booking],
    *,
    start_at: datetime,
    end_at: datetime,
) -> None:
    if not conflicts:
        return
    conflict = conflicts[0]
    associate_name = conflict.associate.name if conflict.associate else None
    raise BookingConflictError(
        start_at=start_at,
        end_at=end_at,
        conflicting_booking_id=conflict.id,
        conflicting_start_at=as_utc(conflict.start_at),
        conflicting_end_at=as_utc(conflict.end_at),
        conflicting_associate_id=conflict.associate_id,
        conflicting_associate_name=associate_name,
    )


def _is_booking_exclusion_violation(exc: IntegrityError) -> bool:
    original = exc.orig
    constraint_name = getattr(getattr(original, "diag", None), "constraint_name", None)
    return constraint_name == "ex_bookings_confirmed_room_time"


def _raise_database_conflict(
    session: Session,
    *,
    room_id: int,
    start_at: datetime,
    end_at: datetime,
) -> None:
    conflicts = find_conflicts(session, room_id, start_at, end_at)
    if conflicts:
        _raise_if_conflicts(conflicts, start_at=start_at, end_at=end_at)
    raise BookingConflictError(
        start_at=start_at,
        end_at=end_at,
        conflicting_booking_id=None,
        conflicting_start_at=start_at,
        conflicting_end_at=end_at,
        message="Room is already booked for the requested time.",
    )


def ensure_booking_owner(
    session: Session,
    booking_id: int,
    associate_id: int,
) -> Booking:
    booking = session.get(Booking, booking_id, with_for_update=True)
    if booking is None:
        raise BookingNotFoundError(booking_id)
    if booking.associate_id != associate_id:
        raise BookingOwnershipError(booking_id, associate_id)
    return booking


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
    except IntegrityError as exc:
        session.rollback()
        if not _is_booking_exclusion_violation(exc):
            raise
        _raise_database_conflict(
            session,
            room_id=resolved_room_id,
            start_at=start_utc,
            end_at=end_utc,
        )
    except Exception:
        session.rollback()
        raise
    dispatch_booking_notification("booking.confirmed", booking)
    logger.info(
        "booking.outcome action=create booking_id=%s associate_id=%s room_id=%s result=success",
        booking.id,
        booking.associate_id,
        booking.room_id,
    )
    return booking


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
    except IntegrityError as exc:
        session.rollback()
        if not _is_booking_exclusion_violation(exc):
            raise
        _raise_database_conflict(
            session,
            room_id=booking.room_id,
            start_at=start_utc,
            end_at=end_utc,
        )
    except Exception:
        session.rollback()
        raise
    return booking


def cancel_booking(
    session: Session,
    booking_id: int,
    *,
    associate_id: int | None = None,
) -> Booking:
    """Mark a booking cancelled inside a transaction."""
    try:
        if associate_id is not None:
            ensure_booking_owner(session, booking_id, associate_id)
        booking = session.get(Booking, booking_id, with_for_update=True)
        if booking is None:
            raise BookingNotFoundError(booking_id)

        booking.status = BookingStatus.cancelled
        session.commit()
        session.refresh(booking)
    except Exception as exc:
        session.rollback()
        logger.warning(
            "booking.outcome action=cancel booking_id=%s associate_id=%s result=failure error=%s",
            booking_id,
            associate_id,
            type(exc).__name__,
        )
        raise
    dispatch_booking_notification("booking.cancelled", booking)
    logger.info(
        "booking.outcome action=cancel booking_id=%s associate_id=%s room_id=%s result=success",
        booking.id,
        booking.associate_id,
        booking.room_id,
    )
    return booking


def extend_booking(
    session: Session,
    booking_id: int,
    *,
    minutes: int,
    associate_id: int | None = None,
) -> Booking:
    """Extend a confirmed booking's end time by minutes, or fail closed on conflict."""
    if minutes <= 0:
        raise ValueError("minutes must be a positive integer")

    if associate_id is not None:
        ensure_booking_owner(session, booking_id, associate_id)
    booking = session.get(Booking, booking_id)
    if booking is None:
        raise BookingNotFoundError(booking_id)
    if booking.status != BookingStatus.confirmed:
        raise BookingNotFoundError(booking_id)

    previous_end_at = as_utc(booking.end_at)
    new_end = previous_end_at + timedelta(minutes=minutes)
    try:
        updated = update_booking_window(
            session,
            booking_id,
            start_at=as_utc(booking.start_at),
            end_at=new_end,
        )
        dispatch_booking_notification(
            "booking.extended", updated, previous_end_at=previous_end_at
        )
    except Exception as exc:
        logger.warning(
            "booking.outcome action=extend booking_id=%s associate_id=%s minutes=%s result=failure error=%s",
            booking_id,
            associate_id,
            minutes,
            type(exc).__name__,
        )
        raise
    logger.info(
        "booking.outcome action=extend booking_id=%s associate_id=%s room_id=%s minutes=%s result=success",
        updated.id,
        updated.associate_id,
        updated.room_id,
        minutes,
    )
    return updated


def list_bookings(
    session: Session,
    *,
    start_at: datetime,
    end_at: datetime,
    status: BookingStatus | None = BookingStatus.confirmed,
    room_id: int | None = None,
) -> list[Booking]:
    start_utc = ensure_utc(start_at)
    end_utc = ensure_utc(end_at)
    _validate_window(start_utc, end_utc)
    resolved_room_id = room_id if room_id is not None else get_odc_room(session).id

    stmt = select(Booking).where(
        Booking.room_id == resolved_room_id,
        Booking.start_at < end_utc,
        Booking.end_at > start_utc,
    )
    if status is not None:
        stmt = stmt.where(Booking.status == status)
    stmt = stmt.order_by(Booking.start_at)
    return list(session.scalars(stmt).all())


def list_my_bookings(
    session: Session,
    associate_id: int,
    *,
    start_at: datetime,
    end_at: datetime,
    status: BookingStatus | None = BookingStatus.confirmed,
    room_id: int | None = None,
) -> list[Booking]:
    """Confirmed (or filtered) bookings owned by associate in [start_at, end_at)."""
    start_utc = ensure_utc(start_at)
    end_utc = ensure_utc(end_at)
    _validate_window(start_utc, end_utc)
    resolved_room_id = room_id if room_id is not None else get_odc_room(session).id

    stmt = select(Booking).where(
        Booking.room_id == resolved_room_id,
        Booking.associate_id == associate_id,
        Booking.start_at < end_utc,
        Booking.end_at > start_utc,
    )
    if status is not None:
        stmt = stmt.where(Booking.status == status)
    stmt = stmt.order_by(Booking.start_at).options(joinedload(Booking.associate))
    return list(session.scalars(stmt).unique().all())


def find_current_booking(
    session: Session,
    associate_id: int,
    *,
    at: datetime | None = None,
    room_id: int | None = None,
) -> Booking | None:
    """Confirmed booking owned by associate that is in progress at `at`."""
    moment = as_utc(at) if at is not None else datetime.now(timezone.utc)
    resolved_room_id = room_id if room_id is not None else get_odc_room(session).id
    stmt = (
        select(Booking)
        .where(
            Booking.room_id == resolved_room_id,
            Booking.associate_id == associate_id,
            Booking.status == BookingStatus.confirmed,
            Booking.start_at <= moment,
            Booking.end_at > moment,
        )
        .order_by(Booking.start_at.desc())
        .options(joinedload(Booking.associate))
    )
    return session.scalars(stmt).first()


def find_next_booking(
    session: Session,
    associate_id: int,
    *,
    at: datetime | None = None,
    room_id: int | None = None,
) -> Booking | None:
    """Next upcoming confirmed booking owned by associate at/after `at`."""
    moment = as_utc(at) if at is not None else datetime.now(timezone.utc)
    resolved_room_id = room_id if room_id is not None else get_odc_room(session).id
    stmt = (
        select(Booking)
        .where(
            Booking.room_id == resolved_room_id,
            Booking.associate_id == associate_id,
            Booking.status == BookingStatus.confirmed,
            Booking.start_at >= moment,
        )
        .order_by(Booking.start_at)
        .options(joinedload(Booking.associate))
    )
    return session.scalars(stmt).first()


def resolve_my_meeting(
    session: Session,
    associate_id: int,
    *,
    at: datetime | None = None,
    room_id: int | None = None,
) -> Booking:
    """Resolve “my meeting” to the current booking, else the next upcoming one."""
    current = find_current_booking(
        session, associate_id, at=at, room_id=room_id
    )
    if current is not None:
        return current
    upcoming = find_next_booking(session, associate_id, at=at, room_id=room_id)
    if upcoming is not None:
        return upcoming
    raise MyMeetingNotFoundError(associate_id)


def extend_my_meeting(
    session: Session,
    associate_id: int,
    *,
    minutes: int,
    at: datetime | None = None,
) -> Booking:
    booking = resolve_my_meeting(session, associate_id, at=at)
    return extend_booking(session, booking.id, minutes=minutes)


def cancel_my_meeting(
    session: Session,
    associate_id: int,
    *,
    at: datetime | None = None,
) -> Booking:
    booking = resolve_my_meeting(session, associate_id, at=at)
    return cancel_booking(session, booking.id)
