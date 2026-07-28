from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

import httpx

from app.config import Settings

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

logger = logging.getLogger(__name__)

EVENT_TEMPLATE_MAP = {
    "booking.confirmed": "brevo_template_booking_confirmed",
    "booking.extended": "brevo_template_booking_extended",
    "booking.cancelled": "brevo_template_booking_cancelled",
    "booking.vacate_reminder": "brevo_template_vacate_reminder",
    "waitlist.slot_available": "brevo_template_waitlist_available",
}


@dataclass
class BrevoSendResult:
    success: bool
    error: str | None = None
    status_code: int | None = None
    event: str | None = None
    recipient: str | None = None


class BrevoClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(
        self,
        event: str,
        recipient_email: str,
        recipient_name: str,
        payload: Mapping[str, Any],
    ) -> BrevoSendResult:
        if not self.settings.brevo_api_key:
            message = "Missing Brevo API key"
            logger.error(
                "brevo.send failure event=%s recipient=%s error=%s",
                event,
                recipient_email,
                message,
            )
            return BrevoSendResult(success=False, error=message, event=event, recipient=recipient_email)

        if not self.settings.brevo_sender_email:
            message = "Missing Brevo sender email"
            logger.error(
                "brevo.send failure event=%s recipient=%s error=%s",
                event,
                recipient_email,
                message,
            )
            return BrevoSendResult(success=False, error=message, event=event, recipient=recipient_email)

        try:
            template_id = self._get_template_id_for_event(event)
        except ValueError as exc:
            message = str(exc)
            logger.error(
                "brevo.send failure event=%s recipient=%s error=%s",
                event,
                recipient_email,
                message,
            )
            return BrevoSendResult(success=False, error=message, event=event, recipient=recipient_email)

        request_body = {
            "sender": {
                "email": self.settings.brevo_sender_email,
                "name": self.settings.brevo_sender_name,
            },
            "to": [{"email": recipient_email, "name": recipient_name}],
            "templateId": template_id,
            "params": self._build_template_params(event, recipient_name, payload),
        }

        try:
            response = httpx.post(
                BREVO_API_URL,
                headers={"api-key": self.settings.brevo_api_key},
                json=request_body,
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            response = exc.response
            status_code = response.status_code if response is not None else None
            error_text = self._extract_response_error(response) or str(exc)
            logger.error(
                "brevo.send failure event=%s recipient=%s template_id=%s status_code=%s error=%s",
                event,
                recipient_email,
                template_id,
                status_code,
                error_text,
            )
            return BrevoSendResult(
                success=False,
                error=error_text,
                status_code=status_code,
                event=event,
                recipient=recipient_email,
            )
        except httpx.RequestError as exc:
            message = str(exc)
            logger.error(
                "brevo.send failure event=%s recipient=%s template_id=%s error=%s",
                event,
                recipient_email,
                template_id,
                message,
            )
            return BrevoSendResult(
                success=False,
                error=message,
                event=event,
                recipient=recipient_email,
            )
        except Exception as exc:
            message = str(exc)
            logger.exception(
                "brevo.send unexpected failure event=%s recipient=%s template_id=%s",
                event,
                recipient_email,
                template_id,
            )
            return BrevoSendResult(
                success=False,
                error=message,
                event=event,
                recipient=recipient_email,
            )

        logger.info(
            "brevo.send success event=%s recipient=%s template_id=%s",
            event,
            recipient_email,
            template_id,
        )
        return BrevoSendResult(success=True, event=event, recipient=recipient_email, status_code=response.status_code)

    def _get_template_id_for_event(self, event: str) -> int:
        attr = EVENT_TEMPLATE_MAP.get(event)
        if attr is None:
            raise ValueError(f"Unsupported Brevo event: {event}")

        template_value = getattr(self.settings, attr, None)
        if not template_value:
            raise ValueError(f"Missing Brevo template ID for event {event}")

        try:
            return int(template_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid Brevo template ID for event {event}: {template_value}"
            ) from exc

    def _build_template_params(
        self, event: str, recipient_name: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        params = {
            "recipient_name": recipient_name,
            "room_name": payload.get("room_name"),
            "start_at": self._normalize(payload.get("start_at")),
            "end_at": self._normalize(payload.get("end_at")),
            "purpose": payload.get("purpose"),
            "lead_minutes": payload.get("lead_minutes"),
            "previous_end_at": self._normalize(payload.get("previous_end_at")),
            "waitlist_entry_id": payload.get("waitlist_entry_id"),
        }
        return {k: v for k, v in params.items() if v is not None}

    @staticmethod
    def _normalize(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return value

    @staticmethod
    def _extract_response_error(response: httpx.Response | None) -> str | None:
        if response is None:
            return None
        try:
            json_body = response.json()
        except ValueError:
            return response.text
        if isinstance(json_body, dict):
            return json_body.get("message") or json_body.get("error") or str(json_body)
        return str(json_body)
