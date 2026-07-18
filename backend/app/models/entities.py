from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class BookingStatus(str, enum.Enum):
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)

    bookings: Mapped[list[Booking]] = relationship(back_populates="room")


class Associate(Base):
    __tablename__ = "associates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    teams_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    bookings: Mapped[list[Booking]] = relationship(back_populates="associate")
    waitlist_entries: Mapped[list[WaitlistEntry]] = relationship(
        back_populates="associate"
    )


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ck_bookings_end_after_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    associate_id: Mapped[int] = mapped_column(
        ForeignKey("associates.id"), nullable=False
    )
    purpose: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        Enum(
            BookingStatus,
            name="booking_status",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=BookingStatus.confirmed,
    )
    reminder_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Delivery ownership marker used by the reminder worker. It is kept
    # separate from reminder_sent_at so known failed delivery can be retried.
    reminder_claimed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    room: Mapped[Room] = relationship(back_populates="bookings")
    associate: Mapped[Associate] = relationship(back_populates="bookings")


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"
    __table_args__ = (
        CheckConstraint(
            "desired_end > desired_start",
            name="ck_waitlist_desired_end_after_start",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    associate_id: Mapped[int] = mapped_column(
        ForeignKey("associates.id"), nullable=False
    )
    desired_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    desired_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    associate: Mapped[Associate] = relationship(back_populates="waitlist_entries")


ODC_COMMON_ROOM_NAME = "ODC Common Meeting Room"
