import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/drive"]  # full Drive access needed to create subfolders in any parent


# Set the shared folder ID to monitor.
# You can find the folder ID in the folder's URL:
# https://drive.google.com/drive/folders/<FOLDER_ID>
FOLDER_ID = "13WX9umc50PfmhSbTDmD5hMRvv2ZyQ2W3"

# How often to check for changes (in seconds)
POLL_INTERVAL = 30


def get_credentials():
    creds = None
    if os.path.exists("drive_token.json"):
        creds = Credentials.from_authorized_user_file("drive_token.json", SCOPES)
        # If the saved token was issued with a narrower scope, force re-auth
        if not creds.has_scopes(SCOPES):
            creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "./credentials/credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open("drive_token.json", "w") as token:
            token.write(creds.to_json())
    return creds


def get_drive_service():
    """Returns an authenticated Google Drive API service instance."""
    return build("drive", "v3", credentials=get_credentials())

