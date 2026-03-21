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
recipient_email = "shreenat@terpmail.umd.edu"

def authenticate():
  """Handle authentication and returns valid credentials.
  """
  creds = None
  # The file token.json stores the user's access and refresh tokens.
  if os.path.exists("email_token.json"):
    creds = Credentials.from_authorized_user_file("email_token.json", SCOPES)
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
    with open("email_token.json", "w") as token:
      token.write(creds.to_json())
  return creds

def get_weekly_events(creds):
  """Fetch events from calendar for the week"""
  try:
    service = build("calendar", "v3", credentials=creds)
    now = datetime.datetime.utcnow().isoformat()+"Z"
    end_time = (datetime.datetime.utcnow() + datetime.timedelta(days=7)).isoformat() + "Z"
    events_result = (
      service.events().list(
        calendarId="primary",
        timeMin=now,
        timeMax=end_time,
        singleEvents=True,
        orderBy="startTime",
      )
      .execute()
    )
    return events_result.get("items", [])
  
  except HttpError as error:
    print(f"Error: {error}")
    return None

def format_event(events):
  if not events:
    return "No upcoming deadlines/events for the next 7 days"
  
  bulletin_lines = ["Weekly bulletin:\n"]
  for event in events:
    start = event["start"].get("dateTime", event["start"].get("date"))
    start_dt = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
    formatted_date = start_dt.strftime("%A, %B, %d")
    line = f"- {event.get('summary', 'Untitled event')} (Due: {formatted_date})"
    bulletin_lines.append(line)

  return "\n".join(bulletin_lines)

# Send message
def gmail_send_message(creds, email_body):
  try:
    service = build("gmail", "v1", credentials=creds)
    message = EmailMessage()
    # message.set_content("This is automated draft mail")
    message.set_content(email_body) #changed this

    message["To"] = "shreenat@terpmail.umd.edu"
    message["From"] = "shreenat@terpmail.umd.edu"
    message["Subject"] = "Weekly Deadlines Bulletin"

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
  
  weekly_event = get_weekly_events(creds)
  email_body = format_event(weekly_event)
  gmail_send_message(creds, email_body) 

  print("\nGenerated Email Body")
  print(email_body)
  
  print("\nProcess finished. Check your mail")
 
if __name__ == "__main__":
  main()
