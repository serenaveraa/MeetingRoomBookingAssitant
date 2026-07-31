from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Booking, BookingStatus, WaitlistEntry
from app.notifications import NotificationService
from app.services.errors import InvalidBookingWindowError
from app.services.schedule import assert_no_weekend_booking
from app.services.timeutil import ensure_utc

logger = logging.getLogger(__name__)


def create_waitlist_entry(
    session: Session,
    *,
    associate_id: int,
    room_id: int,
    desired_start: datetime,
    desired_end: datetime,
) -> WaitlistEntry:
    start_utc = ensure_utc(desired_start)
    end_utc = ensure_utc(desired_end)
    if end_utc <= start_utc:
        raise InvalidBookingWindowError(start_utc, end_utc)
    assert_no_weekend_booking(start_utc, end_utc)
    occupied = session.scalar(
        select(Booking.id).where(
            Booking.room_id == room_id,
            Booking.status == BookingStatus.confirmed,
            Booking.start_at < end_utc,
            Booking.end_at > start_utc,
        )
    )
    if occupied is None:
        raise ValueError("Waitlist entries require a currently unavailable room window")

    entry = WaitlistEntry(
        associate_id=associate_id,
        room_id=room_id,
        desired_start=start_utc,
        desired_end=end_utc,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def notify_waitlist_for_cancelled_booking(
    session: Session,
    booking: Booking,
    *,
    notification_service: NotificationService | None = None,
) -> None:
    """Claim and notify all active entries fully covered by the freed slot."""
    notifier = notification_service or NotificationService()
    entries = session.scalars(
        select(WaitlistEntry)
        .where(
            WaitlistEntry.room_id == booking.room_id,
            WaitlistEntry.notified_at.is_(None),
            WaitlistEntry.desired_start >= booking.start_at,
            WaitlistEntry.desired_end <= booking.end_at,
        )
        .order_by(WaitlistEntry.created_at, WaitlistEntry.id)
        .options(joinedload(WaitlistEntry.associate), joinedload(WaitlistEntry.room))
    ).unique().all()

    for candidate in entries:
        entry = session.scalar(
            select(WaitlistEntry)
            .where(
                WaitlistEntry.id == candidate.id,
                WaitlistEntry.notified_at.is_(None),
            )
            .options(joinedload(WaitlistEntry.associate), joinedload(WaitlistEntry.room))
            .with_for_update()
        )
        if entry is None:
            continue
        try:
            channels = notifier.send_waitlist_slot_available(entry)
            entry.notified_at = datetime.now(timezone.utc)
            session.commit()
            logger.info(
                "Waitlist notification sent entry_id=%s booking_id=%s channels=%s",
                entry.id,
                booking.id,
                ",".join(channels),
            )
        except Exception:
            session.rollback()
            logger.exception(
                "Waitlist notification failed entry_id=%s booking_id=%s; "
                "the claimed notification can be diagnosed manually",
                entry.id,
                booking.id,
            )