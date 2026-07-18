from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.models import Associate
from app.notifications.channels import BrevoChannel, TeamsWebhookChannel
from app.notifications.service import NotificationService


@pytest.fixture
def associate() -> Associate:
    return Associate(id=7, name="Ada", email="ada@example.com")


def _channel(name: str) -> MagicMock:
    channel = MagicMock()
    channel.name = name
    return channel


def test_notify_fans_out_to_every_enabled_channel(associate):
    brevo = _channel("brevo")
    teams = _channel("teams")
    service = NotificationService(channels=[brevo, teams])
    payload = {"room_name": "ODC", "start_at": "10:00", "end_at": "11:00"}

    results = service.notify("booking.confirmed", associate, payload)

    brevo.send.assert_called_once_with("booking.confirmed", associate, payload)
    teams.send.assert_called_once_with("booking.confirmed", associate, payload)
    assert [result.channel for result in results] == ["brevo", "teams"]
    assert all(result.success for result in results)


def test_brevo_succeeds_when_teams_is_unconfigured(monkeypatch, associate):
    settings = Settings(brevo_api_key="key", brevo_sender_email="sender@example.com")
    send = MagicMock()
    monkeypatch.setattr(BrevoChannel, "send", send)
    service = NotificationService(settings)

    results = service.notify("booking.confirmed", associate, {})

    assert [channel.name for channel in service.channels] == ["brevo"]
    send.assert_called_once()
    assert results[0].success is True


def test_channel_failures_do_not_block_other_channels(associate):
    brevo = _channel("brevo")
    brevo.send.side_effect = RuntimeError("Brevo unavailable")
    teams = _channel("teams")
    service = NotificationService(channels=[brevo, teams])

    results = service.notify("booking.cancelled", associate, {})

    teams.send.assert_called_once()
    assert [(result.channel, result.success) for result in results] == [
        ("brevo", False),
        ("teams", True),
    ]
    assert results[0].error == "Brevo unavailable"


@pytest.mark.parametrize(
    ("enabled", "url", "expected"),
    [(False, "https://teams.example/webhook", False), (True, "", False), (True, "https://teams.example/webhook", True)],
)
def test_teams_enablement_requires_flag_and_webhook(enabled, url, expected):
    settings = Settings(teams_notifications_enabled=enabled, teams_webhook_url=url)
    assert TeamsWebhookChannel.is_enabled(settings) is expected


def test_teams_webhook_uses_readable_incoming_webhook_payload(monkeypatch, associate):
    settings = Settings(
        teams_notifications_enabled=True,
        teams_webhook_url="https://teams.example/webhook",
    )
    post = MagicMock()
    response = MagicMock()
    post.return_value = response
    monkeypatch.setattr("app.notifications.channels.httpx.post", post)

    TeamsWebhookChannel(settings).send(
        "booking.confirmed",
        associate,
        {"room_name": "ODC", "start_at": "10:00", "end_at": "11:00"},
    )

    post.assert_called_once_with(
        "https://teams.example/webhook",
        json={
            "text": "Meeting room booking confirmed\n\nYour booking for ODC from 10:00 to 11:00 has been confirmed."
        },
        timeout=10.0,
    )
    response.raise_for_status.assert_called_once()
