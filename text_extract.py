"""Parse a Thai chat message like "นัดลูกค้าพรุ่งนี้บ่าย 3" into
subject/date/time/location - the text-message sibling of image_extract.py's
photo-based extraction. Uses Claude (same ANTHROPIC_API_KEY as the rest of
the bot)."""

import json
import re

import anthropic

from config import ANTHROPIC_API_KEY
from now_local import now_local

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """คุณคือตัวช่วยแปลงข้อความภาษาไทยที่พูดถึงการนัดหมาย ให้เป็น JSON เท่านั้น ห้ามมีข้อความอื่นนอกเหนือจาก JSON object นี้:

{
  "subject": "ชื่อนัดหมายสั้นๆ (ตัดคำว่า \"นัด\" นำหน้าออก)",
  "date": "วันที่นัดหมาย รูปแบบ YYYY-MM-DD หรือ null ถ้าไม่ระบุ",
  "time": "เวลานัดหมาย รูปแบบ HH:MM แบบ 24 ชั่วโมง หรือ null ถ้าไม่ระบุ",
  "location": "สถานที่ ถ้ามีระบุ ไม่งั้นใส่ null",
  "category": "หมวดหมู่แบบสั้นๆ 1-2 คำ เช่น ประชุม, คำสั่ง, หนังสือเวียน, นัดลูกค้า, อื่นๆ",
  "assignee": "ชื่อคนที่ถูกมอบหมาย/รับผิดชอบ ถ้าข้อความระบุไว้ชัดเจน (เช่น \"มอบหมายให้สมชาย\", \"ให้คุณสมหญิงไป...\") ไม่งั้นใส่ null"
}

กติกาตีความเวลา: "บ่าย 3"=15:00, "บ่ายโมง"=13:00, "เที่ยง"=12:00, "เช้า" (ไม่ระบุเวลา)=09:00,
"เย็น" (ไม่ระบุเวลา)=17:00, "ทุ่ม" นับจาก 19:00 (1 ทุ่ม=19:00, 2 ทุ่ม=20:00, ...), "ค่ำ" (ไม่ระบุ)=19:00

กติกาตีความวันที่สัมพัทธ์ (อิงจากวันเวลาปัจจุบันที่ให้มา): "วันนี้", "พรุ่งนี้", "มะรืนนี้", "จันทร์หน้า", "วันศุกร์" ฯลฯ

ถ้าข้อความไม่ได้พูดถึงวันเวลานัดหมายที่ชัดเจนพอ ให้ตอบ {"subject": null, "date": null, "time": null, "location": null, "category": null, "assignee": null}"""


def extract_meeting_info_from_text(text: str):
    """Returns a dict with subject/date/time/location, or None if the
    message wasn't confident enough to contain a real date+time."""
    now_str = now_local().strftime("%A %d %B %Y %H:%M")
    user_message = f'วันเวลาปัจจุบัน: {now_str}\nข้อความ: "{text}"'

    response = _client.messages.create(
        model=_MODEL,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    raw = "".join(block.text for block in response.content if block.type == "text").strip()

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    if not data.get("date") or not data.get("time"):
        return None
    return data


RECURRING_SYSTEM_PROMPT = """คุณคือตัวช่วยแปลงข้อความภาษาไทยที่พูดถึงนัดหมายประจำ (ทุกสัปดาห์) ให้เป็น JSON เท่านั้น
ห้ามมีข้อความอื่นนอกเหนือจาก JSON object นี้ (ข้อความนี้ไม่มีวันที่ที่แน่นอน มีแค่เวลาและเรื่อง):

{
  "time": "เวลา รูปแบบ HH:MM แบบ 24 ชั่วโมง หรือ null ถ้าไม่ระบุ",
  "subject": "ชื่อนัดหมายสั้นๆ",
  "location": "สถานที่ ถ้ามีระบุ ไม่งั้นใส่ null",
  "category": "หมวดหมู่แบบสั้นๆ 1-2 คำ เช่น ประชุม, คำสั่ง, หนังสือเวียน, อื่นๆ",
  "assignee": "ชื่อคนที่ถูกมอบหมาย/รับผิดชอบ ถ้าระบุไว้ชัดเจน (เช่น \"มอบหมายให้สมชาย\") ไม่งั้นใส่ null"
}

กติกาตีความเวลา: "บ่าย 3"=15:00, "บ่ายโมง"=13:00, "9 โมง"=09:00, "เที่ยง"=12:00,
"เช้า" (ไม่ระบุเวลา)=09:00, "เย็น" (ไม่ระบุเวลา)=17:00, "ทุ่ม" นับจาก 19:00, "ค่ำ" (ไม่ระบุ)=19:00

ถ้าไม่ระบุเวลาที่ชัดเจนพอ ให้ตอบ {"time": null, "subject": null, "location": null, "category": null, "assignee": null}"""


def extract_recurring_info(text: str):
    """Parses the remainder of a "นัดประจำ ทุก<วัน> ..." command (weekday
    already stripped out by app.py) into time/subject/location/category.
    Returns None if no usable time was found."""
    response = _client.messages.create(
        model=_MODEL,
        max_tokens=300,
        system=RECURRING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    raw = "".join(block.text for block in response.content if block.type == "text").strip()

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    if not data.get("time"):
        return None
    return data
