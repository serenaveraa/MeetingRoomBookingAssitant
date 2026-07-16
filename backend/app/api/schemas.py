from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

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
