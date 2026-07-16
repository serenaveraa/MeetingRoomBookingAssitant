from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Booking, BookingStatus
from app.services.booking import find_conflicts, get_odc_room
from app.services.errors import InvalidBookingWindowError
from app.services.timeutil import ensure_utc, get_odc_tz

BUSINESS_DAY_START = time(8, 0)
BUSINESS_DAY_END = time(18, 0)
DEFAULT_ALTERNATIVE_LIMIT = 3


@dataclass(frozen=True)
class TimeWindow:
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class AvailabilityResult:
    available: bool
    requested: TimeWindow
    conflict: Booking | None


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC bounds for an ODC local calendar day [midnight, next midnight)."""
    tz = get_odc_tz()
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def business_hours_bounds(day: date) -> tuple[datetime, datetime]:
    tz = get_odc_tz()
    start_local = datetime.combine(day, BUSINESS_DAY_START, tzinfo=tz)
    end_local = datetime.combine(day, BUSINESS_DAY_END, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def check_availability(
    session: Session,
    start_at: datetime,
    end_at: datetime,
    *,
    room_id: int | None = None,
) -> AvailabilityResult:
    start_utc = ensure_utc(start_at)
    end_utc = ensure_utc(end_at)
    if end_utc <= start_utc:
        raise InvalidBookingWindowError(start_utc, end_utc)

    resolved_room_id = room_id if room_id is not None else get_odc_room(session).id
    conflicts = find_conflicts(session, resolved_room_id, start_utc, end_utc)
    requested = TimeWindow(start_at=start_utc, end_at=end_utc)
    if not conflicts:
        return AvailabilityResult(available=True, requested=requested, conflict=None)
    return AvailabilityResult(
        available=False,
        requested=requested,
        conflict=conflicts[0],
    )


def _confirmed_bookings_for_day(
    session: Session,
    room_id: int,
    day: date,
) -> list[Booking]:
    day_start, day_end = local_day_bounds(day)
    stmt = (
        select(Booking)
        .where(
            Booking.room_id == room_id,
            Booking.status == BookingStatus.confirmed,
            Booking.start_at < day_end,
            Booking.end_at > day_start,
        )
        .order_by(Booking.start_at)
    )
    return list(session.scalars(stmt).all())


def _free_gaps(
    occupied: list[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, datetime]]:
    gaps: list[tuple[datetime, datetime]] = []
    cursor = window_start
    for start, end in occupied:
        clipped_start = max(start, window_start)
        clipped_end = min(end, window_end)
        if clipped_end <= window_start or clipped_start >= window_end:
            continue
        if clipped_start > cursor:
            gaps.append((cursor, clipped_start))
        cursor = max(cursor, clipped_end)
    if cursor < window_end:
        gaps.append((cursor, window_end))
    return gaps


def suggest_alternatives(
    session: Session,
    start_at: datetime,
    end_at: datetime,
    *,
    room_id: int | None = None,
    limit: int = DEFAULT_ALTERNATIVE_LIMIT,
) -> list[TimeWindow]:
    """Nearest same-day free windows of equal duration within business hours."""
    start_utc = ensure_utc(start_at)
    end_utc = ensure_utc(end_at)
    if end_utc <= start_utc:
        raise InvalidBookingWindowError(start_utc, end_utc)

    duration = end_utc - start_utc
    resolved_room_id = room_id if room_id is not None else get_odc_room(session).id
    local_day = start_utc.astimezone(get_odc_tz()).date()
    biz_start, biz_end = business_hours_bounds(local_day)

    bookings = _confirmed_bookings_for_day(session, resolved_room_id, local_day)
    occupied = [
        (ensure_utc(b.start_at), ensure_utc(b.end_at)) for b in bookings
    ]
    gaps = _free_gaps(occupied, biz_start, biz_end)

    candidates: list[TimeWindow] = []
    for gap_start, gap_end in gaps:
        if gap_end - gap_start < duration:
            continue
        # Place one exact-duration slot at the start of each viable gap.
        slot_start = gap_start
        slot_end = slot_start + duration
        if slot_end <= gap_end:
            candidates.append(TimeWindow(start_at=slot_start, end_at=slot_end))
        # Also try a slot ending at the gap end when that fits and differs.
        alt_start = gap_end - duration
        if alt_start >= gap_start and alt_start != slot_start:
            candidates.append(
                TimeWindow(start_at=alt_start, end_at=alt_start + duration)
            )

    # Deduplicate and rank by distance to the originally requested start.
    unique: dict[tuple[datetime, datetime], TimeWindow] = {
        (c.start_at, c.end_at): c for c in candidates
    }
    ranked = sorted(
        unique.values(),
        key=lambda w: (abs(w.start_at - start_utc), w.start_at),
    )
    return ranked[:limit]
