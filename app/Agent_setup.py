import json
from datetime import datetime, timedelta, timezone
from uuid import UUID
from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langchain_core.callbacks.base import BaseCallbackHandler
from langgraph.prebuilt import create_react_agent

import app.RagPipeline as rp
from app.RagPipeline import _GEMINI_MODEL
from app.canvas import (
    list_canvas_courses as _list_canvas_courses,
    extract_assignments_from_canvas as _extract_assignments,
    get_canvas_calendar_events as _get_calendar_events,
    _get_course_text,
)

# ── Progress tracking ─────────────────────────────────────────────────────────

_STEP_NAMES = [
    "Discovering courses",
    "Extracting assignments",
    "Adding to calendar",
    "Filtering deadlines",
    "Sending email",
    "Generating study plans",
]

_TOOL_TO_STEP: dict[str, int] = {
    "list_canvas_courses":           0,
    "extract_assignments_from_canvas": 1,
    "get_canvas_calendar_events":    1,
    "create_calendar_event":         2,
    "extract_weekly_deadlines":      3,
    "send_weekly_calendar_bulletin": 4,
    "generate_study_plan":           5,
}

_step_progress: list[dict] = [{"name": s, "status": "pending"} for s in _STEP_NAMES]
_courses_data: list[dict]  = []
added_events: list[dict]   = []
_course_content_cache: dict[str, str] = {}


def reset_progress() -> None:
    for step in _step_progress:
        step["status"] = "pending"
    _courses_data.clear()
    added_events.clear()
    _course_content_cache.clear()


class ProgressTracker(BaseCallbackHandler):
    def __init__(self) -> None:
        self._runs: dict[str, str] = {}

    def on_tool_start(self, serialized: dict[str, Any], _input_str: str, *, run_id: UUID, **_kwargs: Any) -> None:
        tool_name = serialized.get("name", "")
        self._runs[str(run_id)] = tool_name
        idx = _TOOL_TO_STEP.get(tool_name)
        if idx is not None and _step_progress[idx]["status"] == "pending":
            _step_progress[idx]["status"] = "active"

    def on_tool_end(self, output: Any, *, run_id: UUID, **_kwargs: Any) -> None:
        tool_name = self._runs.pop(str(run_id), None)
        if not tool_name:
            return
        idx = _TOOL_TO_STEP.get(tool_name)
        if idx is not None:
            _step_progress[idx]["status"] = "done"
        if tool_name == "list_canvas_courses":
            try:
                courses = json.loads(str(output))
                if isinstance(courses, list):
                    for c in courses:
                        if isinstance(c, dict) and "course_id" in c:
                            _courses_data.append({
                                "course_id": c["course_id"],
                                "course_name": c.get("course_name", f"Course {c['course_id']}"),
                            })
            except Exception:
                pass


# ── Agent prompt ──────────────────────────────────────────────────────────────

_agent_prompt = """You are TaskBuddy, an academic assistant that automates course
management end-to-end. Follow the steps below IN ORDER and DO NOT skip any step.

=== TOOLS AVAILABLE ===

[Canvas → Assignments]
1. list_canvas_courses
   - Discover all available Canvas courses.
   - Returns JSON list of {course_id, course_name}.

2a. extract_assignments_from_canvas  (call for EACH course)
    - Uses RAG to embed and retrieve assignments from course content.
    - Returns JSON list of {assignment_name, due_date}.

2b. get_canvas_calendar_events  (call for EACH course)
    - Queries the Canvas Calendar Events API directly — authoritative source
      for due dates. Returns JSON list of {assignment_name, due_date}.
    - Merge with 2a results; deduplicate by assignment_name before proceeding.

[Session Calendar]
3. create_calendar_event  (call for EACH unique assignment with a valid due_date)
   - Records the assignment in the session calendar for this run.
   - Pass: title = assignment_name, due_date = due_date (YYYY-MM-DD).
   - SKIP assignments where due_date is null.

[Weekly Digest + Calendar File]
4. extract_weekly_deadlines
   - Filters the session calendar to assignments due within the next 7 days.
   - Returns JSON list or a "no upcoming deadlines" message.

5. send_weekly_calendar_bulletin
   - Sends a digest email to the user listing upcoming deadlines.
   - Attaches an .ics calendar file containing ALL session assignments so the
     user can import them into Google Calendar, Outlook, or Apple Calendar.

[Study Plans]
6. generate_study_plan  (call for EACH course)
   - Uses RAG to embed and retrieve course content, then asks the LLM to
     produce a concise course summary and a weekly study plan.
   - Returns formatted text with ## Course Summary and ## Study Plan sections.

=== WORKFLOW ===

Step 1 : Call list_canvas_courses.
Step 2 : For EACH course:
           a) call extract_assignments_from_canvas(course_id)
           b) call get_canvas_calendar_events(course_id)
           Merge both lists and deduplicate by assignment_name (prefer the
           due_date from get_canvas_calendar_events when both are present).
Step 3 : For each unique assignment from Step 2 whose due_date is NOT null →
           call create_calendar_event(title, due_date).
Step 4 : Call extract_weekly_deadlines.
Step 5 : Call send_weekly_calendar_bulletin.
Step 6 : For EACH course → call generate_study_plan(course_id, course_name).
Step 7 : Provide a final summary with the following sections IN FULL:
         - How many courses were processed.
         - How many calendar events were recorded this run.
         - Which assignments are due in the next 7 days.
         - Confirm the digest email was sent with the .ics attachment.
         - For EACH course, copy the COMPLETE text returned by generate_study_plan
           verbatim. Do NOT summarise, truncate, or write "see above".

=== RULES ===
- Never hallucinate assignments or due dates.
- Never call create_calendar_event when due_date is null.
- Process courses one at a time (do not batch tool calls for multiple courses).
- In the final summary, always reproduce each study plan IN FULL.
"""


