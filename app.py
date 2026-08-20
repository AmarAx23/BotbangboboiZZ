"""LINE reminder bot.

Two-group setup:
  - "Staff" group (or 1:1 chat): officers send a photo of an official
    document (or type corrections). The bot only ever responds here to
    images and to its own known commands/quick-replies - it stays silent
    on unrelated chatter so it doesn't spam a busy group.
  - "Employee" group: a fixed destination (config.EMPLOYEE_GROUP_ID) that
    every confirmed reminder is pushed to, regardless of which staff
    member confirmed it or which chat they confirmed it in.

Flow:
  1. Officer types a message starting with "นัด" (e.g. "นัดลูกค้าพรุ่งนี้บ่าย
     3") or sends a voice message - Claude/Gemini parses subject/date/time/
     location/category/assignee out of the text/audio (text_extract.py /
     voice_extract.py) and shows a summary card with buttons: confirm /
     attach document / edit / cancel.
     NOTE: photos are NOT read by AI anymore (image_extract.py is disabled -
     it was misreading dotted/blank fill-in lines on scanned Thai official
     documents as literal field values). Sending a photo instead just
     uploads it to R2 and attaches it to whatever nadd is already in
     progress (or starts a blank one if none is), for the officer to fill
     in by hand via "แก้ไขข้อมูล" or "แนบเอกสาร".
  2. "ยืนยัน" (confirm) -> reminder is scheduled as-is.
     "แนบเอกสาร"/"เปลี่ยนเอกสาร" (attach/change document) -> prompts the
     officer to send a photo, which gets attached to the pending record
     with no AI parsing involved.
     "แก้ไขข้อมูล" (edit) -> officer types corrected fields in a small
      template; whatever they typed overwrites those fields (fields left
      out keep their previous value).
     "ยกเลิก" (cancel) -> pending row is dropped.
  3. On confirm, the background scheduler (scheduler.py) pushes a message
     + the attached image (if any) to the employee group at the scheduled
     time, and (if REMINDER_MINUTES_BEFORE > 0) a shorter heads-up N
     minutes earlier. scheduler.py also pushes a daily summary of the
     day's reminders each morning if MORNING_BRIEF_ENABLED.

On confirm, if GOOGLE_CALENDAR_ID is configured (see .env.example), the
reminder is also created as an event on that Google Calendar via
google_calendar.py (service-account auth, no separate sign-in needed).

Additional commands (all work in the staff chat/group):
  - "รายการนัดหมาย" - list upcoming confirmed reminders, numbered.
  - "เลื่อนนัด <เลข> เป็น <วันเวลาใหม่>" / "ยกเลิกนัด <เลข>" - reschedule or
    cancel one of the reminders from that numbered list (also updates/
    deletes the matching Google Calendar event, if any).
  - "ค้นหา <คำ>" - search all past documents by subject/location/category.
  - "รายการเอกสาร" / "ลบเอกสาร <เลข>" - browse the document archive and
    remove an entry logged by mistake.
  - "รายงานเดือนนี้" / "รายงานเดือน YYYY-MM" / "รายงานสัปดาห์นี้" - generate
    an Excel summary of that period's documents, uploaded to R2, link sent
    back in chat. The monthly/weekly versions also push automatically on a
    schedule (see scheduler.py).
  - "นัดประจำ ทุก<วัน> <ข้อความนัดหมาย>" (เช่น "นัดประจำ ทุกวันจันทร์ 9 โมง
    ประชุมทีม") - recurring weekly appointment. "รายการนัดประจำ" lists
    them, "ยกเลิกนัดประจำ <เลข>" deactivates one.
  - Voice messages - transcribed and parsed the same way as "นัด..." text,
    via voice_extract.py (requires GEMINI_API_KEY - see .env.example).
  - Every confirmed/rescheduled reminder is checked against other upcoming
    reminders within COLLISION_WINDOW_MINUTES and flags a heads-up if two
    land close together (doesn't block creation).
  - Reminders are pushed to the employee group as a LINE Flex card (see
    flex_messages.py) with the document photo and a "ดูปฏิทิน" button when
    a Calendar link exists.
  - Every appointment can carry a "มอบหมาย" (assignee) - who's responsible
    for it. Claude best-effort-extracts it from documents/text/voice when
    stated explicitly, and it's always editable via the แก้ไขข้อมูล form.
"""

import os
import re
from datetime import timedelta

from flask import Flask, request, abort

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    ImageMessageContent,
    AudioMessageContent,
)
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
    ImageMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction,
)

import config
import db
import ai_chat
import date_fmt
import flex_messages
import google_calendar
import list_cache
import report
import report_page
import screenshot
from now_local import now_local, today_local
import voice_extract
# NOTE: image_extract.py (Claude vision on document photos) is no longer
# used - see the module docstring above for why.
from text_extract import extract_meeting_info_from_text, extract_recurring_info
from storage import upload_image
from scheduler import start_scheduler, generate_instance_for_rule

app = Flask(__name__)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)
_line_configuration = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)

EDIT_TEMPLATE = (
    "พิมพ์แก้ไขข้อมูลตามฟอร์มนี้ครับ (บรรทัดไหนไม่พิมพ์มา จะใช้ค่าเดิมที่มีอยู่)\n\n"
    "เรื่อง: ...\n"
    "วันที่: YYYY-MM-DD\n"
    "เวลา: HH:MM\n"
    "สถานที่: ...\n"
    "หมวดหมู่: ...\n"
    "มอบหมาย: ..."
)

