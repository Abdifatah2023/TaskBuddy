import json
from datetime import datetime, timedelta, timezone
from uuid import UUID
from typing import Any

from dotenv import load_dotenv

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langchain_core.callbacks.base import BaseCallbackHandler
from langgraph.prebuilt import create_react_agent

import app.RagPipeline as rp
from app.Google_calendar import GoogleCalendarTool
from app.email_alerts import authenticate, gmail_send_message
from app.canvas import list_canvas_courses, save_canvas_course_to_drive, extract_assignments_from_canvas, get_canvas_calendar_events, _get_course_text

load_dotenv()

# ── Progress tracking ─────────────────────────────────────────────────────────

_STEP_NAMES = [
    "Discovering courses",
    "Saving to Drive",
    "Extracting assignments",
    "Adding to Calendar",
    "Filtering deadlines",
    "Sending email",
    "Generating study plans",
]

_TOOL_TO_STEP: dict[str, int] = {
    "list_canvas_courses": 0,
    "save_canvas_course_to_drive": 1,
    "extract_assignments_from_canvas": 2,
    "get_canvas_calendar_events": 2,   # same step — both are assignment extraction
    "create_calendar_event": 3,
    "extract_weekly_deadlines": 4,
    "send_weekly_calendar_bulletin": 5,
    "generate_study_plan": 6,
}

_step_progress: list[dict] = [{"name": s, "status": "pending"} for s in _STEP_NAMES]
_courses_data: list[dict] = []

# Events added to the calendar this session (for the weekly digest)
added_events: list[dict] = []

# Full course text cached after each study-plan generation — used by the chat RAG
_course_content_cache: dict[str, str] = {}  # course_id -> full text


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


# ── Tools: Calendar ───────────────────────────────────────────────────────────

@tool
def create_calendar_event(title: str, due_date: str) -> str:
    """
    Create or update a Google Calendar event for an assignment.
    - Creates the event if it does not exist.
    - Updates the date if the assignment due date has changed.
    - Skips (idempotent) if the event already exists on the correct date.
    Only call this when due_date is a valid date string (not null).
    """
    result = GoogleCalendarTool(title=title, start_time=due_date, end_time=due_date)
    if result.startswith("Event created successfully") or result.startswith("Event updated successfully"):
        # Remove stale entry for this title if present, then record current state
        added_events[:] = [e for e in added_events if e["title"] != title]
        added_events.append({"title": title, "due_date": due_date})
    return result


@tool
def extract_weekly_deadlines(_: str = "") -> str:
    """
    Return all assignments added to Google Calendar this session whose due date
    falls within the next 7 days from today.
    Call this after all create_calendar_event calls are complete.
    Returns a JSON list of {title, due_date} objects, or a message if none.
    """
    today = datetime.now(timezone.utc).date()
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


# ── Tools: Email ──────────────────────────────────────────────────────────────

@tool
def send_weekly_calendar_bulletin(_: str = "") -> str:
    """
    Send a weekly bulletin email listing only the assignments added to Google
    Calendar by TaskBuddy this session that are due within the next 7 days.
    If none are due in that window, the email says so explicitly.
    Call this after extract_weekly_deadlines.
    """
    try:
        creds = authenticate()
        if not creds:
            return "Failed: could not authenticate Gmail."

        today = datetime.now(timezone.utc).date()
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
            email_body = "\n".join(lines)
        else:
            email_body = (
                "TaskBuddy — Weekly Deadlines Bulletin\n\n"
                "You have no assignments due in the next 7 days. Great work staying on top of things!"
            )

        result = gmail_send_message(creds, email_body)
        if result is None:
            return "Failed: Gmail send returned no result."
        return "Success: weekly bulletin email sent."
    except Exception as e:
        return f"Failed with error: {str(e)}"


# ── Tools: Study plans ────────────────────────────────────────────────────────

@tool
def generate_study_plan(course_id: str, course_name: str) -> str:
    """
    Fetch all content from a Canvas course, embed and vector-store it via RAG,
    then use the LLM to generate a concise course summary and a detailed weekly
    study plan.

    Returns the generated summary and study plan as formatted text.
    Call this for EACH course after all calendar events have been created.
    """
    full_text = _get_course_text(course_id)
    if not full_text.strip():
        return f"No content found for course '{course_name}' ({course_id})."

    # Cache for chat RAG
    _course_content_cache[course_id] = full_text

    rp.build_study_plan_chain(full_text)
    if rp.study_plan_rag_chain is None:
        return f"Could not build study plan chain for course '{course_name}'."

    return rp.study_plan_rag_chain.invoke(
        f"Generate a comprehensive summary and study plan for: {course_name}"
    )


