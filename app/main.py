import asyncio
import io
import logging
import os
import uuid
import zipfile
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request

logger = logging.getLogger("taskbuddy")
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from pydantic import BaseModel
from dotenv import load_dotenv

from app.Agent_setup import build_agent, reset_progress, ProgressTracker, _step_progress, _courses_data, added_events, _course_content_cache
from app.chat_agent import run_chat_turn, _sessions
import app.auth as auth

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")

_agent_context: str = ""
_last_run: str = ""
_jobs: dict[str, dict] = {}

app = FastAPI(
    title="TaskBuddy Agent API",
    description="An AI-powered academic support agent",
    version="2.0.0",
)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _get_session(request: Request) -> dict | None:
    auth_header = request.headers.get("Authorization", "")
    cookie_value = request.cookies.get("taskbuddy_session")
    logger.info(
        "auth: bearer=%s cookie=%s path=%s",
        "yes" if auth_header.startswith("Bearer ") else "no",
        "yes" if cookie_value else "no",
        request.url.path,
    )
    if auth_header.startswith("Bearer "):
        result = auth.get_session(auth_header[7:])
        logger.info("bearer decode: %s", "ok" if result else "fail")
        return result
    result = auth.get_session(cookie_value)
    logger.info("cookie decode: %s", "ok" if result else "fail")
    return result


def _require_session(request: Request) -> dict:
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


# ── Auth endpoints ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    canvas_token: str
    canvas_base_url: str
    canvas_course_ids: str = ""


@app.post("/login")
async def login(request: Request, body: LoginRequest):
    valid = await asyncio.to_thread(
        auth.validate_canvas_token, body.canvas_base_url, body.canvas_token
    )
    if not valid:
        raise HTTPException(
            status_code=400,
            detail="Invalid Canvas credentials. Check your Base URL and API token.",
        )
    session_id = auth.create_session(
        email=body.email,
        canvas_token=body.canvas_token,
        canvas_base_url=body.canvas_base_url,
        canvas_course_ids=body.canvas_course_ids,
    )
    response = JSONResponse({"status": "ok", "token": session_id})
    is_https = BASE_URL.startswith("https")
    response.set_cookie(
        "taskbuddy_session",
        session_id,
        httponly=True,
        samesite="none" if is_https else "lax",
        secure=is_https,
        max_age=86400,  # 24 hours
    )
    return response


@app.get("/logout")
async def logout(request: Request):
    session_id = request.cookies.get("taskbuddy_session")
    if session_id:
        auth.delete_session(session_id)
    response = RedirectResponse(url="/landing.html")
    response.delete_cookie("taskbuddy_session")
    return response


# ── Config endpoint ───────────────────────────────────────────────────────────

@app.get("/config")
async def get_config(request: Request):
    session = _require_session(request)
    return {"email": session["email"]}


# ── Agent endpoints ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    response: str


class JobResponse(BaseModel):
    job_id: str


class JobStatus(BaseModel):
    status: str
    result: str | None = None
    progress: list | None = None


@app.get("/health")
async def health_check():
    return {"status": "ok"}


async def _run_agent(job_id: str, message: str, session: dict):
    global _agent_context, _last_run
    reset_progress()
    tracker = ProgressTracker()
    try:
        agent = build_agent(session)
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={"callbacks": [tracker], "recursion_limit": 100},
        )
        raw = result["messages"][-1].content
        if isinstance(raw, list):
            _agent_context = " ".join(
                part["text"] for part in raw if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            _agent_context = raw
        _last_run = datetime.now().strftime("%Y-%m-%d %H:%M")
        _jobs[job_id] = {"status": "done", "result": _agent_context}
    except Exception as e:
        _jobs[job_id] = {"status": "error", "result": str(e)}


@app.post("/chat", response_model=JobResponse)
async def chat(request: Request, body: ChatRequest, background_tasks: BackgroundTasks):
    session = _require_session(request)
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None}
    background_tasks.add_task(_run_agent, job_id, body.message, session)
    return JobResponse(job_id=job_id)


@app.get("/result/{job_id}", response_model=JobStatus)
async def get_result(request: Request, job_id: str):
    _require_session(request)
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(status=job["status"], result=job["result"], progress=list(_step_progress))


@app.get("/status")
async def get_status(request: Request):
    _require_session(request)
    return {
        "last_run": _last_run,
        "courses": list(_courses_data),
        "deadlines": list(added_events),
        "progress": list(_step_progress),
        "summary": _agent_context,
    }


@app.get("/calendar-events")
async def calendar_events(request: Request):
    _require_session(request)
    return {"events": [{"title": e["title"], "due_date": e["due_date"]} for e in added_events]}


@app.post("/ask", response_model=ChatResponse)
async def ask(request: Request, body: ChatRequest):
    session = _require_session(request)
    try:
        response = await run_chat_turn(
            session_id=body.session_id or "default",
            message=body.message,
            agent_context=_agent_context,
            user_email=session["email"],
        )
        return ChatResponse(response=response)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download/courses")
async def download_courses(request: Request):
    _require_session(request)
    if not _course_content_cache:
        raise HTTPException(status_code=404, detail="No course content available. Run the agent first.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for course_id, text in _course_content_cache.items():
            name = next(
                (c["course_name"] for c in _courses_data if c["course_id"] == course_id),
                course_id,
            )
            safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()
            zf.writestr(f"{safe}.txt", text)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=course_content.zip"},
    )


@app.post("/reset")
async def reset(request: Request):
    global _agent_context, _last_run
    _require_session(request)
    reset_progress()
    _jobs.clear()
    _sessions.clear()
    _agent_context = ""
    _last_run = ""
    return {"status": "reset"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