EDIT_FIELD_PATTERNS = {
    "subject": r"เรื่อง\s*[:：]\s*(.+)",
    "meeting_date": r"วันที่\s*[:：]\s*(.+)",
    "meeting_time": r"เวลา\s*[:：]\s*(.+)",
    "location": r"สถานที่\s*[:：]\s*(.+)",
    "category": r"หมวดหมู่\s*[:：]\s*(.+)",
    "assignee": r"มอบหมาย\s*[:：]\s*(.+)",
}

_THAI_MONTH_NAMES = (
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
)


def _looks_like_formal_document_text(text: str) -> bool:
    """Broader trigger than text.startswith("นัด") - catches a whole
    official memo pasted in as-is (e.g. starting with "มอบหมายให้ เวร ฯ
    70 ..." then "เรื่อง ร่วมตรวจสอบ...") instead of a short "นัด..."
    sentence. Requires "เรื่อง" (the standard subject-line marker in Thai
    official documents) PLUS a second, independent signal - "มอบหมาย", a
    Buddhist-era year marker, or a Thai month name - so ordinary group
    chatter that happens to say the word "เรื่อง" in passing doesn't
    accidentally trigger a parse attempt (and the "ไม่เข้าใจ..." reply that
    follows a failed one)."""
    if "เรื่อง" not in text:
        return False
    return (
        "มอบหมาย" in text
        or "พ.ศ." in text
        or any(month in text for month in _THAI_MONTH_NAMES)
    )

# Scoped AI Q&A trigger: only messages starting with "ถามบอท:" get an AI
# reply, so the bot doesn't auto-respond to normal group conversation.
ASK_BOT_PATTERN = r"^ถามบอท\s*[:：]\s*(.+)"

RESCHEDULE_PATTERN = r"^เลื่อนนัด\s+(\d+)\s+เป็น\s+(.+)$"
CANCEL_CONFIRMED_PATTERN = r"^ยกเลิกนัด\s+(\d+)\s*$"
# Sent by the "✅ รับทราบแล้ว" button on the at-time reminder push (see
# flex_messages.reminder_card's reminder_id param) - uses the reminder's
# real db id directly (not a list_cache index) since the user never types
# this by hand, just taps the button.
ACK_REMINDER_PATTERN = r"^รับทราบแจ้งเตือน\s+(\d+)\s*$"
SEARCH_PATTERN = r"^ค้นหา\s+(.+)$"
REPORT_MONTH_PATTERN = r"^รายงานเดือน\s+(\d{4})-(\d{1,2})\s*$"
DELETE_DOCUMENT_PATTERN = r"^ลบเอกสาร\s+(\d+)\s*$"
CANCEL_RECURRING_PATTERN = r"^ยกเลิกนัดประจำ\s+(\d+)\s*$"

# "นัดประจำ ทุกวันจันทร์ 9 โมง ประชุมทีม" -> weekday word captured in group 1,
# the rest of the appointment text (time/subject/location) in group 2.
WEEKDAY_CHOICES = "วันจันทร์|วันอังคาร|วันพุธ|วันพฤหัสบดี|วันพฤหัส|วันศุกร์|วันเสาร์|วันอาทิตย์"
RECURRING_PATTERN = rf"^นัดประจำ\s+ทุก({WEEKDAY_CHOICES})\s+(.+)$"
WEEKDAY_TO_INDEX = {
    "วันจันทร์": 0, "วันอังคาร": 1, "วันพุธ": 2,
    "วันพฤหัสบดี": 3, "วันพฤหัส": 3,
    "วันศุกร์": 4, "วันเสาร์": 5, "วันอาทิตย์": 6,
}
WEEKDAY_NAMES = ["วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์"]

