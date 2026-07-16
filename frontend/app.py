import streamlit as st

st.set_page_config(
    page_title="ODC Meeting Room Booking Assistant",
    layout="centered",
)

st.title("ODC Meeting Room Booking Assistant")
st.write(
    "Scaffold UI for the ODC meeting room agent. "
    "Chat and calendar views will land in later issues."
)
st.info("Backend health endpoint: `GET /health` (FastAPI).")
