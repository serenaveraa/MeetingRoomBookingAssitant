from unittest.mock import MagicMock

import pytest

from app.agent.graph import apply_clarification_guard, invoke_agent
from app.agent.schema import AgentDecision, ExtractedEntities, Intent


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


def test_clarification_guard_forces_start_time_for_book():
    decision = _decision(
        entities=ExtractedEntities(date="tomorrow", duration_minutes=30),
        needs_clarification=False,
    )
    guarded = apply_clarification_guard(decision)
    assert guarded.needs_clarification is True
    assert guarded.clarification_question
    assert "start" in guarded.clarification_question.lower()


def test_clarification_guard_allows_book_with_start():
    decision = _decision(
        entities=ExtractedEntities(
            date="2026-07-16",
            start_time="14:00",
            end_time="15:00",
            purpose="Standup",
        ),
        assistant_message="I can book 14:00–15:00.",
    )
    guarded = apply_clarification_guard(decision)
    assert guarded.needs_clarification is False


def test_invoke_agent_clear_book_request_no_session_skips_tools(monkeypatch):
    booked = _decision(
        entities=ExtractedEntities(
            date="2026-07-16",
            start_time="14:00",
            end_time="15:00",
        ),
        assistant_message="Understood: book 14:00–15:00.",
    )

    mock_model = MagicMock()
    structured = MagicMock()
    structured.invoke.return_value = booked
    mock_model.with_structured_output.return_value = structured

    import app.services.booking as booking_mod

    monkeypatch.setattr(
        booking_mod,
        "create_booking",
        MagicMock(side_effect=AssertionError("DB write must not happen")),
    )

    turn = invoke_agent(
        "Book the room today from 2 PM to 3 PM",
        associate_email="ada@example.com",
        associate_name="Ada",
        model=mock_model,
    )
    assert turn.decision.intent == Intent.book
    assert turn.decision.needs_clarification is False
    assert turn.decision.entities.start_time == "14:00"
    assert turn.tool_results == []
    booking_mod.create_booking.assert_not_called()


def test_invoke_agent_missing_start_clarifies(monkeypatch):
    incomplete = _decision(
        entities=ExtractedEntities(date="tomorrow", duration_minutes=30),
        needs_clarification=False,
        assistant_message="Booking tomorrow for 30 minutes.",
    )
    mock_model = MagicMock()
    structured = MagicMock()
    structured.invoke.return_value = incomplete
    mock_model.with_structured_output.return_value = structured

    turn = invoke_agent(
        "Book room tomorrow for 30 minutes",
        associate_email="ada@example.com",
        associate_name="Ada",
        model=mock_model,
    )
    assert turn.decision.intent == Intent.book
    assert turn.decision.needs_clarification is True
    assert turn.decision.clarification_question
