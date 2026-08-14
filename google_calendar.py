"""Sync confirmed reminders to a shared Google Calendar (optional - only
active if GOOGLE_CALENDAR_ID and a service-account credential are set in
.env). Uses a service account so no one has to log in with a Gmail account;
the calendar just needs to be shared with the service account's email
(permission level "เปลี่ยนแปลงกิจกรรม" / Make changes to events) for event
creation to work."""

import json
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

from config import (
    GOOGLE_CALENDAR_ID,
    GOOGLE_SERVICE_ACCOUNT_KEY_PATH,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    TIMEZONE,
)

SCOPES = ["https://www.googleapis.com/auth/calendar"]

_service = None
_service_checked = False


def _get_service():
    """Lazily build (and cache) the Calendar API client. Returns None if
    Calendar sync isn't configured, so callers can no-op gracefully."""
    global _service, _service_checked
    if _service_checked:
        return _service
    _service_checked = True

    if not GOOGLE_CALENDAR_ID:
        return None

    try:
        if GOOGLE_SERVICE_ACCOUNT_JSON:
            info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
            creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        elif GOOGLE_SERVICE_ACCOUNT_KEY_PATH:
            creds = service_account.Credentials.from_service_account_file(
                GOOGLE_SERVICE_ACCOUNT_KEY_PATH, scopes=SCOPES
            )
        else:
            print("[google_calendar] GOOGLE_CALENDAR_ID is set but no service account credential found - skipping sync")
            return None
        _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as exc:
        print(f"[google_calendar] failed to init Calendar client: {exc}")
        _service = None

    return _service


def _event_body(subject, meeting_date, meeting_time, location, duration_minutes):
    start_dt = datetime.strptime(f"{meeting_date} {meeting_time}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    return {
        "summary": subject or "นัดหมาย",
        "location": location or "",
        "description": "สร้างโดย LINE bot",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE},
    }


def create_event(subject, meeting_date, meeting_time, location=None, duration_minutes=60):
    """meeting_date: 'YYYY-MM-DD', meeting_time: 'HH:MM'.
    Returns {"id": ..., "htmlLink": ...} on success, or None if Calendar
    sync isn't configured, the date/time is missing, or the API call fails.
    The id is stored so the event can later be rescheduled or deleted."""
    service = _get_service()
    if not service or not meeting_date or not meeting_time:
        return None

    try:
        body = _event_body(subject, meeting_date, meeting_time, location, duration_minutes)
    except ValueError:
        return None

    try:
        event = service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=body).execute()
        return {"id": event.get("id"), "htmlLink": event.get("htmlLink")}
    except Exception as exc:
        print(f"[google_calendar] failed to create event: {exc}")
        return None


def update_event(event_id, subject, meeting_date, meeting_time, location=None, duration_minutes=60):
    """Reschedule/edit an existing event. Returns the updated event's
    htmlLink on success, or None if not configured / event_id missing / the
    API call fails (e.g. the event was already deleted by hand)."""
    service = _get_service()
    if not service or not event_id or not meeting_date or not meeting_time:
        return None

    try:
        body = _event_body(subject, meeting_date, meeting_time, location, duration_minutes)
    except ValueError:
        return None

    try:
        event = service.events().patch(
            calendarId=GOOGLE_CALENDAR_ID, eventId=event_id, body=body
        ).execute()
        return event.get("htmlLink")
    except Exception as exc:
        print(f"[google_calendar] failed to update event {event_id}: {exc}")
        return None


def delete_event(event_id):
    """Returns True if deleted (or already gone), False if Calendar sync
    isn't configured or event_id is missing."""
    service = _get_service()
    if not service or not event_id:
        return False

    try:
        service.events().delete(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id).execute()
    except Exception as exc:
        # 410/404 means it's already gone - treat as success either way.
        print(f"[google_calendar] delete_event {event_id}: {exc}")
    return True
