import logging
import os
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from uuid import uuid4

log = logging.getLogger(__name__)

GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")


def generate_ics(events: list[dict]) -> bytes:
    """Generate RFC 5545 iCalendar data from a list of {title, due_date} dicts."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TaskBuddy//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for ev in events:
        try:
            d = datetime.strptime(ev.get("due_date", "")[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        uid     = str(uuid4()).replace("-", "")
        dt_str  = d.strftime("%Y%m%d")
        end_str = (d + timedelta(days=1)).strftime("%Y%m%d")
        title   = (ev.get("title", "") or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}@taskbuddy",
            f"DTSTART;VALUE=DATE:{dt_str}",
            f"DTEND;VALUE=DATE:{end_str}",
            f"SUMMARY:{title}",
            "DESCRIPTION:Added by TaskBuddy",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines).encode("utf-8")


def gmail_send_message(
    email_body: str,
    recipient: str,
    subject: str = "TaskBuddy — Weekly Deadline Digest",
    ics_data: bytes | None = None,
) -> bool:
    """Send an email from the TaskBuddy Gmail account via SMTP with an optional .ics attachment."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        log.error("GMAIL_ADDRESS or GMAIL_APP_PASSWORD not configured")
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = recipient
        msg.set_content(email_body)

        if ics_data:
            msg.add_attachment(
                ics_data,
                maintype="text",
                subtype="calendar",
                filename="assignments.ics",
            )

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.send_message(msg)

        log.info("Email sent to %s | Subject: %s", recipient, subject)
        return True
    except Exception as e:
        log.error("Email send failed: %s", e)
        return False
