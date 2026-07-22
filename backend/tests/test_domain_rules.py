from datetime import timedelta

import pytest

from app.config import Settings, get_settings
from app.db import get_session_factory, init_db, reset_engine
from app.models import BookingStatus, Room
from app.scheduler.vacate_reminders import candidate_bookings
from app.services import BookingConflictError, create_booking, update_booking_window
from app.services.availability import suggest_alternatives
from tests.factories import at, make_associate, make_booking


@pytest.fixture(autouse=True)
def fresh_sqlite_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("ODC_TIMEZONE", "America/Sao_Paulo")
    get_settings.cache_clear()
    reset_engine()
    init_db()
    yield
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        reminder_lead_minutes=15,
        reminder_back_to_back_tolerance_minutes=2,
    )


def test_cancelled_overlap_does_not_block_confirmed_booking():
    with get_session_factory()() as session:
        associate = make_associate(session)
        room = session.query(Room).one()
        make_booking(
            session,
            associate_id=associate.id,
            room_id=room.id,
            start_at=at(10),
            end_at=at(12),
            status=BookingStatus.cancelled,
        )
        booking = create_booking(
            session,
            associate_id=associate.id,
            room_id=room.id,
            start_at=at(11),
            end_at=at(13),
        )
        assert booking.status == BookingStatus.confirmed


def test_extend_success_preserves_reminder_state_and_rejects_conflict():
    with get_session_factory()() as session:
        ada = make_associate(session)
        grace = make_associate(session, name="Grace", email="grace@example.com")
        room = session.query(Room).one()
        current = make_booking(
            session,
            associate_id=ada.id,
            room_id=room.id,
            start_at=at(10),
            end_at=at(11),
            reminder_sent_at=at(10, 45),
        )
        blocker = make_booking(
            session,
            associate_id=grace.id,
            room_id=room.id,
            start_at=at(12),
            end_at=at(13),
        )

        extended = update_booking_window(
            session,
            current.id,
            start_at=at(10),
            end_at=at(11, 30),
        )
        assert extended.end_at.replace(tzinfo=at(0).tzinfo) == at(11, 30)
        assert extended.reminder_sent_at.replace(tzinfo=at(0).tzinfo) == at(10, 45)

        with pytest.raises(BookingConflictError):
            update_booking_window(
                session,
                current.id,
                start_at=at(10),
                end_at=at(12, 30),
            )
        assert session.get(type(blocker), blocker.id).status == BookingStatus.confirmed


def test_alternatives_are_empty_when_business_day_has_no_free_window():
    with get_session_factory()() as session:
        associate = make_associate(session)
        room = session.query(Room).one()
        for start_hour in range(11, 21, 2):
            make_booking(
                session,
                associate_id=associate.id,
                room_id=room.id,
                start_at=at(start_hour),
                end_at=at(start_hour + 2),
            )

        alternatives = suggest_alternatives(
            session,
            at(20),
            at(21),
            room_id=room.id,
        )
        assert alternatives == []


def test_vacate_eligibility_requires_next_booking_and_unreminded_state(settings):
    with get_session_factory()() as session:
        ada = make_associate(session)
        grace = make_associate(session, name="Grace", email="grace@example.com")
        room = session.query(Room).one()
        adjacent = make_booking(
            session,
            associate_id=ada.id,
            room_id=room.id,
            start_at=at(10),
            end_at=at(11),
        )
        make_booking(
            session,
            associate_id=grace.id,
            room_id=room.id,
            start_at=at(11),
            end_at=at(12),
        )
        gapped = make_booking(
            session,
            associate_id=ada.id,
            room_id=room.id,
            start_at=at(13),
            end_at=at(14),
        )
        make_booking(
            session,
            associate_id=grace.id,
            room_id=room.id,
            start_at=at(14, 3),
            end_at=at(15),
        )
        reminded = make_booking(
            session,
            associate_id=ada.id,
            room_id=room.id,
            start_at=at(16),
            end_at=at(17),
            reminder_sent_at=at(16, 45),
        )
        make_booking(
            session,
            associate_id=grace.id,
            room_id=room.id,
            start_at=at(17),
            end_at=at(18),
        )

        candidates = candidate_bookings(session, now=at(10, 45), settings=settings)
        candidate_ids = {booking.id for booking in candidates}
        assert adjacent.id in candidate_ids
        assert gapped.id not in candidate_ids
        assert reminded.id not in candidate_ids
