from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Associate, Booking, BookingStatus, Room


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 16, hour, minute, tzinfo=timezone.utc)


def make_associate(
    session: Session,
    *,
    name: str = "Ada",
    email: str = "ada@example.com",
) -> Associate:
    associate = Associate(name=name, email=email)
    session.add(associate)
    session.commit()
    session.refresh(associate)
    return associate


def make_booking(
    session: Session,
    *,
    associate_id: int,
    room_id: int,
    start_at: datetime,
    end_at: datetime,
    status: BookingStatus = BookingStatus.confirmed,
    reminder_sent_at: datetime | None = None,
) -> Booking:
    booking = Booking(
        associate_id=associate_id,
        room_id=room_id,
        start_at=start_at,
        end_at=end_at,
        status=status,
        reminder_sent_at=reminder_sent_at,
    )
    session.add(booking)
    session.commit()
    session.refresh(booking)
    return booking


def make_room(session: Session, *, name: str = "Test Room") -> Room:
    room = Room(name=name)
    session.add(room)
    session.commit()
    session.refresh(room)
    return room


def duration(hours: int = 1) -> timedelta:
    return timedelta(hours=hours)
