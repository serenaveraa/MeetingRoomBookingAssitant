from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Intent(str, Enum):
    availability = "availability"
    book = "book"
    extend = "extend"
    cancel = "cancel"
    insights = "insights"
    other = "other"


class ExtractedEntities(BaseModel):
    date: str | None = Field(
        default=None,
        description=(
            "Meeting date as YYYY-MM-DD or relative phrase like 'tomorrow'. "
            "Keep any year the associate named exactly."
        ),
    )
    start_time: str | None = Field(
        default=None,
        description="Start time like '14:00', '2 PM', or '12:00' (from '12 hs')",
    )
    end_time: str | None = Field(
        default=None,
        description="End time like '15:00', '3 PM', or '13:00' (from '13 hs')",
    )
    duration_minutes: int | None = Field(
        default=None,
        description="Duration in minutes when end time is not given",
    )
    purpose: str | None = Field(
        default=None,
        description="Meeting purpose if mentioned",
    )


class AgentDecision(BaseModel):
    intent: Intent
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    needs_clarification: bool = False
    clarification_question: str | None = None
    assistant_message: str = Field(
        description=(
            "User-facing reply for clarify/help. For unrelated, jailbreak, math, "
            "or code requests: brief meeting-room redirect only — never answer them."
        )
    )


class AgentTurn(BaseModel):
    decision: AgentDecision
    associate_email: str
    associate_name: str
    user_message: str
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    final_message: str = ""