# ── Agent factory ─────────────────────────────────────────────────────────────

def build_agent(session: dict):
    """
    Build and return a LangGraph ReAct agent with all tools closed over
    the session's Canvas credentials and user email.
    """
    canvas_base    = session["canvas_base_url"]
    canvas_token   = session["canvas_token"]
    canvas_courses = session.get("canvas_course_ids", "")
    user_email     = session["email"]

    from app.email_alerts import gmail_send_message, generate_ics

    @tool
    def list_canvas_courses(_: str = "") -> str:
        """List all Canvas courses for this user. Returns JSON list of {course_id, course_name}."""
        return _list_canvas_courses(canvas_base, canvas_token, canvas_courses)

    @tool
    def extract_assignments_from_canvas(course_id: str) -> str:
        """Extract assignments from a Canvas course using RAG. Returns JSON list of {assignment_name, due_date}."""
        return _extract_assignments(course_id, canvas_base, canvas_token)

    @tool
    def get_canvas_calendar_events(course_id: str) -> str:
        """Fetch assignments directly from Canvas APIs. Returns JSON list of {assignment_name, due_date}."""
        return _get_calendar_events(course_id, canvas_base, canvas_token)

    @tool
    def create_calendar_event(title: str, due_date: str) -> str:
        """
        Record an assignment in the session calendar.
        Only call when due_date is a valid YYYY-MM-DD date string.
        """
        added_events[:] = [e for e in added_events if e["title"] != title]
        added_events.append({"title": title, "due_date": due_date})
        return f"Recorded: '{title}' on {due_date}."

    @tool
    def extract_weekly_deadlines(_: str = "") -> str:
        """
        Return all assignments in the session calendar whose due date
        falls within the next 7 days. Call after all create_calendar_event calls.
        """
        today  = datetime.now(timezone.utc).date()
        cutoff = today + timedelta(days=7)
        upcoming = []
        for event in added_events:
            try:
                event_date = datetime.fromisoformat(event["due_date"]).date()
            except ValueError:
                event_date = datetime.strptime(event["due_date"], "%Y-%m-%d").date()
            if today <= event_date <= cutoff:
                upcoming.append(event)
        if not upcoming:
            return "No deadlines added this session fall within the next 7 days."
        return json.dumps(upcoming)

    @tool
    def send_weekly_calendar_bulletin(_: str = "") -> str:
        """
        Send a digest email to the user listing upcoming deadlines and attaching
        an .ics file for all session assignments. Call after extract_weekly_deadlines.
        """
        try:
            today  = datetime.now(timezone.utc).date()
            cutoff = today + timedelta(days=7)
            upcoming = []
            for event in added_events:
                try:
                    event_date = datetime.fromisoformat(event["due_date"]).date()
                except ValueError:
                    event_date = datetime.strptime(event["due_date"], "%Y-%m-%d").date()
                if today <= event_date <= cutoff:
                    upcoming.append(event)

            if upcoming:
                lines = ["TaskBuddy — Upcoming Deadlines (Next 7 Days)\n"]
                for ev in sorted(upcoming, key=lambda e: e["due_date"]):
                    lines.append(f"- {ev['title']}  (Due: {ev['due_date']})")
                lines.append(
                    "\n\nA full .ics calendar file is attached — open it to import "
                    "all your assignments into Google Calendar, Outlook, or Apple Calendar."
                )
                email_body = "\n".join(lines)
            else:
                email_body = (
                    "TaskBuddy — Weekly Deadlines Bulletin\n\n"
                    "You have no assignments due in the next 7 days. Great work staying on top of things!\n\n"
                    "A full .ics calendar file is attached with all your assignments."
                )

            ics_data = generate_ics(added_events) if added_events else None
            success  = gmail_send_message(email_body, user_email, ics_data=ics_data)
            if success:
                return "Success: digest email sent with .ics calendar attachment."
            return "Failed to send digest email."
        except Exception as e:
            return f"Failed with error: {str(e)}"

    @tool
    def generate_study_plan(course_id: str, course_name: str) -> str:
        """
        Fetch all content from a Canvas course, embed and vector-store it via RAG,
        then generate a course summary and weekly study plan. Call for EACH course.
        """
        full_text = _get_course_text(course_id, canvas_base, canvas_token)
        if not full_text.strip():
            return f"No content found for course '{course_name}' ({course_id})."
        _course_content_cache[course_id] = full_text
        rp.build_study_plan_chain(full_text)
        if rp.study_plan_rag_chain is None:
            return f"Could not build study plan chain for course '{course_name}'."
        return rp.study_plan_rag_chain.invoke(
            f"Generate a comprehensive summary and study plan for: {course_name}"
        )

    return create_react_agent(
        model=ChatGoogleGenerativeAI(model=_GEMINI_MODEL),
        tools=[
            list_canvas_courses,
            extract_assignments_from_canvas,
            get_canvas_calendar_events,
            create_calendar_event,
            extract_weekly_deadlines,
            send_weekly_calendar_bulletin,
            generate_study_plan,
        ],
        prompt=_agent_prompt,
    )
