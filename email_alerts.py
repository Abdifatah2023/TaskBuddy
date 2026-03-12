# go to google cloud console and enable Email API
# also, you need to delete token.json every time you run this
import os.path
import datetime
import base64
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Scope: Calendar read-only + Gmail modify for drafts
SCOPES = [
  "https://www.googleapis.com/auth/calendar.readonly",
  "https://www.googleapis.com/auth/gmail.modify",
  "https://www.googleapis.com/auth/gmail.send"
]

# change this to your email address to receive alerts
recipient_email = "messycanvastahia@gmail.com"

def authenticate():
  """Handle authentication and returns valid credentials.
  """
  creds = None
  # The file token.json stores the user's access and refresh tokens.
  if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
  # If there are no (valid) credentials available, let the user log in.
  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
      creds.refresh(Request())
    else:
      flow = InstalledAppFlow.from_client_secrets_file(
          "credentials.json", SCOPES
      )
      creds = flow.run_local_server(port=0)
    # Save the credentials for the next run
    with open("token.json", "w") as token:
      token.write(creds.to_json())
    return creds
  
# Send message
def gmail_send_message(creds):
  try:
    service = build("gmail", "v1", credentials=creds)
    message = EmailMessage()
    message.set_content("This is automated draft mail")

    message["To"] = "messycanvastahia@gmail.com"
    message["From"] = "messycanvastahia@gmail.com"
    message["Subject"] = "Automated draft"

    #encoded message
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

    create_message = {"raw":encoded_message}
    send_message = (
      service.users().messages().send(userId="me", body=create_message).execute()
    )
    print(f'Message id: {send_message["id"]}')

  except HttpError as error:
    print(f"An error occured: {error}")
    send_message=None
  
  return send_message

def main():
  print("Starting the weekly bulletin process...")

   #authenticate and get credentials
  creds = authenticate()
  if not creds:
    print("Could not authenticate. Exiting.")
    return

  gmail_send_message(creds) 
  
  print("\nProcess finished. Check your mail")
 
if __name__ == "__main__":
  main()
