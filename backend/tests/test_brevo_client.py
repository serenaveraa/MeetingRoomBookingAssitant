from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.config import Settings
from app.notifications.brevo import BrevoClient, BrevoSendResult


@pytest.fixture
def settings() -> Settings:
    return Settings(
        brevo_api_key="test-key",
        brevo_sender_email="sender@example.com",
        brevo_sender_name="ODC Meeting Room",
        brevo_template_booking_confirmed="101",
        brevo_template_booking_extended="102",
        brevo_template_booking_cancelled="103",
        brevo_template_vacate_reminder="104",
        brevo_template_waitlist_available="105",
    )


@patch("app.notifications.brevo.httpx.post")
def test_send_booking_confirmed_uses_template(post, settings):
    response = MagicMock(spec=httpx.Response)
    response.status_code = 201
    response.raise_for_status.return_value = None
    post.return_value = response

    client = BrevoClient(settings)
    result = client.send(
        "booking.confirmed",
        recipient_email="ada@example.com",
        recipient_name="Ada",
        payload={"room_name": "ODC", "start_at": datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc), "end_at": datetime(2026, 7, 25, 11, 0, tzinfo=timezone.utc)},
    )

    assert result.success is True
    post.assert_called_once()
    assert post.call_args.kwargs["json"]["templateId"] == 101
    assert post.call_args.kwargs["json"]["sender"]["email"] == "sender@example.com"


@patch("app.notifications.brevo.httpx.post")
def test_send_extended_uses_template(post, settings):
    response = MagicMock(spec=httpx.Response)
    response.status_code = 201
    response.raise_for_status.return_value = None
    post.return_value = response

    client = BrevoClient(settings)
    result = client.send(
        "booking.extended",
        recipient_email="ada@example.com",
        recipient_name="Ada",
        payload={
            "room_name": "ODC",
            "start_at": datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc),
            "end_at": datetime(2026, 7, 25, 11, 30, tzinfo=timezone.utc),
            "previous_end_at": datetime(2026, 7, 25, 11, 0, tzinfo=timezone.utc),
        },
    )

    assert result.success is True
    assert post.call_args.kwargs["json"]["templateId"] == 102
    assert post.call_args.kwargs["json"]["templateId"] == 102


@patch("app.notifications.brevo.httpx.post")
def test_send_cancelled_uses_template(post, settings):
    response = MagicMock(spec=httpx.Response)
    response.status_code = 201
    response.raise_for_status.return_value = None
    post.return_value = response

    client = BrevoClient(settings)
    result = client.send(
        "booking.cancelled",
        recipient_email="ada@example.com",
        recipient_name="Ada",
        payload={"room_name": "ODC", "start_at": datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc), "end_at": datetime(2026, 7, 25, 11, 0, tzinfo=timezone.utc)},
    )

    assert result.success is True
    assert post.call_args.kwargs["json"]["templateId"] == 103


@patch("app.notifications.brevo.httpx.post")
def test_send_vacate_reminder_uses_template(post, settings):
    response = MagicMock(spec=httpx.Response)
    response.status_code = 201
    response.raise_for_status.return_value = None
    post.return_value = response

    client = BrevoClient(settings)
    result = client.send(
        "booking.vacate_reminder",
        recipient_email="ada@example.com",
        recipient_name="Ada",
        payload={"room_name": "ODC", "start_at": datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc), "end_at": datetime(2026, 7, 25, 11, 0, tzinfo=timezone.utc), "lead_minutes": 15},
    )

    assert result.success is True
    assert post.call_args.kwargs["json"]["templateId"] == 104


@patch("app.notifications.brevo.httpx.post")
def test_send_waitlist_available_uses_template(post, settings):
    response = MagicMock(spec=httpx.Response)
    response.status_code = 201
    response.raise_for_status.return_value = None
    post.return_value = response

    client = BrevoClient(settings)
    result = client.send(
        "waitlist.slot_available",
        recipient_email="ada@example.com",
        recipient_name="Ada",
        payload={"room_name": "ODC", "start_at": datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc), "end_at": datetime(2026, 7, 25, 11, 0, tzinfo=timezone.utc)},
    )

    assert result.success is True
    assert post.call_args.kwargs["json"]["templateId"] == 105


def test_send_returns_failure_result_on_http_error(settings):
    post = MagicMock()
    response = MagicMock(spec=httpx.Response)
    response.status_code = 400
    response.json.return_value = {"message": "Invalid template"}
    response.raise_for_status.side_effect = httpx.HTTPStatusError("Bad Request", request=MagicMock(), response=response)
    post.return_value = response

    with patch("app.notifications.brevo.httpx.post", post):
        client = BrevoClient(settings)
        result = client.send(
            "booking.confirmed",
            recipient_email="ada@example.com",
            recipient_name="Ada",
            payload={"room_name": "ODC", "start_at": datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc), "end_at": datetime(2026, 7, 25, 11, 0, tzinfo=timezone.utc)},
        )

    assert result.success is False
    assert result.error == "Invalid template"
    assert result.status_code == 400


def test_send_returns_failure_result_when_template_id_missing(settings):
    settings.brevo_template_booking_confirmed = ""
    client = BrevoClient(settings)

    result = client.send(
        "booking.confirmed",
        recipient_email="ada@example.com",
        recipient_name="Ada",
        payload={"room_name": "ODC"},
    )

    assert result.success is False
    assert "Missing Brevo template ID" in result.error