# ── Agent setup ───────────────────────────────────────────────────────────────

_agent_llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")

_agent_prompt = """You are TaskBuddy, an academic assistant that automates course
management end-to-end. Follow the steps below IN ORDER and DO NOT skip any step.

=== TOOLS AVAILABLE ===

[Canvas → Drive]
1. list_canvas_courses
   - Discover all available Canvas courses.
   - Returns JSON list of {course_id, course_name}.

2. save_canvas_course_to_drive  (call for EACH course)
   - Extracts the syllabus and all module content from Canvas.
   - Saves them as syllabus.txt and course_content.txt in a per-course
     subfolder inside the configured Google Drive folder.
   - Returns a JSON summary of what was saved.

[Assignment Extraction → Calendar]
3a. extract_assignments_from_canvas  (call for EACH course)
    - Uses RAG to embed and retrieve assignments from course content.
    - Returns JSON list of {assignment_name, due_date}.

3b. get_canvas_calendar_events  (call for EACH course)
    - Queries the Canvas Calendar Events API directly — authoritative source
      for due dates. Returns JSON list of {assignment_name, due_date}.
    - Merge with 3a results; deduplicate by assignment_name before proceeding.

4. create_calendar_event  (call for EACH unique assignment with a valid due_date)
   - Adds the assignment as a Google Calendar event.
   - Pass: title = assignment_name, due_date = due_date.
   - SKIP assignments where due_date is null.

[Weekly Digest]
5. extract_weekly_deadlines
   - Filters the events added this session to those due within the next 7 days.
   - Returns JSON list or a "no upcoming deadlines" message.

6. send_weekly_calendar_bulletin
   - Sends a bulletin email listing only the deadlines added this session
     that fall within the next 7 days. Addresses the user if none exist.

[Study Plans]
7. generate_study_plan  (call for EACH course)
   - Uses RAG to embed and retrieve course content, then asks the LLM to
     produce a concise course summary and a weekly study plan.
   - Returns formatted text with ## Course Summary and ## Study Plan sections.

=== WORKFLOW ===

Step 1 : Call list_canvas_courses.
Step 2 : For EACH course → call save_canvas_course_to_drive(course_id, course_name).
Step 3 : For EACH course:
           a) call extract_assignments_from_canvas(course_id)
           b) call get_canvas_calendar_events(course_id)
           Merge both lists and deduplicate by assignment_name (prefer the
           due_date from get_canvas_calendar_events when both are present).
Step 4 : For each unique assignment from Step 3 whose due_date is NOT null →
           call create_calendar_event(title, due_date).
Step 5 : Call extract_weekly_deadlines.
Step 6 : Call send_weekly_calendar_bulletin.
Step 7 : For EACH course → call generate_study_plan(course_id, course_name).
Step 8 : Provide a final summary with the following sections IN FULL:
         - How many courses were processed.
         - How many calendar events were NEWLY created this run (count only
           "Event created successfully" responses — do NOT count "already exists").
         - Which assignments are due in the next 7 days.
         - Confirm the bulletin email was sent.
         - For EACH course, copy the COMPLETE text returned by generate_study_plan
           verbatim. Do NOT summarise, truncate, or write "see above" / "printed above".
           The full study plan text must appear in this final message.

=== RULES ===
- Never hallucinate assignments or due dates.
- Never call create_calendar_event when due_date is null.
- Process courses one at a time (do not batch tool calls for multiple courses).
- In the final summary, always reproduce each study plan IN FULL. Never write
  "see above", "printed above", or any other reference to prior output.
  The final message must be self-contained and include all study plan text.
"""

agent = create_react_agent(
    model=_agent_llm,
    tools=[
        list_canvas_courses,
        save_canvas_course_to_drive,
        extract_assignments_from_canvas,
        get_canvas_calendar_events,
        create_calendar_event,
        extract_weekly_deadlines,
        send_weekly_calendar_bulletin,
        generate_study_plan,
    ],
    prompt=_agent_prompt,
)

