from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from app.agent.schema import ExtractedEntities, Intent
from app.models import BookingStatus


class TimeWindowOut(BaseModel):
    start_at: datetime
    end_at: datetime


class BookingOut(BaseModel):
    id: int
    room_id: int
    associate_id: int
    associate_email: str | None = None
    associate_name: str | None = None
    purpose: str | None
    start_at: datetime
    end_at: datetime
    status: BookingStatus

    model_config = {"from_attributes": True}


class ConflictOut(BaseModel):
    booking_id: int
    associate_id: int | None = None
    start_at: datetime
    end_at: datetime


class CreateBookingIn(BaseModel):
    associate_email: EmailStr
    associate_name: str = Field(min_length=1, max_length=120)
    start_at: datetime
    end_at: datetime
    purpose: str | None = None


class ExtendBookingIn(BaseModel):
    minutes: int = Field(gt=0, description="Minutes to add to the booking end time")


class AvailabilityOut(BaseModel):
    available: bool
    requested: TimeWindowOut
    conflict: ConflictOut | None = None
    alternatives: list[TimeWindowOut] = Field(default_factory=list)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    associate_email: EmailStr
    associate_name: str = Field(min_length=1, max_length=120)
    conversation_id: str | None = Field(
        default=None,
        max_length=120,
        description="Optional client conversation id; echoed in the response",
    )


class ChatOut(BaseModel):
    reply: str
    conversation_id: str
    associate_email: str
    associate_name: str
    intent: Intent
    needs_clarification: bool = False
    clarification_question: str | None = None
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
