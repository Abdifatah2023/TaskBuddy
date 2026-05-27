"""
TaskBuddy Chat Agent
====================
Multi-turn chat with tool support:
  - search_course_content       : RAG search over cached course materials
  - update_assignment_due_date  : Update a Google Calendar event
  - draft_email                 : Stage an email for user review
  - send_email                  : Send the staged draft
  - generate_quiz               : Generate an interactive quiz
Quiz answers (A/B/C/D) and email "send" confirmations are handled as
lightweight shortcuts that bypass the LLM call entirely.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI

log = logging.getLogger(__name__)

# ── Session state ──────────────────────────────────────────────────────────────

@dataclass
class QuizState:
    questions: list[dict]
    current: int = 0
    score: int = 0
    answers: list[str] = field(default_factory=list)
    topic: str = ""
    course_name: str = ""


@dataclass
class ChatSession:
    history: list[BaseMessage] = field(default_factory=list)
    quiz: Optional[QuizState] = None
    email_draft: Optional[dict] = None   # {to, subject, body}


_sessions: dict[str, ChatSession] = {}
_HISTORY_LIMIT = 40


def get_session(session_id: str) -> ChatSession:
    if session_id not in _sessions:
        _sessions[session_id] = ChatSession()
    return _sessions[session_id]


# ── Chat RAG cache ─────────────────────────────────────────────────────────────
_chat_rag_chain = None
_chat_rag_cache_keys: frozenset = frozenset()


def _ensure_chat_rag() -> bool:
    """Rebuild the chat RAG chain if new course content is available."""
    global _chat_rag_chain, _chat_rag_cache_keys
    try:
        from app.Agent_setup import _course_content_cache
        current_keys = frozenset(_course_content_cache.keys())
        if current_keys == _chat_rag_cache_keys and _chat_rag_chain is not None:
            return True
        if not current_keys:
            return False
        all_text = "\n\n=== NEXT COURSE ===\n\n".join(_course_content_cache.values())
        if not all_text.strip():
            return False
        import app.RagPipeline as rp
        rp.build_chat_rag_chain(all_text)
        _chat_rag_chain = rp.chat_rag_chain
        _chat_rag_cache_keys = current_keys
        log.info("Chat RAG built for %d course(s): %s", len(current_keys), list(current_keys))
        return _chat_rag_chain is not None
    except Exception as e:
        log.error("Chat RAG build failed: %s", e)
        return False


# ── Internal tool implementations ─────────────────────────────────────────────

def _search_content(query: str) -> str:
    if not _ensure_chat_rag():
        return (
            "Course content is not loaded yet. "
            "Please run the agent first so I can access your course materials."
        )
    try:
        return _chat_rag_chain.invoke(query)
    except Exception as e:
        log.error("RAG search error: %s", e)
        return f"Search error: {e}"


def _update_assignment(assignment_name: str, new_due_date: str) -> str:
    from app.Google_calendar import GoogleCalendarTool
    from app.Agent_setup import added_events
    result = GoogleCalendarTool(title=assignment_name, start_time=new_due_date, end_time=new_due_date)
    # Keep in-session event list consistent
    added_events[:] = [e for e in added_events if e["title"] != assignment_name]
    if result.startswith(("Event created", "Event updated")):
        added_events.append({"title": assignment_name, "due_date": new_due_date})
    log.info("update_assignment: '%s' → %s | %s", assignment_name, new_due_date, result[:60])
    return result


def _store_draft(session: ChatSession, to: str, subject: str, body: str) -> str:
    session.email_draft = {"to": to, "subject": subject, "body": body}
    return (
        f"📧 **Email Draft Ready for Review**\n\n"
        f"**To:** {to}  \n"
        f"**Subject:** {subject}\n\n"
        f"---\n\n{body}\n\n---\n\n"
        f"Reply **'send email'** to send it, or tell me what to change."
    )


def _send_draft(session: ChatSession) -> str:
    if not session.email_draft:
        return "No email draft found. Please draft an email first."
    draft = session.email_draft
    try:
        import base64
        from email.message import EmailMessage
        from googleapiclient.discovery import build
        from app.email_alerts import authenticate

        creds = authenticate()
        service = build("gmail", "v1", credentials=creds)
        msg = EmailMessage()
        msg.set_content(draft["body"])
        msg["To"]      = draft["to"]
        msg["From"]    = "me"
        msg["Subject"] = draft["subject"]
        encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": encoded}).execute()
        session.email_draft = None
        log.info("Email sent to %s | Subject: %s", draft["to"], draft["subject"])
        return f"✅ Email sent successfully to **{draft['to']}**."
    except Exception as e:
        log.error("Email send failed: %s", e)
        return f"Failed to send email: {e}"


def _generate_quiz(
    session: ChatSession,
    course_name: str,
    topic: str,
    num_questions: int,
    agent_context: str,
) -> str:
    # Use RAG-retrieved content for the question prompt; fall back to agent context
    context = ""
    if _ensure_chat_rag():
        try:
            context = _chat_rag_chain.invoke(f"Key concepts about {topic} in {course_name}")
        except Exception:
            pass
    if not context and agent_context:
        context = agent_context[:3000]

    llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
    prompt = (
        f'Generate a {num_questions}-question multiple-choice quiz on "{topic}" '
        f'for the course "{course_name}".\n\n'
        f"Course context:\n{context}\n\n"
        "Return ONLY a valid JSON array (no markdown, no code fences):\n"
        '[\n  {\n    "question": "...",\n'
        '    "options": {"A": "...", "B": "...", "C": "...", "D": "..."},\n'
        '    "correct": "A",\n'
        '    "explanation": "Why A is correct"\n'
        "  }\n]"
    )
    try:
        raw = llm.invoke([
            SystemMessage(content="You are a quiz generator. Respond ONLY with a valid JSON array."),
            HumanMessage(content=prompt),
        ]).content.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        questions = json.loads(raw)
        if not isinstance(questions, list) or not questions:
            raise ValueError("Empty or invalid question list")
        session.quiz = QuizState(questions=questions, topic=topic, course_name=course_name)
        log.info("Quiz generated: %d Qs on '%s' / '%s'", len(questions), topic, course_name)
        return _format_question(session.quiz)
    except Exception as e:
        log.error("Quiz generation failed: %s", e)
        return f"Sorry, I couldn't generate the quiz. {e}"


def _format_question(quiz: QuizState) -> str:
    if quiz.current >= len(quiz.questions):
        total = len(quiz.questions)
        pct   = int(quiz.score / total * 100) if total else 0
        review = "\n\n".join(
            f"**Q{i+1}**: {q['question']}  \n"
            f"Your answer: **{quiz.answers[i] if i < len(quiz.answers) else '—'}** | "
            f"Correct: **{q['correct']}** — _{q.get('explanation', '')}_"
            for i, q in enumerate(quiz.questions)
        )
        return (
            f"## Quiz Complete! 🎉\n\n"
            f"**Score: {quiz.score} / {total} ({pct}%)**\n\n"
            f"### Review\n\n{review}\n\n"
            f"_Start a new quiz anytime by asking!_"
        )
    q = quiz.questions[quiz.current]
    num, total = quiz.current + 1, len(quiz.questions)
    opts = "\n".join(f"**{k}.** {v}" for k, v in q["options"].items())
    return (
        f"### Question {num} / {total}\n\n"
        f"{q['question']}\n\n{opts}\n\n"
        f"_Reply with **A**, **B**, **C**, or **D**._"
    )


def _process_answer(session: ChatSession, answer: str) -> str:
    quiz = session.quiz
    q    = quiz.questions[quiz.current]
    correct    = q["correct"].upper()
    is_correct = answer == correct
    if is_correct:
        quiz.score += 1
    quiz.answers.append(answer)
    quiz.current += 1

    feedback = (
        f"✅ **Correct!** — _{q.get('explanation', '')}_"
        if is_correct
        else f"❌ **Incorrect.** Correct answer: **{correct}** — _{q.get('explanation', '')}_"
    )
    score_line = f"\n\nScore so far: **{quiz.score}/{quiz.current}**\n\n---\n\n"
    return feedback + score_line + _format_question(quiz)


# ── Tool factory ───────────────────────────────────────────────────────────────

def _make_tools(session_id: str, agent_context: str) -> list:
    """Return LangChain tools with session state captured via closures."""
    session = get_session(session_id)

    @tool
    def search_course_content(query: str) -> str:
        """Search course materials, syllabi, assignments, and study plans for relevant information. Use this any time the user asks about course content, topics, or deadlines."""
        return _search_content(query)

    @tool
    def update_assignment_due_date(assignment_name: str, new_due_date: str) -> str:
        """Update the due date of an assignment in Google Calendar. new_due_date must be in YYYY-MM-DD format. Use this when the user asks to change or reschedule an assignment."""
        return _update_assignment(assignment_name, new_due_date)

    @tool
    def draft_email(recipient_email: str, subject: str, body: str) -> str:
        """Create an email draft for the user to review before sending. Always use this before send_email — never send without showing the draft first."""
        return _store_draft(session, recipient_email, subject, body)

    @tool
    def send_email(_: str = "") -> str:
        """Send the email that was previously drafted. Only call this after the user has explicitly confirmed they want to send the draft."""
        return _send_draft(session)

    @tool
    def generate_quiz(course_name: str, topic: str, num_questions: int = 5) -> str:
        """Generate an interactive multiple-choice quiz on a topic from a course. The user will answer questions one at a time. num_questions defaults to 5."""
        return _generate_quiz(session, course_name, topic, num_questions, agent_context)

    return [search_course_content, update_assignment_due_date, draft_email, send_email, generate_quiz]


# ── Public entry point ─────────────────────────────────────────────────────────

async def run_chat_turn(session_id: str, message: str, agent_context: str) -> str:
    """
    Process one chat message with full tool-calling support and conversation history.
    Returns the assistant's response as a markdown string.
    """
    session = get_session(session_id)
    stripped = message.strip()

    # ── Quiz answer shortcut (no LLM needed) ─────────────────────────────────
    if (
        session.quiz
        and session.quiz.current < len(session.quiz.questions)
        and re.fullmatch(r"[ABCDabcd][.)\s]*", stripped)
    ):
        answer = stripped[0].upper()
        response = _process_answer(session, answer)
        session.history.append(HumanMessage(content=message))
        session.history.append(AIMessage(content=response))
        return response

    # ── Email send confirmation shortcut ─────────────────────────────────────
    if (
        session.email_draft
        and re.search(r"\b(send(\s+it)?|confirm|yes|go\s+ahead)\b", stripped, re.I)
    ):
        response = _send_draft(session)
        session.history.append(HumanMessage(content=message))
        session.history.append(AIMessage(content=response))
        return response

    # ── Build system prompt ───────────────────────────────────────────────────
    now = datetime.now()
    system_content = (
        "You are TaskBuddy, an AI-powered academic assistant.\n"
        f"Today is {now.strftime('%A, %B %d, %Y')} and the time is {now.strftime('%I:%M %p')}.\n\n"
        "## Available tools\n"
        "- **search_course_content**: search syllabi, modules, assignments, and study plans.\n"
        "- **update_assignment_due_date**: reschedule an assignment on Google Calendar.\n"
        "- **draft_email**: create an email draft for user review.\n"
        "- **send_email**: send the confirmed draft.\n"
        "- **generate_quiz**: create an interactive quiz (quiz answers are handled automatically).\n\n"
        "## Rules\n"
        "- Always call search_course_content when answering questions about course topics, "
        "assignments, or deadlines to ground your answer in retrieved content.\n"
        "- Always call draft_email BEFORE send_email — never skip the review step.\n"
        "- Be concise, accurate, and use markdown formatting.\n"
    )
    if agent_context:
        system_content += f"\n## Agent summary (reference)\n{agent_context[:1500]}\n"

    # ── Assemble messages ─────────────────────────────────────────────────────
    session.history.append(HumanMessage(content=message))
    messages = [SystemMessage(content=system_content)] + session.history[-20:]

    # ── LLM call with tools ───────────────────────────────────────────────────
    tools    = _make_tools(session_id, agent_context)
    tool_map = {t.name: t for t in tools}
    llm      = ChatGoogleGenerativeAI(model="gemini-2.0-flash").bind_tools(tools)

    first_response = await asyncio.to_thread(llm.invoke, messages)

    if not first_response.tool_calls:
        answer = first_response.content
    else:
        # Execute each requested tool
        tool_msgs: list[ToolMessage] = []
        for tc in first_response.tool_calls:
            fn = tool_map.get(tc["name"])
            if fn:
                try:
                    result = await asyncio.to_thread(fn.invoke, tc["args"])
                except Exception as e:
                    result = f"Tool error ({tc['name']}): {e}"
                log.info("Tool: %s | args: %s | result: %.80s", tc["name"], tc["args"], str(result))
            else:
                result = f"Unknown tool: {tc['name']}"
            tool_msgs.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

        # Second LLM call to synthesize tool results into a final response
        synthesis_llm  = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
        final_messages = messages + [first_response] + tool_msgs
        final_response = await asyncio.to_thread(synthesis_llm.invoke, final_messages)
        answer = final_response.content

    # ── Update history ────────────────────────────────────────────────────────
    session.history.append(AIMessage(content=answer))
    if len(session.history) > _HISTORY_LIMIT:
        session.history = session.history[-_HISTORY_LIMIT:]

    return answer
