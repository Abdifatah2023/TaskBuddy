import os

from fastapi import FastAPI, HTTPException

from pydantic import BaseModel

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from dotenv import load_dotenv

from fastapi.staticfiles import StaticFiles

from app.Agent_setup import agent

_llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
_agent_context: str = ""


load_dotenv()


app = FastAPI(

    title="TaskBuddy Agent API",

    description="An AI-powered academic support agent",

    version="1.0.0"

)


class ChatRequest(BaseModel):

    message: str


class ChatResponse(BaseModel):

    response: str


@app.get("/health")

async def health_check():

    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)

async def chat(request: ChatRequest):

    global _agent_context

    try:

        result = agent.invoke({

            "messages": [HumanMessage(content=request.message)]

        })

        _agent_context = result["messages"][-1].content

        return ChatResponse(response=_agent_context)

    except Exception as e:

        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ask", response_model=ChatResponse)

async def ask(request: ChatRequest):

    try:

        context_block = (
            f"\n\nHere is the course content, assignments, deadlines, and study plans "
            f"already retrieved from Canvas:\n\n{_agent_context}"
            if _agent_context else
            "\n\nThe agent has not been run yet. Let the user know they should click 'Run Agent' first."
        )

        messages = [
            SystemMessage(content=(
                "You are TaskBuddy, a helpful academic assistant. "
                "Answer the user's follow-up questions about their courses, assignments, deadlines, and study plans. "
                "Use only the course context provided — do not run any tools or workflows."
                + context_block
            )),
            HumanMessage(content=request.message),
        ]

        result = await _llm.ainvoke(messages)

        return ChatResponse(response=result.content)

    except Exception as e:

        raise HTTPException(status_code=500, detail=str(e))


app.mount("/ui", StaticFiles(directory="static", html=True), name="static")