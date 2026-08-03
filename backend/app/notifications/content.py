"""Shared event-to-human-readable content mapping for notification channels."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime

from app.config import get_settings
from app.local_time import format_local


def _local(value: object, fallback: str) -> str:
    if isinstance(value, (datetime, date, str)):
        formatted = format_local(value, tz_name=get_settings().odc_timezone)
        if formatted:
            return formatted
    if value is None:
        return fallback
    return str(value)


def render_notification(event: str, payload: Mapping[str, object]) -> tuple[str, str]:
    room = payload.get("room_name", "the meeting room")
    start = _local(payload.get("start_at"), "the scheduled start time")
    end = _local(payload.get("end_at"), "the scheduled end time")
    if event == "booking.confirmed":
        return "Meeting room booking confirmed", f"Your booking for {room} from {start} to {end} has been confirmed."
    if event == "booking.extended":
        previous_end = _local(payload.get("previous_end_at"), "the previous end time")
        return "Meeting room booking extended", f"Your booking for {room} has been extended from {previous_end} to {end}."
    if event == "booking.cancelled":
        return "Meeting room booking cancelled", f"Your booking for {room} from {start} to {end} has been cancelled."
    if event == "waitlist.slot_available":
        return "Meeting room slot available", f"The meeting room is now available from {start} to {end}."
    if event == "booking.vacate_reminder":
        lead = payload.get("lead_minutes", 15)
        return "Please vacate the meeting room soon", f"Your meeting will end in {lead} minutes. Another meeting is scheduled immediately after yours. Kindly vacate the room."
    return "Meeting room notification", str(payload.get("message", "You have a meeting room update."))
