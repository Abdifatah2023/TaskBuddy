import base64
import logging
from email.message import EmailMessage

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)


def gmail_send_message(credentials, email_body: str, recipient: str) -> dict | None:
    """Send a plain-text email to recipient using the provided credentials."""
    try:
        service = build("gmail", "v1", credentials=credentials)
        message = EmailMessage()
        message.set_content(email_body)
        message["To"]      = recipient
        message["From"]    = "me"
        message["Subject"] = "TaskBuddy — Weekly Deadlines Bulletin"

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        result = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": encoded_message})
            .execute()
        )
        log.info("Message sent, id: %s", result["id"])
        return result
    except HttpError as error:
        log.error("Gmail send error: %s", error)
        return None