# Full command/feature reference, sent whenever someone types "คำสั่งบอท"
# (works in both the staff group and 1:1 - unlike the silent-fallback
# behavior for unrecognized text, this one always replies).
BOT_HELP_TEXT = (
    "คำสั่งทั้งหมดที่ใช้กับบอทตัวนี้ได้ครับ:\n"
    "\n"
    "📸 สร้างนัดหมาย\n"
    "• พิมพ์ \"นัด...\" เช่น \"นัดลูกค้าพรุ่งนี้บ่าย 3\" - บอทอ่านวันที่/เวลา/สถานที่/หมวดหมู่/มอบหมายให้อัตโนมัติ\n"
    "• ส่งข้อความเสียง - พูดนัดหมายได้เลย\n"
    "• \"นัดประจำ ทุก<วัน> <นัดหมาย>\" เช่น \"นัดประจำ ทุกวันจันทร์ 9 โมง ประชุมทีม\" - นัดซ้ำทุกสัปดาห์\n"
    "• ส่งรูปเอกสาร - แนบเข้ากับนัดหมายที่กำลังทำอยู่ ส่งได้หลายรูป (ไม่ได้อ่านเนื้อหาในรูปให้ ต้องพิมพ์/แก้ไขข้อมูลเอง)\n"
    "\n"
    "✅ ยืนยัน/แก้ไข (หลังพิมพ์นัด/ส่งเสียง/ส่งรูป)\n"
    "• \"ยืนยัน\" / \"แนบเอกสาร\" / \"แก้ไขข้อมูล\" / \"ยกเลิก\"\n"
    "• ตอนแก้ไข พิมพ์ตามฟอร์ม เรื่อง/วันที่/เวลา/สถานที่/หมวดหมู่/มอบหมาย บรรทัดไหนไม่พิมพ์จะใช้ค่าเดิม\n"
    "\n"
    "📋 จัดการนัดหมาย\n"
    "• \"รายการนัดหมาย\" - ดูนัดที่กำลังจะถึง พร้อมเลขกำกับ\n"
    "• \"เลื่อนนัด <เลข> เป็น <วันเวลาใหม่>\"\n"
    "• \"ยกเลิกนัด <เลข>\"\n"
    "• \"รายการนัดประจำ\" / \"ยกเลิกนัดประจำ <เลข>\"\n"
    "• สรุปนัดหมายรายเดือน (ทั้งเดือนปัจจุบัน) ส่งอัตโนมัติทุกเช้า 06:55 น.\n"
    "• ตอนแจ้งเตือนถึงเวลานัด มีปุ่ม \"✅ รับทราบแล้ว ไม่ต้องแจ้งซ้ำ\" กดได้ถ้าบอทแจ้งซ้ำผิดพลาด\n"
    "\n"
    "📁 เอกสาร\n"
    "• \"รายการเอกสาร\" - เอกสารล่าสุด 5 รายการ\n"
    "• \"ค้นหา <คำ>\" - ค้นย้อนหลัง (เรื่อง/สถานที่/หมวดหมู่/ผู้รับมอบหมาย)\n"
    "• \"ลบเอกสาร <เลข>\" - ลบรายการที่บันทึกผิด\n"
    "\n"
    "📊 รายงาน\n"
    "• \"รายงานเดือนนี้\" / \"รายงานเดือน YYYY-MM\"\n"
    "• \"รายงานสัปดาห์นี้\"\n"
    "(ทั้งสองแบบส่งอัตโนมัติตามกำหนดด้วย - รายเดือนทุกวันที่ 1, รายสัปดาห์ทุกวันจันทร์)\n"
    "\n"
    "🤖 อื่นๆ\n"
    "• \"ถามบอท: ...\" - ถามอะไรก็ได้\n"
    "• \"คำสั่งบอท\" - ดูข้อความนี้อีกครั้ง\n"
    "\n"
    "หมายเหตุ: ถ้าตั้งนัดเวลาใกล้กับนัดที่มีอยู่แล้ว บอทจะเตือนให้ด้วยครับ"
)


def parse_manual_edit(text):
    fields = {}
    for key, pattern in EDIT_FIELD_PATTERNS.items():
        match = re.search(pattern, text)
        if match:
            fields[key] = match.group(1).strip()
    return fields


def reply_documents(reply_token, user_id, docs, empty_text, alt_text):
    """Shared by "รายการเอกสาร" and "ค้นหา ..." - shows results as a Flex
    carousel (photo, category, subject, date) instead of a text list of
    links, numbered so "ลบเอกสาร <เลข>" can reference an entry. Caches the
    shown numbering into list_cache under the "documents" namespace."""
    list_cache.set_list(user_id, [d["id"] for d in docs], namespace="documents")

    if not docs:
        reply(reply_token, empty_text)
        return

    card = flex_messages.document_carousel(docs, alt_text=alt_text)
    with ApiClient(_line_configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[card, TextMessage(text='พิมพ์ "ลบเอกสาร <เลข>" เพื่อลบรายการที่บันทึกผิดครับ')],
            )
        )


def upcoming_reminders_text(user_id):
    """Lists upcoming confirmed reminders and caches the shown numbering so
    "เลื่อนนัด <เลข>" / "ยกเลิกนัด <เลข>" can resolve back to a real id."""
    reminders = db.list_upcoming_reminders(limit=10)
    list_cache.set_list(user_id, [r["id"] for r in reminders], namespace="reminders")

    if not reminders:
        return "ไม่มีนัดหมายที่กำลังจะถึงครับ"

    lines = ["นัดหมายที่กำลังจะถึง:"]
    for i, r in enumerate(reminders, start=1):
        loc = f" @ {r['location']}" if r.get("location") else ""
        cat = f" [{r['category']}]" if r.get("category") else ""
        who = f" (มอบหมาย: {r['assignee']})" if r.get("assignee") else ""
        lines.append(f"{i}. {r['subject'] or 'การประชุม'} — {date_fmt.to_thai_datetime(r['remind_at'])}{loc}{cat}{who}")
    lines.append("")
    lines.append('พิมพ์ "เลื่อนนัด <เลข> เป็น <วันเวลาใหม่>" หรือ "ยกเลิกนัด <เลข>" เพื่อจัดการครับ')
    return "\n".join(lines)


def recurring_rules_text(user_id):
    rules = db.list_recurring_rules(active_only=True)
    list_cache.set_list(user_id, [r["id"] for r in rules], namespace="recurring")

    if not rules:
        return 'ยังไม่มีนัดหมายประจำครับ ตั้งได้โดยพิมพ์ เช่น "นัดประจำ ทุกวันจันทร์ 9 โมง ประชุมทีม"'

    lines = ["นัดหมายประจำที่ตั้งไว้:"]
    for i, r in enumerate(rules, start=1):
        loc = f" @ {r['location']}" if r.get("location") else ""
        cat = f" [{r['category']}]" if r.get("category") else ""
        who = f" (มอบหมาย: {r['assignee']})" if r.get("assignee") else ""
        lines.append(f"{i}. ทุก{WEEKDAY_NAMES[r['weekday']]} {r['time_str']} - {r['subject'] or 'การประชุม'}{loc}{cat}{who}")
    lines.append("")
    lines.append('พิมพ์ "ยกเลิกนัดประจำ <เลข>" เพื่อยกเลิกครับ')
    return "\n".join(lines)


