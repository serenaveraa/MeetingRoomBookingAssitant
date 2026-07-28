from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.entities import EntityResolutionError, resolve_booking_window, resolve_day
from app.agent.schema import AgentDecision, Intent
from app.models import Booking
from app.services.associates import get_or_create_associate
from app.services.availability import (
    DEFAULT_ALTERNATIVE_LIMIT,
    check_availability as svc_check_availability,
    suggest_alternatives as svc_suggest_alternatives,
)
from app.services.booking import (
    cancel_my_meeting,
    create_booking as svc_create_booking,
    extend_my_meeting,
    list_my_bookings as svc_list_my_bookings,
)
from app.services.errors import (
    BookingConflictError,
    BookingServiceError,
    InvalidBookingWindowError,
    MyMeetingNotFoundError,
)
from app.services.timeutil import as_utc
from app.services.utilization import (
    get_utilization_summary as svc_get_utilization_summary,
    local_today,
)
from app.services.waitlist import create_waitlist_entry

logger = logging.getLogger(__name__)


class WindowArgs(BaseModel):
    start_at: datetime
    end_at: datetime


class CreateBookingArgs(WindowArgs):
    purpose: str | None = None


class CreateWaitlistArgs(WindowArgs):
    room_id: int = Field(gt=0)


class SuggestAlternativesArgs(WindowArgs):
    limit: int = Field(default=DEFAULT_ALTERNATIVE_LIMIT, ge=1, le=10)


class ExtendBookingArgs(BaseModel):
    minutes: int = Field(ge=1)


class ListMyBookingsArgs(WindowArgs):
    pass


class UtilizationArgs(BaseModel):
    day: date | None = None
    start_date: date | None = None
    end_date: date | None = None


@dataclass
class ToolContext:
    session: Session
    associate_email: str
    associate_name: str


class ToolResult(BaseModel):
    tool: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    error_type: str | None = None


def _iso(dt: datetime) -> str:
    return as_utc(dt).isoformat()


def _booking_payload(booking: Booking) -> dict[str, Any]:
    return {
        "id": booking.id,
        "associate_id": booking.associate_id,
        "purpose": booking.purpose,
        "start_at": _iso(booking.start_at),
        "end_at": _iso(booking.end_at),
        "status": booking.status.value if hasattr(booking.status, "value") else booking.status,
    }


def _conflict_payload(exc: BookingConflictError) -> dict[str, Any]:
    return {
        "conflicting_booking_id": exc.conflicting_booking_id,
        "conflicting_associate_id": exc.conflicting_associate_id,
        "conflicting_associate_name": exc.conflicting_associate_name,
        "conflicting_start_at": _iso(exc.conflicting_start_at),
        "conflicting_end_at": _iso(exc.conflicting_end_at),
        "requested_start_at": _iso(exc.start_at),
        "requested_end_at": _iso(exc.end_at),
    }


def _fail(
    tool: str,
    ctx: ToolContext,
    exc: Exception,
    *,
    data: dict[str, Any] | None = None,
    duration_ms: int | None = None,
) -> ToolResult:
    logger.info(
        "tool.call tool=%s associate_email=%s action=%s ok=false error_type=%s duration_ms=%s",
        tool,
        ctx.associate_email,
        tool,
        type(exc).__name__,
        duration_ms,
    )
    return ToolResult(
        tool=tool,
        ok=False,
        data=data or {},
        error=str(exc),
        error_type=type(exc).__name__,
    )


def _log_tool_call(
    tool: str,
    ctx: ToolContext,
    action: str,
    ok: bool,
    duration_ms: int,
) -> None:
    logger.info(
        "tool.call tool=%s associate_email=%s action=%s ok=%s duration_ms=%s",
        tool,
        ctx.associate_email,
        action,
        ok,
        duration_ms,
    )


