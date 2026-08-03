"""ODC local-time helpers shared by notifications and services (no import cycles)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def as_utc(dt: datetime) -> datetime:
    """Normalize datetimes to UTC; naive values are assumed already UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_local(dt: datetime, tz_name: str) -> datetime:
    """Convert a datetime to the given local zone. Naive values are treated as UTC."""
    return as_utc(dt).astimezone(ZoneInfo(tz_name))


def format_local(
    value: datetime | date | str | None,
    *,
    tz_name: str,
) -> str | None:
    """Human-readable local timestamp for emails and other user-facing text.

    Example: ``25/07/2026 07:00`` (24h).
    """
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
    if isinstance(value, datetime):
        return to_local(value, tz_name).strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)
