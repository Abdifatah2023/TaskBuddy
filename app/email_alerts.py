import os
import os.path
import base64
import logging
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

recipient_email = os.getenv("RECIPIENT_EMAIL", "abdifatahiid@gmail.com")


_ON_RENDER = os.path.exists("/etc/secrets/email_token.json")
_EMAIL_TOKEN_READ = (
    "/tmp/email_token.json"
    if (_ON_RENDER and os.path.exists("/tmp/email_token.json"))
    else "/etc/secrets/email_token.json"
    if _ON_RENDER
    else "email_token.json"
)
_EMAIL_TOKEN_WRITE = "/tmp/email_token.json" if _ON_RENDER else "email_token.json"


def authenticate():
    """Handle authentication and return valid Gmail credentials."""
    creds = None
    if os.path.exists(_EMAIL_TOKEN_READ):
        creds = Credentials.from_authorized_user_file(_EMAIL_TOKEN_READ, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "./credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(_EMAIL_TOKEN_WRITE, "w") as token:
            token.write(creds.to_json())
    return creds


def gmail_send_message(creds, email_body):
    """Send an email with the given body to the recipient."""
    try:
        service = build("gmail", "v1", credentials=creds)
        message = EmailMessage()
        message.set_content(email_body)
        message["To"] = recipient_email
        message["From"] = recipient_email
        message["Subject"] = "Weekly Deadlines Bulletin"

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        send_message = (
            service.users().messages().send(
                userId="me", body={"raw": encoded_message}
            ).execute()
        )
        log.info("Message id: %s", send_message["id"])
        return send_message
    except HttpError as error:
        log.error("An error occurred: %s", error)
        return None
