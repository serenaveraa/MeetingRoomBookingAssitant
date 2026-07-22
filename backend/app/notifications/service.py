"""Channel-agnostic notification fan-out service."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime

from app.config import Settings, get_settings
from app.models import Associate, Booking, WaitlistEntry
from app.notifications.channels import (
    BrevoChannel,
    ChannelResult,
    NotificationChannel,
    TeamsWebhookChannel,
)

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        channels: Sequence[NotificationChannel] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.channels = list(channels) if channels is not None else self._enabled_channels()

    def _enabled_channels(self) -> list[NotificationChannel]:
        channels: list[NotificationChannel] = []
        if BrevoChannel.is_enabled(self.settings):
            channels.append(BrevoChannel(self.settings))
        if TeamsWebhookChannel.is_enabled(self.settings):
            channels.append(TeamsWebhookChannel(self.settings))
        return channels

    def notify(
        self,
        event: str,
        associate: Associate,
        payload: Mapping[str, object],
    ) -> list[ChannelResult]:
        """Fan an event out to every enabled channel independently."""
        results: list[ChannelResult] = []
        for channel in self.channels:
            try:
                channel.send(event, associate, payload)
            except Exception as exc:
                logger.exception(
                    "Notification channel failed event=%s channel=%s recipient=%s",
                    event,
                    channel.name,
                    associate.email,
                )
                results.append(ChannelResult(channel.name, success=False, error=str(exc)))
            else:
                results.append(ChannelResult(channel.name, success=True))
        return results

    # Compatibility helpers for existing callers. New callers should use
    # notify(event, associate, payload) directly.
    def send_vacate_reminder(self, booking: Booking) -> list[str]:
        return self._successful_channels(
            self.notify(
                "booking.vacate_reminder",
                self._associate(booking),
                self._booking_payload(booking, lead_minutes=self.settings.reminder_lead_minutes),
            ),
            require_delivery=True,
        )

    def send_booking_confirmation(self, booking: Booking) -> list[str]:
        return self._successful_channels(
            self.notify("booking.confirmed", self._associate(booking), self._booking_payload(booking)),
            require_delivery=True,
        )

    def send_booking_extended(self, booking: Booking, *, previous_end_at: datetime) -> list[str]:
        return self._successful_channels(
            self.notify("booking.extended", self._associate(booking), self._booking_payload(booking, previous_end_at=previous_end_at)),
            require_delivery=True,
        )

    def send_booking_cancelled(self, booking: Booking) -> list[str]:
        return self._successful_channels(
            self.notify("booking.cancelled", self._associate(booking), self._booking_payload(booking)),
            require_delivery=True,
        )

    def send_waitlist_slot_available(self, entry: WaitlistEntry) -> list[str]:
        return self._successful_channels(
            self.notify(
                "waitlist.slot_available",
                entry.associate,
                {
                    "waitlist_entry_id": entry.id,
                    "room_name": entry.room.name,
                    "start_at": entry.desired_start,
                    "end_at": entry.desired_end,
                },
            ),
            require_delivery=True,
        )

    @staticmethod
    def _associate(booking: Booking) -> Associate:
        if booking.associate is None:
            raise RuntimeError(f"Booking {booking.id} has no notification recipient")
        return booking.associate

    @staticmethod
    def _booking_payload(booking: Booking, **extra: object) -> dict[str, object]:
        return {
            "booking_id": booking.id,
            "room_name": booking.room.name,
            "start_at": booking.start_at,
            "end_at": booking.end_at,
            "purpose": booking.purpose,
            **extra,
        }

    @staticmethod
    def _successful_channels(
        results: Sequence[ChannelResult], *, require_delivery: bool = False
    ) -> list[str]:
        successful = [result.channel for result in results if result.success]
        if require_delivery and not successful:
            raise RuntimeError("No notification channel delivered the reminder")
        return successful
