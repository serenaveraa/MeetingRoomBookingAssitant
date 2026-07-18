"""Notification delivery used by background jobs.

The service keeps channel choice in one place: email is preferred when Brevo
is configured for the associate; otherwise the configured Teams webhook is
used.  Selecting one channel avoids retrying a successful first delivery when
another provider fails.
"""

from __future__ import annotations

import logging

import httpx

from app.config import Settings, get_settings
from app.models import Booking

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def send_vacate_reminder(self, booking: Booking) -> list[str]:
        """Deliver the vacate notice and return the channels used.

        A deployment without a configured channel is intentionally treated as
        a delivery failure.  Marking such a reminder as sent would silently
        lose the operational notification.
        """
        channels: list[str] = []
        message = (
            f"Your meeting will end in {self.settings.reminder_lead_minutes} minutes. Another meeting is "
            "scheduled immediately after yours. Kindly vacate the room."
        )
        if self.settings.brevo_api_key and self.settings.brevo_sender_email:
            self._send_brevo(booking, message)
            channels.append("brevo")
        elif self.settings.teams_webhook_url:
            self._send_teams(booking, message)
            channels.append("teams")
        if not channels:
            raise RuntimeError("No notification channel is configured for vacate reminders")
        return channels

    def _send_brevo(self, booking: Booking, message: str) -> None:
        recipient = booking.associate
        if recipient is None:
            raise RuntimeError(f"Booking {booking.id} has no notification recipient")
        payload = {
            "sender": {
                "email": self.settings.brevo_sender_email,
                "name": self.settings.brevo_sender_name,
            },
            "to": [{"email": recipient.email, "name": recipient.name}],
            "subject": "Please vacate the meeting room soon",
            "textContent": message,
        }
        response = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": self.settings.brevo_api_key},
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()

    def _send_teams(self, booking: Booking, message: str) -> None:
        response = httpx.post(
            self.settings.teams_webhook_url,
            json={"text": message},
            timeout=10.0,
        )
        response.raise_for_status()
