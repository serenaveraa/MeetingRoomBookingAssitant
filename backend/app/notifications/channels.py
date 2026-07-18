"""Transport adapters for independently enabled notification channels."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings
from app.models import Associate
from app.notifications.content import render_notification


@dataclass(frozen=True)
class ChannelResult:
    channel: str
    success: bool
    error: str | None = None


class NotificationChannel(Protocol):
    name: str

    def send(
        self, event: str, associate: Associate, payload: Mapping[str, object]
    ) -> None: ...


class BrevoChannel:
    name = "brevo"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @classmethod
    def is_enabled(cls, settings: Settings) -> bool:
        return bool(settings.brevo_api_key and settings.brevo_sender_email)

    def send(
        self, event: str, associate: Associate, payload: Mapping[str, object]
    ) -> None:
        subject, text = render_notification(event, payload)
        response = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": self.settings.brevo_api_key},
            json={
                "sender": {
                    "email": self.settings.brevo_sender_email,
                    "name": self.settings.brevo_sender_name,
                },
                "to": [{"email": associate.email, "name": associate.name}],
                "subject": subject,
                "textContent": text,
            },
            timeout=10.0,
        )
        response.raise_for_status()


class TeamsWebhookChannel:
    name = "teams"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @classmethod
    def is_enabled(cls, settings: Settings) -> bool:
        return bool(settings.teams_notifications_enabled and settings.teams_webhook_url)

    def send(
        self, event: str, associate: Associate, payload: Mapping[str, object]
    ) -> None:
        subject, text = render_notification(event, payload)
        response = httpx.post(
            self.settings.teams_webhook_url,
            # Incoming Webhooks accept this simple MessageCard-compatible text
            # payload; richer Adaptive Cards can be added without changing the
            # service API.
            json={"text": f"{subject}\n\n{text}"},
            timeout=10.0,
        )
        response.raise_for_status()
