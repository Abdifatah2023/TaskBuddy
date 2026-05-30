import os

from googleapiclient.discovery import build

FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "13WX9umc50PfmhSbTDmD5hMRvv2ZyQ2W3")


def get_drive_service(credentials):
    """Return an authenticated Google Drive API service instance."""
    return build("drive", "v3", credentials=credentials)
