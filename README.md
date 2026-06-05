---
title: TaskBuddy
emoji: 📚
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# TaskBuddy — Agent Documentation

TaskBuddy is an AI-powered academic assistant that automates course management end-to-end. It connects to a Canvas LMS instance, pulls all course content, extracts assignments and deadlines via a RAG pipeline, syncs them to a session calendar, delivers a weekly email digest with an `.ics` attachment, and generates personalised study plans — all exposed through a web UI with a conversational chat interface.

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
            │ POST /login         │ POST /chat         │ POST /ask
            ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI Server (main.py)                      │
│   Auth endpoints    │   Background jobs   │   Chat router       │
│   /login            │   /chat → job_id    │   /ask              │
│   /logout           │   /result/{job_id}  │   /calendar-events  │
│   /config           │   /status           │   /reset            │
└──────────┬──────────┴────────┬────────────┴────────────────────┘
           │                   │
           ▼                   ▼
┌──────────────────┐  ┌────────────────────────────────────────┐
│  app/auth.py     │  │  LangGraph ReAct Agent (Agent_setup.py)│
│  Canvas token    │  │  build_agent(session)                  │
│  validation      │  │  6-step workflow                       │
│  Signed cookies  │  └──────────────────────────────────────┬─┘
└──────────────────┘                                         │
                                                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   External Services                              │
│  Canvas LMS API  │  SendGrid / Resend / SMTP  │  Gemini API     │
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

1. User visits `/landing.html` and submits their email, Canvas API token, Canvas base URL, and (optionally) course IDs.
2. The server validates the Canvas token against the Canvas API, then returns a signed `taskbuddy_session` cookie containing the session data.
3. The main UI (`/`) reads the cookie via `/config` to get the user's email and displays it in the header.
4. The UI triggers the agent via `POST /chat`, which starts a background job. Progress is polled via `GET /result/{job_id}`.
5. All Canvas operations use the token from the session cookie. Email is sent via SendGrid, Resend, or SMTP (whichever is configured).

---

## 2. System Components

### `app/auth.py` — Authentication & Session Management

Handles Canvas token validation and stateless signed-cookie sessions.

| Function | Purpose |
|---|---|
| `validate_canvas_token(base_url, token)` | Hits the Canvas `/courses` endpoint to verify the token is valid |
| `create_session(email, canvas_token, canvas_base_url, canvas_course_ids)` | Returns a signed, base64-encoded cookie string |
| `get_session(cookie_value)` | Decodes and verifies the cookie; returns the session dict or `None` |
| `delete_session(cookie_value)` | No-op — stateless cookies are invalidated by deleting the cookie client-side |

**Session store:** Stateless — all session data is encoded in the signed cookie itself. No server-side store means sessions survive restarts and scale horizontally without shared state.

**Signing:** HMAC-SHA256 with `SESSION_SECRET`. The cookie payload is `base64(json).hmac`.

---

### `app/main.py` — FastAPI Application

The entry point and HTTP layer. Responsibilities:

- Exposes all API and auth endpoints.
- Manages an in-memory job store (`_jobs`) mapping `job_id → {status, result}`.
- Runs the agent as a FastAPI `BackgroundTask` so the HTTP response returns immediately.
- Stores the agent's last output in `_agent_context` and passes it to every chat turn.
- All non-auth endpoints require a valid `taskbuddy_session` cookie or `Authorization: Bearer <token>` header (returns 401 otherwise).
- Serves the static web UI from the `static/` directory at `/`.

Key globals:

| Variable | Type | Purpose |
|---|---|---|
| `_agent_context` | `str` | Last agent final summary — used as chat context |
| `_last_run` | `str` | Timestamp of the most recent agent run |
| `_jobs` | `dict` | In-memory job store: `job_id → {status, result}` |

---

### `app/Agent_setup.py` — LangGraph ReAct Agent

Defines the main autonomous agent and all workflow tools. The agent is created per-request via `build_agent(session)` with all tool closures capturing the session's Canvas credentials and user email.

Key globals:

| Variable | Type | Purpose |
|---|---|---|
| `_step_progress` | `list[dict]` | `[{name, status}]` — mutated in-place so the UI reference stays valid |
| `_courses_data` | `list[dict]` | Courses discovered this run: `[{course_id, course_name}]` |
| `added_events` | `list[dict]` | Events recorded to the session calendar: `[{title, due_date}]` |
| `_course_content_cache` | `dict[str, str]` | `course_id → full_text` — populated by `generate_study_plan`, consumed by chat RAG |

**Progress tracking** — `ProgressTracker(BaseCallbackHandler)` is attached to the agent as a LangChain callback. It listens for `on_tool_start` / `on_tool_end` events and updates `_step_progress` entries in-place:

| Step index | Step name | Triggered by tool |
|---|---|---|
| 0 | Discovering courses | `list_canvas_courses` |
| 1 | Extracting assignments | `extract_assignments_from_canvas`, `get_canvas_calendar_events` |
| 2 | Adding to calendar | `create_calendar_event` |
| 3 | Filtering deadlines | `extract_weekly_deadlines` |
| 4 | Sending email | `send_weekly_calendar_bulletin` |
| 5 | Generating study plans | `generate_study_plan` |

---

### `app/canvas.py` — Canvas LMS Integration

All Canvas API communication and the Canvas-facing agent tools.

Key internal helpers:

| Function | Purpose |
|---|---|
| `_canvas_utc_to_local_date(dt_string)` | Converts a Canvas UTC ISO string to a local `YYYY-MM-DD` string |
| `_get_paginated(url)` | Follows Canvas `Link: rel="next"` pagination and returns all items |
| `_html_to_text(html)` | Strips HTML tags to clean UTF-8 text using BeautifulSoup |
| `_get_syllabus_text(course_id)` | Fetches only the syllabus body for a course |
| `_get_modules_text(course_id)` | Iterates all modules and items (pages, assignments, quizzes, files) and returns their text |
| `_get_course_text(course_id)` | Combines syllabus + module text into one string ready for RAG |
| `_fetch_canvas_calendar_events(course_id)` | Dual-source fetch: Assignments API (primary) + Calendar Events API (supplement), deduplicated by name |

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

### `app/email_alerts.py` — Email Integration

`gmail_send_message(email_body, recipient, subject, ics_data)` — sends a plain-text email with an optional `.ics` attachment. Backend priority: **SendGrid → Resend → SMTP** (whichever credentials are configured).

| Backend | Env vars required | Notes |
|---|---|---|
| SendGrid | `SENDGRID_API_KEY` | Primary; recommended for HF Spaces |
| Resend | `RESEND_API_KEY` | Fallback; no domain ownership needed |
| SMTP | `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` | Local dev only; blocked on HF Spaces |

`generate_ics(events)` — generates RFC 5545 iCalendar data from a list of `{title, due_date}` dicts.

---

### `app/chat_agent.py` — Chat Agent

Multi-turn conversational assistant with tool-calling support. Accepts the session's `user_email` so email operations use the correct recipient.

**Session state (`ChatSession`):**

| Field | Type | Purpose |
|---|---|---|
| `history` | `list[BaseMessage]` | Conversation history, capped at 40 messages |
| `quiz` | `Optional[QuizState]` | Active quiz state (questions, current index, score) |
| `email_draft` | `Optional[dict]` | Staged email `{to, subject, body}` awaiting confirmation |

**Shortcuts (bypass LLM entirely):**
- **Quiz answer** — If a quiz is active and the message matches `[A/B/C/D]`, the answer is processed directly without an LLM call.
- **Email confirmation** — If an email draft is staged and the message contains "send", "confirm", "yes", or "go ahead", the email is sent directly.

**LLM instance:** A single module-level `_llm = ChatGoogleGenerativeAI(model=_GEMINI_MODEL)` is reused across all chat turns. Tool binding (`_llm.bind_tools(tools)`) is done per-call since tools capture per-session credentials.

---

## 3. Agent Workflow

The full workflow is triggered by a `POST /chat` request. The LangGraph ReAct agent executes the following steps **in strict order**, one course at a time:

```
Step 1  list_canvas_courses
        └── Returns [{course_id, course_name}, ...]

Step 2  For each course:
        ├── extract_assignments_from_canvas  (RAG-based extraction)
        └── get_canvas_calendar_events       (direct Canvas API)
        Merge both lists, deduplicate by assignment_name,
        prefer due_date from get_canvas_calendar_events when both present.

Step 3  create_calendar_event  (× each unique assignment with a non-null due_date)
        └── Records the event in the in-memory session calendar

Step 4  extract_weekly_deadlines
        └── Filters session calendar to assignments due within next 7 days

Step 5  send_weekly_calendar_bulletin
        └── Sends email with upcoming deadlines + .ics attachment for all events

Step 6  generate_study_plan  (× each course)
        ├── Caches full course text into _course_content_cache
        ├── Builds study plan RAG chain
        └── Returns ## Course Summary + ## Study Plan

Step 7  Final summary (LLM-generated, self-contained)
        ├── Number of courses processed
        ├── Number of newly recorded calendar events
        ├── Assignments due in the next 7 days
        ├── Confirmation that bulletin email was sent with .ics
        └── Full verbatim study plan text for every course
```

---

## 4. Agent Tools Reference

### `list_canvas_courses`

Returns all Canvas courses for the configured token. If `CANVAS_COURSE_IDS` is set in the session, only those course IDs are returned.

**Returns:** `[{"course_id": "12345", "course_name": "..."}]`

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

Records an assignment in the in-memory session calendar (`added_events`). Deduplicates by title (last write wins).

**Returns:** `"Recorded: '<title>' on <date>."`

---

### `extract_weekly_deadlines`

Filters `added_events` to those due within the next 7 days.

---

### `send_weekly_calendar_bulletin`

Sends an email to the user listing upcoming deadlines, with an `.ics` file attached containing all session assignments.

---

### `generate_study_plan`

Builds the study plan RAG chain and returns a `## Course Summary` + `## Study Plan` for the course. Also caches course text into `_course_content_cache` so the chat agent can answer course-specific questions.

---

## 5. Chat Agent

The chat agent (`app/chat_agent.py`) operates after the main agent workflow has run. It uses the agent's final summary as background context and the course content RAG for grounded answers.

### Tools Available to the Chat Agent

- **`search_course_content`** — semantic search over cached course materials.
- **`update_assignment_due_date`** — reschedules an event in the session calendar.
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
| `POST` | `/login` | No | Validates Canvas token, sets signed session cookie, returns `{status, token}` |
| `GET` | `/logout` | No | Clears session cookie, redirects to `/landing.html` |
| `GET` | `/config` | Yes | Returns `{"email": "user@example.com"}` |
| `GET` | `/health` | No | Returns `{"status": "ok"}` |
| `POST` | `/chat` | Yes | Starts the agent workflow. Returns `{job_id}`. |
| `GET` | `/result/{job_id}` | Yes | Returns `{status, result, progress}`. |
| `GET` | `/status` | Yes | Returns `{last_run, courses, deadlines, progress, summary}`. |
| `GET` | `/calendar-events` | Yes | Returns `{events: [{title, due_date}]}` from the session calendar. |
| `POST` | `/ask` | Yes | Runs one chat turn. Returns `{response}`. |
| `GET` | `/download/courses` | Yes | Downloads all cached course content as a `.zip`. |
| `POST` | `/reset` | Yes | Clears all server-side state and chat sessions. |
| `GET` | `/` | — | Serves `static/index.html`. |

**Auth:** Every protected endpoint reads the `taskbuddy_session` cookie or `Authorization: Bearer <token>` header and returns `401` if absent or invalid.

---

## 8. Web UI

### Pages

- **`/landing.html`** — sign-in form (email, Canvas token, Canvas base URL, optional course IDs). Shown to unauthenticated users.
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

