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
        description="Meeting date as YYYY-MM-DD or relative phrase like 'tomorrow'",
    )
    start_time: str | None = Field(
        default=None,
        description="Start time like '14:00' or '2 PM'",
    )
    end_time: str | None = Field(
        default=None,
        description="End time like '15:00' or '3 PM'",
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
        description="Natural-language reply to show the user for this turn"
    )


class AgentTurn(BaseModel):
    decision: AgentDecision
    associate_email: str
    associate_name: str
    user_message: str
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    final_message: str = ""
