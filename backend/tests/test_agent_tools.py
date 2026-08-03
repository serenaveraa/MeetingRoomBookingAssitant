from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.agent.entities import (
    EntityResolutionError,
    extract_explicit_calendar_date,
    resolve_booking_window,
    resolve_day,
)
from app.agent.graph import (
    apply_clarification_guard,
    apply_entity_grounding,
    apply_scope_guard,
    invoke_agent,
)
from app.agent.prompts import build_system_prompt
from app.agent.schema import AgentDecision, ExtractedEntities, Intent
from app.agent.tools import (
    OTHER_SCOPE_REPLY,
    CreateBookingArgs,
    ExtendBookingArgs,
    ListMyBookingsArgs,
    SuggestAlternativesArgs,
    ToolContext,
    ToolResult,
    UtilizationArgs,
    WindowArgs,
    compose_reply,
    run_tools_for_intent,
    tool_cancel_booking,
    tool_check_availability,
    tool_create_booking,
    tool_extend_booking,
    tool_get_utilization_summary,
    tool_list_my_bookings,
    tool_suggest_alternatives,
)
from app.services.availability import AvailabilityResult, TimeWindow
from app.services.errors import (
    BookingConflictError,
    InvalidBookingWindowError,
    MyMeetingNotFoundError,
)
from app.services.timeutil import get_odc_tz
from app.services.utilization import UtilizationSummary


def _decision(**kwargs) -> AgentDecision:
    base = dict(
        intent=Intent.book,
        entities=ExtractedEntities(),
        needs_clarification=False,
        clarification_question=None,
        assistant_message="OK",
    )
    base.update(kwargs)
    return AgentDecision(**base)


def _ctx() -> ToolContext:
    return ToolContext(
        session=MagicMock(),
        associate_email="ada@example.com",
        associate_name="Ada",
    )


def _booking_mock(**kwargs):
    booking = MagicMock()
    booking.id = kwargs.get("id", 1)
    booking.associate_id = kwargs.get("associate_id", 10)
    booking.purpose = kwargs.get("purpose", "Standup")
    booking.start_at = kwargs.get(
        "start_at", datetime(2026, 7, 16, 17, 0, tzinfo=timezone.utc)
    )
    booking.end_at = kwargs.get(
        "end_at", datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
    )
    booking.status = kwargs.get("status", MagicMock(value="confirmed"))
    return booking


def test_resolve_booking_window_with_end_time():
    start, end = resolve_booking_window(
        ExtractedEntities(
            date="2026-07-16",
            start_time="14:00",
            end_time="15:00",
        ),
        today=date(2026, 7, 16),
    )
    assert start < end
    assert start.tzinfo is not None


def test_resolve_booking_window_duration_and_relative_day():
    start, end = resolve_booking_window(
        ExtractedEntities(
            date="tomorrow",
            start_time="2 PM",
            duration_minutes=30,
        ),
        today=date(2026, 7, 16),
    )
    assert (end - start).total_seconds() == 30 * 60


def test_resolve_day_month_names_and_weekday_phrases():
    saturday = date(2026, 8, 1)
    assert resolve_day("august 3rd", today=saturday) == date(2026, 8, 3)
    assert resolve_day("on August 3, 2026", today=saturday) == date(2026, 8, 3)
    assert resolve_day("3 of august", today=saturday) == date(2026, 8, 3)
    assert resolve_day("next Monday", today=saturday) == date(2026, 8, 3)
    assert resolve_day("friday", today=saturday) == date(2026, 8, 7)
    assert resolve_day("in 3 days", today=saturday) == date(2026, 8, 4)
    assert resolve_day("day after tomorrow", today=saturday) == date(2026, 8, 3)
    assert resolve_day("Saturday, August 8th", today=saturday) == date(2026, 8, 8)
    assert resolve_day("Monday august 3", today=saturday) == date(2026, 8, 3)
    assert resolve_day("January 1s 2025", today=saturday) == date(2025, 1, 1)


def test_resolve_day_rolls_month_without_year_forward():
    assert resolve_day("january 5", today=date(2026, 8, 1)) == date(2027, 1, 5)


def test_resolve_day_rejects_unknown_phrase():
    with pytest.raises(EntityResolutionError):
        resolve_day("whenever you like", today=date(2026, 8, 1))