def collision_warning_text(remind_at, exclude_id=None):
    """Returns a warning string (or "" if no conflicts) listing other
    upcoming reminders within COLLISION_WINDOW_MINUTES of remind_at -
    appended to the confirm/reschedule reply, doesn't block creation."""
    nearby = db.get_reminders_near(
        remind_at, window_minutes=config.COLLISION_WINDOW_MINUTES, exclude_id=exclude_id
    )
    if not nearby:
        return ""
    lines = ["\n⚠️ มีนัดหมายใกล้เคียงเวลานี้อยู่แล้ว:"]
    for r in nearby:
        loc = f" @ {r['location']}" if r.get("location") else ""
        lines.append(f"- {r['subject'] or 'การประชุม'} — {date_fmt.to_thai_datetime(r['remind_at'])}{loc}")
    return "\n".join(lines)


def report_reply_text(year, month):
    """Returns (text, page_url) - page_url is None if there's nothing to
    screenshot (failed/empty report, or BASE_URL not configured)."""
    label = report.month_label(year, month)
    url, page_url, count = report.generate_monthly_report(year, month)
    if not url:
        return f"ไม่สามารถสร้างรายงานเดือน{label}ได้ครับ (เช็คว่าตั้งค่า R2 ไว้ถูกต้องหรือยัง)", None
    if count == 0:
        return f"เดือน{label}ยังไม่มีเอกสารเข้ามาเลยครับ", None
    if page_url:
        return f"รายงานเดือน{label} ({count} รายการ)\n{page_url}\n\nไฟล์ Excel: {url}", page_url
    return f"รายงานเดือน{label} ({count} รายการ)\n{url}", None


def weekly_report_reply_text(monday):
    """Returns (text, page_url) - see report_reply_text."""
    label = report.week_label(monday)
    url, page_url, count = report.generate_weekly_report(monday)
    if not url:
        return f"ไม่สามารถสร้างรายงานสัปดาห์ {label} ได้ครับ (เช็คว่าตั้งค่า R2 ไว้ถูกต้องหรือยัง)", None
    if count == 0:
        return f"สัปดาห์ {label} ยังไม่มีเอกสารเข้ามาเลยครับ", None
    if page_url:
        return f"รายงานสัปดาห์ {label} ({count} รายการ)\n{page_url}\n\nไฟล์ Excel: {url}", page_url
    return f"รายงานสัปดาห์ {label} ({count} รายการ)\n{url}", None


def log_source(event):
    """Print the group/room ID of any non-1:1 message so it can be
    captured once and pasted into .env as EMPLOYEE_GROUP_ID."""
    source_type = getattr(event.source, "type", "user")
    if source_type == "group":
        print(f"[DEBUG] message from GROUP id = {event.source.group_id}")
    elif source_type == "room":
        print(f"[DEBUG] message from ROOM id = {event.source.room_id}")
    return source_type


def reply(reply_token, text, quick_reply=None):
    with ApiClient(_line_configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text, quick_reply=quick_reply)],
            )
        )


def reply_flex(reply_token, flex_message):
    with ApiClient(_line_configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=[flex_message])
        )


def reply_confirmation_card(reply_token, pending):
    """The styled Flex version of summary_text() - ยืนยัน/แก้ไขข้อมูล/ยกเลิก
    are buttons on the card itself, so no separate quick-reply chip row."""
    card = flex_messages.confirmation_card(pending)
    reply_flex(reply_token, card)


def reply_report(reply_token, text, page_url):
    """Sends the report text, and - if page_url is set - a screenshot of
    the web report page (report_page.py) as a second message, via the free
    thum.io URL-to-image API (see screenshot.py). No image if BASE_URL
    isn't configured (page_url is None) or the report was empty/failed."""
    messages = [TextMessage(text=text)]
    if page_url:
        original_url, preview_url = screenshot.report_screenshot_urls(page_url)
        messages.append(
            ImageMessage(original_content_url=original_url, preview_image_url=preview_url)
        )
    with ApiClient(_line_configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=messages)
        )


def confirm_edit_cancel_quick_reply():
    return QuickReply(
        items=[
            QuickReplyItem(action=MessageAction(label="ยืนยัน", text="ยืนยัน")),
            QuickReplyItem(action=MessageAction(label="แก้ไขข้อมูล", text="แก้ไขข้อมูล")),
            QuickReplyItem(action=MessageAction(label="ยกเลิก", text="ยกเลิก")),
        ]
    )


