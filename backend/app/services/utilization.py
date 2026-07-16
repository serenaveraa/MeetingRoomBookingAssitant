from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.services.availability import (
    _confirmed_bookings_for_day,
    _free_gaps,
    business_hours_bounds,
)
from app.services.booking import get_odc_room
from app.services.timeutil import as_utc, get_odc_tz


@dataclass(frozen=True)
class UtilizationSummary:
    day: date
    booking_count: int
    total_booked_minutes: int
    avg_duration_minutes: float
    idle_gap_count: int
    business_minutes: int


def get_utilization_summary(
    session: Session,
    *,
    day: date,
    room_id: int | None = None,
) -> UtilizationSummary:
    """Summarize ODC room utilization for a local calendar day (business hours)."""
    resolved_room_id = room_id if room_id is not None else get_odc_room(session).id
    biz_start, biz_end = business_hours_bounds(day)
    business_minutes = int((biz_end - biz_start).total_seconds() // 60)

    bookings = _confirmed_bookings_for_day(session, resolved_room_id, day)
    occupied: list[tuple[datetime, datetime]] = []
    total_booked = 0
    for booking in bookings:
        start = as_utc(booking.start_at)
        end = as_utc(booking.end_at)
        clipped_start = max(start, biz_start)
        clipped_end = min(end, biz_end)
        if clipped_end <= clipped_start:
            continue
        occupied.append((clipped_start, clipped_end))
        total_booked += int((clipped_end - clipped_start).total_seconds() // 60)

    occupied.sort(key=lambda w: w[0])
    gaps = _free_gaps(occupied, biz_start, biz_end)
    count = len(occupied)
    avg = (total_booked / count) if count else 0.0

    return UtilizationSummary(
        day=day,
        booking_count=count,
        total_booked_minutes=total_booked,
        avg_duration_minutes=round(avg, 1),
        idle_gap_count=len(gaps),
        business_minutes=business_minutes,
    )


def local_today() -> date:
    return datetime.now(get_odc_tz()).date()