def test_parse_spanish_hs_times():
    start, end = resolve_booking_window(
        ExtractedEntities(
            date="2026-08-03",
            start_time="12 hs",
            end_time="13 hs",
        ),
        today=date(2026, 8, 1),
    )
    local_tz = get_odc_tz()
    assert start.astimezone(local_tz).hour == 12
    assert end.astimezone(local_tz).hour == 13


def test_extract_explicit_calendar_date_keeps_named_year():
    today = date(2026, 8, 3)
    assert (
        extract_explicit_calendar_date(
            "book the room for January 1s 2025 from 12 to 13 hs",
            today=today,
        )
        == date(2025, 1, 1)
    )


def test_apply_entity_grounding_blocks_past_year_and_wrong_roll_forward():
    # Model invents 2027; user named 2025 — ground then clarify as past.
    decision = _decision(
        entities=ExtractedEntities(
            date="2027-01-01",
            start_time="12:00",
            end_time="13:00",
        ),
        assistant_message="Booking January 2027.",
    )
    grounded = apply_entity_grounding(
        decision,
        "book the room for January 1s 2025 from 12 to 13 hs",
        today=date(2026, 8, 3),
    )
    assert grounded.entities.date == "2025-01-01"
    assert grounded.needs_clarification is True
    assert "past" in (grounded.assistant_message or "").lower()
    assert run_tools_for_intent(grounded, _ctx()) == []


def test_apply_scope_guard_and_compose_reply_refuse_off_topic():
    decision = _decision(
        intent=Intent.other,
        assistant_message=(
            "The square root of 2 is approximately 1.414. "
            "Here is HTML: <html><body>Hello</body></html>"
        ),
    )
    guarded = apply_scope_guard(decision)
    assert guarded.assistant_message == OTHER_SCOPE_REPLY
    assert "1.414" not in guarded.assistant_message
    assert "<html>" not in guarded.assistant_message
    reply = compose_reply(guarded, [])
    assert reply == OTHER_SCOPE_REPLY
    assert "pi" not in reply.lower()


def test_system_prompt_states_scope_and_date_fidelity():
    prompt = build_system_prompt(
        odc_timezone="America/Sao_Paulo", today=date(2026, 8, 1)
    )
    assert "2026-08-01" in prompt
    assert "Saturday" in prompt
    assert "Never answer off-topic" in prompt or "Never answer off-topic requests" in prompt
    assert "explicit calendar year" in prompt
    assert "jailbreak" in prompt.lower() or "Ignore" in prompt or "instruction overrides" in prompt


def test_resolve_booking_window_corrects_stale_model_year():
    # A Monday the model mislabelled with its training year (2025-08-03 was a Sunday).
    start, _ = resolve_booking_window(
        ExtractedEntities(date="2025-08-03", start_time="2 PM", end_time="3 PM"),
        today=date(2026, 8, 1),
    )
    assert start.astimezone(get_odc_tz()).date() == date(2026, 8, 3)


def test_resolve_booking_window_keeps_explicitly_named_year():
    start, _ = resolve_booking_window(
        ExtractedEntities(date="august 3 2025", start_time="2 PM", end_time="3 PM"),
        today=date(2026, 8, 1),
    )
    assert start.astimezone(get_odc_tz()).date() == date(2025, 8, 3)


def test_resolve_booking_window_requires_start():
    with pytest.raises(EntityResolutionError):
        resolve_booking_window(
            ExtractedEntities(date="2026-07-16", duration_minutes=30)
        )


def test_clarification_guard_extend_needs_duration():
    guarded = apply_clarification_guard(
        _decision(intent=Intent.extend, entities=ExtractedEntities())
    )
    assert guarded.needs_clarification is True
    assert "minute" in (guarded.clarification_question or "").lower()


def test_clarification_guard_extend_with_duration_ok():
    guarded = apply_clarification_guard(
        _decision(
            intent=Intent.extend,
            entities=ExtractedEntities(duration_minutes=15),
        )
    )
    assert guarded.needs_clarification is False


