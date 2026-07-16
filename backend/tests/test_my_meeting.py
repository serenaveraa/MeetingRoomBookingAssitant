from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.db import get_session_factory, init_db, reset_engine
from app.models import Associate, BookingStatus
from app.services import (
    BookingConflictError,
    MyMeetingNotFoundError,
    cancel_my_meeting,
    create_booking,
    extend_my_meeting,
    find_current_booking,
    find_next_booking,
    resolve_my_meeting,
)
from app.services.timeutil import as_utc


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


def _add_associate(name: str, email: str) -> int:
    with get_session_factory()() as session:
        associate = Associate(name=name, email=email)
        session.add(associate)
        session.commit()
        session.refresh(associate)
        return associate.id


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 16, hour, minute, tzinfo=timezone.utc)


def test_resolve_prefers_current_then_next():
    ada = _add_associate("Ada", "ada@example.com")
    with get_session_factory()() as session:
        create_booking(
            session,
            associate_id=ada,
            start_at=_at(10),
            end_at=_at(11),
            purpose="Now",
        )
        create_booking(
            session,
            associate_id=ada,
            start_at=_at(14),
            end_at=_at(15),
            purpose="Later",
        )

        current = find_current_booking(session, ada, at=_at(10, 30))
        assert current is not None
        assert current.purpose == "Now"

        nxt = find_next_booking(session, ada, at=_at(12))
        assert nxt is not None
        assert nxt.purpose == "Later"

        resolved = resolve_my_meeting(session, ada, at=_at(10, 30))
        assert resolved.purpose == "Now"

        resolved_later = resolve_my_meeting(session, ada, at=_at(12))
        assert resolved_later.purpose == "Later"


def test_extend_my_meeting_success_and_conflict_message():
    ada = _add_associate("Ada", "ada@example.com")
    grace = _add_associate("Grace Hopper", "grace@example.com")

    with get_session_factory()() as session:
        create_booking(
            session,
            associate_id=ada,
            start_at=_at(10),
            end_at=_at(11),
        )
        create_booking(
            session,
            associate_id=grace,
            start_at=_at(11),
            end_at=_at(12),
        )

        with pytest.raises(BookingConflictError) as exc:
            extend_my_meeting(session, ada, minutes=30, at=_at(10, 15))

        assert exc.value.conflicting_associate_name == "Grace Hopper"
        assert "Grace Hopper" in str(exc.value)
        assert "starting at" in str(exc.value)

    with get_session_factory()() as session:
        create_booking(
            session,
            associate_id=ada,
            start_at=_at(14),
            end_at=_at(15),
        )
        extended = extend_my_meeting(session, ada, minutes=30, at=_at(14, 10))
        assert as_utc(extended.end_at) == _at(15, 30)


def test_cancel_my_meeting_frees_slot():
    ada = _add_associate("Ada", "ada@example.com")
    grace = _add_associate("Grace", "grace@example.com")

    with get_session_factory()() as session:
        mine = create_booking(
            session,
            associate_id=ada,
            start_at=_at(10),
            end_at=_at(11),
        )
        cancelled = cancel_my_meeting(session, ada, at=_at(10, 15))
        assert cancelled.id == mine.id
        assert cancelled.status == BookingStatus.cancelled

        rebooked = create_booking(
            session,
            associate_id=grace,
            start_at=_at(10),
            end_at=_at(11),
        )
        assert rebooked.status == BookingStatus.confirmed


def test_my_meeting_not_found():
    ada = _add_associate("Ada", "ada@example.com")
    with get_session_factory()() as session:
        with pytest.raises(MyMeetingNotFoundError):
            resolve_my_meeting(session, ada, at=_at(10))
