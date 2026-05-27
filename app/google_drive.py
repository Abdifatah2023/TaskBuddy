import os
import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/drive"]

FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "13WX9umc50PfmhSbTDmD5hMRvv2ZyQ2W3")

_ON_RENDER = os.path.exists("/etc/secrets/drive_token.json")
_DRIVE_TOKEN_READ = (
    "/tmp/drive_token.json"
    if (_ON_RENDER and os.path.exists("/tmp/drive_token.json"))
    else "/etc/secrets/drive_token.json"
    if _ON_RENDER
    else "drive_token.json"
)
_DRIVE_TOKEN_WRITE = "/tmp/drive_token.json" if _ON_RENDER else "drive_token.json"


def get_credentials():
    creds = None
    if os.path.exists(_DRIVE_TOKEN_READ):
        creds = Credentials.from_authorized_user_file(_DRIVE_TOKEN_READ, SCOPES)
        if not creds.has_scopes(SCOPES):
            creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "./credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(_DRIVE_TOKEN_WRITE, "w") as token:
            token.write(creds.to_json())
    return creds


def get_drive_service():
    """Returns an authenticated Google Drive API service instance."""
    return build("drive", "v3", credentials=get_credentials())

