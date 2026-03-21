from datetime import datetime, time, timedelta
import zoneinfo
import os.path

from googleapiclient.errors import HttpError
from google_auth import get_calendar_service




TIMEZONE = "America/Chicago"
tz = zoneinfo.ZoneInfo(TIMEZONE)




# Helper function : Parse Date / DateTime
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


# Helper function : checks if event exists
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


def create_calendar_event(title: str, start_time: str, end_time: str = None,  description : str = "", location : str = "N/A"):
    try :   
        service = get_calendar_service()

        start_dt = parse_datetime(start_time, default_hour=9)

        if end_time:
            end_dt = parse_datetime(end_time, default_hour=10)
        else:
            # Default to 1 hour event
            end_dt = start_dt + timedelta(hours=1)

        # Ensure end is after start
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(hours=1)

        # Convert dates to iso format
        start_iso = start_dt.isoformat()
        end_iso = end_dt.isoformat()

        # Check for duplicate events
        if event_exists(service, title, start_iso, end_iso):
                return f"Event '{title}' already exists during that time."

        event = {
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

        result = service.events().insert(
            calendarId="primary",
            body=event
        ).execute()

        return f"Event created: {result['htmlLink']}"
    
    except HttpError as error:
        return f"Google Calendar API error: {error}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"