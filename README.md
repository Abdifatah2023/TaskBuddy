---
title: TaskBuddy
emoji: 📚
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# TaskBuddy — Agent Documentation

TaskBuddy is an AI-powered academic assistant that automates course management end-to-end. It connects to a Canvas LMS instance, pulls all course content, extracts assignments and deadlines via a RAG pipeline, syncs them to Google Calendar, delivers a weekly email digest, and generates personalised study plans — all exposed through a web UI with a conversational chat interface.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [System Components](#2-system-components)
3. [Agent Workflow](#3-agent-workflow)
4. [Agent Tools Reference](#4-agent-tools-reference)
5. [Chat Agent](#5-chat-agent)
6. [RAG Pipeline](#6-rag-pipeline)
7. [API Endpoints](#7-api-endpoints)
8. [Web UI](#8-web-ui)
9. [Configuration & Setup](#9-configuration--setup)
10. [Tech Stack & Dependencies](#10-tech-stack--dependencies)
11. [Deployment](#11-deployment)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Web Browser (UI)                         │
│  ┌─────────────────────┐      ┌──────────────────────────────┐  │
│  │    Agent Panel      │      │        Chat Panel            │  │
│  │  (run workflow,     │      │  (multi-turn Q&A, quizzes,   │  │
│  │   step tracker,     │      │   email drafting, assignment  │  │
│  │   calendar, cards)  │      │   rescheduling)              │  │
│  └────────┬────────────┘      └──────────────┬───────────────┘  │
└───────────┼───────────────────────────────────┼─────────────────┘
            │  POST /chat  (job)                 │  POST /ask
            │  GET  /result/{job_id}             │
            ▼                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI Server (main.py)                      │
│  Background job queue  │  Session router  │  /calendar-events   │
└──────────┬─────────────┴────────┬──────────┴──────────────────┘
           │                      │
           ▼                      ▼
┌──────────────────┐   ┌─────────────────────────────────────────┐
│  LangGraph ReAct │   │           Chat Agent (chat_agent.py)    │
│  Agent           │   │  Tools: search_course_content,          │
│  (Agent_setup.py)│   │         update_assignment_due_date,     │
│                  │   │         draft_email, send_email,        │
│  8-step workflow │   │         generate_quiz                   │
└──────┬───────────┘   └──────────────────┬──────────────────────┘
       │                                  │
       ▼                                  ▼
┌──────────────────────────────────────────────────────────────┐
│                   External Services                           │
│  Canvas LMS API  │  Google Drive  │  Google Calendar  │ Gmail │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│       RAG Pipeline           │
│  Gemini Embeddings + Chroma  │
│  (assignment extraction,     │
│   study plans, chat Q&A)     │
└──────────────────────────────┘
```

**Data flow summary:**
The UI triggers the agent via `POST /chat`, which starts a background job. The LangGraph ReAct agent calls its tools in a fixed order, each tool invoking one or more external services. Progress is polled by the UI via `GET /result/{job_id}`. Once the agent completes, its final summary is stored server-side and passed as context to every subsequent `/ask` chat request.

---

## 2. System Components

### `app/main.py` — FastAPI Application

The entry point and HTTP layer. Responsibilities:
- Exposes all API endpoints.
- Manages an in-memory job store (`_jobs`) mapping `job_id → {status, result}`.
- Runs the agent as a FastAPI `BackgroundTask` so the HTTP response returns immediately.
- Stores the agent's last output in `_agent_context` and passes it to every chat turn.
- Exposes a `/reset` endpoint that clears all server-side state and chat sessions.
- Serves the static web UI from the `static/` directory at `/`.

Key globals:

| Variable | Type | Purpose |
|---|---|---|
| `_agent_context` | `str` | Last agent final summary — used as chat context |
| `_last_run` | `str` | Timestamp of the most recent agent run |
| `_jobs` | `dict` | In-memory job store: `job_id → {status, result}` |

---

### `app/Agent_setup.py` — LangGraph ReAct Agent

Defines the main autonomous agent and all workflow tools. Responsibilities:
- Configures the LangGraph ReAct agent with Gemini 2.0 Flash and 8 tools.
- Manages progress tracking (`_step_progress`) so the UI can show live step status.
- Caches course text (`_course_content_cache`) after study plan generation for the chat RAG.
- Provides `reset_progress()` to clear state between runs.

Key globals:

| Variable | Type | Purpose |
|---|---|---|
| `_step_progress` | `list[dict]` | `[{name, status}]` — mutated in-place so the UI reference stays valid |
| `_courses_data` | `list[dict]` | Courses discovered this run: `[{course_id, course_name}]` |
| `added_events` | `list[dict]` | Events added to Calendar this run: `[{title, due_date}]` |
| `_course_content_cache` | `dict[str, str]` | `course_id → full_text` — populated by `generate_study_plan`, consumed by chat RAG |

**Progress tracking** — `ProgressTracker(BaseCallbackHandler)` is attached to the agent as a LangChain callback. It listens for `on_tool_start` / `on_tool_end` events and updates `_step_progress` entries in-place:

| Step index | Step name | Triggered by tool |
|---|---|---|
| 0 | Discovering courses | `list_canvas_courses` |
| 1 | Saving to Drive | `save_canvas_course_to_drive` |
| 2 | Extracting assignments | `extract_assignments_from_canvas`, `get_canvas_calendar_events` |
| 3 | Adding to Calendar | `create_calendar_event` |
| 4 | Filtering deadlines | `extract_weekly_deadlines` |
| 5 | Sending email | `send_weekly_calendar_bulletin` |
| 6 | Generating study plans | `generate_study_plan` |

---

### `app/canvas.py` — Canvas LMS Integration

All Canvas API communication and the four Canvas-facing agent tools. Responsibilities:
- Authenticates every request with the `CANVAS_API_TOKEN` header.
- Handles paginated responses via `Link` header navigation.
- Converts all Canvas UTC timestamps to the configured local timezone to avoid off-by-one date errors.
- Extracts readable text from pages, assignments, quizzes, and text files for RAG ingestion.

Key internal helpers:

| Function | Purpose |
|---|---|
| `_canvas_utc_to_local_date(dt_string)` | Converts a Canvas UTC ISO string (e.g. `2026-03-30T05:59:00Z`) to a local `YYYY-MM-DD` string. Critical for correct calendar dates. |
| `_get_paginated(url)` | Follows Canvas `Link: rel="next"` pagination and returns all items. |
| `_html_to_text(html)` | Strips HTML tags to clean UTF-8 text using BeautifulSoup. |
| `_get_syllabus_text(course_id)` | Fetches only the syllabus body for a course. |
| `_get_modules_text(course_id)` | Iterates all modules and items (pages, assignments, quizzes, files) and returns their text. |
| `_get_course_text(course_id)` | Combines syllabus + module text into one string ready for RAG. |
| `_fetch_canvas_calendar_events(course_id)` | Dual-source fetch: Assignments API (primary) + Calendar Events API (supplement), deduplicated by name. |

---

### `app/RagPipeline.py` — RAG Pipeline

Builds and holds three separate LangChain RAG chains backed by ChromaDB (in-memory, ephemeral). Each chain is rebuilt when new content arrives.

| Chain | Function | Collection | k | Purpose |
|---|---|---|---|---|
| Assignment extraction | `build_rag_chain(text)` | `academic_docs` | 5 | Extracts `[{assignment_name, due_date}]` JSON from course content |
| Study plan | `build_study_plan_chain(text)` | `course_content` | 8 | Generates a course summary + weekly study plan |
| Chat Q&A | `build_chat_rag_chain(text)` | `chat_qa` | 6 | Answers student questions grounded in course materials |

All chains share the same embedding model (`gemini-embedding-001`) and text splitter (1500 token chunks, 500 overlap). Each uses a separate `EphemeralClient()` so collections are isolated and never persist to disk.

---

### `app/Google_calendar.py` — Google Calendar Integration

Manages authentication and all Calendar API operations.

| Function | Purpose |
|---|---|
| `_load_creds()` | Loads and refreshes OAuth credentials from token file. Handles token path differences between local and hosted deployments. |
| `parse_datetime(dt_string, default_hour=23)` | Parses Canvas/ISO date strings and returns a timezone-aware local datetime. Handles plain dates (sets to 23:00 local), naive datetimes, and UTC-aware datetimes (converts to local). |
| `find_event_by_title(service, title)` | Searches 6 months back to 12 months forward for a TaskBuddy-owned event matching the title. Used for upsert logic. |
| `GoogleCalendarTool(title, start_time, end_time)` | **Upsert**: creates the event if missing, updates it if the date changed, skips if already correct. Writes events in the local timezone. |
| `get_taskbuddy_events()` | Returns all TaskBuddy-created events in the Calendar (identified by `"TaskBuddy"` in the event description) for the `/calendar-events` endpoint. |

**Upsert logic detail:**

```
find_event_by_title()
    ├── Not found  →  Create new event with 24h and 1h popup reminders
    ├── Found, same date  →  Skip (idempotent)
    └── Found, different date  →  Update event in place (preserves event ID and other fields)
```

---

### `app/google_drive.py` — Google Drive Authentication

Minimal module providing Drive credentials and a service client. `FOLDER_ID` reads from the `DRIVE_FOLDER_ID` environment variable (falls back to the hardcoded default) and points to the parent Drive folder where all course subfolders are created.

---

### `app/drive_upload_utils.py` — Drive File Utilities

Two helpers used by `save_canvas_course_to_drive`:

| Function | Purpose |
|---|---|
| `get_or_create_folder(service, folder_name, parent_id)` | Returns the folder ID if it already exists under the parent, otherwise creates it. |
| `upload_text_file(service, folder_id, file_name, text)` | Upserts a text file: updates if it already exists in the folder, creates it otherwise. |

---

### `app/email_alerts.py` — Gmail Integration

Provides Gmail authentication and the send function used by the weekly bulletin tool.

| Function | Purpose |
|---|---|
| `authenticate()` | OAuth flow for Gmail. Reads token from `/etc/secrets/email_token.json` on hosted platforms, `email_token.json` locally. |
| `gmail_send_message(creds, email_body)` | Constructs and sends a plain-text email to the address set in `RECIPIENT_EMAIL`. Used by `send_weekly_calendar_bulletin`. |

---

### `app/chat_agent.py` — Chat Agent

Multi-turn conversational assistant with tool-calling support. Maintains per-session state and implements four capabilities on top of the base LLM.

**Session state (`ChatSession`):**

| Field | Type | Purpose |
|---|---|---|
| `history` | `list[BaseMessage]` | Conversation history, capped at 40 messages |
| `quiz` | `Optional[QuizState]` | Active quiz state (questions, current index, score) |
| `email_draft` | `Optional[dict]` | Staged email `{to, subject, body}` awaiting confirmation |

**Shortcuts (bypass LLM entirely):**
- **Quiz answer** — If a quiz is active and the message matches `[A/B/C/D]`, the answer is processed directly without an LLM call.
- **Email confirmation** — If an email draft is staged and the message contains "send", "confirm", "yes", or "go ahead", the email is sent directly.

**Two-round tool call loop:**
1. First LLM call with tools bound — may return direct text or a list of tool calls.
2. If tool calls are present, each tool is invoked, results collected as `ToolMessage` objects.
3. Second LLM call (no tools bound) synthesizes the tool results into a final response.

**Chat RAG lazy rebuild (`_ensure_chat_rag`):**
Compares the current keys of `_course_content_cache` (populated by the agent after study plan generation) against a cached `frozenset`. Rebuilds the `chat_rag_chain` only when new course content is available, avoiding re-embedding on every message.

---

## 3. Agent Workflow

The full workflow is triggered by a `POST /chat` request. The LangGraph ReAct agent executes the following steps **in strict order**, one course at a time:

```
Step 1  list_canvas_courses
        └── Returns [{course_id, course_name}, ...]

Step 2  save_canvas_course_to_drive  (× each course)
        ├── Extracts syllabus → uploads as syllabus.txt
        └── Extracts module content → uploads as course_content.txt

Step 3  For each course:
        ├── extract_assignments_from_canvas  (RAG-based extraction)
        └── get_canvas_calendar_events       (direct Canvas API)
        Merge both lists, deduplicate by assignment_name,
        prefer due_date from get_canvas_calendar_events when both present.

Step 4  create_calendar_event  (× each unique assignment with a non-null due_date)
        └── Upsert: create / update / skip

Step 5  extract_weekly_deadlines
        └── Filters added_events to due within next 7 days

Step 6  send_weekly_calendar_bulletin
        └── Sends Gmail with the filtered deadline list

Step 7  generate_study_plan  (× each course)
        ├── Caches full course text into _course_content_cache
        ├── Builds study plan RAG chain
        └── Returns ## Course Summary + ## Study Plan

Step 8  Final summary (LLM-generated, self-contained)
        ├── Number of courses processed
        ├── Number of newly created Calendar events
        ├── Assignments due in the next 7 days
        ├── Confirmation that bulletin email was sent
        └── Full verbatim study plan text for every course
```

**Rules enforced by the agent prompt:**
- Never hallucinate assignments or due dates.
- Never call `create_calendar_event` when `due_date` is null.
- Process one course at a time (no batched tool calls across courses).
- The final summary must reproduce each study plan in full — never "see above".

---

## 4. Agent Tools Reference

### `list_canvas_courses`

**Module:** `canvas.py`
**Description:** Discovers all Canvas courses accessible with the configured API token. If `CANVAS_COURSE_IDS` is set in `.env`, only those courses are returned (useful for restricting the agent to specific courses).

**Parameters:** None (accepts an ignored `_` string for LangChain compatibility)

**Returns:** JSON array
```json
[
  {"course_id": "12345", "course_name": "Introduction to CS"},
  {"course_id": "67890", "course_name": "Data Structures"}
]
```

---

### `save_canvas_course_to_drive`

**Module:** `canvas.py`
**Description:** Extracts all readable content from a Canvas course and saves it to Google Drive. Creates a per-course subfolder inside the configured `DRIVE_FOLDER_ID`. Handles upsert — if files already exist from a prior run, they are updated.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `course_id` | `str` | Numeric Canvas course ID |
| `course_name` | `str` | Human-readable course name (used as folder name) |

**Returns:** JSON object
```json
{
  "course_id": "12345",
  "course_name": "Introduction to CS",
  "drive_folder_id": "1AbC...",
  "files_saved": [
    {"file": "syllabus.txt", "file_id": "1XyZ..."},
    {"file": "course_content.txt", "file_id": "2AbC..."}
  ]
}
```

---

### `extract_assignments_from_canvas`

**Module:** `canvas.py`
**Description:** Fetches all course content (syllabus + all module items) and runs it through the assignment-extraction RAG chain. The LLM reads the retrieved chunks and returns every assignment, project, and exam it can identify. This is a semantic extraction — it finds assignments mentioned anywhere in the course text.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `course_id` | `str` | Numeric Canvas course ID |

**Returns:** JSON array (or `[]` if no content / chain error)
```json
[
  {"assignment_name": "Homework 1", "due_date": "2026-03-15"},
  {"assignment_name": "Midterm Exam", "due_date": "2026-03-28"},
  {"assignment_name": "Final Project", "due_date": null}
]
```

---

### `get_canvas_calendar_events`

**Module:** `canvas.py`
**Description:** Queries the Canvas APIs directly (no RAG) to retrieve assignments and their due dates. Uses two complementary sources merged together: the **Assignments API** (authoritative for all published assignments) and the **Calendar Events API** (supplements with any student-calendar items not in the Assignments API). All UTC due dates are converted to local time.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `course_id` | `str` | Numeric Canvas course ID |

**Returns:** JSON array
```json
[
  {"assignment_name": "Lab 3", "due_date": "2026-03-20"},
  {"assignment_name": "Quiz 2", "due_date": "2026-03-22"}
]
```

**Why both tools?** `extract_assignments_from_canvas` catches assignments described in pages and syllabi but may miss exact due dates. `get_canvas_calendar_events` has authoritative dates but only covers published assignments. The agent merges both, preferring the API date when both sources agree on a name.

---

### `create_calendar_event`

**Module:** `Agent_setup.py`
**Description:** Upserts a Google Calendar event for an assignment. Internally calls `GoogleCalendarTool()` which performs a three-way check: create, update, or skip. Only called when `due_date` is non-null. Also updates the in-session `added_events` list used by the weekly digest tools.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `title` | `str` | Assignment name — becomes the event summary |
| `due_date` | `str` | Due date in `YYYY-MM-DD` format |

**Returns:** One of:
- `"Event created successfully: <htmlLink>"` — new event
- `"Event updated successfully: '<title>' moved from <old> to <new>"` — date changed
- `"Event '<title>' already exists on <date>."` — no change needed

---

### `extract_weekly_deadlines`

**Module:** `Agent_setup.py`
**Description:** Filters the `added_events` list (populated by `create_calendar_event` during this run) to only those with a due date within the next 7 days. Pure in-memory operation — no external API calls.

**Parameters:** None

**Returns:** JSON array of upcoming events, or a plain string if none exist
```json
[
  {"title": "Homework 3", "due_date": "2026-04-01"},
  {"title": "Quiz 2", "due_date": "2026-04-03"}
]
```

---

### `send_weekly_calendar_bulletin`

**Module:** `Agent_setup.py`
**Description:** Sends a Gmail bulletin listing the assignments due within the next 7 days that were added by TaskBuddy in this run. If no deadlines fall in the window, the email explicitly says so. Called after `extract_weekly_deadlines`.

**Parameters:** None

**Returns:**
- `"Success: weekly bulletin email sent."` on success
- `"Failed with error: <message>"` on failure

**Email format:**
```
TaskBuddy — Upcoming Deadlines (Next 7 Days)

- Homework 3  (Due: 2026-04-01)
- Quiz 2      (Due: 2026-04-03)
```

---

### `generate_study_plan`

**Module:** `Agent_setup.py`
**Description:** Fetches all course content, caches it for the chat RAG, builds the study plan RAG chain, and asks the LLM to produce a structured course summary and week-by-week study plan. The cache update is the mechanism that activates course-grounded Q&A in the chat panel.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `course_id` | `str` | Numeric Canvas course ID |
| `course_name` | `str` | Human-readable course name |

**Returns:** Formatted markdown text with two sections:
```
## Course Summary
- Key topic 1
- Key topic 2
...

## Study Plan
### Week 1: ...
...
```

---

## 5. Chat Agent

The chat agent (`app/chat_agent.py`) is a separate, stateful assistant that operates after the main agent workflow has run. It uses the agent's final summary as background context and the course content RAG for grounded answers.

### Tools Available to the Chat Agent

#### `search_course_content`

Performs a semantic search over all cached course materials (syllabi, module pages, assignments, study plans). Automatically rebuilds the RAG index when new course content is available. Returns a detailed answer grounded in retrieved chunks.

**When the LLM calls it:** Any question about course topics, assignment details, deadlines, or study plan content.

---

#### `update_assignment_due_date`

Reschedules an existing Google Calendar event by calling `GoogleCalendarTool()` with a new date. Also updates the in-session `added_events` list for consistency.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `assignment_name` | `str` | Title of the assignment to reschedule |
| `new_due_date` | `str` | New date in `YYYY-MM-DD` format |

**Frontend effect:** After a successful reschedule, the UI automatically refreshes the calendar panel by detecting calendar-related keywords in the response.

---

#### `draft_email`

Stores an email draft in the session and returns it formatted for user review. **Never sends automatically** — always requires explicit user confirmation.

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `recipient_email` | `str` | Recipient address |
| `subject` | `str` | Email subject line |
| `body` | `str` | Email body text |

**Returns:** A formatted preview ending with: `Reply 'send email' to send it, or tell me what to change.`

---

#### `send_email`

Sends the email that was previously stored by `draft_email`. Only called after the user explicitly confirms. Clears the draft from session state on success.

**Confirmation triggers (no LLM needed):** "send", "send it", "confirm", "yes", "go ahead"

---

#### `generate_quiz`

Generates a multiple-choice quiz on a topic using the course content RAG as context. The LLM returns a strict JSON array of questions, each with options A–D, the correct answer, and an explanation. The quiz then enters an interactive mode where each answer is processed as a shortcut (no LLM call per answer).

**Parameters:**

| Name | Type | Description |
|---|---|---|
| `course_name` | `str` | Name of the course to quiz on |
| `topic` | `str` | Specific topic within the course |
| `num_questions` | `int` | Number of questions (default: 5) |

**Quiz flow:**
```
generate_quiz called
    └── LLM generates questions as JSON
        └── Question 1/N displayed
            └── User replies A/B/C/D  (shortcut — no LLM)
                └── Feedback + next question
                    ...
                        └── Score summary + full review
```

---

### Chat System Prompt

Every LLM call includes the current date/time and the agent's last summary (truncated to 1500 characters if large). This allows the chat agent to answer deadline questions like "what is due this week?" accurately without re-running the workflow.

---

## 6. RAG Pipeline

### Text Splitting

All course content is split with `RecursiveCharacterTextSplitter`:
- **Chunk size:** 1500 tokens
- **Chunk overlap:** 500 tokens

The overlap ensures assignments and deadlines that span paragraph boundaries are captured in at least one chunk.

### Embedding Model

`GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")` — shared across all three chains.

### Vector Store

All chains use `chromadb.EphemeralClient()` — fully in-memory, no disk persistence. A new client is created each time a chain is rebuilt, so there is no cross-run contamination.

### Chain Types

| Chain | Prompt goal | Retrieval k |
|---|---|---|
| Assignment extraction | Return ONLY a JSON array `[{assignment_name, due_date}]`, no markdown | 5 |
| Study plan | Return `## Course Summary` (3-5 bullets) + `## Study Plan` (week-by-week) | 8 |
| Chat Q&A | Answer the student's question, be specific, cite details | 6 |

---

## 7. API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Returns `{"status": "ok"}` — health probe |
| `POST` | `/chat` | Starts the agent workflow as a background job. Returns `{job_id}` immediately. |
| `GET` | `/result/{job_id}` | Polls job status. Returns `{status, result, progress}`. Status: `running` / `done` / `error`. |
| `GET` | `/status` | Returns current agent state: `last_run`, `courses`, `deadlines`, `progress`, `summary`. |
| `GET` | `/calendar-events` | Returns all TaskBuddy-created Google Calendar events: `{events: [{title, due_date}]}`. |
| `POST` | `/ask` | Runs one chat turn. Returns `{response}`. |
| `POST` | `/reset` | Clears all server-side state: jobs, agent context, progress, and all chat sessions. |
| `GET` | `/` | Serves the static web UI (`static/index.html`). |

### Request / Response Schemas

```
POST /chat
Body: { "message": "run" }          (message content is unused by the agent)
Response: { "job_id": "uuid" }

GET /result/{job_id}
Response: {
  "status": "running" | "done" | "error",
  "result": "<final summary text or error message>",
  "progress": [
    {"name": "Discovering courses", "status": "done"},
    {"name": "Saving to Drive",     "status": "active"},
    ...
  ]
}

POST /ask
Body: { "message": "What's due this week?", "session_id": "uuid" }
Response: { "response": "<markdown string>" }

POST /reset
Body: (none)
Response: { "status": "reset" }
```

**Note on session IDs:** The frontend generates a `session_id` with `crypto.randomUUID()` on first load and stores it in `sessionStorage`. This ensures chat history is preserved across requests within the same browser tab but reset on new tabs.

---

## 8. Web UI

Served at `/` from `static/index.html`. Single-page application with no framework — plain HTML, CSS, and JavaScript using the `marked.js` library for Markdown rendering.

### Layout

```
┌──────────── Agent Panel (380px) ─────────┬─────── Chat Panel (flex) ──────────┐
│ [Run Agent]                              │  Message history                    │
│ Step tracker (live progress)             │                                      │
│ Agent output preview (scrollable)        │  [Prompt chips]                     │
│ [View Full Summary]                      │  [Input + Send]                     │
│                                          │                                      │
│ Course Cards (clickable)                 │                                      │
│                                          │                                      │
│ Monthly Calendar (with dots)             │                                      │
│ [Expand Calendar]                        │                                      │
└──────────────────────────────────────────┴─────────────────────────────────────┘
```

### Key UI Behaviours

- **Agent run** — Clicking "Run Agent" posts to `/chat`, receives a `job_id`, then polls `/result/{job_id}` every 1.5 seconds until `status !== "running"`. Each poll updates the step tracker and agent output preview.
- **Step tracker** — Shows one active step at a time with a pulsing dot and fade-in/out transition. Completed steps show a "All steps done" badge.
- **Course cards** — Rendered from `_courses_data` returned by `/status`. Clicking a card opens a modal that extracts and renders the relevant section from the full summary markdown.
- **Monthly calendar** — Loads events from `/calendar-events` on page load and after each agent run. Navigable with prev/next buttons. Days with events show lime dots. The expand button opens a full-size modal calendar with event names.
- **Chat panel** — Sends every message to `/ask` with the session ID. Bot responses are rendered as Markdown. If the response contains calendar-related keywords (`updated`, `rescheduled`, `calendar`, `due date`), the calendar panel refreshes automatically.
- **Prompt chips** — Quick-start buttons covering all five chat capabilities: deadline lookup, quiz generation, email drafting, assignment rescheduling, and course content search.
- **Reset button** — Sits inline next to Run Agent. Prompts for confirmation, then calls `POST /reset`, clears all UI panels, wipes chat history, and generates a new session ID. Disabled while an agent run is in progress.

---

## 9. Configuration & Setup

### Environment Variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_API_KEY` | Yes | Gemini API key (used for LLM and embeddings) |
| `CANVAS_API_TOKEN` | Yes | Canvas LMS API token |
| `CANVAS_BASE_URL` | Yes | Canvas instance URL, e.g. `https://canvas.school.edu` |
| `CANVAS_COURSE_IDS` | No | Comma-separated course IDs. If omitted, all active courses are fetched. |
| `DRIVE_FOLDER_ID` | No | Google Drive parent folder ID. Falls back to the default folder if not set. |
| `RECIPIENT_EMAIL` | No | Email address for the weekly bulletin. Falls back to the default address if not set. |

### Google OAuth Credentials

Three separate OAuth tokens are used (generated automatically on first run via browser flow):

| Token file | API | Scope |
|---|---|---|
| `Calendar_token.json` | Google Calendar | `calendar.events` |
| `drive_token.json` | Google Drive | `drive` (full) |
| `email_token.json` | Gmail | `gmail.modify`, `gmail.send` |

All three share one `credentials.json` (OAuth Desktop client). Place `credentials.json` in the project root.

### Local Installation

```bash
git clone <repo-url>
cd TaskBuddy
pip install -r requirements.txt
```

Create `.env` with the variables above, place `credentials.json` in the root, then run:

```bash
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000**. On first run, three browser OAuth windows will open (one per Google service) to generate the token files.

---

## 10. Tech Stack & Dependencies

| Category | Library / Service | Version | Role |
|---|---|---|---|
| LLM | Gemini 2.0 Flash | — | Agent reasoning, study plans, chat Q&A, quiz generation |
| Embeddings | Gemini Embedding 001 | — | Vectorising course content for RAG |
| Agent framework | LangGraph (`langgraph`) | >=1.0.0 | ReAct agent loop (`create_react_agent`) |
| LLM abstraction | LangChain Core | >=1.2.0 | Tools, messages, callbacks, prompt templates |
| LLM provider | `langchain-google-genai` | >=4.2.0 | `ChatGoogleGenerativeAI`, `GoogleGenerativeAIEmbeddings` |
| Vector store | ChromaDB (`chromadb`) | 1.3.4 | In-memory vector storage for RAG chains |
| Text splitting | `langchain-text-splitters` | >=1.0.0 | `RecursiveCharacterTextSplitter` |
| Community tools | `langchain-community` | >=0.4.0 | `Chroma` vectorstore wrapper |
| API server | FastAPI + Uvicorn | latest | HTTP endpoints and background tasks |
| HTML parsing | BeautifulSoup4 | latest | Stripping HTML from Canvas page bodies |
| HTTP client | Requests | latest | All Canvas API calls |
| Google APIs | `google-api-python-client` | 2.188.0 | Drive, Calendar, Gmail API clients |
| Google Auth | `google-auth`, `google-auth-oauthlib` | 2.48.0 / 1.2.4 | OAuth 2.0 token management |
| Config | `python-dotenv` | 1.2.1 | `.env` file loading |
| Markdown | `marked.js` (CDN) | latest | Browser-side Markdown rendering |

---

## 11. Deployment

### Hugging Face Spaces (Docker)

TaskBuddy runs on Hugging Face Spaces as a Docker container exposing port 7860.

**How it works:**
- The `Dockerfile` builds a Python 3.11-slim image and starts the app via `startup.sh`.
- `startup.sh` reads HF Space secrets (environment variables) and writes them to `/etc/secrets/` as JSON files before starting uvicorn. The app's token-path logic already handles this path automatically.

**Secrets** (set in the Space Settings → Secrets tab):

| Secret name | Content |
|---|---|
| `GOOGLE_API_KEY` | Gemini API key |
| `CANVAS_API_TOKEN` | Canvas LMS API token |
| `CANVAS_BASE_URL` | Canvas instance URL |
| `CREDENTIALS_JSON` | Full contents of `credentials.json` |
| `CALENDAR_TOKEN` | Full contents of `Calendar_token.json` |
| `DRIVE_TOKEN` | Full contents of `drive_token.json` |
| `EMAIL_TOKEN` | Full contents of `email_token.json` |

Optional secrets: `CANVAS_COURSE_IDS`, `DRIVE_FOLDER_ID`, `RECIPIENT_EMAIL`.

**Deploy:**
```bash
git remote add space https://<username>:<HF_TOKEN>@huggingface.co/spaces/<username>/TaskBuddy
git push space main
```

> **Token refresh note:** Google OAuth tokens auto-refresh to `/tmp/` inside the container. These refreshed tokens are lost on container restart. If authentication starts failing after a restart, copy fresh token file contents into the corresponding HF Secrets.

> **Single instance:** The in-memory job store, progress list, and course cache are process-local. Do not scale beyond one container replica or run with `--workers > 1`.
