from __future__ import annotations

from typing import Any


def format_tool_outcomes(tool_results: list[dict[str, Any]]) -> list[str]:
    """Human-readable lines highlighting bookings, conflicts, and alternatives."""
    lines: list[str] = []
    by_name = {r.get("tool"): r for r in tool_results if isinstance(r, dict)}

    create = by_name.get("create_booking")
    if create:
        if create.get("ok"):
            data = create.get("data") or {}
            lines.append(
                f"Booking confirmed — #{data.get('id')} "
                f"{data.get('start_at', '')} -> {data.get('end_at', '')}"
            )
        else:
            lines.append(
                f"Booking conflict — {create.get('error') or 'slot unavailable'}"
            )
            alts = by_name.get("suggest_alternatives")
            if alts and alts.get("ok"):
                windows = (alts.get("data") or {}).get("alternatives") or []
                if windows:
                    lines.append("Suggested alternatives:")
                    for window in windows:
                        lines.append(
                            f"  - {window.get('start_at')} -> {window.get('end_at')}"
                        )
                else:
                    lines.append("No same-duration free slots found today.")

    extend = by_name.get("extend_booking")
    if extend:
        if extend.get("ok"):
            data = extend.get("data") or {}
            lines.append(
                f"Meeting extended — now ends {data.get('end_at')} "
                f"(+{data.get('extended_by_minutes')} min)"
            )
        else:
            lines.append(f"Could not extend — {extend.get('error')}")

    cancel = by_name.get("cancel_booking")
    if cancel:
        if cancel.get("ok"):
            data = cancel.get("data") or {}
            lines.append(
                f"Booking cancelled — #{data.get('id')} "
                f"{data.get('start_at')} -> {data.get('end_at')}"
            )
        else:
            lines.append(f"Could not cancel — {cancel.get('error')}")

    avail = by_name.get("check_availability")
    if avail and avail.get("ok"):
        data = avail.get("data") or {}
        status = "free" if data.get("available") else "busy"
        lines.append(
            f"Availability — room is {status} "
            f"({data.get('start_at')} -> {data.get('end_at')})"
        )

    util = by_name.get("get_utilization_summary")
    if util and util.get("ok"):
        data = util.get("data") or {}
        lines.append(
            f"Utilization {data.get('day')}: {data.get('booking_count')} booking(s), "
            f"{data.get('total_booked_minutes')} booked minutes"
        )

    for result in tool_results:
        name = result.get("tool")
        if name in {
            "create_booking",
            "suggest_alternatives",
            "extend_booking",
            "cancel_booking",
            "check_availability",
            "get_utilization_summary",
        }:
            continue
        if not result.get("ok"):
            lines.append(f"{name} failed — {result.get('error')}")

    return lines
