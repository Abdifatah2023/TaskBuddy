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

Users sign in with Google, and all Google API operations (Calendar, Gmail, Drive) run under their own credentials for the duration of the session.

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
│  ┌──────────────────┐  ┌─────────────────┐  ┌───────────────┐  │
│  │  Landing Page    │  │  Agent Panel    │  │  Chat Panel   │  │
│  │  (sign in)       │  │  (run workflow) │  │  (multi-turn) │  │
│  └────────┬─────────┘  └────────┬────────┘  └───────┬───────┘  │
└───────────┼─────────────────────┼────────────────────┼─────────┘
            │ GET /auth/login     │ POST /chat         │ POST /ask
            ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI Server (main.py)                      │
│   OAuth endpoints   │   Background jobs   │   Session router    │
│   /auth/login       │   /chat → job_id    │   /ask (chat)       │
│   /auth/callback    │   /result/{job_id}  │   /calendar-events  │
│   /auth/logout      │   /status           │   /reset            │
└──────────┬──────────┴────────┬────────────┴────────────────────┘
           │                   │
           ▼                   ▼
┌──────────────────┐  ┌────────────────────────────────────────┐
│  app/auth.py     │  │  LangGraph ReAct Agent (Agent_setup.py)│
│  Web OAuth flow  │  │  build_agent(credentials, user_email)  │
│  Session store   │  │  8-step workflow                       │
│  (in-memory)     │  └──────────────────────────────────────┬─┘
└──────────────────┘                                         │
                                                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   External Services                              │
│  Canvas LMS API  │  Google Drive  │  Google Calendar  │  Gmail  │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │     RAG Pipeline      │
                  │  Gemini Embeddings   │
                  │  + ChromaDB          │
                  └──────────────────────┘
