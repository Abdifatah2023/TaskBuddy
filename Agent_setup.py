import io
import os
import json
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from pypdf import PdfReader
from docx import Document

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

import RagPipeline as rp
from Google_calendar import GoogleCalendarTool
from google_drive import get_drive_service, list_folder_files, download_file_content, FOLDER_ID
from email_alerts import authenticate, get_weekly_events, format_event, gmail_send_message
from canvas import list_canvas_courses, save_canvas_course_to_drive, extract_assignments_from_canvas

load_dotenv()

# Events added to the calendar this session (for the weekly digest)
added_events: list[dict] = []


# # ── Tools: Google Drive syllabus fallback ─────────────────────────────────────

@tool
# def list_drive_syllabi(_: str = "") -> str:
#     """
#     List all syllabus files already stored in the configured Google Drive folder.
#     Returns a JSON list of {file_id, file_name}.
#     Use this to discover any Drive files that were not sourced from Canvas.
#     """
#     service = get_drive_service()
#     files = list_folder_files(service, FOLDER_ID)
#     result = [{"file_id": fid, "file_name": f["name"]} for fid, f in files.items()]
#     return json.dumps(result)


# @tool
# def extract_assignments_from_file(file_id: str, file_name: str) -> str:
#     """
#     Download a syllabus file from Google Drive by its file_id and extract all
#     assignments and deadlines from it using the RAG pipeline.
#     Returns a JSON list of {assignment_name, due_date} objects.
#     Only call this for Drive files that are NOT already processed via Canvas.
#     """
#     service = get_drive_service()
#     meta = service.files().get(fileId=file_id, fields="mimeType").execute()
#     mime_type = meta["mimeType"]

#     try:
#         file_bytes = download_file_content(service, file_id, mime_type)
#     except Exception as e:
#         return f"[]  # Error downloading file: {e}"

#     DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
#     DOC  = "application/msword"

#     if mime_type in (DOCX, DOC):
#         doc = Document(io.BytesIO(file_bytes))
#         full_text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
#     else:
#         reader = PdfReader(io.BytesIO(file_bytes))
#         full_text = "\n\n".join(page.extract_text() or "" for page in reader.pages)

#     if not full_text.strip():
#         return f"[]  # No extractable text found in {file_name}"

#     rp.build_rag_chain(full_text)
#     if rp.rag_chain is None:
#         return f"[]  # RAG chain could not be built for {file_name}"

#     return rp.rag_chain.invoke(
#         f"Extract all assignments, projects, and exams with their due dates from: {file_name}"
#     )


# ── Tools: Calendar ───────────────────────────────────────────────────────────

@tool
def create_calendar_event(title: str, due_date: str) -> str:
    """
    Create a Google Calendar event for an assignment.
    The event starts and ends on the due_date (treated as a 1-hour block at 9 AM).
    Only call this when due_date is a valid date string (not null).
    """
    result = GoogleCalendarTool(title=title, start_time=due_date, end_time=due_date)
    if result.startswith("Event created successfully"):
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
    Fetch all Google Calendar events for the next 7 days and send a weekly
    bulletin email summarising the upcoming deadlines.
    Call this after extract_weekly_deadlines.
    """
    try:
        creds = authenticate()
        if not creds:
            return "Failed: could not authenticate Gmail/Calendar."
        events = get_weekly_events(creds)
        if events is None:
            return "Failed: could not fetch calendar events."
        email_body = format_event(events)
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
    from canvas import _get_course_text

    full_text = _get_course_text(course_id)
    if not full_text.strip():
        return f"No content found for course '{course_name}' ({course_id})."

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

[RAG Assignments → Calendar]
3. extract_assignments_from_canvas  (call for EACH course)
   - Uses RAG to embed, vector-store, and retrieve every assignment and
     deadline from the Canvas course content.
   - Returns JSON list of {assignment_name, due_date}.

4. create_calendar_event  (call for EACH assignment with a valid due_date)
   - Adds the assignment as a Google Calendar event.
   - Pass: title = assignment_name, due_date = due_date.
   - SKIP assignments where due_date is null.

[Weekly Digest]
5. extract_weekly_deadlines
   - Filters the events added this session to those due within the next 7 days.
   - Returns JSON list or a "no upcoming deadlines" message.

6. send_weekly_calendar_bulletin
   - Reads Google Calendar for the next 7 days and sends a bulletin email
     listing every upcoming deadline.

[Study Plans]
7. generate_study_plan  (call for EACH course)
   - Uses RAG to embed and retrieve course content, then asks the LLM to
     produce a concise course summary and a weekly study plan.
   - Returns formatted text with ## Course Summary and ## Study Plan sections.

=== WORKFLOW ===

Step 1 : Call list_canvas_courses.
Step 2 : For EACH course → call save_canvas_course_to_drive(course_id, course_name).
Step 3 : For EACH course → call extract_assignments_from_canvas(course_id).
Step 4 : Parse every JSON result from Step 3.
         For each assignment whose due_date is NOT null →
           call create_calendar_event(title, due_date).
Step 5 : Call extract_weekly_deadlines.
Step 6 : Call send_weekly_calendar_bulletin.
Step 7 : For EACH course → call generate_study_plan(course_id, course_name).
Step 8 : Provide a final summary:
         - How many courses were processed.
         - How many calendar events were created.
         - Which assignments are due in the next 7 days.
         - Confirm the bulletin email was sent.
         - Print each study plan in full.

=== RULES ===
- Never hallucinate assignments or due dates.
- Never call create_calendar_event when due_date is null.
- Process courses one at a time (do not batch tool calls for multiple courses).
"""

agent = create_react_agent(
    model=_agent_llm,
    tools=[
        list_canvas_courses,
        save_canvas_course_to_drive,
        extract_assignments_from_canvas,
        create_calendar_event,
        extract_weekly_deadlines,
        send_weekly_calendar_bulletin,
        generate_study_plan,
    ],
    prompt=_agent_prompt,
)


def run_agent() -> str:
    """Run the full TaskBuddy workflow and return the final summary."""
    global added_events
    added_events = []  # reset for each run

    response = agent.invoke({
        "messages": [
            {
                "role": "human",
                "content": (
                    "Run the full workflow: "
                    "scan available Canvas courses, save their syllabi and content to Google Drive, "
                    "extract every assignment and deadline and add them to Google Calendar, "
                    "generate the upcoming 7-day deadline list, send the weekly bulletin email, "
                    "and produce a summary and study plan for each course available."
                ),
            }
        ]
    })
    return response["messages"][-1].content


if __name__ == "__main__":
    print("TaskBuddy agent starting...")
    print("=" * 60)
    print(run_agent())