def summary_text(pending):
    return (
        "ข้อมูลตอนนี้เป็นดังนี้ครับ\n"
        f"เรื่อง: {pending.get('subject') or '-'}\n"
        f"วันที่: {date_fmt.to_thai_date(pending.get('meeting_date')) or '-'}\n"
        f"เวลา: {pending.get('meeting_time') or '-'}\n"
        f"สถานที่: {pending.get('location') or '-'}\n"
        f"หมวดหมู่: {pending.get('category') or '-'}\n"
        f"มอบหมาย: {pending.get('assignee') or '-'}\n\n"
        "ยืนยันตั้งการแจ้งเตือนนี้ไหมครับ? (หรือกด \"แก้ไขข้อมูล\" ถ้าอ่านผิด)"
    )


@app.route("/health", methods=["GET"])
@app.route("/", methods=["GET"])
def health():
    """Plain 200 OK for uptime pingers (e.g. UptimeRobot) to hit every few
    minutes - keeps a free-tier host that sleeps on inactivity (like
    Render's Hobby plan) awake so scheduled reminders keep firing on time."""
    return "OK"


@app.route("/report/<token>", methods=["GET"])
def report_page_view(token):
    """The web summary page report.py's generate_*_report() links to (see
    report_page.py for the HTML builder). token-gated, no login - anyone
    with the link can view it, same as the R2 document links."""
    page = db.get_report_page(token)
    if not page:
        return "ไม่พบรายงานนี้ครับ (ลิงก์อาจพิมพ์ผิดหรือไม่ถูกต้อง)", 404

    documents = db.get_documents_in_range(page["start_date"], page["end_date"])
    return report_page.render_report_html(page["label"], documents, page["xlsx_url"])


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    """No AI reading of the photo (see module docstring) - just uploads it
    to R2 and attaches it to whatever nadd is already in progress (from
    "นัด..." text, a voice message, or prior photos). Photos accumulate -
    each one sent while a nadd is in progress gets appended to the list of
    attached images (not replaced), so multiple pages/photos of the same
    document can all ride along with one appointment. Shown in the
    confirmation card / reminder push as a hero photo plus a thumbnail
    strip for the rest. If there's no nadd in progress yet, starts a blank
    one with just the photo attached, for the officer to fill in by hand
    via "แก้ไขข้อมูล"."""
    log_source(event)
    user_id = event.source.user_id
    message_id = event.message.id

    with ApiClient(_line_configuration) as api_client:
        image_bytes = MessagingApiBlob(api_client).get_message_content(message_id)

    image_url = upload_image(image_bytes)
    pending = db.get_pending(user_id) or {}

    subject = pending.get("subject")
    meeting_date = pending.get("meeting_date")
    meeting_time = pending.get("meeting_time")
    location = pending.get("location")
    category = pending.get("category")
    assignee = pending.get("assignee")
    image_urls = (pending.get("image_urls") or []) + [image_url]

    # Archive this nadd as ONE documents row, updated in place as more
    # photos/edits come in (instead of a fresh row per touch) - so texting,
    # then attaching 3 photos, shows up as one entry in "รายการเอกสาร"/
    # reports, not four near-duplicates.
    document_id = db.upsert_draft_document(
        document_id=pending.get("draft_document_id"),
        user_id=user_id,
        subject=subject,
        meeting_date=meeting_date,
        meeting_time=meeting_time,
        location=location,
        image_urls=image_urls,
        category=category,
        assignee=assignee,
    )

    db.save_pending(
        user_id=user_id,
        subject=subject,
        meeting_date=meeting_date,
        meeting_time=meeting_time,
        location=location,
        image_urls=image_urls,
        category=category,
        assignee=assignee,
        draft_document_id=document_id,
    )

    pending = db.get_pending(user_id)
    reply_confirmation_card(event.reply_token, pending)


