import json
import os
import uuid

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# Allow OAuth over HTTP for local development (harmless on HTTPS deployments)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive",
]

_CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "credentials.json")

# session_id → {"credentials": Credentials, "email": str}
_auth_sessions: dict[str, dict] = {}


def _make_flow(redirect_uri: str) -> Flow:
    """Build a Flow using credentials.json if present, otherwise fall back to env vars."""
    if os.path.exists(_CREDENTIALS_FILE):
        return Flow.from_client_secrets_file(_CREDENTIALS_FILE, scopes=SCOPES, redirect_uri=redirect_uri)
    client_config = {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [],
        }
    }
    return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)


def build_auth_url(redirect_uri: str) -> str:
    """Return the Google OAuth consent-screen URL."""
    flow = _make_flow(redirect_uri)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return auth_url


def exchange_code(code: str, redirect_uri: str):
    """Exchange an authorization code for credentials. Returns a Credentials object."""
    flow = _make_flow(redirect_uri)
    flow.fetch_token(code=code)
    return flow.credentials


def get_user_email(credentials) -> str:
    """Fetch the authenticated user's email address from the Gmail API."""
    service = build("gmail", "v1", credentials=credentials)
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "")


def create_session(credentials, email: str) -> str:
    """Store credentials + email in the session store. Returns the new session ID."""
    session_id = str(uuid.uuid4())
    _auth_sessions[session_id] = {"credentials": credentials, "email": email}
    return session_id


def get_session(session_id: str | None) -> dict | None:
    """Return the session dict or None if not found."""
    if not session_id:
        return None
    return _auth_sessions.get(session_id)


def delete_session(session_id: str) -> None:
    _auth_sessions.pop(session_id, None)