```

**Data flow summary:**

1. User visits `/landing.html` and clicks "Sign in with Google".
2. OAuth redirects through `/auth/login` → Google consent → `/auth/callback`, which creates a server-side session and sets an `HttpOnly` cookie.
3. The main UI (`/`) reads the cookie, fetches `/config` to get the user's email, and displays it in the header.
4. The UI triggers the agent via `POST /chat`, which starts a background job using the session's Google credentials. Progress is polled via `GET /result/{job_id}`.
5. All Gmail, Calendar, and Drive operations run under the authenticated user's OAuth token for that session.

---

## 2. System Components

### `app/auth.py` — Authentication & Session Management

Handles the Google web OAuth 2.0 flow and maintains server-side sessions.

| Function | Purpose |
|---|---|
| `build_auth_url(redirect_uri)` | Builds the Google consent URL with all required scopes |
| `exchange_code(code, redirect_uri)` | Exchanges the one-time auth code for OAuth credentials |
| `get_user_email(credentials)` | Calls Gmail `users().getProfile()` to fetch the authenticated email |
| `create_session(credentials, email)` | Stores credentials + email in the in-memory session store, returns a UUID session ID |
| `get_session(session_id)` | Returns `{credentials, email}` dict or `None` |
| `delete_session(session_id)` | Removes session on logout |

**Scopes requested:** `openid`, `userinfo.email`, `gmail.modify`, `gmail.send`, `calendar.events`, `drive`

**Session store:** In-memory `dict[session_id → {credentials, email}]`. Sessions are lost on server restart; users are redirected to sign in again.

---

### `app/main.py` — FastAPI Application

The entry point and HTTP layer. Responsibilities:

- Exposes all API and auth endpoints.
- Manages an in-memory job store (`_jobs`) mapping `job_id → {status, result}`.
- Runs the agent as a FastAPI `BackgroundTask` so the HTTP response returns immediately.
- Stores the agent's last output in `_agent_context` and passes it to every chat turn.
- All non-auth endpoints require a valid `taskbuddy_session` cookie (returns 401 otherwise).
- Serves the static web UI from the `static/` directory at `/`.

Key globals:

| Variable | Type | Purpose |
|---|---|---|
| `_agent_context` | `str` | Last agent final summary — used as chat context |
| `_last_run` | `str` | Timestamp of the most recent agent run |
| `_jobs` | `dict` | In-memory job store: `job_id → {status, result}` |

---

### `app/Agent_setup.py` — LangGraph ReAct Agent

Defines the main autonomous agent and all workflow tools. The agent is created per-request via `build_agent(credentials, user_email)` so each run uses the signed-in user's own Google credentials.

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

All Canvas API communication and the four Canvas-facing agent tools.

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

Accepts the user's `credentials` object (passed from the session) and manages all Calendar API operations.

| Function | Purpose |
|---|---|
| `parse_datetime(dt_string, default_hour=23)` | Parses Canvas/ISO date strings and returns a timezone-aware local datetime. |
| `find_event_by_title(service, title)` | Searches 6 months back to 12 months forward for a TaskBuddy-owned event matching the title. |
| `GoogleCalendarTool(title, start_time, end_time, credentials)` | Upsert: create / update / skip based on whether the event already exists at the correct date. |
| `get_taskbuddy_events(credentials)` | Returns all TaskBuddy-created events in the Calendar for the `/calendar-events` endpoint. |

---

### `app/google_drive.py` — Google Drive

Minimal module: `FOLDER_ID` from `DRIVE_FOLDER_ID` env var and `get_drive_service(credentials)` that builds the Drive v3 client from the session credentials.

---

### `app/drive_upload_utils.py` — Drive File Utilities

| Function | Purpose |
|---|---|
| `get_or_create_folder(service, folder_name, parent_id)` | Returns the folder ID if it already exists under the parent, otherwise creates it. |
| `upload_text_file(service, folder_id, file_name, text)` | Upserts a text file: updates if it already exists, creates it otherwise. |

---

### `app/email_alerts.py` — Gmail Integration

`gmail_send_message(credentials, email_body, recipient)` — constructs and sends a plain-text email using the signed-in user's Gmail account via the Gmail API.

---

### `app/chat_agent.py` — Chat Agent

Multi-turn conversational assistant with tool-calling support. Accepts the session's `credentials` and `user_email` so all operations run under the authenticated user.

**Session state (`ChatSession`):**

| Field | Type | Purpose |
|---|---|---|
| `history` | `list[BaseMessage]` | Conversation history, capped at 40 messages |
| `quiz` | `Optional[QuizState]` | Active quiz state (questions, current index, score) |
| `email_draft` | `Optional[dict]` | Staged email `{to, subject, body}` awaiting confirmation |

**Shortcuts (bypass LLM entirely):**
- **Quiz answer** — If a quiz is active and the message matches `[A/B/C/D]`, the answer is processed directly without an LLM call.
- **Email confirmation** — If an email draft is staged and the message contains "send", "confirm", "yes", or "go ahead", the email is sent directly.

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

---

## 4. Agent Tools Reference

### `list_canvas_courses`

Returns all Canvas courses for the configured token. If `CANVAS_COURSE_IDS` is set, only those are returned.

**Returns:** `[{"course_id": "12345", "course_name": "..."}]`

---

### `save_canvas_course_to_drive`

Extracts syllabus and all module content from a Canvas course and saves them as `syllabus.txt` / `course_content.txt` in a per-course Drive subfolder.

---

### `extract_assignments_from_canvas`

RAG-based semantic extraction: embeds all course content and asks the LLM to find every assignment with its due date.

**Returns:** `[{"assignment_name": "...", "due_date": "YYYY-MM-DD or null"}]`

---

### `get_canvas_calendar_events`

Direct API fetch from Canvas Assignments API + Calendar Events API, merged and deduplicated.

**Returns:** `[{"assignment_name": "...", "due_date": "YYYY-MM-DD"}]`

---

### `create_calendar_event`

Upserts a Google Calendar event. Only called when `due_date` is non-null.

**Returns:** `"Event created successfully: ..."` / `"Event updated successfully: ..."` / `"Event '...' already exists on ..."`.

---

### `extract_weekly_deadlines`

Filters `added_events` (in-memory) to those due within the next 7 days.

---

### `send_weekly_calendar_bulletin`

Sends a Gmail bulletin to the signed-in user's email address listing upcoming deadlines.

---

### `generate_study_plan`

Builds the study plan RAG chain and returns a `## Course Summary` + `## Study Plan` for the course. Also caches course text so the chat agent can answer course-specific questions.