- **Auth guard** — On load, `fetch('/config')` returns the user's email (200) or 401. On 401, the page redirects to `/landing.html`.
- **Agent run** — Clicks "Run Agent" → `POST /chat` → polls `GET /result/{job_id}` every 1.5s until done.
- **Step tracker** — Shows one active step at a time with a pulsing dot.
- **Calendar panel** — Loads events from `/calendar-events` on load and after each agent run.
- **Reset** — Calls `POST /reset`, clears all UI panels, wipes chat history, generates a new session ID.

---

## 9. Configuration & Setup

### Environment Variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_API_KEY` | Yes | Gemini API key (LLM and embeddings) |
| `SESSION_SECRET` | Yes | Secret for signing session cookies (use a long random string in production) |
| `SENDGRID_API_KEY` | One email backend required | SendGrid API key (recommended) |
| `SENDGRID_FROM_EMAIL` | No | Sender address for SendGrid (default: `taskbuddy0001@gmail.com`) |
| `SENDGRID_FROM_NAME` | No | Sender display name (default: `TaskBuddy`) |
| `RESEND_API_KEY` | One email backend required | Resend API key (alternative to SendGrid) |
| `RESEND_FROM` | No | Sender for Resend (default: `TaskBuddy <onboarding@resend.dev>`) |
| `GMAIL_ADDRESS` | One email backend required | Gmail address for SMTP (local dev only) |
| `GMAIL_APP_PASSWORD` | One email backend required | Gmail app password for SMTP (local dev only) |
| `BASE_URL` | Yes | Public base URL, e.g. `http://127.0.0.1:8000` |

> Email backends are tried in priority order: SendGrid → Resend → SMTP. Set at least one.

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

Open **http://127.0.0.1:8000** and sign in with your Canvas credentials. No Google account is required.

---

## 10. Tech Stack & Dependencies

| Category | Library / Service | Role |
|---|---|---|
| LLM | Gemini 2.5 Flash | Agent reasoning, study plans, chat Q&A, quiz generation |
| Embeddings | Gemini Embedding 001 | Vectorising course content for RAG |
| Agent framework | LangGraph (`langgraph`) | ReAct agent loop (`create_react_agent`) |
| LLM abstraction | LangChain Core | Tools, messages, callbacks |
| LLM provider | `langchain-google-genai` | `ChatGoogleGenerativeAI`, `GoogleGenerativeAIEmbeddings` |
| Vector store | ChromaDB (`chromadb`) | In-memory vector storage |
| Text splitting | `langchain-text-splitters` | `RecursiveCharacterTextSplitter` |
| Community tools | `langchain-community` | `Chroma` vectorstore wrapper |
| API server | FastAPI + Uvicorn | HTTP endpoints and background tasks |
| HTML parsing | BeautifulSoup4 | Stripping HTML from Canvas pages |
| HTTP client | Requests | Canvas API calls, SendGrid/Resend calls |
| Email | SendGrid / Resend / smtplib | Weekly digest + `.ics` attachment |
| Config | `python-dotenv` | `.env` file loading |
| Markdown | `marked.js` (CDN) | Browser-side Markdown rendering |

---

## 11. Deployment

### Hugging Face Spaces (Docker)

TaskBuddy runs on Hugging Face Spaces as a Docker container exposing port 7860.

**Secrets** (set in the Space Settings → Secrets tab):

| Secret name | Content |
|---|---|
| `GOOGLE_API_KEY` | Gemini API key |
| `SESSION_SECRET` | Long random string for cookie signing |
| `SENDGRID_API_KEY` | SendGrid API key (recommended email backend) |
| `SENDGRID_FROM_EMAIL` | Verified sender address |
| `BASE_URL` | Your Space URL, e.g. `https://moeid25-taskbuddy.hf.space` |

Optional secrets: `RESEND_API_KEY`, `RESEND_FROM`, `SENDGRID_FROM_NAME`.

**Deploy:**
```bash
git remote add space https://<username>:<HF_TOKEN>@huggingface.co/spaces/<username>/TaskBuddy
git push space deploy:main
```

> **Single instance:** The in-memory job store, session calendar, and course cache are process-local. Do not scale beyond one container replica or run with `--workers > 1`.
