from googleapiclient.http import MediaInMemoryUpload

def get_or_create_folder(service, folder_name, parent_id):
    """Get folder ID if exists, otherwise create it."""
    existing = service.files().list(
        q=f"name='{folder_name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id)",
        pageSize=1
    ).execute().get("files", [])
    if existing:
        return existing[0]["id"]
    file_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=file_metadata, fields="id").execute()
    return folder["id"]

def upload_text_file(service, folder_id, file_name, text):
    media = MediaInMemoryUpload(text.encode("utf-8"), mimetype="text/plain")
    file_metadata = {"name": file_name, "parents": [folder_id]}
    file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return file.get("id")