def tool_check_availability(ctx: ToolContext, args: WindowArgs) -> ToolResult:
    start = datetime.now(timezone.utc)
    try:
        result = svc_check_availability(ctx.session, args.start_at, args.end_at)
        data: dict[str, Any] = {
            "available": result.available,
            "start_at": _iso(result.requested.start_at),
            "end_at": _iso(result.requested.end_at),
        }
        if result.conflict is not None:
            data["conflict"] = _booking_payload(result.conflict)
        _log_tool_call(
            "check_availability",
            ctx,
            action="availability",
            ok=True,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )
        return ToolResult(tool="check_availability", ok=True, data=data)
    except (InvalidBookingWindowError, BookingServiceError) as exc:
        return _fail(
            "check_availability",
            ctx,
            exc,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )


def tool_create_booking(ctx: ToolContext, args: CreateBookingArgs) -> ToolResult:
    start = datetime.now(timezone.utc)
    try:
        associate = get_or_create_associate(
            ctx.session,
            email=ctx.associate_email,
            name=ctx.associate_name,
        )
        booking = svc_create_booking(
            ctx.session,
            associate_id=associate.id,
            start_at=args.start_at,
            end_at=args.end_at,
            purpose=args.purpose,
        )
        result = ToolResult(
            tool="create_booking",
            ok=True,
            data=_booking_payload(booking),
        )
        _log_tool_call(
            "create_booking",
            ctx,
            action="create_booking",
            ok=True,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )
        return result
    except BookingConflictError as exc:
        return _fail(
            "create_booking",
            ctx,
            exc,
            data=_conflict_payload(exc),
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )
    except (InvalidBookingWindowError, BookingServiceError) as exc:
        return _fail(
            "create_booking",
            ctx,
            exc,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )


def tool_create_waitlist_entry(ctx: ToolContext, args: CreateWaitlistArgs) -> ToolResult:
    start = datetime.now(timezone.utc)
    try:
        associate = get_or_create_associate(
            ctx.session, email=ctx.associate_email, name=ctx.associate_name
        )
        entry = create_waitlist_entry(
            ctx.session,
            associate_id=associate.id,
            room_id=args.room_id,
            desired_start=args.start_at,
            desired_end=args.end_at,
        )
        result = ToolResult(
            tool="create_waitlist_entry",
            ok=True,
            data={
                "id": entry.id,
                "associate_id": entry.associate_id,
                "room_id": entry.room_id,
                "desired_start": _iso(entry.desired_start),
                "desired_end": _iso(entry.desired_end),
            },
        )
        _log_tool_call(
            "create_waitlist_entry",
            ctx,
            action="create_waitlist_entry",
            ok=True,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )
        return result
    except (InvalidBookingWindowError, BookingServiceError, ValueError) as exc:
        return _fail(
            "create_waitlist_entry",
            ctx,
            exc,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )


def tool_suggest_alternatives(
    ctx: ToolContext, args: SuggestAlternativesArgs
) -> ToolResult:
    start = datetime.now(timezone.utc)
    try:
        windows = svc_suggest_alternatives(
            ctx.session,
            args.start_at,
            args.end_at,
            limit=args.limit,
        )
        result = ToolResult(
            tool="suggest_alternatives",
            ok=True,
            data={
                "alternatives": [
                    {"start_at": _iso(w.start_at), "end_at": _iso(w.end_at)}
                    for w in windows
                ]
            },
        )
        _log_tool_call(
            "suggest_alternatives",
            ctx,
            action="suggest_alternatives",
            ok=True,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )
        return result
    except (InvalidBookingWindowError, BookingServiceError) as exc:
        return _fail(
            "suggest_alternatives",
            ctx,
            exc,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )


def tool_extend_booking(ctx: ToolContext, args: ExtendBookingArgs) -> ToolResult:
    start = datetime.now(timezone.utc)
    try:
        associate = get_or_create_associate(
            ctx.session,
            email=ctx.associate_email,
            name=ctx.associate_name,
        )
        booking = extend_my_meeting(
            ctx.session, associate.id, minutes=args.minutes
        )
        result = ToolResult(
            tool="extend_booking",
            ok=True,
            data={**_booking_payload(booking), "extended_by_minutes": args.minutes},
        )
        _log_tool_call(
            "extend_booking",
            ctx,
            action="extend_booking",
            ok=True,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )
        return result
    except BookingConflictError as exc:
        return _fail(
            "extend_booking",
            ctx,
            exc,
            data=_conflict_payload(exc),
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )
    except (MyMeetingNotFoundError, BookingServiceError, ValueError) as exc:
        return _fail(
            "extend_booking",
            ctx,
            exc,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )


