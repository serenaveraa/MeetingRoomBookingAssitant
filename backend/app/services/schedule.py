"""ODC room schedule rules (weekdays only)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.services.errors import InvalidBookingWindowError
from app.services.timeutil import get_odc_tz

WEEKEND_BOOKING_MESSAGE = (
    "The meeting room cannot be booked on weekends "
    "(Saturday–Sunday, ODC local time). Please choose a weekday."
)


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def local_dates_touched(start_at: datetime, end_at: datetime) -> list[date]:
    """Local calendar dates that intersect half-open UTC window [start_at, end_at)."""
    tz = get_odc_tz()
    start_local = start_at.astimezone(tz)
    end_local = end_at.astimezone(tz)
    first = start_local.date()
    if end_local <= start_local:
        return [first]
    # Half-open: last included instant is just before end_at.
    last = (end_local - timedelta(microseconds=1)).date()
    if last < first:
        last = first

    days: list[date] = []
    cursor = first
    while cursor <= last:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def assert_no_weekend_booking(start_at: datetime, end_at: datetime) -> None:
    """Reject windows that touch Saturday or Sunday in ODC local time."""
    weekend_days = [d for d in local_dates_touched(start_at, end_at) if is_weekend(d)]
    if weekend_days:
        raise InvalidBookingWindowError(
            start_at,
            end_at,
            reason=WEEKEND_BOOKING_MESSAGE,
        )
