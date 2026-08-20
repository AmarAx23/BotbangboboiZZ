"""Background jobs:
  - check_and_send_reminders: every minute, pushes due reminders (as a Flex
    card with the original document image, if we have one) to the fixed
    employee group.
  - check_and_send_advance_notices: every minute, pushes a shorter
    heads-up Flex card REMINDER_MINUTES_BEFORE minutes ahead of the same
    reminders.
  - generate_recurring_instances: once a day, turns any "นัดประจำ" rule
    whose weekday matches today into a real reminder row (idempotent - safe
    to run more than once on the same day).
  - send_morning_brief: once a day, pushes a summary of everything still
    due today.
  - send_weekly_report / send_monthly_report: turn last week's / last
    month's documents into an Excel file on R2 and push the link.
  - backup_database: once a day, uploads a copy of the local SQLite file
    to R2 so it survives a lost/reset machine."""

import calendar
import os
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
    ImageMessage,
)

from config import (
    LINE_CHANNEL_ACCESS_TOKEN,
    EMPLOYEE_GROUP_ID,
    TIMEZONE,
    DATABASE_PATH,
    REMINDER_MINUTES_BEFORE,
    MORNING_BRIEF_HOUR,
    MORNING_BRIEF_ENABLED,
    MONTHLY_APPOINTMENT_BRIEF_HOUR,
    MONTHLY_APPOINTMENT_BRIEF_MINUTE,
    MONTHLY_REPORT_ENABLED,
    MONTHLY_REPORT_DAY,
    MONTHLY_REPORT_HOUR,
    WEEKLY_REPORT_ENABLED,
    WEEKLY_REPORT_HOUR,
    RECURRING_GENERATE_HOUR,
    RECURRING_GENERATE_MINUTE,
    BACKUP_ENABLED,
    BACKUP_HOUR,
)
import db
import report
import google_calendar
import date_fmt
import screenshot
from now_local import now_local
from flex_messages import reminder_card
from storage import upload_file

_configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

WEEKDAY_NAMES_TH = ["วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์"]


def _push_text(text: str):
    if not EMPLOYEE_GROUP_ID:
        return
    with ApiClient(_configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=EMPLOYEE_GROUP_ID, messages=[TextMessage(text=text)])
        )


def _push_flex(flex_message):
    if not EMPLOYEE_GROUP_ID:
        print("[WARN] EMPLOYEE_GROUP_ID is not set in .env - skipping push. See README.")
        return
    with ApiClient(_configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=EMPLOYEE_GROUP_ID, messages=[flex_message])
        )


def _push_report(text: str, page_url: str = None):
    """Same as _push_text, but also attaches a screenshot of the web report
    page (see screenshot.py) as a second message when page_url is set."""
    if not EMPLOYEE_GROUP_ID:
        return
    messages = [TextMessage(text=text)]
    if page_url:
        original_url, preview_url = screenshot.report_screenshot_urls(page_url)
        messages.append(
            ImageMessage(original_content_url=original_url, preview_image_url=preview_url)
        )
    with ApiClient(_configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=EMPLOYEE_GROUP_ID, messages=messages)
        )


def _push_reminder(reminder: dict):
    flex = reminder_card(
        header_text="🔔 แจ้งเตือน",
        subject=reminder.get("subject"),
        remind_at=date_fmt.to_thai_datetime(reminder["remind_at"]),
        location=reminder.get("location"),
        category=reminder.get("category"),
        assignee=reminder.get("assignee"),
        image_url=reminder.get("image_url"),
        image_urls=db.decode_image_urls(reminder.get("image_urls")),
        calendar_link=reminder.get("calendar_event_link"),
    )
    _push_flex(flex)


def check_and_send_reminders():
    now_str = now_local().strftime("%Y-%m-%d %H:%M:%S")
    for reminder in db.get_due_reminders(now_str):
        try:
            _push_reminder(reminder)
        finally:
            db.mark_sent(reminder["id"])


def check_and_send_advance_notices():
    """Push a short heads-up REMINDER_MINUTES_BEFORE minutes ahead of each
    reminder's remind_at, once, in addition to the at-time push above."""
    if REMINDER_MINUTES_BEFORE <= 0:
        return

    now = now_local()
    # +/-1 minute window around the target mark so a 60s-interval poll
    # reliably catches it exactly once.
    window_start = (now + timedelta(minutes=REMINDER_MINUTES_BEFORE - 1)).strftime("%Y-%m-%d %H:%M:%S")
    window_end = (now + timedelta(minutes=REMINDER_MINUTES_BEFORE + 1)).strftime("%Y-%m-%d %H:%M:%S")

    for reminder in db.get_reminders_needing_advance_notice(window_start, window_end):
        try:
            flex = reminder_card(
                header_text=f"⏰ อีก {REMINDER_MINUTES_BEFORE} นาที ถึงเวลานัดหมาย",
                subject=reminder.get("subject"),
                remind_at=date_fmt.to_thai_datetime(reminder["remind_at"]),
                location=reminder.get("location"),
                category=reminder.get("category"),
                assignee=reminder.get("assignee"),
                image_url=reminder.get("image_url"),
                image_urls=db.decode_image_urls(reminder.get("image_urls")),
                calendar_link=reminder.get("calendar_event_link"),
            )
            _push_flex(flex)
        finally:
            db.mark_advance_notified(reminder["id"])