def tool_cancel_booking(ctx: ToolContext) -> ToolResult:
    start = datetime.now(timezone.utc)
    try:
        associate = get_or_create_associate(
            ctx.session,
            email=ctx.associate_email,
            name=ctx.associate_name,
        )
        booking = cancel_my_meeting(ctx.session, associate.id)
        result = ToolResult(
            tool="cancel_booking",
            ok=True,
            data=_booking_payload(booking),
        )
        _log_tool_call(
            "cancel_booking",
            ctx,
            action="cancel_booking",
            ok=True,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )
        return result
    except (MyMeetingNotFoundError, BookingServiceError) as exc:
        return _fail(
            "cancel_booking",
            ctx,
            exc,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )


def tool_list_my_bookings(ctx: ToolContext, args: ListMyBookingsArgs) -> ToolResult:
    start = datetime.now(timezone.utc)
    try:
        associate = get_or_create_associate(
            ctx.session,
            email=ctx.associate_email,
            name=ctx.associate_name,
        )
        bookings = svc_list_my_bookings(
            ctx.session,
            associate.id,
            start_at=args.start_at,
            end_at=args.end_at,
        )
        result = ToolResult(
            tool="list_my_bookings",
            ok=True,
            data={"bookings": [_booking_payload(b) for b in bookings]},
        )
        _log_tool_call(
            "list_my_bookings",
            ctx,
            action="list_my_bookings",
            ok=True,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )
        return result
    except (InvalidBookingWindowError, BookingServiceError) as exc:
        return _fail(
            "list_my_bookings",
            ctx,
            exc,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )


