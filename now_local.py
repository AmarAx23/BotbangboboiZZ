"""Timezone-safe "what time is it right now" for this app.

Every date/time this app stores or compares (reminders.remind_at,
documents.meeting_date, the "นัดประจำ" weekday check, "วันนี้"/"พรุ่งนี้"
parsing, report date ranges, ...) is a plain naive "YYYY-MM-DD HH:MM:SS"
wall-clock value that's always meant to represent TIMEZONE (Asia/Bangkok by
default) - never UTC.

Bare `datetime.now()` / `date.today()` return the *server's own* local
time instead. That was harmless while this bot ran on a Windows PC already
set to Bangkok time, but broke silently after moving to Render, whose
containers run in UTC: a 16:00 reminder was firing at 23:00 (UTC catching
up to what the server thought was "16:00" - a straight 7-hour gap, exactly
Thailand's UTC+7 offset). Around local midnight it could also misjudge
"วันนี้"/"พรุ่งนี้" by a whole day.

Use now_local()/today_local() everywhere instead of datetime.now()/
date.today() - anywhere this app needs "now", not just the reminder
scheduler."""

from datetime import datetime
from zoneinfo import ZoneInfo

from config import TIMEZONE

_TZ = ZoneInfo(TIMEZONE)


def now_local() -> datetime:
    """Current time in TIMEZONE, as a naive datetime (no tzinfo) - matches
    the plain wall-clock strings this app stores/compares everywhere."""
    return datetime.now(_TZ).replace(tzinfo=None)


def today_local():
    """Current date in TIMEZONE, as a plain date object."""
    return now_local().date()
