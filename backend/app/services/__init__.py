from app.services.associates import get_associate_by_email, get_or_create_associate
from app.services.availability import (
    AvailabilityResult,
    TimeWindow,
    check_availability,
    suggest_alternatives,
)
from app.services.booking import (
    cancel_booking,
    cancel_my_meeting,
    create_booking,
    extend_booking,
    extend_my_meeting,
    find_conflicts,
    find_current_booking,
    find_next_booking,
    get_odc_room,
    list_bookings,
    resolve_my_meeting,
    update_booking_window,
)
from app.services.errors import (
    BookingConflictError,
    BookingNotFoundError,
    BookingServiceError,
    InvalidBookingWindowError,
    MyMeetingNotFoundError,
)

__all__ = [
    "AvailabilityResult",
    "BookingConflictError",
    "BookingNotFoundError",
    "BookingServiceError",
    "InvalidBookingWindowError",
    "MyMeetingNotFoundError",
    "TimeWindow",
    "cancel_booking",
    "cancel_my_meeting",
    "check_availability",
    "create_booking",
    "extend_booking",
    "extend_my_meeting",
    "find_conflicts",
    "find_current_booking",
    "find_next_booking",
    "get_associate_by_email",
    "get_odc_room",
    "get_or_create_associate",
    "list_bookings",
    "resolve_my_meeting",
    "suggest_alternatives",
    "update_booking_window",
]
