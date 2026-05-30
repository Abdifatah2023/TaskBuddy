import os
import uuid
import asyncio
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv

from app.Agent_setup import build_agent, reset_progress, ProgressTracker, _step_progress, _courses_data, added_events
from app.Google_calendar import get_taskbuddy_events
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
    version="1.0.0",
)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _redirect_uri(request: Request) -> str:
    return f"{BASE_URL}/auth/callback"


def _get_session(request: Request) -> dict | None:
    session_id = request.cookies.get("taskbuddy_session")
    return auth.get_session(session_id)


def _require_session(request: Request) -> dict:
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.get("/auth/login")
async def login(request: Request):
    url = auth.build_auth_url(_redirect_uri(request))
    return RedirectResponse(url=url)


@app.get("/auth/callback")
async def callback(request: Request, code: str):
    try:
        credentials = auth.exchange_code(code, _redirect_uri(request))
        email = await asyncio.to_thread(auth.get_user_email, credentials)
        session_id = auth.create_session(credentials, email)
        response = RedirectResponse(url="/")
        response.set_cookie(
            "taskbuddy_session",
            session_id,
            httponly=True,
            samesite="lax",
            secure=BASE_URL.startswith("https"),
        )
        return response
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth error: {e}")


@app.get("/auth/logout")
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


async def _run_agent(job_id: str, message: str, credentials, user_email: str):
    global _agent_context, _last_run
    reset_progress()
    tracker = ProgressTracker()
    try:
        agent = build_agent(credentials, user_email)
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={"callbacks": [tracker], "recursion_limit": 100},
        )
        _agent_context = result["messages"][-1].content
        _last_run = datetime.now().strftime("%Y-%m-%d %H:%M")
        _jobs[job_id] = {"status": "done", "result": _agent_context}
    except Exception as e:
        _jobs[job_id] = {"status": "error", "result": str(e)}


@app.post("/chat", response_model=JobResponse)
async def chat(request: Request, body: ChatRequest, background_tasks: BackgroundTasks):
    session = _require_session(request)
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None}
    background_tasks.add_task(
        _run_agent, job_id, body.message, session["credentials"], session["email"]
    )
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
    session = _require_session(request)
    events = await asyncio.to_thread(get_taskbuddy_events, session["credentials"])
    return {"events": events}


@app.post("/ask", response_model=ChatResponse)
async def ask(request: Request, body: ChatRequest):
    session = _require_session(request)
    try:
        response = await run_chat_turn(
            session_id=body.session_id or "default",
            message=body.message,
            agent_context=_agent_context,
            credentials=session["credentials"],
            user_email=session["email"],
        )
        return ChatResponse(response=response)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
