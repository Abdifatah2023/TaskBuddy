from datetime import datetime, time, timedelta
import zoneinfo
import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Scope allows creating + modifying events
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

TIMEZONE = "America/Chicago"
tz = zoneinfo.ZoneInfo(TIMEZONE)


# ----------------------------
# Helper: Parse Date / DateTime
# ----------------------------
def parse_datetime(dt_string: str, default_hour=9):
    """
    Accepts:
        YYYY-MM-DD
        YYYY-MM-DD HH:MM:SS
        ISO formats like YYYY-MM-DDTHH:MM:SS
    Returns timezone-aware datetime.
    """

    try:
        # Try ISO format first (most flexible)
        dt = datetime.fromisoformat(dt_string)
    except ValueError:
        # If only date (YYYY-MM-DD)
        dt = datetime.strptime(dt_string, "%Y-%m-%d")
        dt = datetime.combine(dt.date(), time(default_hour, 0))

    # Attach timezone if missing
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)

    return dt


# ----------------------------
# Helper: Check if event exists
# ----------------------------
def event_exists(service, title, start_iso, end_iso):
    events_result = service.events().list(
        calendarId="primary",
        timeMin=start_iso,
        timeMax=end_iso,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    events = events_result.get("items", [])

    for event in events:
        if event.get("summary", "").strip().lower() == title.strip().lower():
            return True

    return False


# ----------------------------
# Main Tool Function
# ----------------------------
def GoogleCalendarTool(title: str, start_time: str, end_time: str = None):
    creds = None

    TOKEN_FILE = "Calendar_token.json"

    # Load token if exists and scopes match
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if not creds.has_scopes(SCOPES):
            creds = None

    # Refresh or authenticate if needed
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "./credentials/credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    try:
        service = build("calendar", "v3", credentials=creds)

        # ----------------------------
        # Parse Start / End Times
        # ----------------------------
        start_dt = parse_datetime(start_time, default_hour=9)

        if end_time:
            end_dt = parse_datetime(end_time, default_hour=10)
        else:
            # Default to 1 hour event
            end_dt = start_dt + timedelta(hours=1)

        # Ensure end is after start
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=1)

        start_iso = start_dt.isoformat()
        end_iso = end_dt.isoformat()

        # ----------------------------
        # Duplicate Check
        # ----------------------------
        if event_exists(service, title, start_iso, end_iso):
            return f"Event '{title}' already exists during that time."

        # ----------------------------
        # Event Payload
        # ----------------------------
        event_body = {
            "summary": title,
            "location": "Not specified",
            "description": "Created via TaskBuddy AI assistant",
            "start": {
                "dateTime": start_iso,
                "timeZone": TIMEZONE,
            },
            "end": {
                "dateTime": end_iso,
                "timeZone": TIMEZONE,
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 30},
                    {"method": "popup", "minutes": 10},
                ],
            },
        }

        created_event = service.events().insert(
            calendarId="primary",
            body=event_body
        ).execute()

        return f"Event created successfully: {created_event.get('htmlLink')}"

    except HttpError as error:
        return f"Google Calendar API error: {error}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"