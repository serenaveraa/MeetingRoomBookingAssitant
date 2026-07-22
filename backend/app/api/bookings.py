from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    AvailabilityOut,
    BookingOut,
    ConflictOut,
    CreateBookingIn,
    CreateWaitlistIn,
    ExtendBookingIn,
    TimeWindowOut,
    UtilizationDayOut,
    UtilizationOut,
    WaitlistOut,
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
from app.services.utilization import get_utilization_summary
from app.services.waitlist import create_waitlist_entry

router = APIRouter(prefix="/bookings", tags=["bookings"])
insights_router = APIRouter(prefix="/insights", tags=["insights"])
waitlist_router = APIRouter(prefix="/waitlist", tags=["waitlist"])


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


def _waitlist_out(entry) -> WaitlistOut:
    return WaitlistOut.model_validate(entry)


@waitlist_router.post("", response_model=WaitlistOut, status_code=status.HTTP_201_CREATED)
def post_waitlist_entry(body: CreateWaitlistIn, db: Session = Depends(get_db)) -> WaitlistOut:
    associate = get_or_create_associate(
        db, email=str(body.associate_email), name=body.associate_name
    )
    try:
        entry = create_waitlist_entry(
            db,
            associate_id=associate.id,
            room_id=body.room_id,
            desired_start=ensure_utc(body.desired_start),
            desired_end=ensure_utc(body.desired_end),
        )
    except InvalidBookingWindowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _waitlist_out(entry)


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
            "conflicting_associate_name": exc.conflicting_associate_name,
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


@insights_router.get("/utilization", response_model=UtilizationOut)
def get_utilization(
    start_date: date = Query(..., alias="start_date"),
    end_date: date = Query(..., alias="end_date"),
    db: Session = Depends(get_db),
) -> UtilizationOut:
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")
    try:
        summary = get_utilization_summary(db, start_date=start_date, end_date=end_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return UtilizationOut(
        start_date=summary.start_date,
        end_date=summary.end_date,
        booking_count=summary.booking_count,
        total_booked_minutes=summary.total_booked_minutes,
        avg_duration_minutes=summary.avg_duration_minutes,
        idle_gap_count=summary.idle_gap_count,
        business_minutes=summary.business_minutes,
        bookings_per_day=[
            UtilizationDayOut(
                day=entry["day"],
                booking_count=int(entry["booking_count"]),
                total_booked_minutes=int(entry["total_booked_minutes"]),
                avg_duration_minutes=float(entry["avg_duration_minutes"]),
                idle_gap_count=int(entry["idle_gap_count"]),
                business_minutes=int(entry["business_minutes"]),
            )
            for entry in summary.bookings_per_day
        ],
        busiest_day=summary.busiest_day,
        summary=summary.overall_summary,
    )


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
