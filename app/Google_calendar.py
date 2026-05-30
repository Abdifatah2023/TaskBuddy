from datetime import datetime, time, timedelta
import logging
import zoneinfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

TIMEZONE = "America/Chicago"
tz = zoneinfo.ZoneInfo(TIMEZONE)


# ── Date / time helpers ───────────────────────────────────────────────────────

def parse_datetime(dt_string: str, default_hour: int = 23) -> datetime:
    """
    Parse a Canvas/ISO date string and return a timezone-aware datetime in the
    configured local timezone. Correctly handles:

      YYYY-MM-DD              → local date at default_hour (default: 23:00)
      YYYY-MM-DDTHH:MM:SS     → naive local datetime
      YYYY-MM-DDTHH:MM:SSZ    → UTC → converted to local timezone
      Any other ISO format    → converted to local timezone
    """
    normalized = dt_string.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        dt = datetime.strptime(dt_string.strip()[:10], "%Y-%m-%d")
        dt = datetime.combine(dt.date(), time(default_hour, 0))

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)

    return dt


# ── Event lookup ──────────────────────────────────────────────────────────────

def find_event_by_title(service, title: str):
    """
    Search for a TaskBuddy-created calendar event by exact title (case-insensitive).
    Searches 6 months back to 12 months forward. Returns the event dict or None.
    """
    time_min = (datetime.now(tz) - timedelta(days=180)).isoformat()
    time_max = (datetime.now(tz) + timedelta(days=365)).isoformat()
    try:
        result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            q=title,
            singleEvents=True,
            maxResults=25,
        ).execute()
        for event in result.get("items", []):
            if (
                event.get("summary", "").strip().lower() == title.strip().lower()
                and "TaskBuddy" in event.get("description", "")
            ):
                return event
    except HttpError as e:
        log.warning("find_event_by_title failed for '%s': %s", title, e)
    return None


# ── Main tool function ────────────────────────────────────────────────────────

def GoogleCalendarTool(title: str, start_time: str, end_time: str = None, credentials=None) -> str:
    """
    Upsert a Google Calendar event for an assignment:
      - Creates the event if it does not exist.
      - Updates the date if the assignment due date has changed.
      - Skips (idempotent) if the event already exists on the correct date.
    """
    if credentials is None:
        return "Authentication error: no credentials provided."

    try:
        service = build("calendar", "v3", credentials=credentials)

        start_dt = parse_datetime(start_time)
        end_dt = (
            parse_datetime(end_time)
            if end_time
            else start_dt + timedelta(hours=1)
        )
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=1)

        start_iso   = start_dt.isoformat()
        end_iso     = end_dt.isoformat()
        target_date = start_dt.strftime("%Y-%m-%d")

        existing = find_event_by_title(service, title)

        if existing:
            existing_start = existing.get("start", {})
            existing_date  = (
                existing_start.get("date")
                or existing_start.get("dateTime", "")[:10]
            )
            if existing_date == target_date:
                log.info("Skipped (exists, same date): '%s' on %s", title, target_date)
                return f"Event '{title}' already exists on {target_date}."

            existing["start"] = {"dateTime": start_iso, "timeZone": TIMEZONE}
            existing["end"]   = {"dateTime": end_iso,   "timeZone": TIMEZONE}
            service.events().update(
                calendarId="primary",
                eventId=existing["id"],
                body=existing,
            ).execute()
            log.info("Updated: '%s'  %s → %s", title, existing_date, target_date)
            return (
                f"Event updated successfully: '{title}' "
                f"moved from {existing_date} to {target_date}."
            )

        event_body = {
            "summary": title,
            "description": "Created via TaskBuddy AI assistant",
            "start": {"dateTime": start_iso, "timeZone": TIMEZONE},
            "end":   {"dateTime": end_iso,   "timeZone": TIMEZONE},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 1440},
                    {"method": "popup", "minutes": 60},
                ],
            },
        }
        created = service.events().insert(
            calendarId="primary", body=event_body
        ).execute()
        log.info("Created: '%s' on %s", title, target_date)
        return f"Event created successfully: {created.get('htmlLink')}"

    except HttpError as error:
        log.error("Calendar API error for '%s': %s", title, error)
        return f"Google Calendar API error: {error}"
    except Exception as e:
        log.error("Unexpected error for '%s': %s", title, e)
        return f"Unexpected error: {str(e)}"


# ── Read-back helper (for /calendar-events endpoint) ─────────────────────────

def get_taskbuddy_events(credentials) -> list[dict]:
    """Fetch all TaskBuddy-created events from Google Calendar."""
    if credentials is None:
        return []
    try:
        service = build("calendar", "v3", credentials=credentials)
        time_min = (datetime.now(tz) - timedelta(days=180)).isoformat()
        time_max = (datetime.now(tz) + timedelta(days=365)).isoformat()

        result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=500,
        ).execute()

        events = []
        for event in result.get("items", []):
            if "TaskBuddy" not in event.get("description", ""):
                continue
            start = event.get("start", {})
            dt_str = start.get("dateTime", "")
            if dt_str:
                date_str = datetime.fromisoformat(dt_str).strftime("%Y-%m-%d")
            else:
                date_str = start.get("date", "")
            events.append({"title": event.get("summary", ""), "due_date": date_str})
        return events
    except Exception:
        return []
