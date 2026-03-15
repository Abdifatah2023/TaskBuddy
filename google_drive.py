import io
import os.path
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Set the shared folder ID to monitor.
# You can find the folder ID in the folder's URL:
# https://drive.google.com/drive/folders/<FOLDER_ID>
FOLDER_ID = "1ujBDlxP-BnJTajOuSEeOWeWirrawjXGT"

# How often to check for changes (in seconds)
POLL_INTERVAL = 30


def get_credentials():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "./credentials/credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return creds


def get_drive_service():
    """Returns an authenticated Google Drive API service instance."""
    return build("drive", "v3", credentials=get_credentials())


def download_file_content(service, file_id: str, mime_type: str) -> bytes:
    """
    Download a file's content as bytes using chunked streaming.
    - Google Workspace files (Docs, Sheets, etc.) are exported as PDF.
    - All other files (PDF, DOCX, DOC) are downloaded as-is.
    """
    if mime_type.startswith("application/vnd.google-apps."):
        request = service.files().export_media(
            fileId=file_id, mimeType="application/pdf"
        )
    else:
        request = service.files().get_media(fileId=file_id)

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def list_folder_files(service, folder_id):
    """Returns a dict of {file_id: file_info} for all files in the folder."""
    files = {}
    page_token = None
    query = f"'{folder_id}' in parents and trashed = false"

    while True:
        response = (
            service.files()
            .list(
                q=query,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                pageToken=page_token,
            )
            .execute()
        )
        for f in response.get("files", []):
            files[f["id"]] = f
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return files


def print_files(files):
    if not files:
        print("  (no files found)")
        return
    for f in files.values():
        print(f"  {f['name']}  [{f['mimeType']}]  (id: {f['id']})")


def monitor_folder(service, folder_id, poll_interval):
    print(f"Monitoring folder: {folder_id}")
    print(f"Polling every {poll_interval} seconds. Press Ctrl+C to stop.\n")

    previous = list_folder_files(service, folder_id)
    print("Current files:")
    print_files(previous)
    print()

    while True:
        time.sleep(poll_interval)
        current = list_folder_files(service, folder_id)

        added = {fid: f for fid, f in current.items() if fid not in previous}
        removed = {fid: f for fid, f in previous.items() if fid not in current}
        modified = {
            fid: f
            for fid, f in current.items()
            if fid in previous and f["modifiedTime"] != previous[fid]["modifiedTime"]
        }

        if added or removed or modified:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] Changes detected:")
            for f in added.values():
                print(f"  + ADDED:    {f['name']} (id: {f['id']})")
            for f in removed.values():
                print(f"  - REMOVED:  {f['name']} (id: {f['id']})")
            for f in modified.values():
                print(f"  ~ MODIFIED: {f['name']} (id: {f['id']})")
            print()
            previous = current
        else:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] No changes.")


def main():
    try:
        creds = get_credentials()
        service = build("drive", "v3", credentials=creds)
        monitor_folder(service, FOLDER_ID, POLL_INTERVAL)
    except HttpError as error:
        print(f"An error occurred: {error}")
    except KeyboardInterrupt:
        print("\nStopped monitoring.")


if __name__ == "__main__":
    main()
