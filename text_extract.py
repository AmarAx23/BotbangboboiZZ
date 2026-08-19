"""Parse a Thai chat message into subject/date/time/location/category/
assignee - the text-message sibling of image_extract.py's (deprecated)
photo-based extraction. Uses Claude (same ANTHROPIC_API_KEY as the rest of
the bot).

Handles two shapes of input, both routed here by app.py's trigger check
(text.startswith("นัด") or _looks_like_formal_document_text()):
  - Short casual sentences: "นัดลูกค้าพรุ่งนี้บ่าย 3"
  - Whole official memos pasted in as-is, e.g. starting with "มอบหมายให้
    เวร ฯ 70 ... " then "เรื่อง ร่วมตรวจสอบ..." with the actual date/time
    buried in a paragraph further down, and a separate "ประสานได้ที่..."
    contact person near the bottom who is NOT the assignee."""

import json
import re

import anthropic

from config import ANTHROPIC_API_KEY
from now_local import now_local

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
_MODEL = "claude-haiku-4-5-20251001"

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _fix_buddhist_year(date_str):
    """Deterministic safety net: the model doesn't always reliably subtract
    543 for a Buddhist-era (พ.ศ.) year despite the prompt instruction below
    (confirmed in production - "20 สิงหาคม 2569" came back as "2569-08-20"
    instead of "2026-08-20", silently scheduling the reminder ~543 years in
    the future). Any extracted year over 2400 is unambiguously a Buddhist
    year that slipped through unconverted - a real appointment is never
    that far out - so correct it here in code instead of trusting the
    model to always get it right."""
    if not date_str:
        return date_str
    match = _ISO_DATE_RE.match(date_str)
    if not match:
        return date_str
    year, month, day = match.groups()
    year_int = int(year)
    if year_int > 2400:
        year_int -= 543
        return f"{year_int:04d}-{month}-{day}"
    return date_str

SYSTEM_PROMPT = """คุณคือตัวช่วยแปลงข้อความภาษาไทยที่พูดถึงการนัดหมาย ให้เป็น JSON เท่านั้น ห้ามมีข้อความอื่นนอกเหนือจาก JSON object นี้:

{
  "subject": "ชื่อนัดหมายสั้นๆ กระชับ ไม่เกินประมาณ 15-20 คำ",
  "date": "วันที่นัดหมาย รูปแบบ YYYY-MM-DD หรือ null ถ้าไม่ระบุ",
  "time": "เวลานัดหมาย รูปแบบ HH:MM แบบ 24 ชั่วโมง หรือ null ถ้าไม่ระบุ",
  "location": "สถานที่ ถ้ามีระบุ ไม่งั้นใส่ null",
  "category": "หมวดหมู่แบบสั้นๆ 1-2 คำ เช่น ประชุม, ตรวจสอบ, คำสั่ง, หนังสือเวียน, นัดลูกค้า, อื่นๆ",
  "assignee": "ชื่อ/รหัสผู้ถูกมอบหมายให้ดำเนินการ ถ้าข้อความระบุไว้ชัดเจน (เช่น \"เวร 70\", \"สมชาย\") ไม่งั้นใส่ null"
}

ข้อความที่ต้องแปลงมี 2 แบบ:
1. ประโยคพูดทั่วไป เช่น "นัดลูกค้าพรุ่งนี้บ่าย 3" - ตัดคำว่า "นัด" นำหน้าออกจาก subject
2. หนังสือราชการ/บันทึกที่วางมาทั้งข้อความ มักขึ้นต้นด้วย "มอบหมายให้ ..." ตามด้วย "เรื่อง ..." แล้วมีเหตุผล/วันเวลานัดหมายอยู่ในย่อหน้าถัดมา สำหรับแบบนี้:
   - "subject" ให้ใช้เนื้อหาหลังคำว่า "เรื่อง" เป็นหลัก สรุปให้สั้นลงถ้ายาวเกินไป (ไม่ต้องคัดลอกทั้งย่อหน้า)
   - "assignee" ให้ดูเฉพาะคนที่ระบุหลังคำว่า "มอบหมายให้" เท่านั้น (เช่น "เวร ฯ 70 / เวร ฯ 20" ให้ใส่ "เวร 70, เวร 20") ห้ามใช้ชื่อผู้ประสานงาน/ผู้ให้ข้อมูลเพิ่มเติมที่ปรากฏท้ายเอกสาร (เช่น หลังคำว่า "ประสานได้ที่", "ติดต่อ", "โทรศัพท์") เป็น assignee เด็ดขาด คนละบทบาทกัน
   - "location" ถ้าไม่มีสถานที่ระบุตรงๆ แต่มีชื่อสถานประกอบการ/ร้าน/หน่วยงานที่เป็นเป้าหมาย (เช่น สถานที่ที่จะไปตรวจสอบ) ให้ใช้ชื่อนั้นเป็น location ได้

กติกาตีความเวลา: "บ่าย 3"=15:00, "บ่ายโมง"=13:00, "เที่ยง"=12:00, "เช้า" (ไม่ระบุเวลา)=09:00,
"เย็น" (ไม่ระบุเวลา)=17:00, "ทุ่ม" นับจาก 19:00 (1 ทุ่ม=19:00, 2 ทุ่ม=20:00, ...), "ค่ำ" (ไม่ระบุ)=19:00,
เวลาที่เขียนแบบ "09.30 น." หรือ "9.30 น." (จุดแทนโคลอน แบบหนังสือราชการ) ให้ตีความเป็น 09:30

กติกาตีความวันที่:
- วันที่สัมพัทธ์ (อิงจากวันเวลาปัจจุบันที่ให้มา): "วันนี้", "พรุ่งนี้", "มะรืนนี้", "จันทร์หน้า", "วันศุกร์" ฯลฯ
- วันที่แบบเต็ม เช่น "วันพฤหัสบดีที่ 20 สิงหาคม 2569" - ถ้าปีที่ระบุเป็น พ.ศ. (ตัวเลขมากกว่า 2400) ให้แปลงเป็น ค.ศ. โดยลบ 543 ก่อนแปลงเป็น YYYY-MM-DD

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
    data["date"] = _fix_buddhist_year(data.get("date"))
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
