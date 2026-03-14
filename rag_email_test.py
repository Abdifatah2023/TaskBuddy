#this is a test file, can be referenced when working on agent_setup to integrate

import os
import json
from dotenv import load_dotenv

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_community.vectorstores import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.tools import tool
from langchain.agents import create_agent

# Reuse your existing email tool functions
from email_alerts import authenticate, get_weekly_events, format_event, gmail_send_message


load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Point to your syllabus PDF
SYLLABUS_PATH = r"Syllabi\Syllabus.pdf"


def build_rag_chain():
    loader = PyPDFLoader(SYLLABUS_PATH)
    pages = loader.load()
    full_text = "\n\n".join(page.page_content for page in pages)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1500,
        chunk_overlap=300,
    )
    chunks = splitter.split_text(full_text)

    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001"
    )

    vectorstore = Chroma.from_texts(
        texts=chunks,
        embedding=embeddings,
        collection_name="taskbuddy_rag_email_test"
    )

    prompt_template = """
You are an academic assistant.
Extract only assignments, quizzes, exams, and projects due in the next 7 days.

Return STRICT JSON only in this format:
[
  {{
    "assignment_name": "Homework 1",
    "due_date": "YYYY-MM-DD"
  }}
]

Rules:
- If no due date is found, set due_date to null
- Do not include markdown or extra text
- If no matching items, return []

Context:
{context}

Question:
{question}
"""

    prompt = ChatPromptTemplate.from_template(prompt_template)

    def format_docs(docs):
        return "\n\n".join(doc.page_content for doc in docs)

    chain = (
        {
            "context": vectorstore.as_retriever(search_kwargs={"k": 5}) | format_docs,
            "question": RunnablePassthrough(),
        }
        | prompt
        | ChatGoogleGenerativeAI(model="gemini-2.0-flash")
        | StrOutputParser()
    )
    return chain


rag_chain = build_rag_chain()


@tool
def extract_weekly_deadlines(query: str) -> str:
    """
    Extract upcoming deadlines (next 7 days) from syllabus using RAG.
    """
    return rag_chain.invoke(query)


@tool
def send_weekly_calendar_bulletin(_: str = "send") -> str:
    """
    Build and send a weekly bulletin from Google Calendar events using email_alerts.py.
    """
    try:
        creds = authenticate()
        if not creds:
            return "Failed: could not authenticate."

        events = get_weekly_events(creds)
        if events is None:
            return "Failed: could not fetch calendar events."

        email_body = format_event(events)
        result = gmail_send_message(creds, email_body)

        if result is None:
            return "Failed: Gmail send returned no result."
        return "Success: weekly bulletin email sent."
    except Exception as e:
        return f"Failed with error: {str(e)}"


agent_llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")

system_prompt = """
You are TaskBuddy test agent for weekly bulletin.

You have 2 tools:
1) extract_weekly_deadlines
2) send_weekly_calendar_bulletin

Workflow:
- First call extract_weekly_deadlines with the user question.
- Then call send_weekly_calendar_bulletin.
- Finally summarize:
  - extracted items (from tool output)
  - whether email was sent successfully.
"""

agent = create_agent(
    model=agent_llm,
    tools=[extract_weekly_deadlines, send_weekly_calendar_bulletin],
    system_prompt=system_prompt
)


if __name__ == "__main__":
    response = agent.invoke({
        "messages": [
            {
                "role": "human",
                "content": "Extract this week's deadlines from the syllabus and send the weekly calendar bulletin email."
            }
        ]
    })

    print("\nAgent response:\n")
    print(response["messages"][-1].content)