from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.agent.schema import ExtractedEntities
from app.services.timeutil import ensure_utc, get_odc_tz


class EntityResolutionError(ValueError):
    """Raised when extracted entities cannot be turned into a booking window."""


_TIME_PATTERNS = (
    re.compile(r"^(\d{1,2}):(\d{2})$"),
    re.compile(r"^(\d{1,2})$"),
    re.compile(r"^(\d{1,2}):(\d{2})\s*(am|pm)$", re.IGNORECASE),
    re.compile(r"^(\d{1,2})\s*(am|pm)$", re.IGNORECASE),
)


def _parse_time_of_day(raw: str) -> tuple[int, int]:
    text = raw.strip().lower().replace(".", "")
    text = re.sub(r"\s+", " ", text)

    for pattern in _TIME_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        groups = match.groups()
        hour = int(groups[0])
        if len(groups) == 1:
            minute = 0
            meridiem = None
        elif len(groups) == 2 and groups[1] in {"am", "pm"}:
            minute = 0
            meridiem = groups[1]
        elif len(groups) == 2:
            minute = int(groups[1])
            meridiem = None
        else:
            minute = int(groups[1])
            meridiem = groups[2]

        if meridiem:
            if hour < 1 or hour > 12:
                raise EntityResolutionError(f"Invalid time: {raw!r}")
            if meridiem == "am":
                hour = 0 if hour == 12 else hour
            else:
                hour = 12 if hour == 12 else hour + 12
        if hour > 23 or minute > 59:
            raise EntityResolutionError(f"Invalid time: {raw!r}")
        return hour, minute

    raise EntityResolutionError(f"Could not parse time: {raw!r}")


def resolve_day(raw: str | None, *, today: date | None = None) -> date:
    tz = get_odc_tz()
    base = today if today is not None else datetime.now(tz).date()
    if raw is None or not raw.strip():
        return base

    text = raw.strip().lower()
    if text in {"today"}:
        return base
    if text in {"tomorrow"}:
        return base + timedelta(days=1)

    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise EntityResolutionError(f"Could not parse date: {raw!r}") from exc


def resolve_booking_window(
    entities: ExtractedEntities,
    *,
    today: date | None = None,
) -> tuple[datetime, datetime]:
    """Build UTC (via ensure_utc) start/end from extracted entities."""
    if not entities.start_time:
        raise EntityResolutionError("start_time is required to build a booking window")

    day = resolve_day(entities.date, today=today)
    start_h, start_m = _parse_time_of_day(entities.start_time)
    tz = get_odc_tz()
    start_local = datetime(day.year, day.month, day.day, start_h, start_m, tzinfo=tz)

    if entities.end_time:
        end_h, end_m = _parse_time_of_day(entities.end_time)
        end_local = datetime(day.year, day.month, day.day, end_h, end_m, tzinfo=tz)
        if end_local <= start_local:
            raise EntityResolutionError("end_time must be after start_time")
    elif entities.duration_minutes is not None:
        if entities.duration_minutes <= 0:
            raise EntityResolutionError("duration_minutes must be positive")
        end_local = start_local + timedelta(minutes=entities.duration_minutes)
    else:
        raise EntityResolutionError(
            "Provide end_time or duration_minutes to build a booking window"
        )

    return ensure_utc(start_local), ensure_utc(end_local)