def test_tool_check_availability_delegates():
    ctx = _ctx()
    args = WindowArgs(
        start_at=datetime(2026, 7, 16, 17, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc),
    )
    fake = AvailabilityResult(
        available=True,
        requested=TimeWindow(start_at=args.start_at, end_at=args.end_at),
        conflict=None,
    )
    with patch("app.agent.tools.svc_check_availability", return_value=fake) as mock_svc:
        result = tool_check_availability(ctx, args)
    mock_svc.assert_called_once_with(ctx.session, args.start_at, args.end_at)
    assert result.ok is True
    assert result.data["available"] is True


def test_tool_create_booking_delegates():
    ctx = _ctx()
    args = CreateBookingArgs(
        start_at=datetime(2026, 7, 16, 17, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc),
        purpose="Standup",
    )
    associate = MagicMock(id=10)
    booking = _booking_mock()
    with (
        patch("app.agent.tools.get_or_create_associate", return_value=associate),
        patch("app.agent.tools.svc_create_booking", return_value=booking) as mock_create,
    ):
        result = tool_create_booking(ctx, args)
    mock_create.assert_called_once()
    assert result.ok is True
    assert result.data["id"] == 1


def test_tool_suggest_alternatives_delegates():
    ctx = _ctx()
    args = SuggestAlternativesArgs(
        start_at=datetime(2026, 7, 16, 17, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc),
    )
    windows = [
        TimeWindow(
            start_at=datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 7, 16, 19, 0, tzinfo=timezone.utc),
        )
    ]
    with patch(
        "app.agent.tools.svc_suggest_alternatives", return_value=windows
    ) as mock_alts:
        result = tool_suggest_alternatives(ctx, args)
    mock_alts.assert_called_once()
    assert result.ok is True
    assert len(result.data["alternatives"]) == 1


def test_tool_extend_booking_delegates():
    ctx = _ctx()
    associate = MagicMock(id=10)
    booking = _booking_mock()
    with (
        patch("app.agent.tools.get_or_create_associate", return_value=associate),
        patch("app.agent.tools.extend_my_meeting", return_value=booking) as mock_ext,
    ):
        result = tool_extend_booking(ctx, ExtendBookingArgs(minutes=15))
    mock_ext.assert_called_once_with(ctx.session, 10, minutes=15)
    assert result.ok is True
    assert result.data["extended_by_minutes"] == 15


def test_tool_cancel_booking_delegates():
    ctx = _ctx()
    associate = MagicMock(id=10)
    booking = _booking_mock()
    with (
        patch("app.agent.tools.get_or_create_associate", return_value=associate),
        patch("app.agent.tools.cancel_my_meeting", return_value=booking) as mock_cancel,
    ):
        result = tool_cancel_booking(ctx)
    mock_cancel.assert_called_once_with(ctx.session, 10)
    assert result.ok is True


def test_tool_list_my_bookings_delegates():
    ctx = _ctx()
    args = ListMyBookingsArgs(
        start_at=datetime(2026, 7, 16, 11, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 7, 17, 3, 0, tzinfo=timezone.utc),
    )
    associate = MagicMock(id=10)
    with (
        patch("app.agent.tools.get_or_create_associate", return_value=associate),
        patch(
            "app.agent.tools.svc_list_my_bookings", return_value=[_booking_mock()]
        ) as mock_list,
    ):
        result = tool_list_my_bookings(ctx, args)
    mock_list.assert_called_once()
    assert result.ok is True
    assert len(result.data["bookings"]) == 1


def test_tool_get_utilization_summary_delegates():
    ctx = _ctx()
    summary = UtilizationSummary(
        start_date=date(2026, 7, 16),
        end_date=date(2026, 7, 16),
        booking_count=2,
        total_booked_minutes=90,
        avg_duration_minutes=45.0,
        idle_gap_count=3,
        business_minutes=600,
        bookings_per_day=[
            {
                "day": "2026-07-16",
                "booking_count": 2,
                "total_booked_minutes": 90,
                "avg_duration_minutes": 45.0,
                "idle_gap_count": 3,
                "business_minutes": 600,
            }
        ],
        day=date(2026, 7, 16),
    )
    with patch(
        "app.agent.tools.svc_get_utilization_summary", return_value=summary
    ) as mock_util:
        result = tool_get_utilization_summary(
            ctx, UtilizationArgs(day=date(2026, 7, 16))
        )
    mock_util.assert_called_once()
    assert result.ok is True
    assert result.data["booking_count"] == 2