@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio(event):
    log_source(event)
    user_id = event.source.user_id
    message_id = event.message.id

    if not voice_extract.is_available():
        reply(
            event.reply_token,
            "ฟีเจอร์นัดหมายด้วยเสียงยังไม่เปิดใช้งานครับ ต้องตั้งค่า GEMINI_API_KEY ใน .env ก่อน (ดู README)",
        )
        return

    with ApiClient(_line_configuration) as api_client:
        audio_bytes = MessagingApiBlob(api_client).get_message_content(message_id)

    info = voice_extract.extract_meeting_info_from_audio(audio_bytes) or {}
    if not info.get("date") or not info.get("time"):
        reply(
            event.reply_token,
            "ฟังไม่ออกว่านัดหมายวันเวลาไหนครับ ลองพูดใหม่ให้ชัดขึ้น หรือพิมพ์แทนก็ได้",
        )
        return

    pending = db.get_pending(user_id) or {}

    document_id = db.upsert_draft_document(
        document_id=pending.get("draft_document_id"),
        user_id=user_id,
        subject=info.get("subject"),
        meeting_date=info.get("date"),
        meeting_time=info.get("time"),
        location=info.get("location"),
        # image_urls omitted - preserves any photos already attached.
        category=info.get("category"),
        assignee=info.get("assignee"),
    )
    db.save_pending(
        user_id=user_id,
        subject=info.get("subject"),
        meeting_date=info.get("date"),
        meeting_time=info.get("time"),
        location=info.get("location"),
        # image_urls omitted - preserves any photos already attached (e.g.
        # a photo sent before this voice message), instead of wiping them.
        category=info.get("category"),
        assignee=info.get("assignee"),
        draft_document_id=document_id,
    )
    pending = db.get_pending(user_id)
    reply_confirmation_card(event.reply_token, pending)


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    source_type = log_source(event)
    is_group_chat = source_type in ("group", "room")

    user_id = event.source.user_id
    text = event.message.text.strip()
    pending = db.get_pending(user_id)

    # Scoped AI Q&A: only replies when explicitly asked via "ถามบอท: ...".
    ask_match = re.match(ASK_BOT_PATTERN, text)
    if ask_match:
        answer = ai_chat.ask(ask_match.group(1).strip())
        reply(event.reply_token, answer)
        return

    # Full command reference - always replies, even in a group, since it's
    # an explicit request (unlike the silent-fallback behavior below).
    if text == "คำสั่งบอท":
        reply(event.reply_token, BOT_HELP_TEXT)
        return

    if text == "รายการเอกสาร":
        docs = db.get_recent_documents(limit=5)
        reply_documents(
            event.reply_token, user_id, docs,
            empty_text="ยังไม่มีเอกสารที่ส่งเข้ามาครับ",
            alt_text="เอกสารล่าสุด 5 รายการ",
        )
        return

    search_match = re.match(SEARCH_PATTERN, text)
    if search_match:
        keyword = search_match.group(1).strip()
        docs = db.search_documents(keyword)
        reply_documents(
            event.reply_token, user_id, docs,
            empty_text=f'ไม่พบเอกสารที่เกี่ยวกับ "{keyword}" ครับ',
            alt_text=f'ผลค้นหา "{keyword}" ({len(docs)} รายการ)',
        )
        return

    delete_document_match = re.match(DELETE_DOCUMENT_PATTERN, text)
    if delete_document_match:
        index = int(delete_document_match.group(1))
        document_id = list_cache.resolve(user_id, index, namespace="documents")
        if not document_id:
            reply(
                event.reply_token,
                'ไม่พบเอกสารหมายเลขนี้ครับ พิมพ์ "รายการเอกสาร" หรือ "ค้นหา ..." เพื่อดูรายการล่าสุดก่อน',
            )
            return
        doc = db.get_document(document_id)
        if not doc:
            reply(event.reply_token, "เอกสารนี้ถูกลบไปแล้วครับ")
            return
        db.delete_document(document_id)
        reply(
            event.reply_token,
            f"ลบเอกสาร \"{doc['subject'] or '(ไม่มีชื่อเรื่อง)'}\" ออกจากคลังแล้วครับ "
            "(นัดหมายที่ยืนยันไปแล้วจากเอกสารนี้ ถ้ามี จะไม่ถูกลบไปด้วย ใช้ \"ยกเลิกนัด <เลข>\" แยกต่างหากครับ)",
        )
        return

    if text == "รายการนัดหมาย":
        reply(event.reply_token, upcoming_reminders_text(user_id))
        return

    if text == "รายการนัดประจำ":
        reply(event.reply_token, recurring_rules_text(user_id))
        return

    recurring_match = re.match(RECURRING_PATTERN, text)
    if recurring_match:
        weekday_word = recurring_match.group(1)
        rest_text = recurring_match.group(2).strip()
        weekday_index = WEEKDAY_TO_INDEX[weekday_word]

        info = extract_recurring_info(rest_text) or {}
        if not info.get("time"):
            reply(
                event.reply_token,
                'ไม่เข้าใจเวลาครับ ลองพิมพ์ใหม่ เช่น "นัดประจำ ทุกวันจันทร์ 9 โมง ประชุมทีม"',
            )
            return

        rule_id = db.create_recurring_rule(
            user_id=user_id,
            subject=info.get("subject"),
            weekday=weekday_index,
            time_str=info["time"],
            location=info.get("location"),
            category=info.get("category"),
            assignee=info.get("assignee"),
        )

        # If today is the matching weekday and the time hasn't passed yet,
        # generate today's instance right away instead of making the user
        # wait a full week for the first reminder.
        now = now_local()
        note = ""
        if now.weekday() == weekday_index and now.strftime("%H:%M") < info["time"]:
            rule = db.get_recurring_rule(rule_id)
            if rule and generate_instance_for_rule(rule, now.strftime("%Y-%m-%d")):
                note = " (รวมถึงวันนี้ด้วยครับ)"

        reply(
            event.reply_token,
            f"ตั้งนัดหมายประจำ \"{info.get('subject') or 'การประชุม'}\" ทุก{weekday_word} เวลา {info['time']} "
            f"เรียบร้อยครับ{note}\nดูรายการทั้งหมดได้ที่ \"รายการนัดประจำ\"",
        )
        return

    cancel_recurring_match = re.match(CANCEL_RECURRING_PATTERN, text)
    if cancel_recurring_match:
        index = int(cancel_recurring_match.group(1))
        rule_id = list_cache.resolve(user_id, index, namespace="recurring")
        if not rule_id:
            reply(
                event.reply_token,
                'ไม่พบนัดหมายประจำหมายเลขนี้ครับ พิมพ์ "รายการนัดประจำ" เพื่อดูรายการล่าสุดก่อน',
            )
            return
        rule = db.get_recurring_rule(rule_id)
        if not rule or not rule.get("active"):
            reply(event.reply_token, "นัดหมายประจำนี้ถูกยกเลิกไปแล้วครับ")
            return
        db.deactivate_recurring_rule(rule_id)
        reply(event.reply_token, f"ยกเลิกนัดหมายประจำ \"{rule['subject'] or 'การประชุม'}\" เรียบร้อยครับ")
        return

    ack_match = re.match(ACK_REMINDER_PATTERN, text)
    if ack_match:
        reminder_id = int(ack_match.group(1))
        reminder = db.get_reminder(reminder_id)
        # Idempotent either way - this exists as a manual escape hatch for
        # the rare case a reminder re-fires after already being handled
        # (e.g. a Render redeploy resetting sent=0 on a stale disk restore,
        # see scheduler.backup_database), not a normal part of the flow.
        db.mark_sent(reminder_id)
        if reminder:
            reply(
                event.reply_token,
                f"รับทราบแล้วครับ ไม่ต้องแจ้งเตือน \"{reminder['subject'] or 'การประชุม'}\" ซ้ำอีก 👍",
            )
        else:
            reply(event.reply_token, "รับทราบแล้วครับ")
        return

    reschedule_match = re.match(RESCHEDULE_PATTERN, text)
    if reschedule_match:
        index = int(reschedule_match.group(1))
        new_time_text = reschedule_match.group(2).strip()
        reminder_id = list_cache.resolve(user_id, index)
        if not reminder_id:
            reply(
                event.reply_token,
                'ไม่พบนัดหมายหมายเลขนี้ครับ พิมพ์ "รายการนัดหมาย" เพื่อดูรายการล่าสุดก่อน',
            )
            return
        reminder = db.get_reminder(reminder_id)
        if not reminder:
            reply(event.reply_token, "นัดหมายนี้ถูกยกเลิกไปแล้วครับ")
            return

        parsed = extract_meeting_info_from_text(new_time_text) or {}
        if not parsed.get("date") or not parsed.get("time"):
            reply(
                event.reply_token,
                'ไม่เข้าใจวันเวลาใหม่ครับ ลองพิมพ์ใหม่ เช่น "เลื่อนนัด 1 เป็น พรุ่งนี้บ่าย 4"',
            )
            return

        new_remind_at = f"{parsed['date']} {parsed['time']}:00"
        db.update_reminder_time(reminder_id, new_remind_at)

        calendar_link = None
        if reminder.get("calendar_event_id"):
            calendar_link = google_calendar.update_event(
                reminder["calendar_event_id"],
                subject=reminder["subject"],
                meeting_date=parsed["date"],
                meeting_time=parsed["time"],
                location=reminder["location"],
            )

        confirm_text = f"เลื่อนนัด \"{reminder['subject'] or 'การประชุม'}\" เป็น {date_fmt.to_thai_datetime(new_remind_at)} เรียบร้อยครับ"
        if calendar_link:
            confirm_text += f"\nอัปเดต Google Calendar แล้ว: {calendar_link}"
        confirm_text += collision_warning_text(new_remind_at, exclude_id=reminder_id)
        reply(event.reply_token, confirm_text)
        return

    cancel_confirmed_match = re.match(CANCEL_CONFIRMED_PATTERN, text)
    if cancel_confirmed_match:
        index = int(cancel_confirmed_match.group(1))
        reminder_id = list_cache.resolve(user_id, index)
        if not reminder_id:
            reply(
                event.reply_token,
                'ไม่พบนัดหมายหมายเลขนี้ครับ พิมพ์ "รายการนัดหมาย" เพื่อดูรายการล่าสุดก่อน',
            )
            return
        reminder = db.get_reminder(reminder_id)
        if not reminder:
            reply(event.reply_token, "นัดหมายนี้ถูกยกเลิกไปแล้วครับ")
            return

        if reminder.get("calendar_event_id"):
            google_calendar.delete_event(reminder["calendar_event_id"])
        db.delete_reminder(reminder_id)
        reply(event.reply_token, f"ยกเลิกนัด \"{reminder['subject'] or 'การประชุม'}\" เรียบร้อยครับ")
        return

    if text == "รายงานเดือนนี้":
        now = now_local()
        report_text, page_url = report_reply_text(now.year, now.month)
        reply_report(event.reply_token, report_text, page_url)
        return

    report_match = re.match(REPORT_MONTH_PATTERN, text)
    if report_match:
        year, month = int(report_match.group(1)), int(report_match.group(2))
        if not (1 <= month <= 12):
            reply(event.reply_token, "เดือนไม่ถูกต้องครับ ใช้รูปแบบ YYYY-MM เช่น 2026-08")
            return
        report_text, page_url = report_reply_text(year, month)
        reply_report(event.reply_token, report_text, page_url)
        return

    if text == "รายงานสัปดาห์นี้":
        today = today_local()
        monday = today - timedelta(days=today.weekday())
        report_text, page_url = weekly_report_reply_text(monday)
        reply_report(event.reply_token, report_text, page_url)
        return

    # Text-based nadd: either a short "นัดลูกค้าพรุ่งนี้บ่าย 3" sentence, or
    # a whole official memo pasted in (see _looks_like_formal_document_text)
    # - both parsed straight into the same pending -> confirm/edit/cancel
    # flow used for photos, so it's archived and synced to Calendar the
    # same way once confirmed.
    if text.startswith("นัด") or _looks_like_formal_document_text(text):
        info = extract_meeting_info_from_text(text) or {}
        if not info.get("date") or not info.get("time"):
            reply(
                event.reply_token,
                "ไม่เข้าใจวันเวลานัดหมายครับ ลองพิมพ์ใหม่ เช่น \"นัดลูกค้าพรุ่งนี้บ่าย 3\"",
            )
            return

        document_id = db.upsert_draft_document(
            document_id=(pending or {}).get("draft_document_id"),
            user_id=user_id,
            subject=info.get("subject"),
            meeting_date=info.get("date"),
            meeting_time=info.get("time"),
            location=info.get("location"),
            # image_urls omitted - preserves any photos already attached.
            category=info.get("category"),
            assignee=info.get("assignee"),
        )
        db.save_pending(
            user_id=user_id,
            subject=info.get("subject"),
            meeting_date=info.get("date"),
            meeting_time=info.get("time"),
            location=info.get("location"),
            # image_urls omitted - preserves any photos already attached.
            category=info.get("category"),
            assignee=info.get("assignee"),
            draft_document_id=document_id,
        )
        pending = db.get_pending(user_id)
        reply_confirmation_card(event.reply_token, pending)
        return

    # User is in the middle of typing a manual correction.
    if pending and pending.get("awaiting_edit"):
        fields = parse_manual_edit(text)
        if not fields:
            reply(
                event.reply_token,
                "ไม่พบข้อมูลที่พิมพ์มาครับ ลองพิมพ์ตามฟอร์มนี้อีกครั้ง:\n\n" + EDIT_TEMPLATE,
            )
            return
        db.save_pending(
            user_id=user_id,
            subject=fields.get("subject", pending["subject"]),
            meeting_date=fields.get("meeting_date", pending["meeting_date"]),
            meeting_time=fields.get("meeting_time", pending["meeting_time"]),
            location=fields.get("location", pending["location"]),
            image_urls=pending.get("image_urls"),
            category=fields.get("category", pending.get("category")),
            assignee=fields.get("assignee", pending.get("assignee")),
            draft_document_id=pending.get("draft_document_id"),
        )
        pending = db.get_pending(user_id)
        reply_confirmation_card(event.reply_token, pending)
        return

    if text == "แก้ไขข้อมูล" and pending:
        db.set_awaiting_edit(user_id, True)
        reply(event.reply_token, EDIT_TEMPLATE)
        return

    if text == "แนบเอกสาร":
        if not pending:
            reply(
                event.reply_token,
                'ยังไม่มีนัดหมายที่กำลังทำอยู่ครับ พิมพ์ "นัด..." หรือส่งรูปเอกสารมาก่อนได้เลยครับ',
            )
            return
        reply(event.reply_token, "ส่งรูปเอกสารที่ต้องการแนบมาได้เลยครับ")
        return

    if text == "ยืนยัน" and pending:
        if not pending["meeting_date"] or not pending["meeting_time"]:
            db.set_awaiting_edit(user_id, True)
            reply(
                event.reply_token,
                "ยังไม่มีวันที่หรือเวลาครับ พิมพ์ข้อมูลตามฟอร์มนี้ก่อน:\n\n" + EDIT_TEMPLATE,
            )
            return

        remind_at = f"{pending['meeting_date']} {pending['meeting_time']}:00"

        calendar_result = google_calendar.create_event(
            subject=pending["subject"],
            meeting_date=pending["meeting_date"],
            meeting_time=pending["meeting_time"],
            location=pending["location"],
        )
        calendar_link = calendar_result.get("htmlLink") if calendar_result else None
        calendar_event_id = calendar_result.get("id") if calendar_result else None

        db.create_reminder(
            user_id=user_id,
            subject=pending["subject"],
            remind_at=remind_at,
            location=pending["location"],
            image_url=pending["image_url"],
            image_urls=pending.get("image_urls"),
            calendar_event_link=calendar_link,
            calendar_event_id=calendar_event_id,
            category=pending.get("category"),
            assignee=pending.get("assignee"),
        )
        db.delete_pending(user_id)

        confirm_text = f"ตั้งเตือนเรียบร้อยครับ จะแจ้งเตือนวันที่ {date_fmt.to_thai_datetime(remind_at)}"
        if calendar_link:
            confirm_text += f"\nเพิ่มลง Google Calendar แล้ว: {calendar_link}"
        confirm_text += collision_warning_text(remind_at)
        reply(event.reply_token, confirm_text)
        return

    if text == "ยกเลิก" and pending:
        db.delete_pending(user_id)
        reply(event.reply_token, "ยกเลิกรายการนี้แล้วครับ")
        return

    # No matching command/pending state. In a group/room, stay silent so
    # the bot doesn't reply to every unrelated message people send each
    # other. In a 1:1 chat, give a helpful nudge.
    if is_group_chat:
        return

    reply(
        event.reply_token,
        "พิมพ์นัดหมายตรงๆ ได้เลยครับ เช่น \"นัดลูกค้าพรุ่งนี้บ่าย 3\" หรือส่งข้อความเสียงพูดนัดหมายก็ได้ ผมจะช่วยอ่านวันเวลาให้\n\n"
        "ถ้ามีรูปเอกสาร ส่งมาได้เลย จะแนบเข้ากับนัดหมายให้ (ไม่ได้อ่านเนื้อหาในรูปให้นะครับ ต้องพิมพ์รายละเอียดเองหรือกด \"แก้ไขข้อมูล\")\n\n"
        "พิมพ์ \"คำสั่งบอท\" เพื่อดูคำสั่งทั้งหมดที่ใช้ได้ครับ",
    )


db.restore_from_backup_if_missing()
db.init_db()
start_scheduler()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