def generate_instance_for_rule(rule: dict, date_str: str):
    """Turns one recurring rule into a real `reminders` row for date_str,
    syncing to Google Calendar the same way a manually-confirmed reminder
    would. Idempotent - no-ops if that day's instance already exists.
    Shared by the daily scheduler job and app.py's "create rule" handler
    (which calls this immediately if the new rule's weekday is today and
    the time hasn't passed yet, so the first occurrence isn't a whole week
    away)."""
    if db.reminder_exists_for_rule_on(rule["id"], date_str):
        return None

    remind_at = f"{date_str} {rule['time_str']}:00"

    calendar_result = google_calendar.create_event(
        subject=rule["subject"],
        meeting_date=date_str,
        meeting_time=rule["time_str"],
        location=rule.get("location"),
    )
    calendar_link = calendar_result.get("htmlLink") if calendar_result else None
    calendar_event_id = calendar_result.get("id") if calendar_result else None

    return db.create_reminder(
        user_id=rule["user_id"],
        subject=rule["subject"],
        remind_at=remind_at,
        location=rule.get("location"),
        image_url=None,
        calendar_event_link=calendar_link,
        calendar_event_id=calendar_event_id,
        category=rule.get("category"),
        recurring_rule_id=rule["id"],
        assignee=rule.get("assignee"),
    )


def generate_recurring_instances():
    """For every active "นัดประจำ" rule whose weekday matches today, create
    today's reminders row if we haven't already (idempotent)."""
    now = now_local()
    today_str = now.strftime("%Y-%m-%d")
    today_weekday = now.weekday()  # 0=Monday ... 6=Sunday

    for rule in db.list_recurring_rules(active_only=True):
        if rule["weekday"] != today_weekday:
            continue
        generate_instance_for_rule(rule, today_str)


def send_morning_brief():
    """Push a summary of everything still due today, to the employee group."""
    if not MORNING_BRIEF_ENABLED:
        return

    today_str = now_local().strftime("%Y-%m-%d")
    today_display = date_fmt.to_thai_date(today_str)
    reminders = db.get_today_reminders(today_str)

    if not reminders:
        _push_text(f"📅 สรุปนัดหมายวันนี้ ({today_display})\nวันนี้ไม่มีนัดหมายครับ")
        return

    lines = [f"📅 สรุปนัดหมายวันนี้ ({today_display}) มีทั้งหมด {len(reminders)} รายการ", ""]
    for r in reminders:
        time_part = r["remind_at"][11:16] if len(r["remind_at"]) >= 16 else r["remind_at"]
        loc = f" @ {r['location']}" if r.get("location") else ""
        cat = f" [{r['category']}]" if r.get("category") else ""
        who = f" (มอบหมาย: {r['assignee']})" if r.get("assignee") else ""
        lines.append(f"• {time_part} — {r['subject'] or 'การประชุม'}{loc}{cat}{who}")
    _push_text("\n".join(lines))


def send_monthly_appointment_brief():
    """Push a summary of every reminder in the current calendar month, to
    the employee group - same idea as send_morning_brief but for the whole
    month instead of just today. Runs every morning at
    MONTHLY_APPOINTMENT_BRIEF_HOUR:MONTHLY_APPOINTMENT_BRIEF_MINUTE (see
    start_scheduler), so it's a running "here's everything left this
    month" view rather than a one-time send. Replaces the old Sun/Mon
    weekly version - same content shape, wider date range, daily cadence."""
    if not MORNING_BRIEF_ENABLED:
        return

    today = now_local().date()
    month_start = date(today.year, today.month, 1)
    month_end = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    label = report.month_label(today.year, today.month)

    reminders = db.get_reminders_in_range(month_start.isoformat(), month_end.isoformat())

    if not reminders:
        _push_text(f"📅 สรุปนัดหมายเดือนนี้ ({label})\nเดือนนี้ไม่มีนัดหมายครับ")
        return

    lines = [f"📅 สรุปนัดหมายเดือนนี้ ({label}) มีทั้งหมด {len(reminders)} รายการ"]
    current_date = None
    for r in reminders:
        r_date = r["remind_at"][:10]
        if r_date != current_date:
            current_date = r_date
            try:
                weekday_name = WEEKDAY_NAMES_TH[date.fromisoformat(r_date).weekday()]
            except ValueError:
                weekday_name = ""
            lines.append("")
            lines.append(f"{weekday_name} {date_fmt.to_thai_date(r_date)}")
        time_part = r["remind_at"][11:16] if len(r["remind_at"]) >= 16 else r["remind_at"]
        loc = f" @ {r['location']}" if r.get("location") else ""
        cat = f" [{r['category']}]" if r.get("category") else ""
        who = f" (มอบหมาย: {r['assignee']})" if r.get("assignee") else ""
        lines.append(f"• {time_part} — {r['subject'] or 'การประชุม'}{loc}{cat}{who}")
    _push_text("\n".join(lines))