def test_run_tools_book_conflict_triggers_alternatives():
    ctx = _ctx()
    decision = _decision(
        entities=ExtractedEntities(
            date="2026-07-16",
            start_time="14:00",
            end_time="15:00",
        )
    )
    conflict = BookingConflictError(
        start_at=datetime(2026, 7, 16, 17, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc),
        conflicting_booking_id=9,
        conflicting_start_at=datetime(2026, 7, 16, 17, 0, tzinfo=timezone.utc),
        conflicting_end_at=datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc),
        conflicting_associate_id=2,
        conflicting_associate_name="Bob",
    )
    associate = MagicMock(id=10)
    alts = [
        TimeWindow(
            start_at=datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 7, 16, 19, 0, tzinfo=timezone.utc),
        )
    ]
    with (
        patch("app.agent.tools.get_or_create_associate", return_value=associate),
        patch(
            "app.agent.tools.svc_create_booking",
            side_effect=conflict,
        ),
        patch("app.agent.tools.svc_suggest_alternatives", return_value=alts) as mock_alts,
    ):
        results = run_tools_for_intent(decision, ctx)

    assert [r.tool for r in results] == ["create_booking", "suggest_alternatives"]
    assert results[0].ok is False
    assert results[0].error_type == "BookingConflictError"
    assert results[1].ok is True
    mock_alts.assert_called_once()
    reply = compose_reply(decision, results)
    assert "isn't available" in reply.lower() or "not available" in reply.lower()
    assert "alternative" in reply.lower()


def test_compose_reply_weekend_rejection_is_not_framed_as_conflict():
    from app.services.schedule import WEEKEND_BOOKING_MESSAGE

    results = [
        ToolResult(
            tool="create_booking",
            ok=False,
            error=WEEKEND_BOOKING_MESSAGE,
            error_type="InvalidBookingWindowError",
        )
    ]
    reply = compose_reply(_decision(), results)
    assert reply == WEEKEND_BOOKING_MESSAGE
    assert "isn't available" not in reply


def test_system_prompt_states_today_in_odc_timezone():
    prompt = build_system_prompt(
        odc_timezone="America/Sao_Paulo", today=date(2026, 8, 1)
    )
    assert "2026-08-01" in prompt
    assert "Saturday" in prompt


def test_run_tools_skips_when_clarification_needed():
    decision = _decision(
        needs_clarification=True,
        entities=ExtractedEntities(date="tomorrow", duration_minutes=30),
    )
    assert run_tools_for_intent(decision, _ctx()) == []


def test_invoke_agent_book_success_runs_create(monkeypatch):
    booked = _decision(
        entities=ExtractedEntities(
            date="2026-08-10",
            start_time="14:00",
            end_time="15:00",
        ),
        assistant_message="Understood.",
    )
    mock_model = MagicMock()
    structured = MagicMock()
    structured.invoke.return_value = booked
    mock_model.with_structured_output.return_value = structured

    associate = MagicMock(id=10)
    booking = _booking_mock()
    session = MagicMock()

    with (
        patch("app.agent.tools.get_or_create_associate", return_value=associate),
        patch("app.agent.tools.svc_create_booking", return_value=booking) as mock_create,
    ):
        turn = invoke_agent(
            "Book August 10 from 2-3 PM",
            associate_email="ada@example.com",
            associate_name="Ada",
            session=session,
            model=mock_model,
        )

    mock_create.assert_called_once()
    assert turn.tool_results
    assert turn.tool_results[0]["tool"] == "create_booking"
    assert turn.tool_results[0]["ok"] is True
    assert "Booked" in turn.final_message


def test_invoke_agent_clarify_skips_tools():
    incomplete = _decision(
        entities=ExtractedEntities(date="tomorrow", duration_minutes=30),
        needs_clarification=False,
        assistant_message="Booking tomorrow.",
    )
    mock_model = MagicMock()
    structured = MagicMock()
    structured.invoke.return_value = incomplete
    mock_model.with_structured_output.return_value = structured

    with patch("app.agent.tools.tool_create_booking") as mock_create:
        turn = invoke_agent(
            "Book tomorrow 30 minutes",
            associate_email="ada@example.com",
            associate_name="Ada",
            session=MagicMock(),
            model=mock_model,
        )

    mock_create.assert_not_called()
    assert turn.decision.needs_clarification is True
    assert turn.tool_results == []


