from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings


def get_odc_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().odc_timezone)


def ensure_utc(dt: datetime) -> datetime:
    """Normalize datetimes to UTC; naive values are interpreted as ODC local time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=get_odc_tz())
    return dt.astimezone(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """Normalize datetimes to UTC; naive values are assumed already UTC (e.g. SQLite)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
