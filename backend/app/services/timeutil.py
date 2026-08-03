from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.local_time import as_utc as _as_utc
from app.local_time import format_local
from app.local_time import to_local


def get_odc_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().odc_timezone)


def ensure_utc(dt: datetime) -> datetime:
    """Normalize datetimes to UTC; naive values are interpreted as ODC local time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=get_odc_tz())
    return dt.astimezone(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """Normalize datetimes to UTC; naive values are assumed already UTC (e.g. SQLite)."""
    return _as_utc(dt)


def to_odc_local(dt: datetime, *, tz_name: str | None = None) -> datetime:
    """Convert a datetime to ODC local time (Uruguay). Naive values are treated as UTC."""
    return to_local(dt, tz_name or get_settings().odc_timezone)


def format_odc_local(
    value: datetime | date | str | None,
    *,
    tz_name: str | None = None,
) -> str | None:
    """Human-readable ODC local timestamp for emails and other user-facing text.

    Example: ``25/07/2026 07:00`` (24h, Uruguay/Montevideo).
    """
    return format_local(value, tz_name=tz_name or get_settings().odc_timezone)