def tool_get_utilization_summary(
    ctx: ToolContext, args: UtilizationArgs
) -> ToolResult:
    start = datetime.now(timezone.utc)
    try:
        summary = svc_get_utilization_summary(
            ctx.session,
            day=args.day,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        result = ToolResult(
            tool="get_utilization_summary",
            ok=True,
            data={
                "start_date": summary.start_date.isoformat(),
                "end_date": summary.end_date.isoformat(),
                "booking_count": summary.booking_count,
                "total_booked_minutes": summary.total_booked_minutes,
                "avg_duration_minutes": summary.avg_duration_minutes,
                "idle_gap_count": summary.idle_gap_count,
                "business_minutes": summary.business_minutes,
                "bookings_per_day": summary.bookings_per_day,
                "busiest_day": summary.busiest_day,
                "summary": summary.overall_summary,
            },
        )
        _log_tool_call(
            "get_utilization_summary",
            ctx,
            action="get_utilization_summary",
            ok=True,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )
        return result
    except (BookingServiceError, ValueError) as exc:
        return _fail(
            "get_utilization_summary",
            ctx,
            exc,
            duration_ms=int((datetime.now(timezone.utc) - start).total_seconds() * 1000),
        )


def _window_from_decision(decision: AgentDecision) -> tuple[datetime, datetime]:
    return resolve_booking_window(decision.entities)


def run_tools_for_intent(
    decision: AgentDecision, ctx: ToolContext
) -> list[ToolResult]:
    """Deterministically invoke tools for the extracted intent."""
    if decision.needs_clarification or decision.intent == Intent.other:
        return []

    results: list[ToolResult] = []

    if decision.intent == Intent.availability:
        try:
            start_at, end_at = _window_from_decision(decision)
        except EntityResolutionError as exc:
            return [_fail("check_availability", ctx, exc)]
        results.append(
            tool_check_availability(ctx, WindowArgs(start_at=start_at, end_at=end_at))
        )
        return results

    if decision.intent == Intent.book:
        try:
            start_at, end_at = _window_from_decision(decision)
        except EntityResolutionError as exc:
            return [_fail("create_booking", ctx, exc)]
        create_result = tool_create_booking(
            ctx,
            CreateBookingArgs(
                start_at=start_at,
                end_at=end_at,
                purpose=decision.entities.purpose,
            ),
        )
        results.append(create_result)
        if not create_result.ok and create_result.error_type == "BookingConflictError":
            results.append(
                tool_suggest_alternatives(
                    ctx,
                    SuggestAlternativesArgs(start_at=start_at, end_at=end_at),
                )
            )
        return results

    if decision.intent == Intent.extend:
        minutes = decision.entities.duration_minutes
        if minutes is None or minutes <= 0:
            return [
                _fail(
                    "extend_booking",
                    ctx,
                    EntityResolutionError("duration_minutes is required to extend"),
                )
            ]
        results.append(
            tool_extend_booking(ctx, ExtendBookingArgs(minutes=minutes))
        )
        return results

    if decision.intent == Intent.cancel:
        results.append(tool_cancel_booking(ctx))
        return results

    if decision.intent == Intent.insights:
        day = resolve_day(decision.entities.date, today=local_today())
        results.append(
            tool_get_utilization_summary(ctx, UtilizationArgs(day=day))
        )
        return results

    return results


def compose_reply(decision: AgentDecision, results: list[ToolResult]) -> str:
    """Deterministic user-facing message from tool outcomes."""
    if decision.needs_clarification:
        return decision.assistant_message or decision.clarification_question or (
            "I need a bit more detail before I can continue."
        )

    if not results:
        return decision.assistant_message or "How can I help with the meeting room?"

    by_name = {r.tool: r for r in results}
    primary = results[0]

    if primary.tool == "check_availability":
        if not primary.ok:
            return f"I couldn't check availability: {primary.error}"
        if primary.data.get("available"):
            return (
                f"The room is free from {primary.data['start_at']} "
                f"to {primary.data['end_at']}."
            )
        return (
            f"The room is busy from {primary.data['start_at']} "
            f"to {primary.data['end_at']}."
        )

    if primary.tool == "create_booking":
        if primary.ok:
            return (
                f"Booked the room from {primary.data['start_at']} "
                f"to {primary.data['end_at']} (booking #{primary.data['id']})."
            )
        alts = by_name.get("suggest_alternatives")
        lines = [f"That slot isn't available: {primary.error}"]
        if alts and alts.ok and alts.data.get("alternatives"):
            lines.append("Nearest alternatives:")
            for window in alts.data["alternatives"]:
                lines.append(f"- {window['start_at']} → {window['end_at']}")
        elif alts and alts.ok:
            lines.append("I couldn't find another free slot of that length today.")
        return "\n".join(lines)

    if primary.tool == "extend_booking":
        if primary.ok:
            return (
                f"Extended your meeting by {primary.data['extended_by_minutes']} "
                f"minutes (now ends {primary.data['end_at']})."
            )
        return f"Couldn't extend your meeting: {primary.error}"

    if primary.tool == "cancel_booking":
        if primary.ok:
            return (
                f"Cancelled your booking #{primary.data['id']} "
                f"({primary.data['start_at']} → {primary.data['end_at']})."
            )
        return f"Couldn't cancel your meeting: {primary.error}"

    if primary.tool == "list_my_bookings":
        if not primary.ok:
            return f"Couldn't list your bookings: {primary.error}"
        bookings = primary.data.get("bookings") or []
        if not bookings:
            return "You have no bookings in that window."
        lines = ["Your bookings:"]
        for booking in bookings:
            lines.append(
                f"- #{booking['id']}: {booking['start_at']} → {booking['end_at']}"
            )
        return "\n".join(lines)

    if primary.tool == "get_utilization_summary":
        if not primary.ok:
            return f"Couldn't load utilization: {primary.error}"
        d = primary.data
        if d.get("summary"):
            return d["summary"]
        return (
            f"Utilization for {d['start_date']} to {d['end_date']}: "
            f"{d['booking_count']} booking(s), {d['total_booked_minutes']} booked minutes, "
            f"average duration {d['avg_duration_minutes']} min, and "
            f"{d['idle_gap_count']} idle gap(s) within business hours."
        )

    if primary.ok:
        return decision.assistant_message or "Done."
    return primary.error or decision.assistant_message or "Something went wrong."
