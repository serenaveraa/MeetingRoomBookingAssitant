"""Shared event-to-human-readable content mapping for notification channels."""

from __future__ import annotations

from collections.abc import Mapping


def render_notification(event: str, payload: Mapping[str, object]) -> tuple[str, str]:
    room = payload.get("room_name", "the meeting room")
    start = payload.get("start_at", "the scheduled start time")
    end = payload.get("end_at", "the scheduled end time")
    if event == "booking.confirmed":
        return "Meeting room booking confirmed", f"Your booking for {room} from {start} to {end} has been confirmed."
    if event == "booking.extended":
        previous_end = payload.get("previous_end_at", "the previous end time")
        return "Meeting room booking extended", f"Your booking for {room} has been extended from {previous_end} to {end}."
    if event == "booking.cancelled":
        return "Meeting room booking cancelled", f"Your booking for {room} from {start} to {end} has been cancelled."
    if event == "booking.vacate_reminder":
        lead = payload.get("lead_minutes", 15)
        return "Please vacate the meeting room soon", f"Your meeting will end in {lead} minutes. Another meeting is scheduled immediately after yours. Kindly vacate the room."
    return "Meeting room notification", str(payload.get("message", "You have a meeting room update."))
