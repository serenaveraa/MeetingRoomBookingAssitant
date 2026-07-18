"""Idempotent vacate-reminder polling job."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.models import Booking, BookingStatus
from app.notifications import NotificationService

logger = logging.getLogger(__name__)


def candidate_bookings(
    session: Session,
    *,
    now: datetime,
    settings: Settings,
) -> list[Booking]:
    """Return current meetings that need a reminder now.

    The correlated EXISTS query avoids an N+1 next-booking lookup.  Since the
    booking service forbids overlapping confirmed bookings, a confirmed row
    starting in this narrow window is necessarily the next booking in the
    room.
    """
    due_end = now + timedelta(minutes=settings.reminder_lead_minutes)
    tolerance = timedelta(minutes=settings.reminder_back_to_back_tolerance_minutes)
    next_booking = Booking.__table__.alias("next_booking")
    # This scalar subquery finds the actual *next* start in the room in one
    # statement.  Comparing its timestamp to end_at in Python keeps the query
    # portable between PostgreSQL and SQLite (SQLite has no interval type).
    next_start = (
        select(func.min(next_booking.c.start_at))
        .where(
            next_booking.c.room_id == Booking.room_id,
            next_booking.c.id != Booking.id,
            next_booking.c.status == BookingStatus.confirmed.value,
            next_booking.c.start_at >= Booking.end_at,
        )
        .correlate(Booking)
        .scalar_subquery()
    )
    # Include a meeting whose due instant was missed while the app was down,
    # but never send after the meeting itself has ended.
    stmt = (
        select(Booking)
        .where(
            Booking.status == BookingStatus.confirmed,
            Booking.reminder_sent_at.is_(None),
            Booking.reminder_claimed_at.is_(None),
            Booking.end_at > now,
            Booking.end_at <= due_end,
        )
        .order_by(Booking.end_at)
        .options(joinedload(Booking.room), joinedload(Booking.associate))
    )
    rows = session.execute(stmt.add_columns(next_start.label("next_start"))).unique().all()
    return [
        booking
        for booking, following_start in rows
        if following_start is not None and following_start <= booking.end_at + tolerance
    ]


def _claim_booking(session: Session, booking_id: int, now: datetime) -> bool:
    """Atomically acquire delivery ownership; works across app instances."""
    result = session.execute(
        update(Booking)
        .where(
            Booking.id == booking_id,
            Booking.reminder_sent_at.is_(None),
            Booking.reminder_claimed_at.is_(None),
        )
        .values(reminder_claimed_at=now)
    )
    session.commit()
    return result.rowcount == 1


def _finish_claim(session: Session, booking_id: int, sent_at: datetime) -> None:
    session.execute(
        update(Booking)
        .where(Booking.id == booking_id)
        .values(reminder_sent_at=sent_at, reminder_claimed_at=None)
    )
    session.commit()


def _release_claim(session: Session, booking_id: int) -> None:
    session.execute(
        update(Booking)
        .where(Booking.id == booking_id, Booking.reminder_sent_at.is_(None))
        .values(reminder_claimed_at=None)
    )
    session.commit()


def run_vacate_reminder_job(
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
    notification_service: NotificationService | None = None,
) -> int:
    """Send due reminders, returning the number of bookings delivered."""
    settings = settings or get_settings()
    now = now or datetime.now(timezone.utc)
    notifier = notification_service or NotificationService(settings)
    sent_count = 0
    with get_session_factory()() as session:
        candidates = candidate_bookings(session, now=now, settings=settings)
        for candidate in candidates:
            if not _claim_booking(session, candidate.id, now):
                continue
            # Reload relationships after the claim transaction.  This avoids
            # relying on detached objects and keeps provider calls outside DB
            # transaction/row locks.
            booking = session.scalar(
                select(Booking)
                .where(Booking.id == candidate.id)
                .options(joinedload(Booking.room), joinedload(Booking.associate))
            )
            try:
                assert booking is not None
                channels = notifier.send_vacate_reminder(booking)
                _finish_claim(session, candidate.id, datetime.now(timezone.utc))
                sent_count += 1
                logger.info(
                    "Vacate reminder sent booking_id=%s room=%s recipient=%s channels=%s",
                    booking.id,
                    booking.room.name,
                    booking.associate.email,
                    ",".join(channels),
                )
            except Exception:
                session.rollback()
                _release_claim(session, candidate.id)
                logger.exception("Vacate reminder delivery failed booking_id=%s", candidate.id)
    return sent_count
