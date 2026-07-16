from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.agent.llm import LLMNotConfiguredError
from app.agent.schema import AgentDecision, AgentTurn, ExtractedEntities, Intent
from app.config import get_settings
from app.db import init_db, reset_engine
from app.main import app


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
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _turn(**kwargs) -> AgentTurn:
    decision = AgentDecision(
        intent=kwargs.get("intent", Intent.book),
        entities=kwargs.get("entities", ExtractedEntities()),
        needs_clarification=kwargs.get("needs_clarification", False),
        clarification_question=kwargs.get("clarification_question"),
        assistant_message=kwargs.get("assistant_message", "OK"),
    )
    return AgentTurn(
        decision=decision,
        associate_email=kwargs.get("associate_email", "ada@example.com"),
        associate_name=kwargs.get("associate_name", "Ada"),
        user_message=kwargs.get("user_message", "hi"),
        tool_results=kwargs.get("tool_results", []),
        final_message=kwargs.get("final_message", decision.assistant_message),
    )


def test_chat_happy_path(client: TestClient, monkeypatch):
    mock_turn = _turn(
        intent=Intent.book,
        entities=ExtractedEntities(
            date="2026-07-16",
            start_time="14:00",
            end_time="15:00",
            purpose="Standup",
        ),
        final_message="Booked the room from 14:00 to 15:00 (booking #1).",
        tool_results=[
            {
                "tool": "create_booking",
                "ok": True,
                "data": {"id": 1},
                "error": None,
                "error_type": None,
            }
        ],
    )
    mock_invoke = MagicMock(return_value=mock_turn)
    monkeypatch.setattr("app.api.agent.invoke_agent", mock_invoke)

    response = client.post(
        "/agent/chat",
        json={
            "message": "Book tomorrow 2-3 PM for standup",
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
            "conversation_id": "conv-1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reply"].startswith("Booked")
    assert body["conversation_id"] == "conv-1"
    assert body["intent"] == "book"
    assert body["needs_clarification"] is False
    assert body["tool_results"][0]["tool"] == "create_booking"
    mock_invoke.assert_called_once()
    assert mock_invoke.call_args.kwargs["associate_email"] == "ada@example.com"
    assert mock_invoke.call_args.kwargs["session"] is not None


def test_chat_clarification(client: TestClient, monkeypatch):
    mock_turn = _turn(
        intent=Intent.book,
        entities=ExtractedEntities(date="tomorrow", duration_minutes=30),
        needs_clarification=True,
        clarification_question="What start time?",
        final_message="I need a start time before I can continue. What start time?",
        tool_results=[],
    )
    monkeypatch.setattr("app.api.agent.invoke_agent", MagicMock(return_value=mock_turn))

    response = client.post(
        "/agent/chat",
        json={
            "message": "Book room tomorrow for 30 minutes",
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["needs_clarification"] is True
    assert body["clarification_question"]
    assert body["tool_results"] == []
    assert body["conversation_id"]  # generated UUID when omitted


def test_chat_llm_not_configured(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.api.agent.invoke_agent",
        MagicMock(side_effect=LLMNotConfiguredError("Set GROQ_API_KEY")),
    )
    response = client.post(
        "/agent/chat",
        json={
            "message": "Hello",
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
        },
    )
    assert response.status_code == 503
    assert "GROQ" in response.json()["detail"] or "Set" in response.json()["detail"]


def test_chat_provider_error(client: TestClient, monkeypatch):
    class FakeRateLimitError(Exception):
        pass

    FakeRateLimitError.__name__ = "RateLimitError"
    monkeypatch.setattr(
        "app.api.agent.invoke_agent",
        MagicMock(side_effect=FakeRateLimitError("quota exceeded")),
    )
    response = client.post(
        "/agent/chat",
        json={
            "message": "Book something",
            "associate_email": "ada@example.com",
            "associate_name": "Ada",
        },
    )
    assert response.status_code == 502
    assert "LLM provider" in response.json()["detail"]


def test_chat_validation_error(client: TestClient):
    response = client.post(
        "/agent/chat",
        json={"message": "", "associate_email": "bad", "associate_name": ""},
    )
    assert response.status_code == 422
