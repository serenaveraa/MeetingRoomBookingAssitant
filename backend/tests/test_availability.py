from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import get_settings
from app.db import get_session_factory, init_db, reset_engine
from app.models import Associate
from app.services import (
    InvalidBookingWindowError,
    check_availability,
    create_booking,
    suggest_alternatives,
)
from app.services.availability import BUSINESS_DAY_END, BUSINESS_DAY_START
from app.services.timeutil import ensure_utc


TZ = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(autouse=True)
def _fresh_sqlite_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("ODC_TIMEZONE", "America/Sao_Paulo")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def associate_id() -> int:
    with get_session_factory()() as session:
        associate = Associate(name="Ada", email="ada@example.com")
        session.add(associate)
        session.commit()
        session.refresh(associate)
        return associate.id


def _local(hour: int, minute: int = 0) -> datetime:
    """ODC-local datetime on a fixed test day (naive → interpreted as ODC)."""
    return datetime(2026, 7, 16, hour, minute)


def _local_aware(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 16, hour, minute, tzinfo=TZ)


def test_check_availability_free(associate_id: int):
    with get_session_factory()() as session:
        result = check_availability(session, _local(9), _local(10))
        assert result.available is True
        assert result.conflict is None
        assert result.requested.start_at == ensure_utc(_local_aware(9))
        assert result.requested.end_at == ensure_utc(_local_aware(10))


def test_check_availability_busy(associate_id: int):
    with get_session_factory()() as session:
        booking = create_booking(
            session,
            associate_id=associate_id,
            start_at=_local(10),
            end_at=_local(11),
            purpose="Standup",
        )
        result = check_availability(session, _local(10, 30), _local(11, 30))
        assert result.available is False
        assert result.conflict is not None
        assert result.conflict.id == booking.id


def test_suggest_alternatives_busy_day_with_gaps(associate_id: int):
    with get_session_factory()() as session:
        create_booking(
            session,
            associate_id=associate_id,
            start_at=_local(10),
            end_at=_local(11),
        )
        create_booking(
            session,
            associate_id=associate_id,
            start_at=_local(14),
            end_at=_local(15),
        )

        # Request the busy 10–11 hour; expect nearest 1h gaps.
        alternatives = suggest_alternatives(
            session,
            _local(10),
            _local(11),
            limit=3,
        )
        assert len(alternatives) == 3

        duration = timedelta(hours=1)
        for window in alternatives:
            assert window.end_at - window.start_at == duration
            local_start = window.start_at.astimezone(TZ)
            local_end = window.end_at.astimezone(TZ)
            assert local_start.date() == local_end.date() == _local_aware(10).date()
            assert local_start.timetz().replace(tzinfo=None) >= BUSINESS_DAY_START
            assert local_end.timetz().replace(tzinfo=None) <= BUSINESS_DAY_END

        # Nearest should prefer slots closest to 10:00 (e.g. 09–10 or 11–12).
        nearest_starts = [w.start_at.astimezone(TZ).hour for w in alternatives]
        assert nearest_starts[0] in {8, 9, 11}


def test_suggest_alternatives_same_duration_and_day(associate_id: int):
    with get_session_factory()() as session:
        create_booking(
            session,
            associate_id=associate_id,
            start_at=_local(10),
            end_at=_local(12),  # 2h busy
        )
        alternatives = suggest_alternatives(
            session,
            _local(10),
            _local(12),
            limit=3,
        )
        assert alternatives
        for window in alternatives:
            assert window.end_at - window.start_at == timedelta(hours=2)
            assert window.start_at.astimezone(TZ).date() == _local_aware(10).date()


def test_invalid_window_raises():
    with get_session_factory()() as session:
        start = _local(10)
        with pytest.raises(InvalidBookingWindowError):
            check_availability(session, start, start)
        with pytest.raises(InvalidBookingWindowError):
            suggest_alternatives(session, start, start - timedelta(hours=1))
