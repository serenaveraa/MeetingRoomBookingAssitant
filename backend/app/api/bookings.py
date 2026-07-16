from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    AvailabilityOut,
    BookingOut,
    ConflictOut,
    CreateBookingIn,
    ExtendBookingIn,
    TimeWindowOut,
)
from app.db import get_db
from app.models import Booking, BookingStatus
from app.services.associates import get_or_create_associate
from app.services.availability import check_availability, suggest_alternatives
from app.services.booking import (
    cancel_booking,
    create_booking,
    extend_booking,
    list_bookings,
)
from app.services.errors import (
    BookingConflictError,
    BookingNotFoundError,
    InvalidBookingWindowError,
)
from app.services.timeutil import ensure_utc

router = APIRouter(prefix="/bookings", tags=["bookings"])


def _booking_out(booking: Booking) -> BookingOut:
    associate = booking.associate
    return BookingOut(
        id=booking.id,
        room_id=booking.room_id,
        associate_id=booking.associate_id,
        associate_email=associate.email if associate else None,
        associate_name=associate.name if associate else None,
        purpose=booking.purpose,
        start_at=booking.start_at,
        end_at=booking.end_at,
        status=booking.status,
    )


def _require_owner(booking: Booking, associate_email: str) -> None:
    owner_email = booking.associate.email if booking.associate else None
    if owner_email is None or owner_email.lower() != associate_email.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the booking owner can perform this action",
        )


def _conflict_http(exc: BookingConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": str(exc),
            "conflict": ConflictOut(
                booking_id=exc.conflicting_booking_id,
                associate_id=exc.conflicting_associate_id,
                start_at=exc.conflicting_start_at,
                end_at=exc.conflicting_end_at,
            ).model_dump(mode="json"),
        },
    )


@router.get("", response_model=list[BookingOut])
def get_bookings(
    start_at: datetime = Query(..., description="Range start (inclusive)"),
    end_at: datetime = Query(..., description="Range end (exclusive)"),
    status_filter: BookingStatus | None = Query(
        BookingStatus.confirmed,
        alias="status",
        description="Filter by status; omit or null for all",
    ),
    db: Session = Depends(get_db),
) -> list[BookingOut]:
    try:
        bookings = list_bookings(
            db,
            start_at=start_at,
            end_at=end_at,
            status=status_filter,
        )
    except InvalidBookingWindowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Ensure associate relationship is loaded for response.
    for booking in bookings:
        _ = booking.associate
    return [_booking_out(b) for b in bookings]


@router.get("/availability", response_model=AvailabilityOut)
def get_availability(
    start_at: datetime = Query(...),
    end_at: datetime = Query(...),
    db: Session = Depends(get_db),
) -> AvailabilityOut:
    try:
        result = check_availability(db, start_at, end_at)
    except InvalidBookingWindowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    alternatives: list[TimeWindowOut] = []
    conflict: ConflictOut | None = None
    if not result.available and result.conflict is not None:
        conflict = ConflictOut(
            booking_id=result.conflict.id,
            associate_id=result.conflict.associate_id,
            start_at=result.conflict.start_at,
            end_at=result.conflict.end_at,
        )
        alternatives = [
            TimeWindowOut(start_at=w.start_at, end_at=w.end_at)
            for w in suggest_alternatives(db, start_at, end_at)
        ]

    return AvailabilityOut(
        available=result.available,
        requested=TimeWindowOut(
            start_at=result.requested.start_at,
            end_at=result.requested.end_at,
        ),
        conflict=conflict,
        alternatives=alternatives,
    )


@router.post("", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def post_booking(
    body: CreateBookingIn,
    db: Session = Depends(get_db),
) -> BookingOut:
    associate = get_or_create_associate(
        db,
        email=str(body.associate_email),
        name=body.associate_name,
    )
    try:
        booking = create_booking(
            db,
            associate_id=associate.id,
            start_at=ensure_utc(body.start_at),
            end_at=ensure_utc(body.end_at),
            purpose=body.purpose,
        )
    except InvalidBookingWindowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BookingConflictError as exc:
        raise _conflict_http(exc) from exc

    _ = booking.associate
    return _booking_out(booking)


@router.patch("/{booking_id}/extend", response_model=BookingOut)
def patch_extend_booking(
    booking_id: int,
    body: ExtendBookingIn,
    db: Session = Depends(get_db),
    x_associate_email: str = Header(..., alias="X-Associate-Email"),
) -> BookingOut:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail=f"Booking {booking_id} not found")
    _ = booking.associate
    _require_owner(booking, x_associate_email)

    try:
        updated = extend_booking(db, booking_id, minutes=body.minutes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BookingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BookingConflictError as exc:
        raise _conflict_http(exc) from exc

    _ = updated.associate
    return _booking_out(updated)


@router.delete("/{booking_id}", response_model=BookingOut)
def delete_booking(
    booking_id: int,
    db: Session = Depends(get_db),
    x_associate_email: str = Header(..., alias="X-Associate-Email"),
) -> BookingOut:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail=f"Booking {booking_id} not found")
    _ = booking.associate
    _require_owner(booking, x_associate_email)

    try:
        cancelled = cancel_booking(db, booking_id)
    except BookingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _ = cancelled.associate
    return _booking_out(cancelled)
