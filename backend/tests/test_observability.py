from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.agent.tools import ToolContext, WindowArgs, tool_check_availability
from app.config import Settings, get_settings
from app.db import get_session_factory, init_db, reset_engine
from app.models import Room
from app.scheduler.vacate_reminders import run_vacate_reminder_job
from app.services.availability import AvailabilityResult, TimeWindow
from app.services.booking import create_booking
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


def _json_event(caplog, event: str) -> dict:
    for record in reversed(caplog.records):
        try:
            payload = json.loads(record.getMessage())
        except json.JSONDecodeError:
            continue
        if payload.get("event") == event:
            return payload
    raise AssertionError(f"No structured {event} log found")


def test_booking_outcome_log_has_queryable_fields(caplog):
    with get_session_factory()() as session:
        associate = make_associate(session)
        room = session.query(Room).one()
        create_booking(
            session,
            associate_id=associate.id,
            room_id=room.id,
            start_at=at(10),
            end_at=at(11),
        )

    payload = _json_event(caplog, "booking_outcome")
    assert payload["action"] == "create"
    assert payload["result"] == "success"
    assert payload["associate_id"] == associate.id
    assert payload["room_id"] == room.id
    assert "timestamp" in payload


def test_tool_log_has_name_result_and_latency(caplog):
    ctx = ToolContext(
        session=MagicMock(),
        associate_email="ada@example.com",
        associate_name="Ada",
    )
    args = WindowArgs(start_at=at(10), end_at=at(11))
    with patch("app.agent.tools.svc_check_availability") as service:
        service.return_value = AvailabilityResult(
            available=True,
            requested=TimeWindow(start_at=args.start_at, end_at=args.end_at),
            conflict=None,
        )
        result = tool_check_availability(ctx, args)

    assert result.ok is True
    payload = _json_event(caplog, "agent_tool_call")
    assert payload["tool"] == "check_availability"
    assert payload["result"] == "success"
    assert isinstance(payload["latency_ms"], (int, float))
    assert "associate_email" not in payload


def test_reminder_log_has_booking_and_delivery_fields(caplog):
    notifier = MagicMock()
    notifier.send_vacate_reminder.return_value = ["brevo"]
    settings = Settings(
        database_url="sqlite:///:memory:",
        reminder_lead_minutes=15,
        reminder_back_to_back_tolerance_minutes=2,
    )
    with get_session_factory()() as session:
        associate = make_associate(session)
        follower = make_associate(session, name="Grace", email="grace@example.com")
        room = session.query(Room).one()
        current = make_booking(
            session,
            associate_id=associate.id,
            room_id=room.id,
            start_at=at(10),
            end_at=at(11),
        )
        make_booking(
            session,
            associate_id=follower.id,
            room_id=room.id,
            start_at=at(11),
            end_at=at(12),
        )

    assert run_vacate_reminder_job(
        now=at(10, 45), settings=settings, notification_service=notifier
    ) == 1
    payload = _json_event(caplog, "reminder_send")
    assert payload["booking_id"] == current.id
    assert payload["associate_id"] == associate.id
    assert payload["result"] == "success"
    assert payload["channels"] == ["brevo"]
