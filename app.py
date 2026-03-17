import contextlib
import io
import json

import streamlit as st

st.set_page_config(page_title="TaskBuddy", page_icon="📚", layout="centered")

st.title("📚 TaskBuddy")
st.caption("Scans your Google Drive syllabi, extracts deadlines, adds them to Google Calendar, and emails you a weekly bulletin.")
st.divider()

# ── Session state ──────────────────────────────────────────────────────────────
if "run_complete" not in st.session_state:
    st.session_state.run_complete = False
if "summary" not in st.session_state:
    st.session_state.summary = ""
if "events" not in st.session_state:
    st.session_state.events = []
if "log" not in st.session_state:
    st.session_state.log = ""

# ── Run button ─────────────────────────────────────────────────────────────────
if st.button("▶  Run Agent", type="primary", use_container_width=True):
    st.session_state.run_complete = False
    st.session_state.summary = ""
    st.session_state.events = []
    st.session_state.log = ""

    with st.status("Agent running...", expanded=True) as status:
        st.write("Importing agent — this may take a moment on first run...")
        import Agent_setup  # delayed import so Streamlit doesn't run it on startup

        st.write("Scanning Google Drive for syllabus files...")
        log_buffer = io.StringIO()

        try:
            with contextlib.redirect_stdout(log_buffer):
                summary = Agent_setup.run_agent()

            st.session_state.summary = summary
            st.session_state.events = list(Agent_setup.added_events)
            st.session_state.log = log_buffer.getvalue()
            st.session_state.run_complete = True
            status.update(label="Agent finished!", state="complete", expanded=False)

        except Exception as e:
            status.update(label="Agent encountered an error.", state="error", expanded=True)
            st.error(str(e))
            st.session_state.log = log_buffer.getvalue()

# ── Results ────────────────────────────────────────────────────────────────────
if st.session_state.run_complete:
    st.divider()

    # Events added to calendar
    st.subheader("📅 Events Added to Calendar")
    events = st.session_state.events
    if events:
        st.dataframe(
            events,
            column_config={
                "title": st.column_config.TextColumn("Assignment"),
                "due_date": st.column_config.TextColumn("Due Date"),
            },
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No events were added to the calendar.")

    # Upcoming deadlines (next 7 days)
    st.subheader("⏰ Upcoming Deadlines (Next 7 Days)")
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).date()
    cutoff = today + timedelta(days=7)
    upcoming = [
        e for e in events
        if today <= datetime.fromisoformat(e["due_date"]).date() <= cutoff
    ]
    if upcoming:
        for e in upcoming:
            due = datetime.fromisoformat(e["due_date"]).date()
            days_left = (due - today).days
            label = "today" if days_left == 0 else f"in {days_left} day{'s' if days_left != 1 else ''}"
            st.markdown(f"- **{e['title']}** — {due.strftime('%B %d, %Y')} *(due {label})*")
    else:
        st.info("No deadlines in the next 7 days.")

    # Agent summary
    st.subheader("🤖 Agent Summary")
    st.markdown(st.session_state.summary)

    # Collapsible log
    if st.session_state.log:
        with st.expander("View Agent Log"):
            st.code(st.session_state.log, language=None)