---

## 5. Chat Agent

The chat agent (`app/chat_agent.py`) operates after the main agent workflow has run. It uses the agent's final summary as background context and the course content RAG for grounded answers.

### Tools Available to the Chat Agent

- **`search_course_content`** — semantic search over cached course materials.
- **`update_assignment_due_date`** — reschedules an event in Google Calendar.
- **`draft_email`** — stages an email for user review (never sends automatically).
- **`send_email`** — sends the confirmed draft.
- **`generate_quiz`** — generates an interactive multiple-choice quiz; answers bypass the LLM entirely.

### Chat System Prompt

Every LLM call includes the current date/time, the user's email address, and the agent's last summary (truncated to 1500 characters). When drafting emails, the recipient is pre-filled with the user's own address unless they specify otherwise.

---

## 6. RAG Pipeline

- **Text splitter:** `RecursiveCharacterTextSplitter` — 1500 token chunks, 500 overlap
- **Embedding model:** `GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")`
- **Vector store:** `chromadb.EphemeralClient()` — fully in-memory, no disk persistence

| Chain | Retrieval k | Purpose |
|---|---|---|
| Assignment extraction | 5 | Return ONLY a JSON array `[{assignment_name, due_date}]` |
| Study plan | 8 | Return `## Course Summary` + `## Study Plan` |
| Chat Q&A | 6 | Answer student questions grounded in retrieved content |

---

## 7. API Endpoints

| Method | Path | Auth required | Description |
|---|---|---|---|
| `GET` | `/auth/login` | No | Redirects to Google OAuth consent |
| `GET` | `/auth/callback` | No | Exchanges auth code, sets session cookie, redirects to `/` |
| `GET` | `/auth/logout` | No | Clears session and cookie, redirects to `/landing.html` |
| `GET` | `/config` | Yes | Returns `{"email": "user@example.com"}` |
| `GET` | `/health` | No | Returns `{"status": "ok"}` |
| `POST` | `/chat` | Yes | Starts the agent workflow. Returns `{job_id}`. |
| `GET` | `/result/{job_id}` | Yes | Returns `{status, result, progress}`. |
| `GET` | `/status` | Yes | Returns `{last_run, courses, deadlines, progress, summary}`. |
| `GET` | `/calendar-events` | Yes | Returns `{events: [{title, due_date}]}`. |
| `POST` | `/ask` | Yes | Runs one chat turn. Returns `{response}`. |
| `POST` | `/reset` | Yes | Clears all server-side state. |
| `GET` | `/` | — | Serves `static/index.html` (redirected to landing if unauthenticated). |

**Auth:** Every protected endpoint reads the `taskbuddy_session` cookie and returns `401` if absent or invalid. The frontend redirects to `/landing.html` on 401.

---

## 8. Web UI

### Pages

- **`/landing.html`** — sign-in page with "Sign in with Google" button. Shown to unauthenticated users.
- **`/` (index.html)** — main application. On load, fetches `/config`; redirects to `/landing.html` if the response is 401. Displays the user's email and a "Sign out" link in the header.

### Layout

```
┌──────────── Agent Panel (380px) ─────────┬─────── Chat Panel (flex) ──────────┐
│ [Run Agent]  [Reset]                     │  Message history                    │
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

- **Auth guard** — On load, `fetch('/config')` returns the user's email (200) or 401. On 401, the page redirects to `/landing.html` immediately.
- **Agent run** — Clicks "Run Agent" → `POST /chat` → polls `GET /result/{job_id}` every 1.5s until done.
- **Step tracker** — Shows one active step at a time with a pulsing dot.
- **Calendar panel** — Loads events from `/calendar-events` on load and after each agent run. Refreshes automatically if a chat response mentions rescheduling.
- **Reset** — Calls `POST /reset`, clears all UI panels, wipes chat history, generates a new session ID.

---

## 9. Configuration & Setup

### Environment Variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_API_KEY` | Yes | Gemini API key (LLM and embeddings) |
| `GOOGLE_CLIENT_ID` | Yes | OAuth Web Client ID from Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | Yes | OAuth Web Client Secret |
| `CANVAS_API_TOKEN` | Yes | Canvas LMS API token |
| `CANVAS_BASE_URL` | Yes | Canvas instance URL, e.g. `https://canvas.school.edu` |
| `BASE_URL` | Yes | Public base URL for OAuth redirect, e.g. `http://127.0.0.1:8000` |
| `CANVAS_COURSE_IDS` | No | Comma-separated course IDs. If omitted, all active courses are fetched. |
| `DRIVE_FOLDER_ID` | No | Google Drive parent folder ID. Falls back to the default folder if not set. |

