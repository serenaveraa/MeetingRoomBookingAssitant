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
    def __init__(self, booking_id: int | None = None, *, message: str | None = None) -> None:
        self.booking_id = booking_id
        super().__init__(message or f"Booking {booking_id} not found")


class OwnershipError(BookingServiceError):
    def __init__(self, booking_id: int, associate_id: int) -> None:
        self.booking_id = booking_id
        self.associate_id = associate_id
        super().__init__(f"Associate {associate_id} does not own booking {booking_id}")


class MyMeetingNotFoundError(BookingServiceError):
    def __init__(self, associate_id: int) -> None:
        self.associate_id = associate_id
        super().__init__(
            f"No current or upcoming confirmed meeting found for associate {associate_id}"
        )


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
        conflicting_associate_name: str | None = None,
        message: str | None = None,
    ) -> None:
        self.start_at = start_at
        self.end_at = end_at
        self.conflicting_booking_id = conflicting_booking_id
        self.conflicting_start_at = conflicting_start_at
        self.conflicting_end_at = conflicting_end_at
        self.conflicting_associate_id = conflicting_associate_id
        self.conflicting_associate_name = conflicting_associate_name
        who = conflicting_associate_name or (
            f"associate {conflicting_associate_id}"
            if conflicting_associate_id is not None
            else "another associate"
        )
        super().__init__(
            message
            or "Extension not possible. "
            f"Room reserved by {who} starting at {conflicting_start_at}."
        )
