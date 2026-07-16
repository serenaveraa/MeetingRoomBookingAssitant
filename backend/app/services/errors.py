from __future__ import annotations

from datetime import datetime


class BookingServiceError(Exception):
    """Base error for booking domain operations."""


class InvalidBookingWindowError(BookingServiceError):
    def __init__(self, start_at: datetime, end_at: datetime) -> None:
        self.start_at = start_at
        self.end_at = end_at
        super().__init__(
            f"Invalid booking window: end_at ({end_at}) must be after start_at ({start_at})"
        )


class BookingNotFoundError(BookingServiceError):
    def __init__(self, booking_id: int) -> None:
        self.booking_id = booking_id
        super().__init__(f"Booking {booking_id} not found")


class BookingConflictError(BookingServiceError):
    def __init__(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        conflicting_booking_id: int,
        conflicting_start_at: datetime,
        conflicting_end_at: datetime,
        conflicting_associate_id: int | None = None,
    ) -> None:
        self.start_at = start_at
        self.end_at = end_at
        self.conflicting_booking_id = conflicting_booking_id
        self.conflicting_start_at = conflicting_start_at
        self.conflicting_end_at = conflicting_end_at
        self.conflicting_associate_id = conflicting_associate_id
        super().__init__(
            "Booking conflict with confirmed booking "
            f"{conflicting_booking_id} "
            f"[{conflicting_start_at}, {conflicting_end_at})"
        )