### Google Cloud Console Setup

1. Go to **APIs & Services → Credentials** and create a **Web application** OAuth 2.0 client.
2. Add your `BASE_URL/auth/callback` to **Authorised redirect URIs** (e.g. `http://127.0.0.1:8000/auth/callback`).
3. Copy the **Client ID** and **Client Secret** into `.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
4. Enable the following APIs: Gmail API, Google Calendar API, Google Drive API.

No token files or `credentials.json` are needed — the app handles the full OAuth flow at runtime.

### Local Installation

```bash
git clone <repo-url>
cd TaskBuddy
pip install -r requirements.txt
```

Create `.env` with the variables above, then run:

```bash
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000** and sign in with Google. The app will request all necessary permissions on first sign-in.

---

## 10. Tech Stack & Dependencies

| Category | Library / Service | Version | Role |
|---|---|---|---|
| LLM | Gemini 2.0 Flash | — | Agent reasoning, study plans, chat Q&A, quiz generation |
| Embeddings | Gemini Embedding 001 | — | Vectorising course content for RAG |
| Agent framework | LangGraph (`langgraph`) | >=1.0.0 | ReAct agent loop (`create_react_agent`) |
| LLM abstraction | LangChain Core | >=1.2.0 | Tools, messages, callbacks |
| LLM provider | `langchain-google-genai` | >=4.2.0 | `ChatGoogleGenerativeAI`, `GoogleGenerativeAIEmbeddings` |
| Vector store | ChromaDB (`chromadb`) | 1.3.4 | In-memory vector storage |
| Text splitting | `langchain-text-splitters` | >=1.0.0 | `RecursiveCharacterTextSplitter` |
| Community tools | `langchain-community` | >=0.4.0 | `Chroma` vectorstore wrapper |
| API server | FastAPI + Uvicorn | latest | HTTP endpoints and background tasks |
| HTML parsing | BeautifulSoup4 | latest | Stripping HTML from Canvas pages |
| HTTP client | Requests | latest | Canvas API calls |
| Google APIs | `google-api-python-client` | 2.188.0 | Drive, Calendar, Gmail API clients |
| Google Auth | `google-auth`, `google-auth-oauthlib` | 2.48.0 / 1.2.4 | OAuth 2.0 web flow |
| Config | `python-dotenv` | 1.2.1 | `.env` file loading |
| Markdown | `marked.js` (CDN) | latest | Browser-side Markdown rendering |

---

## 11. Deployment

### Hugging Face Spaces (Docker)

TaskBuddy runs on Hugging Face Spaces as a Docker container exposing port 7860.

**Secrets** (set in the Space Settings → Secrets tab):

| Secret name | Content |
|---|---|
| `GOOGLE_API_KEY` | Gemini API key |
| `GOOGLE_CLIENT_ID` | OAuth Web Client ID |
| `GOOGLE_CLIENT_SECRET` | OAuth Web Client Secret |
| `CANVAS_API_TOKEN` | Canvas LMS API token |
| `CANVAS_BASE_URL` | Canvas instance URL |
| `BASE_URL` | Your Space URL, e.g. `https://moeid25-taskbuddy.hf.space` |

Optional secrets: `CANVAS_COURSE_IDS`, `DRIVE_FOLDER_ID`.

**Google Cloud Console:** Add `<BASE_URL>/auth/callback` to the OAuth client's authorised redirect URIs.

**Deploy:**
```bash
git remote add space https://<username>:<HF_TOKEN>@huggingface.co/spaces/<username>/TaskBuddy
git push space main
```

> **Single instance:** The in-memory job store, session store, and course cache are process-local. Do not scale beyond one container replica or run with `--workers > 1`.