def test_invoke_agent_extend_and_cancel(monkeypatch):
    mock_model = MagicMock()
    structured = MagicMock()
    mock_model.with_structured_output.return_value = structured
    associate = MagicMock(id=10)
    booking = _booking_mock()
    session = MagicMock()

    structured.invoke.return_value = _decision(
        intent=Intent.extend,
        entities=ExtractedEntities(duration_minutes=15),
        assistant_message="Extending.",
    )
    with (
        patch("app.agent.tools.get_or_create_associate", return_value=associate),
        patch("app.agent.tools.extend_my_meeting", return_value=booking) as mock_ext,
    ):
        turn = invoke_agent(
            "Extend by 15 minutes",
            associate_email="ada@example.com",
            associate_name="Ada",
            session=session,
            model=mock_model,
        )
    mock_ext.assert_called_once()
    assert turn.tool_results[0]["tool"] == "extend_booking"
    assert "Extended" in turn.final_message

    structured.invoke.return_value = _decision(
        intent=Intent.cancel,
        assistant_message="Cancelling.",
    )
    with (
        patch("app.agent.tools.get_or_create_associate", return_value=associate),
        patch("app.agent.tools.cancel_my_meeting", return_value=booking) as mock_cancel,
    ):
        turn = invoke_agent(
            "Cancel my meeting",
            associate_email="ada@example.com",
            associate_name="Ada",
            session=session,
            model=mock_model,
        )
    mock_cancel.assert_called_once()
    assert turn.tool_results[0]["tool"] == "cancel_booking"
    assert "Cancelled" in turn.final_message


def test_tool_extend_not_found():
    ctx = _ctx()
    associate = MagicMock(id=10)
    with (
        patch("app.agent.tools.get_or_create_associate", return_value=associate),
        patch(
            "app.agent.tools.extend_my_meeting",
            side_effect=MyMeetingNotFoundError(10),
        ),
    ):
        result = tool_extend_booking(ctx, ExtendBookingArgs(minutes=15))
    assert result.ok is False
    assert result.error_type == "MyMeetingNotFoundError"


def test_tool_failure_paths_are_structured_without_external_calls():
    ctx = _ctx()
    window = WindowArgs(
        start_at=datetime(2026, 7, 16, 17, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc),
    )
    associate = MagicMock(id=10)
    conflict = BookingConflictError(
        start_at=window.start_at,
        end_at=window.end_at,
        conflicting_booking_id=9,
        conflicting_start_at=window.start_at,
        conflicting_end_at=window.end_at,
        conflicting_associate_id=2,
    )
    with (
        patch(
            "app.agent.tools.svc_check_availability",
            side_effect=InvalidBookingWindowError(window.start_at, window.end_at),
        ),
        patch(
            "app.agent.tools.get_or_create_associate", return_value=associate
        ),
        patch("app.agent.tools.svc_create_booking", side_effect=conflict),
        patch(
            "app.agent.tools.svc_suggest_alternatives",
            side_effect=InvalidBookingWindowError(window.start_at, window.end_at),
        ),
        patch(
            "app.agent.tools.cancel_my_meeting",
            side_effect=MyMeetingNotFoundError(associate.id),
        ),
    ):
        availability = tool_check_availability(ctx, window)
        booking = tool_create_booking(
            ctx,
            CreateBookingArgs(
                start_at=window.start_at,
                end_at=window.end_at,
            ),
        )
        alternatives = tool_suggest_alternatives(ctx, SuggestAlternativesArgs(**window.model_dump()))
        cancelled = tool_cancel_booking(ctx)

    assert availability.ok is False
    assert availability.error_type == "InvalidBookingWindowError"
    assert booking.ok is False
    assert booking.error_type == "BookingConflictError"
    assert booking.data["conflicting_booking_id"] == 9
    assert alternatives.ok is False
    assert alternatives.error_type == "InvalidBookingWindowError"
    assert cancelled.ok is False
    assert cancelled.error_type == "MyMeetingNotFoundError"
