# TaskBuddy

An AI-powered academic assistant that automates course management end-to-end. TaskBuddy connects to Canvas LMS, extracts assignments and deadlines using a RAG pipeline, syncs them to Google Calendar, sends a weekly email digest, and generates personalised study plans — all accessible through a web UI and a conversational chat interface.

---

## Features

- **Canvas Integration** — Fetches course syllabi, module pages, assignments, quizzes, and files from Canvas LMS
- **Google Drive Sync** — Saves syllabus and course content per course into organised Google Drive subfolders (deduplicates on repeat runs)
- **RAG Pipeline** — Embeds course content with Gemini embeddings and stores it in a Chroma vector store for semantic retrieval
- **Assignment Extraction** — Uses RAG to extract every assignment, project, and exam with due dates from course content
- **Google Calendar** — Creates calendar events for each deadline with duplicate detection and 30/10-minute reminders
- **Weekly Email Digest** — Sends a bulletin email listing only the deadlines added by TaskBuddy that fall within the next 7 days
- **Study Plans** — Generates a course summary and week-by-week study plan for each course using the RAG pipeline
- **Web UI** — Dark navy/lime interface with a dedicated Agent panel and a separate Chat panel
- **Chat Assistant** — Follow-up Q&A powered by Gemini, grounded in the agent's output — answers questions about courses, deadlines, and study plans without re-running the workflow

---

## Project Structure

```text
TaskBuddy/
├── app/
│   ├── main.py              # FastAPI app — /chat, /ask, /health endpoints + static file serving
│   ├── Agent_setup.py       # LangGraph ReAct agent, all tool definitions, agent prompt
│   ├── canvas.py            # Canvas API integration and agent-facing tools
│   ├── RagPipeline.py       # Gemini embedding + Chroma vector store + RAG chains
│   ├── Google_calendar.py   # Google Calendar event creation with duplicate checking
│   ├── google_drive.py      # Google Drive auth and file utilities
│   ├── drive_upload_utils.py# Folder creation and upsert file upload helpers
│   └── email_alerts.py      # Gmail auth and send functions
├── static/
│   ├── index.html           # Web UI (served at /ui)
│   └── favicon.png          # App icon
├── credentials/
│   └── credentials.json     # Google OAuth client secrets (not committed)
├── requirements.txt
└── README.md
```

---

## Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd TaskBuddy
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in the project root:

```env
GOOGLE_API_KEY=your_gemini_api_key

CANVAS_API_TOKEN=your_canvas_api_token
CANVAS_BASE_URL=https://your-institution.instructure.com
CANVAS_COURSE_IDS=12345,67890   # optional — leave blank to fetch all active courses
```

### 4. Set up Google OAuth credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project
2. Enable the **Google Drive API**, **Google Calendar API**, and **Gmail API**
3. Create an OAuth 2.0 Desktop client and download `credentials.json`
4. Place it at `credentials/credentials.json`

The app uses three separate token files (auto-generated on first run):

| File                  | Scope                      |
| --------------------- | -------------------------- |
| `drive_token.json`    | Google Drive (read/write)  |
| `Calendar_token.json` | Google Calendar Events     |
| `email_token.json`    | Gmail send                 |

### 5. Set the Google Drive folder ID

In `app/google_drive.py`, set `FOLDER_ID` to the ID of the Drive folder where course files should be saved. The folder ID is the last segment of its URL:

```text
https://drive.google.com/drive/folders/<FOLDER_ID>
```

### 6. Set the recipient email

In `app/email_alerts.py`, update `recipient_email` to the address that should receive the weekly digest.

---

## Running the App

```bash
uvicorn app.main:app --reload
```

Then open **[http://127.0.0.1:8000/ui](http://127.0.0.1:8000/ui)** in your browser.

---

## API Endpoints

| Method | Endpoint  | Description                                                         |
| ------ | --------- | ------------------------------------------------------------------- |
| `GET`  | `/health` | Health check                                                        |
| `POST` | `/chat`   | Runs the full agent workflow                                        |
| `POST` | `/ask`    | Answers follow-up questions using the agent's last output as context |
| `GET`  | `/ui`     | Serves the web UI                                                   |

### Request / Response format

```json
// POST /chat or /ask
{ "message": "your message here" }

// Response
{ "response": "agent or LLM response" }
```

---

## Agent Workflow

When **Run Agent** is clicked, the agent executes these steps in order:

1. `list_canvas_courses` — discover available courses
2. `save_canvas_course_to_drive` — save syllabus and module content to Drive (per course)
3. `extract_assignments_from_canvas` — RAG-extract all assignments and due dates (per course)
4. `create_calendar_event` — add each deadline to Google Calendar
5. `extract_weekly_deadlines` — filter added events to the next 7 days
6. `send_weekly_calendar_bulletin` — email the upcoming deadlines
7. `generate_study_plan` — produce a course summary and study plan (per course)
8. Final summary — courses processed, events created, upcoming deadlines, full study plans

---

## Tech Stack

| Layer           | Technology                                       |
| --------------- | ------------------------------------------------ |
| LLM             | Gemini 2.0 Flash (`gemini-2.0-flash`)            |
| Embeddings      | Gemini Embeddings (`gemini-embedding-001`)       |
| Agent framework | LangGraph ReAct                                  |
| Vector store    | Chroma (in-memory)                               |
| API server      | FastAPI + Uvicorn                                |
| External APIs   | Canvas LMS, Google Drive, Google Calendar, Gmail |
