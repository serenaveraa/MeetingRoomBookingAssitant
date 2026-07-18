from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Booking, BookingStatus
from app.services.availability import (
    _confirmed_bookings_for_day,
    _free_gaps,
    business_hours_bounds,
)
from app.services.booking import get_odc_room
from app.services.timeutil import as_utc, get_odc_tz


@dataclass(frozen=True)
class UtilizationSummary:
    start_date: date
    end_date: date
    booking_count: int
    total_booked_minutes: int
    avg_duration_minutes: float
    idle_gap_count: int
    business_minutes: int
    bookings_per_day: list[dict[str, object]]
    busiest_day: dict[str, object] | None = None
    overall_summary: str | None = None
    day: date | None = None


def _iter_days(start_date: date, end_date: date) -> list[date]:
    days = []
    current = start_date
    while current <= end_date:
        days.append(current)
        current += timedelta(days=1)
    return days


def _day_summary(
    session: Session,
    *,
    day: date,
    room_id: int,
) -> dict[str, object]:
    biz_start, biz_end = business_hours_bounds(day)
    business_minutes = int((biz_end - biz_start).total_seconds() // 60)
    bookings = _confirmed_bookings_for_day(session, room_id, day)
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
    avg = round((total_booked / count) if count else 0.0, 1)
    return {
        "day": day.isoformat(),
        "booking_count": count,
        "total_booked_minutes": total_booked,
        "avg_duration_minutes": avg,
        "idle_gap_count": len(gaps),
        "business_minutes": business_minutes,
    }


def get_utilization_summary(
    session: Session,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    day: date | None = None,
    room_id: int | None = None,
) -> UtilizationSummary:
    """Summarize room utilization over a date range using business-hours logic."""
    if day is not None:
        start_date = day
        end_date = day

    if start_date is None or end_date is None:
        raise ValueError("start_date and end_date are required")
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    resolved_room_id = room_id if room_id is not None else get_odc_room(session).id
    days = _iter_days(start_date, end_date)
    per_day = [_day_summary(session, day=d, room_id=resolved_room_id) for d in days]

    total_bookings = sum(int(entry["booking_count"]) for entry in per_day)
    total_minutes = sum(int(entry["total_booked_minutes"]) for entry in per_day)
    avg_duration = round((total_minutes / total_bookings) if total_bookings else 0.0, 1)
    idle_gaps = sum(int(entry["idle_gap_count"]) for entry in per_day)
    busiest = max(per_day, key=lambda entry: int(entry["booking_count"]), default=None)
    dated_busiest = None
    if busiest is not None:
        dated_busiest = {
            "day": busiest["day"],
            "booking_count": busiest["booking_count"],
        }

    summary_text = (
        f"Across {len(days)} day(s), the room had {total_bookings} booking(s), "
        f"{total_minutes} booked minutes, an average booking length of {avg_duration} minutes, "
        f"and {idle_gaps} idle gap(s) within business hours."
    )

    return UtilizationSummary(
        start_date=start_date,
        end_date=end_date,
        booking_count=total_bookings,
        total_booked_minutes=total_minutes,
        avg_duration_minutes=avg_duration,
        idle_gap_count=idle_gaps,
        business_minutes=sum(int(entry["business_minutes"]) for entry in per_day),
        bookings_per_day=per_day,
        busiest_day=dated_busiest,
        overall_summary=summary_text,
        day=start_date if start_date == end_date else None,
    )


def local_today() -> date:
    return datetime.now(get_odc_tz()).date()
