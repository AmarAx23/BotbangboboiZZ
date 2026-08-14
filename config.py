import os
from dotenv import load_dotenv

load_dotenv()

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")

# Used for almost all AI tasks: reading document photos, parsing "นัด..."
# chat messages, and the scoped "ถามบอท:" Q&A.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Optional - only used for voice message support (voice_extract.py).
# Claude's API doesn't accept audio input yet, so that one feature falls
# back to Gemini instead. Leave blank to disable voice messages; everything
# else in the bot works fine without it.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "")
R2_PUBLIC_URL_BASE = os.environ.get("R2_PUBLIC_URL_BASE", "")

DATABASE_PATH = os.environ.get("DATABASE_PATH", "reminders.db")

# Public base URL of this app itself (e.g. https://your-app.onrender.com,
# no trailing slash) - used to build links to the web report summary page
# (report_page.py / the /report/<token> route). Leave blank to fall back to
# sending just the raw Excel link, same as before that feature existed.
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

# Fixed destination for reminder push notifications: the "employees" LINE
# group. Officers confirm reminders in a separate "staff" group/chat, but
# every confirmed reminder is always pushed to this one group.
# Leave blank until you've captured the group ID (see README).
EMPLOYEE_GROUP_ID = os.environ.get("EMPLOYEE_GROUP_ID", "")

# --- Google Calendar sync (optional) ---
# When set, every confirmed reminder is also created as an event on this
# Google Calendar, using a service account (no user login required). Leave
# GOOGLE_CALENDAR_ID blank to skip Calendar sync entirely.
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "")
# path to a service-account JSON key file - used when running locally
GOOGLE_SERVICE_ACCOUNT_KEY_PATH = os.environ.get("GOOGLE_SERVICE_ACCOUNT_KEY_PATH", "")
# whole service-account JSON pasted as one env var - used on cloud hosts
# where uploading a separate file isn't convenient. Takes priority over
# GOOGLE_SERVICE_ACCOUNT_KEY_PATH when both are set.
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")

# --- Reminder timing ---
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Bangkok")
# how many minutes before the meeting to send an advance heads-up, in
# addition to the existing at-time push. Set to 0 to disable.
REMINDER_MINUTES_BEFORE = int(os.environ.get("REMINDER_MINUTES_BEFORE", "15"))
# hour (0-23, local TIMEZONE) to push the daily summary of today's
# reminders. Set MORNING_BRIEF_ENABLED=0 to disable.
MORNING_BRIEF_HOUR = int(os.environ.get("MORNING_BRIEF_HOUR", "7"))
MORNING_BRIEF_ENABLED = os.environ.get("MORNING_BRIEF_ENABLED", "1") == "1"

# --- Monthly report (optional) ---
# Auto-generates an Excel summary of the previous month's documents/reminders
# and pushes the download link to the employee group on this day/hour each
# month. Can also always be triggered on demand by typing "รายงานเดือนนี้".
MONTHLY_REPORT_ENABLED = os.environ.get("MONTHLY_REPORT_ENABLED", "1") == "1"
MONTHLY_REPORT_DAY = int(os.environ.get("MONTHLY_REPORT_DAY", "1"))
MONTHLY_REPORT_HOUR = int(os.environ.get("MONTHLY_REPORT_HOUR", "8"))

# --- Weekly report (optional) ---
# Same idea as the monthly report, but for the prior Mon-Sun week, pushed
# every Monday morning. Can also be triggered on demand by typing
# "รายงานสัปดาห์นี้".
WEEKLY_REPORT_ENABLED = os.environ.get("WEEKLY_REPORT_ENABLED", "1") == "1"
WEEKLY_REPORT_HOUR = int(os.environ.get("WEEKLY_REPORT_HOUR", "8"))

# --- Collision detection ---
# When confirming (or rescheduling) a reminder, warn if another reminder
# already exists within this many minutes of the new time. Doesn't block
# creation - just adds a heads-up line to the confirmation reply.
COLLISION_WINDOW_MINUTES = int(os.environ.get("COLLISION_WINDOW_MINUTES", "60"))

# --- Recurring appointments ---
# Hour/minute (local TIMEZONE) the scheduler checks recurring rules
# ("นัดประจำ ทุก...") and generates that day's reminder if today's weekday
# matches. Keep this early in the day, well before MORNING_BRIEF_HOUR.
RECURRING_GENERATE_HOUR = int(os.environ.get("RECURRING_GENERATE_HOUR", "0"))
RECURRING_GENERATE_MINUTE = int(os.environ.get("RECURRING_GENERATE_MINUTE", "10"))

# --- Nightly database backup (optional) ---
# Uploads a copy of the local SQLite file to R2 every night so reminders/
# documents survive a lost or reset machine. Uses the same R2 bucket/
# credentials as everything else - no extra setup needed.
BACKUP_ENABLED = os.environ.get("BACKUP_ENABLED", "1") == "1"
BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "2"))
