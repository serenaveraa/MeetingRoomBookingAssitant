from datetime import datetime, timedelta

import pytest

from app.config import Settings, get_settings
from app.db import get_session_factory, init_db, reset_engine
from app.models import BookingStatus, Room
from app.scheduler.vacate_reminders import candidate_bookings
from app.services import BookingConflictError, create_booking, update_booking_window
from app.services.availability import check_availability, suggest_alternatives
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


def test_weekend_booking_is_rejected():
    from zoneinfo import ZoneInfo

    from app.services.errors import InvalidBookingWindowError
    from app.services.schedule import WEEKEND_BOOKING_MESSAGE

    tz = ZoneInfo("America/Sao_Paulo")
    saturday_start = datetime(2026, 7, 18, 10, 0, tzinfo=tz)  # Saturday
    saturday_end = datetime(2026, 7, 18, 11, 0, tzinfo=tz)

    with get_session_factory()() as session:
        associate = make_associate(session)
        room = session.query(Room).one()
        with pytest.raises(InvalidBookingWindowError, match="weekends") as exc_info:
            create_booking(
                session,
                associate_id=associate.id,
                room_id=room.id,
                start_at=saturday_start,
                end_at=saturday_end,
                purpose="Weekend planning",
            )
        assert str(exc_info.value) == WEEKEND_BOOKING_MESSAGE


def test_listing_bookings_over_a_weekend_range_is_allowed():
    """Reads span whole weeks; only reserving windows are weekday-only."""
    from zoneinfo import ZoneInfo

    from app.services.booking import list_bookings

    tz = ZoneInfo("America/Sao_Paulo")
    with get_session_factory()() as session:
        make_associate(session)
        found = list_bookings(
            session,
            start_at=datetime(2026, 7, 27, 0, 0, tzinfo=tz),  # Monday
            end_at=datetime(2026, 8, 3, 0, 0, tzinfo=tz),  # next Monday
        )
        assert found == []


def test_weekday_booking_still_allowed():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Sao_Paulo")
    friday_start = datetime(2026, 7, 17, 10, 0, tzinfo=tz)  # Friday
    friday_end = datetime(2026, 7, 17, 11, 0, tzinfo=tz)

    with get_session_factory()() as session:
        associate = make_associate(session)
        room = session.query(Room).one()
        booking = create_booking(
            session,
            associate_id=associate.id,
            room_id=room.id,
            start_at=friday_start,
            end_at=friday_end,
            purpose="Friday sync",
        )
        assert booking.id > 0


def test_window_spanning_into_saturday_is_rejected():
    from zoneinfo import ZoneInfo

    from app.services.errors import InvalidBookingWindowError

    tz = ZoneInfo("America/Sao_Paulo")
    # Friday evening into Saturday morning (local)
    start = datetime(2026, 7, 17, 22, 0, tzinfo=tz)
    end = datetime(2026, 7, 18, 1, 0, tzinfo=tz)

    with get_session_factory()() as session:
        associate = make_associate(session)
        room = session.query(Room).one()
        with pytest.raises(InvalidBookingWindowError, match="weekends"):
            create_booking(
                session,
                associate_id=associate.id,
                room_id=room.id,
                start_at=start,
                end_at=end,
            )
