import io
import os
import json

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


# Load environment
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')


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

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


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

    chunks = text_splitter.split_text(full_text)
    vectorstore = Chroma.from_texts(
        texts=chunks,
        embedding=embeddings,
        collection_name=f"syllabus_{file_id[:8]}",
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
    return GoogleCalendarTool(title=title, start_time=due_date, end_time=due_date)


# ── Agent Setup ────────────────────────────────────────────────────────────────

agent_llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")

system_prompt = """
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

    Workflow:
    Step 1: Call list_drive_syllabi to get all files.
    Step 2: For each file, call extract_assignments_from_file individually.
    Step 3: Parse the JSON results from each file.
    Step 4: For each assignment with a valid due_date, call create_calendar_event.
    Step 5: Summarize all assignments added to the calendar.

    Do NOT hallucinate assignments or due dates.
    """

agent = create_agent(
    model=agent_llm,
    tools=[list_drive_syllabi, extract_assignments_from_file, create_calendar_event],
    system_prompt=system_prompt,
)

print("Agent ready with 3 tools!")
print("=" * 50)


response = agent.invoke({
    "messages": [
        {
            "role": "human",
            "content": "Scan all syllabus files in the Drive folder, extract every assignment and deadline, and add them to my Google Calendar.",
        }
    ]
})

result = response["messages"][-1].content
print(result)
