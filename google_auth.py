# google_auth.py

import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

TOKEN_FILE = "google_token.json"
CREDS_FILE = "credentials.json"

_creds = None  # cache credentials


# Credentials
def get_credentials():
    global _creds

    if _creds is not None:
        return _creds

    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    _creds = creds
    return creds


# Calendar service. Same credentials but different API
_calendar_service = None

def get_calendar_service():
    global _calendar_service

    if _calendar_service is None:
        creds = get_credentials()
        _calendar_service = build("calendar", "v3", credentials=creds)

    return _calendar_service



# Gmail service. Same credentials but different API
_gmail_service = None

def get_gmail_service():
    global _gmail_service

    if _gmail_service is None:
        creds = get_credentials()
        _gmail_service = build("gmail", "v1", credentials=creds)

    return _gmail_service