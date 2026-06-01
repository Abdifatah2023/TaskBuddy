import base64
import logging
import os
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from uuid import uuid4

import requests

log = logging.getLogger(__name__)

GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
RESEND_API_KEY     = os.getenv("RESEND_API_KEY", "")
RESEND_FROM        = os.getenv("RESEND_FROM", "TaskBuddy <onboarding@resend.dev>")


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


def _send_via_resend(
    recipient: str,
    subject: str,
    body: str,
    ics_data: bytes | None,
) -> bool:
    payload: dict = {
        "from": RESEND_FROM,
        "to": [recipient],
        "subject": subject,
        "text": body,
    }
    if ics_data:
        payload["attachments"] = [{
            "filename": "assignments.ics",
            "content": base64.b64encode(ics_data).decode(),
        }]
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    if resp.status_code in (200, 201):
        log.info("Email sent via Resend to %s", recipient)
        return True
    log.error("Resend error %s: %s", resp.status_code, resp.text)
    return False


def _send_via_smtp(
    recipient: str,
    subject: str,
    body: str,
    ics_data: bytes | None,
) -> bool:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        log.error("GMAIL_ADDRESS or GMAIL_APP_PASSWORD not configured")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = recipient
    msg.set_content(body)
    if ics_data:
        msg.add_attachment(
            ics_data,
            maintype="text",
            subtype="calendar",
            filename="assignments.ics",
        )
    context = ssl.create_default_context()
    # Try STARTTLS (587) first, then SSL (465)
    for port, use_ssl in ((587, False), (465, True)):
        try:
            if use_ssl:
                with smtplib.SMTP_SSL("smtp.gmail.com", port, context=context) as s:
                    s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
                    s.send_message(msg)
            else:
                with smtplib.SMTP("smtp.gmail.com", port, timeout=15) as s:
                    s.starttls(context=context)
                    s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
                    s.send_message(msg)
            log.info("Email sent via SMTP port %d to %s", port, recipient)
            return True
        except Exception as e:
            log.warning("SMTP port %d failed: %s", port, e)
    return False


def gmail_send_message(
    email_body: str,
    recipient: str,
    subject: str = "TaskBuddy — Weekly Deadline Digest",
    ics_data: bytes | None = None,
) -> bool:
    """Send email. Uses Resend (HTTPS) when RESEND_API_KEY is set, else SMTP."""
    if RESEND_API_KEY:
        return _send_via_resend(recipient, subject, email_body, ics_data)
    return _send_via_smtp(recipient, subject, email_body, ics_data)
