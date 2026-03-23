import os

from fastapi import FastAPI, HTTPException

from pydantic import BaseModel

from langchain_core.messages import HumanMessage

from dotenv import load_dotenv

from app.Agent_setup import agent


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

    try:

        result = agent.invoke({

            "messages": [HumanMessage(content=request.message)]

        })

        return ChatResponse(response=result["messages"][-1].content)

    except Exception as e:

        raise HTTPException(status_code=500, detail=str(e))