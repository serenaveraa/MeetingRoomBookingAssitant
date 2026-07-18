from __future__ import annotations

from datetime import date, datetime, timedelta

import streamlit as st

from api_client import (
    ApiError,
    get_api_base_url,
    get_health,
    get_utilization,
    list_bookings,
    post_chat,
)
from chat_ui import format_tool_outcomes
from timeline import (
    ODC_TIMEZONE,
    build_day_segments,
    build_timeline_figure,
    busy_bookings_table,
    free_gaps,
    get_odc_tz,
    local_day_bounds,
)

st.set_page_config(
    page_title="ODC Meeting Room",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "associate_name" not in st.session_state:
    st.session_state.associate_name = ""
if "associate_email" not in st.session_state:
    st.session_state.associate_email = ""
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []


def _today_odc() -> date:
    return datetime.now(get_odc_tz()).date()


def _identity_ready() -> bool:
    return bool(
        st.session_state.associate_name.strip()
        and st.session_state.associate_email.strip()
    )


with st.sidebar:
    st.header("Associate")
    name_in = st.text_input(
        "Name",
        value=st.session_state.associate_name,
        placeholder="Ada Lovelace",
    )
    email_in = st.text_input(
        "Email",
        value=st.session_state.associate_email,
        placeholder="ada@example.com",
    )
    if st.button("Save identity", type="primary", use_container_width=True):
        st.session_state.associate_name = name_in.strip()
        st.session_state.associate_email = email_in.strip().lower()
        st.success("Identity saved for this session.")

    if _identity_ready():
        st.caption(
            f"Signed in as **{st.session_state.associate_name}** "
            f"<{st.session_state.associate_email}>"
        )
    else:
        st.caption("Save name and email before using chat.")

    st.divider()
    st.header("Calendar day")
    selected_day = st.date_input(
        "Day",
        value=_today_odc(),
        help=f"Times shown in {ODC_TIMEZONE}",
    )
    refresh = st.button("Refresh calendar", use_container_width=True)
    st.caption(f"API: `{get_api_base_url()}`")
    if st.session_state.conversation_id:
        st.caption(f"Chat conversation: `{st.session_state.conversation_id}`")


st.title("ODC Common Meeting Room")

try:
    health = get_health()
    st.success(f"Backend OK — `{health.get('status', 'ok')}` at {get_api_base_url()}")
except ApiError as exc:
    st.error(str(exc))
    st.stop()

calendar_tab, insights_tab, chat_tab = st.tabs(["Calendar", "Insights", "Chat"])

with calendar_tab:
    st.caption(
        "Day occupancy — confirmed bookings and free gaps (business hours 08:00–18:00)."
    )
    day_start, day_end = local_day_bounds(selected_day)
    try:
        _ = refresh  # noqa: F841
        bookings = list_bookings(day_start, day_end, status="confirmed")
    except ApiError as exc:
        st.error(str(exc))
    else:
        segments = build_day_segments(bookings, selected_day)
        fig = build_timeline_figure(segments, selected_day)
        st.plotly_chart(fig, use_container_width=True)

        gaps = free_gaps(segments)
        st.subheader("Free gaps")
        if not gaps:
            st.info("No free gaps within business hours for this day.")
        else:
            for gap in gaps:
                st.markdown(
                    f"- **{gap.start_local.strftime('%H:%M')}–"
                    f"{gap.end_local.strftime('%H:%M')}** "
                    f"({int((gap.end_local - gap.start_local).total_seconds() // 60)} min)"
                )

        st.subheader("Confirmed bookings")
        table = busy_bookings_table(segments)
        if table.empty:
            st.write("No confirmed bookings in business hours for this day.")
        else:
            st.dataframe(table, use_container_width=True, hide_index=True)

with insights_tab:
    st.caption("Utilization metrics over a date range using the shared backend service.")
    col_left, col_right = st.columns(2)
    with col_left:
        start_date = st.date_input("Start date", value=_today_odc() - timedelta(days=6))
    with col_right:
        end_date = st.date_input("End date", value=_today_odc())

    if start_date > end_date:
        st.error("End date must be on or after start date.")
    else:
        try:
            metrics = get_utilization(start_date, end_date)
        except ApiError as exc:
            st.error(str(exc))
        else:
            st.metric("Bookings", metrics.get("booking_count", 0))
            st.metric("Average duration", f"{metrics.get('avg_duration_minutes', 0):.1f} min")
            st.metric("Idle gaps", metrics.get("idle_gap_count", 0))
            if metrics.get("summary"):
                st.write(metrics["summary"])
            if metrics.get("bookings_per_day"):
                st.subheader("Per-day breakdown")
                import pandas as pd

                chart_data = pd.DataFrame(metrics["bookings_per_day"])
                chart_data["day"] = pd.to_datetime(chart_data["day"])
                st.bar_chart(chart_data.set_index("day")["booking_count"])

with chat_tab:
    st.caption(
        "Talk to the booking agent. Confirmations and conflicts from tools are shown "
        "under each assistant reply. Switch to Calendar and refresh to see updates."
    )

    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button("Clear chat", use_container_width=True):
            st.session_state.chat_messages = []
            st.session_state.conversation_id = None
            st.rerun()
    with col_b:
        st.write("")

    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            outcomes = msg.get("outcomes") or []
            if outcomes:
                if any("conflict" in line.lower() for line in outcomes):
                    st.warning("\n".join(outcomes))
                elif any(
                    "confirmed" in line.lower() or "cancelled" in line.lower()
                    or "extended" in line.lower()
                    for line in outcomes
                ):
                    st.success("\n".join(outcomes))
                else:
                    st.info("\n".join(outcomes))
            if msg.get("needs_clarification"):
                st.info(
                    msg.get("clarification_question")
                    or "The agent needs a bit more detail."
                )

    prompt = st.chat_input(
        "Book the room tomorrow from 2 PM to 3 PM…",
        disabled=not _identity_ready(),
    )
    if not _identity_ready():
        st.warning("Save your associate name and email in the sidebar to chat.")

    if prompt:
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    result = post_chat(
                        prompt,
                        associate_email=st.session_state.associate_email,
                        associate_name=st.session_state.associate_name,
                        conversation_id=st.session_state.conversation_id,
                    )
                except ApiError as exc:
                    st.error(str(exc))
                    st.session_state.chat_messages.append(
                        {
                            "role": "assistant",
                            "content": f"Error: {exc}",
                            "outcomes": [],
                        }
                    )
                else:
                    st.session_state.conversation_id = result.get("conversation_id")
                    reply = result.get("reply") or "(empty reply)"
                    outcomes = format_tool_outcomes(result.get("tool_results") or [])
                    st.markdown(reply)
                    if outcomes:
                        if any("conflict" in line.lower() for line in outcomes):
                            st.warning("\n".join(outcomes))
                        elif any(
                            "confirmed" in line.lower()
                            or "cancelled" in line.lower()
                            or "extended" in line.lower()
                            for line in outcomes
                        ):
                            st.success("\n".join(outcomes))
                        else:
                            st.info("\n".join(outcomes))
                    needs = bool(result.get("needs_clarification"))
                    if needs:
                        st.info(
                            result.get("clarification_question")
                            or "The agent needs a bit more detail."
                        )
                    st.session_state.chat_messages.append(
                        {
                            "role": "assistant",
                            "content": reply,
                            "outcomes": outcomes,
                            "needs_clarification": needs,
                            "clarification_question": result.get(
                                "clarification_question"
                            ),
                        }
                    )
