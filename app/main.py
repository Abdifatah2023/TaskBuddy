import os
import uuid
import asyncio
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles

from app.Agent_setup import agent, reset_progress, ProgressTracker, _step_progress, _courses_data, added_events
from app.Google_calendar import get_taskbuddy_events
from app.chat_agent import run_chat_turn, _sessions

load_dotenv()

_agent_context: str = ""
_last_run: str = ""

# job_id -> {"status": "running"|"done"|"error", "result": str}
_jobs: dict[str, dict] = {}

app = FastAPI(
    title="TaskBuddy Agent API",
    description="An AI-powered academic support agent",
    version="1.0.0"
)


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


async def _run_agent(job_id: str, message: str):
    global _agent_context, _last_run
    reset_progress()
    tracker = ProgressTracker()
    try:
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
async def chat(request: ChatRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None}
    background_tasks.add_task(_run_agent, job_id, request.message)
    return JobResponse(job_id=job_id)


@app.get("/result/{job_id}", response_model=JobStatus)
async def get_result(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(status=job["status"], result=job["result"], progress=list(_step_progress))


@app.get("/status")
async def get_status():
    return {
        "last_run": _last_run,
        "courses": list(_courses_data),
        "deadlines": list(added_events),
        "progress": list(_step_progress),
        "summary": _agent_context,
    }


@app.get("/calendar-events")
async def calendar_events():
    events = await asyncio.to_thread(get_taskbuddy_events)
    return {"events": events}


@app.post("/reset")
async def reset():
    global _agent_context, _last_run
    reset_progress()
    _jobs.clear()
    _sessions.clear()
    _agent_context = ""
    _last_run = ""
    return {"status": "reset"}


@app.post("/ask", response_model=ChatResponse)
async def ask(request: ChatRequest):
    try:
        response = await run_chat_turn(
            session_id=request.session_id or "default",
            message=request.message,
            agent_context=_agent_context,
        )
        return ChatResponse(response=response)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.mount("/", StaticFiles(directory="static", html=True), name="static")
