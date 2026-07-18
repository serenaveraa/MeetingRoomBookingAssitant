"""Best-effort, post-commit booking-mutation notifications."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from app.models import Booking
from app.notifications import NotificationService

logger = logging.getLogger(__name__)

BookingNotificationEvent = Literal[
    "booking.confirmed", "booking.extended", "booking.cancelled"
]


def dispatch_booking_notification(
    event: BookingNotificationEvent,
    booking: Booking,
    *,
    previous_end_at: datetime | None = None,
    notification_service: NotificationService | None = None,
) -> None:
    """Send a committed booking event without affecting the API mutation.

    There is intentionally no outbox in this small service yet. Failures are
    logged with enough identifying data for a manual/operator retry.
    """
    notifier = notification_service or NotificationService()
    recipient = booking.associate.email if booking.associate else None
    try:
        if event == "booking.confirmed":
            channels = notifier.send_booking_confirmation(booking)
        elif event == "booking.extended":
            if previous_end_at is None:
                raise ValueError("previous_end_at is required for extension notification")
            channels = notifier.send_booking_extended(
                booking, previous_end_at=previous_end_at
            )
        else:
            channels = notifier.send_booking_cancelled(booking)
        logger.info(
            "Booking notification sent booking_id=%s event=%s recipient=%s channels=%s",
            booking.id,
            event,
            recipient,
            ",".join(channels),
        )
    except Exception:
        logger.exception(
            "Booking notification failed booking_id=%s event=%s recipient=%s; "
            "the committed mutation can be retried manually",
            booking.id,
            event,
            recipient,
        )