def send_monthly_report():
    """Generate the previous month's Excel report and push the R2 link to
    the employee group. Runs on MONTHLY_REPORT_DAY (default the 1st)."""
    if not MONTHLY_REPORT_ENABLED:
        return

    today = now_local()
    # Previous month, handling January -> December of the prior year.
    if today.month == 1:
        year, month = today.year - 1, 12
    else:
        year, month = today.year, today.month - 1

    label = report.month_label(year, month)
    url, page_url, count = report.generate_monthly_report(year, month)

    if not url:
        _push_text(f"⚠️ สร้างรายงานเดือน{label}อัตโนมัติไม่สำเร็จครับ (เช็คการตั้งค่า R2)")
        return
    if count == 0:
        return  # nothing happened that month - skip the push, no need to notify

    if page_url:
        _push_report(f"📊 รายงานสรุปเดือน{label} ({count} รายการ)\n{page_url}\n\nไฟล์ Excel: {url}", page_url)
    else:
        _push_text(f"📊 รายงานสรุปเดือน{label} ({count} รายการ)\n{url}")


def send_weekly_report():
    """Generate last week's (Mon-Sun) Excel report and push the R2 link.
    Runs every Monday morning at WEEKLY_REPORT_HOUR."""
    if not WEEKLY_REPORT_ENABLED:
        return

    last_monday = (now_local() - timedelta(days=7)).date()
    label = report.week_label(last_monday)
    url, page_url, count = report.generate_weekly_report(last_monday)

    if not url:
        _push_text(f"⚠️ สร้างรายงานสัปดาห์ {label} อัตโนมัติไม่สำเร็จครับ (เช็คการตั้งค่า R2)")
        return
    if count == 0:
        return

    if page_url:
        _push_report(f"📊 รายงานสรุปสัปดาห์ {label} ({count} รายการ)\n{page_url}\n\nไฟล์ Excel: {url}", page_url)
    else:
        _push_text(f"📊 รายงานสรุปสัปดาห์ {label} ({count} รายการ)\n{url}")


def backup_database():
    """Nightly copy of the local SQLite file up to R2, so reminders/
    documents survive a lost or reset machine. Silent on success (would
    otherwise spam the group every night); only notifies on failure."""
    if not BACKUP_ENABLED:
        return

    if not os.path.exists(DATABASE_PATH):
        return

    try:
        with open(DATABASE_PATH, "rb") as f:
            db_bytes = f.read()
        date_str = now_local().strftime("%Y-%m-%d")
        upload_file(db_bytes, f"backups/reminders-{date_str}.db", "application/octet-stream")
    except Exception as exc:
        print(f"[scheduler] nightly backup failed: {exc}")
        _push_text(f"⚠️ สำรองฐานข้อมูลประจำคืนนี้ไม่สำเร็จครับ ({exc})")


def start_scheduler():
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(check_and_send_reminders, "interval", seconds=60)
    scheduler.add_job(check_and_send_advance_notices, "interval", seconds=60)
    scheduler.add_job(
        generate_recurring_instances,
        "cron",
        hour=RECURRING_GENERATE_HOUR,
        minute=RECURRING_GENERATE_MINUTE,
        timezone=TIMEZONE,
    )
    scheduler.add_job(
        send_morning_brief,
        "cron",
        hour=MORNING_BRIEF_HOUR,
        minute=0,
        timezone=TIMEZONE,
    )
    scheduler.add_job(
        send_monthly_appointment_brief,
        "cron",
        hour=MONTHLY_APPOINTMENT_BRIEF_HOUR,
        minute=MONTHLY_APPOINTMENT_BRIEF_MINUTE,
        timezone=TIMEZONE,
    )
    scheduler.add_job(
        send_monthly_report,
        "cron",
        day=MONTHLY_REPORT_DAY,
        hour=MONTHLY_REPORT_HOUR,
        minute=0,
        timezone=TIMEZONE,
    )
    scheduler.add_job(
        send_weekly_report,
        "cron",
        day_of_week="mon",
        hour=WEEKLY_REPORT_HOUR,
        minute=0,
        timezone=TIMEZONE,
    )
    scheduler.add_job(
        backup_database,
        "cron",
        hour=BACKUP_HOUR,
        minute=0,
        timezone=TIMEZONE,
    )
    scheduler.start()
    return scheduler
