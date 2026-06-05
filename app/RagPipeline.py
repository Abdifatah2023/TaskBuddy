import os
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_community.vectorstores import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv

load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
_GEMINI_MODEL = "gemini-2.5-flash"


def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


text_splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=500)
embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")

# ── Assignment extraction chain ──────────────────────────────────────────────

_assignment_template = """You are an academic assistant.
Extract every assignment, project, or exam mentioned in the course material.

Return output STRICTLY in JSON format like this:

[
  {{
    "assignment_name": "Homework 1",
    "due_date": "2026-03-10"
  }}
]

If a due date is missing, set "due_date" to null.
Return ONLY the JSON array — no explanation, no markdown fences.

Context:
{context}

Question:
{question}
"""

_assignment_prompt = ChatPromptTemplate.from_template(_assignment_template)

rag_chain = None


def build_rag_chain(text: str):
    """Build the assignment-extraction RAG chain from raw text."""
    global rag_chain
    chunks = text_splitter.split_text(text)
    if not chunks:
        return
    vectorstore = Chroma.from_texts(
        texts=chunks,
        embedding=embeddings,
        collection_name="academic_docs",
        client=chromadb.EphemeralClient(),
    )
    rag_chain = (
        {
            "context": vectorstore.as_retriever(search_kwargs={"k": 5}) | format_docs,
            "question": RunnablePassthrough(),
        }
        | _assignment_prompt
        | ChatGoogleGenerativeAI(model=_GEMINI_MODEL)
        | StrOutputParser()
    )


# ── Study plan chain ─────────────────────────────────────────────────────────

_study_plan_template = """You are an expert academic tutor and study coach.
Based on the course content provided, generate:

1. A concise summary of the key topics covered in this course.
2. A detailed weekly study plan that helps a student master the material.

Format your response with these sections:

## Course Summary
(3-5 bullet points covering the main topics)

## Study Plan
(Week-by-week breakdown with specific activities, readings, and practice exercises)

Context:
{context}

Question:
{question}
"""

_study_plan_prompt = ChatPromptTemplate.from_template(_study_plan_template)

study_plan_rag_chain = None


def build_study_plan_chain(text: str):
    """Build the study-plan / summary RAG chain from raw course content."""
    global study_plan_rag_chain
    chunks = text_splitter.split_text(text)
    if not chunks:
        return
    study_vs = Chroma.from_texts(
        texts=chunks,
        embedding=embeddings,
        collection_name="course_content",
        client=chromadb.EphemeralClient(),
    )
    study_plan_rag_chain = (
        {
            "context": study_vs.as_retriever(search_kwargs={"k": 8}) | format_docs,
            "question": RunnablePassthrough(),
        }
        | _study_plan_prompt
        | ChatGoogleGenerativeAI(model=_GEMINI_MODEL)
        | StrOutputParser()
    )


# ── Chat Q&A chain ────────────────────────────────────────────────────────────

_chat_qa_template = """You are TaskBuddy, a helpful academic assistant.
Answer the student's question using the course materials provided.
Be specific, accurate, and cite relevant details from the content.

Context:
{context}

Question:
{question}
"""

_chat_qa_prompt = ChatPromptTemplate.from_template(_chat_qa_template)
chat_rag_chain = None


def build_chat_rag_chain(text: str):
    """Build the chat Q&A RAG chain from all cached course content."""
    global chat_rag_chain
    chunks = text_splitter.split_text(text)
    if not chunks:
        return
    chat_vs = Chroma.from_texts(
        texts=chunks,
        embedding=embeddings,
        collection_name="chat_qa",
        client=chromadb.EphemeralClient(),
    )
    chat_rag_chain = (
        {
            "context": chat_vs.as_retriever(search_kwargs={"k": 6}) | format_docs,
            "question": RunnablePassthrough(),
        }
        | _chat_qa_prompt
        | ChatGoogleGenerativeAI(model=_GEMINI_MODEL)
        | StrOutputParser()
    )
