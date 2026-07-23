from __future__ import annotations

import logging

from app.models import Booking
from app.observability import emit_event
from app.services.errors import OwnershipError

logger = logging.getLogger(__name__)


def require_booking_owner(booking: Booking, associate_id: int) -> None:
    """Enforce the current single-owner booking model."""
    if booking.associate_id == associate_id:
        return
    emit_event(
        logger,
        "booking_authorization",
        result="denied",
        action="mutate",
        booking_id=booking.id,
        associate_id=associate_id,
        owner_associate_id=booking.associate_id,
        reason="not_owner",
    )
    raise OwnershipError(booking.id, associate_id)