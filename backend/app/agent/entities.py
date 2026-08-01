from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from app.agent.schema import ExtractedEntities
from app.services.timeutil import ensure_utc, get_odc_tz

logger = logging.getLogger(__name__)


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


_WEEKDAY_NAMES = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_MONTH_NAMES = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ORDINAL_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b")
_WEEKDAY_RE = re.compile(r"^(?:(this|next|coming|upcoming)\s+)?([a-z]+)$")
_MONTH_DAY_RE = re.compile(r"^([a-z]+)\s+(\d{1,2})(?:\s+(\d{4}))?$")
_DAY_MONTH_RE = re.compile(r"^(\d{1,2})\s+(?:of\s+)?([a-z]+)(?:\s+(\d{4}))?$")
_IN_DAYS_RE = re.compile(r"^in\s+(\d{1,3})\s+days?$")


def _normalize_day_text(raw: str) -> str:
    text = raw.strip().lower().replace(",", " ")
    text = re.sub(r"^(?:on|for)\s+", "", text)
    text = re.sub(r"\bthe\b", " ", text)
    text = _ORDINAL_RE.sub(r"\1", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def _upcoming_month_day(month: int, day: int, base: date) -> date:
    """Pick the next occurrence of month/day that is not in the past."""
    for year in (base.year, base.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= base:
            return candidate
    raise EntityResolutionError(f"Invalid date: month {month} day {day}")


def resolve_day(raw: str | None, *, today: date | None = None) -> date:
    """Resolve an extracted date phrase to a concrete ODC-local calendar day.

    Relative and month-name phrases are resolved here rather than relying on the
    LLM to do the arithmetic, so a wrong year from the model cannot silently turn
    a weekday into a weekend.
    """
    tz = get_odc_tz()
    base = today if today is not None else datetime.now(tz).date()
    if raw is None or not raw.strip():
        return base

    text = _normalize_day_text(raw)
    if not text:
        return base

    if text == "today":
        return base
    if text == "tomorrow":
        return base + timedelta(days=1)
    if text in {"day after tomorrow", "overmorrow"}:
        return base + timedelta(days=2)

    try:
        return date.fromisoformat(text)
    except ValueError:
        pass

    in_days = _IN_DAYS_RE.match(text)
    if in_days:
        return base + timedelta(days=int(in_days.group(1)))

    weekday_match = _WEEKDAY_RE.match(text)
    if weekday_match:
        qualifier, name = weekday_match.groups()
        target = _WEEKDAY_NAMES.get(name)
        if target is not None:
            ahead = (target - base.weekday()) % 7
            if ahead == 0 and qualifier in {"next", "coming", "upcoming"}:
                ahead = 7
            return base + timedelta(days=ahead)

    # "saturday august 8": the weekday name is redundant beside an explicit date.
    head, _, rest = text.partition(" ")
    if rest and head in _WEEKDAY_NAMES:
        try:
            return resolve_day(rest, today=base)
        except EntityResolutionError:
            pass

    for pattern, order in ((_MONTH_DAY_RE, "md"), (_DAY_MONTH_RE, "dm")):
        match = pattern.match(text)
        if not match:
            continue
        first, second, year = match.groups()
        name, day_str = (first, second) if order == "md" else (second, first)
        month = _MONTH_NAMES.get(name)
        if month is None:
            continue
        day = int(day_str)
        if year:
            try:
                return date(int(year), month, day)
            except ValueError as exc:
                raise EntityResolutionError(f"Could not parse date: {raw!r}") from exc
        return _upcoming_month_day(month, day, base)

    raise EntityResolutionError(f"Could not parse date: {raw!r}")


def _correct_stale_year(day: date, base: date, raw: str | None) -> date:
    """Repair a model-supplied date stuck in a previous year.

    Language models often stamp an ISO date with their training year, which shifts
    the weekday and can make a weekday request look like a weekend one.
    """
    if day.year >= base.year:
        return day
    text = _normalize_day_text(raw) if raw else ""
    # A bare ISO date is what the model emits, so its year is not evidence of
    # intent; a spelled-out date with a year ("august 3 2025") is.
    if text and not _ISO_DATE_RE.match(text) and re.search(r"\b\d{4}\b", text):
        return day
    corrected = _upcoming_month_day(day.month, day.day, base)
    logger.info("entities.stale_year_corrected raw=%r from=%s to=%s", raw, day, corrected)
    return corrected


def resolve_booking_window(
    entities: ExtractedEntities,
    *,
    today: date | None = None,
) -> tuple[datetime, datetime]:
    """Build UTC (via ensure_utc) start/end from extracted entities."""
    if not entities.start_time:
        raise EntityResolutionError("start_time is required to build a booking window")

    tz = get_odc_tz()
    base = today if today is not None else datetime.now(tz).date()
    day = _correct_stale_year(
        resolve_day(entities.date, today=base), base, entities.date
    )
    start_h, start_m = _parse_time_of_day(entities.start_time)
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
