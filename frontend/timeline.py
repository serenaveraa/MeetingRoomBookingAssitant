from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

ODC_TIMEZONE = "America/Sao_Paulo"
BUSINESS_DAY_START = time(8, 0)
BUSINESS_DAY_END = time(18, 0)

SegmentKind = Literal["busy", "free"]


@dataclass(frozen=True)
class DaySegment:
    kind: SegmentKind
    start_local: datetime
    end_local: datetime
    label: str
    booking_id: int | None = None
    associate_name: str | None = None
    purpose: str | None = None


def get_odc_tz() -> ZoneInfo:
    return ZoneInfo(ODC_TIMEZONE)


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC-aware bounds for [midnight, next midnight) in ODC local time."""
    tz = get_odc_tz()
    start_local = datetime.combine(day, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local, end_local


def business_hours_bounds(day: date) -> tuple[datetime, datetime]:
    tz = get_odc_tz()
    start_local = datetime.combine(day, BUSINESS_DAY_START, tzinfo=tz)
    end_local = datetime.combine(day, BUSINESS_DAY_END, tzinfo=tz)
    return start_local, end_local


def _parse_api_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(get_odc_tz())


def build_day_segments(
    bookings: list[dict[str, Any]],
    day: date,
) -> list[DaySegment]:
    """Occupied bookings + free gaps inside business hours for `day`."""
    biz_start, biz_end = business_hours_bounds(day)
    occupied: list[DaySegment] = []

    for booking in bookings:
        start = _parse_api_dt(booking["start_at"])
        end = _parse_api_dt(booking["end_at"])
        clipped_start = max(start, biz_start)
        clipped_end = min(end, biz_end)
        if clipped_end <= clipped_start:
            continue
        name = booking.get("associate_name") or "Associate"
        purpose = booking.get("purpose") or ""
        label = f"{name}" + (f" — {purpose}" if purpose else "")
        occupied.append(
            DaySegment(
                kind="busy",
                start_local=clipped_start,
                end_local=clipped_end,
                label=label,
                booking_id=booking.get("id"),
                associate_name=name,
                purpose=purpose or None,
            )
        )

    occupied.sort(key=lambda s: s.start_local)

    segments: list[DaySegment] = []
    cursor = biz_start
    for block in occupied:
        if block.start_local > cursor:
            segments.append(
                DaySegment(
                    kind="free",
                    start_local=cursor,
                    end_local=block.start_local,
                    label="Free",
                )
            )
        segments.append(block)
        cursor = max(cursor, block.end_local)
    if cursor < biz_end:
        segments.append(
            DaySegment(
                kind="free",
                start_local=cursor,
                end_local=biz_end,
                label="Free",
            )
        )
    return segments


def free_gaps(segments: list[DaySegment]) -> list[DaySegment]:
    return [s for s in segments if s.kind == "free"]


def busy_bookings_table(segments: list[DaySegment]) -> pd.DataFrame:
    rows = []
    for s in segments:
        if s.kind != "busy":
            continue
        rows.append(
            {
                "ID": s.booking_id,
                "Start": s.start_local.strftime("%H:%M"),
                "End": s.end_local.strftime("%H:%M"),
                "Associate": s.associate_name or "",
                "Purpose": s.purpose or "",
            }
        )
    if not rows:
        return pd.DataFrame(columns=["ID", "Start", "End", "Associate", "Purpose"])
    return pd.DataFrame(rows)


def build_timeline_figure(segments: list[DaySegment], day: date) -> go.Figure:
    """Horizontal day timeline: free (light) and busy (solid) blocks."""
    if not segments:
        fig = go.Figure()
        fig.update_layout(
            title=f"ODC room — {day.isoformat()} (no segments)",
            height=220,
            margin=dict(l=40, r=20, t=50, b=40),
        )
        return fig

    records = []
    for s in segments:
        records.append(
            {
                "Task": "ODC Common Meeting Room",
                "Start": s.start_local,
                "Finish": s.end_local,
                "Resource": "Free" if s.kind == "free" else "Busy",
                "Label": s.label,
            }
        )
    df = pd.DataFrame(records)
    fig = px.timeline(
        df,
        x_start="Start",
        x_end="Finish",
        y="Task",
        color="Resource",
        hover_data=["Label"],
        color_discrete_map={"Free": "#C8E6C9", "Busy": "#1565C0"},
    )
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(
        title=f"Day occupancy — {day.isoformat()} (08:00–18:00 {ODC_TIMEZONE})",
        height=280,
        legend_title_text="",
        margin=dict(l=40, r=20, t=60, b=40),
        xaxis=dict(
            tickformat="%H:%M",
            dtick=60 * 60 * 1000,
        ),
    )
    return fig
