from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from api_client import ApiError, get_api_base_url, get_health, list_bookings
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


def _today_odc() -> date:
    return datetime.now(get_odc_tz()).date()


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

    if st.session_state.associate_name or st.session_state.associate_email:
        st.caption(
            f"Signed in as **{st.session_state.associate_name or '—'}** "
            f"<{st.session_state.associate_email or '—'}>"
        )
    else:
        st.caption("Set name and email for booking actions in later issues.")

    st.divider()
    st.header("Day")
    selected_day = st.date_input(
        "Calendar day",
        value=_today_odc(),
        help=f"Times shown in {ODC_TIMEZONE}",
    )
    refresh = st.button("Refresh", use_container_width=True)
    st.caption(f"API: `{get_api_base_url()}`")


st.title("ODC Common Meeting Room")
st.caption("Day occupancy view — confirmed bookings and free gaps (business hours 08:00–18:00).")

# Health badge
try:
    health = get_health()
    st.success(f"Backend OK — `{health.get('status', 'ok')}` at {get_api_base_url()}")
except ApiError as exc:
    st.error(str(exc))
    st.stop()

day_start, day_end = local_day_bounds(selected_day)

try:
    # Force refresh when button clicked by not caching; always fetch on run.
    _ = refresh  # noqa: F841 — intentional dependency for Streamlit rerun
    bookings = list_bookings(day_start, day_end, status="confirmed")
except ApiError as exc:
    st.error(str(exc))
    st.stop()

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
            f"- **{gap.start_local.strftime('%H:%M')}–{gap.end_local.strftime('%H:%M')}** "
            f"({int((gap.end_local - gap.start_local).total_seconds() // 60)} min)"
        )

st.subheader("Confirmed bookings")
table = busy_bookings_table(segments)
if table.empty:
    st.write("No confirmed bookings in business hours for this day.")
else:
    st.dataframe(table, use_container_width=True, hide_index=True)
