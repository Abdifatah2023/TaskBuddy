import io
import os
import json
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from pypdf import PdfReader
from docx import Document

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_community.vectorstores import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.tools import tool
from langchain.agents import create_agent

from Google_calendar import GoogleCalendarTool
from google_drive import get_drive_service, list_folder_files, download_file_content, FOLDER_ID
from email_alerts import authenticate, get_weekly_events, format_event, gmail_send_message


# Load environment
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


# Shared utilities used inside tools
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=500)

embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")

template = """You are an academic assistant. 
            Extract every assignment, project, or exam mentioned.

            Return output STRICTLY in JSON format like this:

            [
            {{
                "assignment_name": "Homework 1",
                "due_date": "2026-03-10"
            }}
            ]

            If a due date is missing, set "due_date" to null

            Context: 
            {context}

            Question: 
            {question}

            """

prompt = ChatPromptTemplate.from_template(template)

vectorstore = None
rag_chain = None
added_events: list[dict] = []  # tracks events added to calendar this session


def build_rag_chain(text: str):
    global vectorstore, rag_chain
    chunks = text_splitter.split_text(text)
    if not chunks:
        return
    vectorstore = Chroma.from_texts(
        texts=chunks,
        embedding=embeddings,
        collection_name="academic_docs",
    )
    rag_chain = (
        {
            "context": vectorstore.as_retriever(search_kwargs={"k": 5}) | format_docs,
            "question": RunnablePassthrough(),
        }
        | prompt
        | ChatGoogleGenerativeAI(model="gemini-2.0-flash")
        | StrOutputParser()
    )



# ── Tools ──────────────────────────────────────────────────────────────────────

@tool
def list_drive_syllabi() -> str:
    """
    List all syllabus files in the shared Google Drive folder.
    Returns a JSON list of objects with file_id and file_name.
    Call this first before extracting assignments.
    """
    service = get_drive_service()
    files = list_folder_files(service, FOLDER_ID)
    result = [{"file_id": fid, "file_name": f["name"]} for fid, f in files.items()]
    return json.dumps(result)


@tool
def extract_assignments_from_file(file_id: str, file_name: str) -> str:
    """
    Download a syllabus file from Google Drive by its file_id and extract
    all assignments and deadlines from it.
    Returns a JSON list of {assignment_name, due_date} objects.
    """
    service = get_drive_service()
    meta = service.files().get(fileId=file_id, fields="mimeType").execute()
    mime_type = meta["mimeType"]
    file_bytes = download_file_content(service, file_id, mime_type)

    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    DOC  = "application/msword"

    if mime_type in (DOCX, DOC):
        doc = Document(io.BytesIO(file_bytes))
        full_text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
    else:
        # PDF — either native or exported from Google Docs
        reader = PdfReader(io.BytesIO(file_bytes))
        full_text = "\n\n".join(page.extract_text() or "" for page in reader.pages)

    if not full_text.strip():
        return f'[]  # No extractable text found in {file_name}'

    build_rag_chain(full_text)

    return rag_chain.invoke(
        f"Extract all assignments, projects, and exams with their due dates from: {file_name}"
    )


@tool
def create_calendar_event(title: str, due_date: str) -> str:
    """
    Create a Google Calendar event for an assignment.
    The event should start and end on the due date.
    Only call this when due_date is NOT null.
    """
    print("Running create_calendar_event")
    result = GoogleCalendarTool(title=title, start_time=due_date, end_time=due_date)
    if result.startswith("Event created successfully"):
        added_events.append({"title": title, "due_date": due_date})
    return result

@tool
def extract_weekly_deadlines(_: str = "") -> str:
    """
    Return deadlines added to the calendar this session that fall within the next 7 days.
    """
    print("Running extract_weekly_deadlines...")
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


@tool
def send_weekly_calendar_bulletin(_: str = "send") -> str:
    """
    Build and send a weekly bulletin from Google Calendar events using email_alerts.py.
    """
    try:
        print("Running send_weekly_calendar_bulletin...")
        creds = authenticate()
        if not creds:
            return "Failed: could not authenticate."

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

# ── Agent Setup ────────────────────────────────────────────────────────────────

agent_llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")

agent_prompt = """
    You are an academic assistant that processes syllabus files from Google Drive.

    You have three tools:

    1. list_drive_syllabi:
       - Call this FIRST to discover all syllabus files in the Drive folder.

    2. extract_assignments_from_file:
       - Call this for EACH file returned by list_drive_syllabi.
       - Pass the file_id and file_name for that file.
       - It returns a JSON list of assignments and due dates.

    3. create_calendar_event:
       - Call this for each assignment that has a valid (non-null) due_date.
       - Pass the assignment name as title and the due_date.

    4. extract_weekly_deadlines:
       - Returns only the deadlines added to the calendar this session that fall within the next 7 days.

    5. send_weekly_calendar_bulletin
       - follows up extract_weekly_deadlines.
       - sends an email to the recipient of the upcoming deadlines.

    Workflow:
    Step 1: Call list_drive_syllabi to get all files.
    Step 2: For each file, call extract_assignments_from_file individually.
    Step 3: Parse the JSON results from each file.
    Step 4: For each assignment with a valid due_date, call create_calendar_event.
    Step 5: Extract upcoming deadlines for the next 7 days by using extract_weekly_deadlines.
    Step 6: Use send_weekly_calendar_bulletin to send a bulletin email of upcoming deadlines to the recipient.
    Step 7: Summarize the tasks completed (a list of tasks added to the calendar and a confirmation of email sent.)

    Do NOT hallucinate assignments or due dates.
    """

agent = create_agent(
    model=agent_llm,
    tools=[list_drive_syllabi, extract_assignments_from_file,
            create_calendar_event, extract_weekly_deadlines,
            send_weekly_calendar_bulletin],
    system_prompt=agent_prompt,
)


def run_agent() -> str:
    """Run the full agent workflow and return the final summary."""
    global added_events
    added_events = []  # reset for each run

    response = agent.invoke({
        "messages": [
            {
                "role": "human",
                "content": (
                    "Scan all syllabus files in the Drive folder, "
                    "extract every assignment and deadline, "
                    "and add them to my Google Calendar. "
                    "Then extract the upcoming deadlines for the next 7 days "
                    "and send out the email."
                ),
            }
        ]
    })
    return response["messages"][-1].content


if __name__ == "__main__":
    print("Agent ready!")
    print("=" * 50)
    print(run_agent())
