"""Transcribe + extract meeting info from a LINE voice message.

Claude's API doesn't accept audio input (as of this bot's build), so this
one feature uses Gemini instead - the same way image_extract.py and
text_extract.py used to before they were switched to Claude. Everything
else in the bot still runs on Claude; this is scoped to voice only.

Requires GEMINI_API_KEY in .env. If it's not set, is_available() returns
False and app.py replies with a friendly "not set up yet" message instead
of crashing."""

import json
import re
from datetime import datetime

from config import GEMINI_API_KEY

_client = None
if GEMINI_API_KEY:
    from google import genai
    from google.genai import types

    _client = genai.Client(api_key=GEMINI_API_KEY)

_MODEL = "gemini-flash-latest"

PROMPT_TEMPLATE = """
คุณเป็นผู้ช่วยฟังข้อความเสียงภาษาไทยที่พูดถึงการนัดหมาย จากไฟล์เสียงที่แนบมา
ให้ถอดความแล้วสกัดข้อมูลออกมาเป็น JSON เท่านั้น ห้ามมีข้อความอื่นนอกเหนือจาก JSON object นี้:

{{
  "subject": "ชื่อนัดหมายสั้นๆ ที่พูดถึง",
  "date": "วันที่นัดหมาย รูปแบบ YYYY-MM-DD หรือ null ถ้าไม่ระบุ",
  "time": "เวลานัดหมาย รูปแบบ HH:MM แบบ 24 ชั่วโมง หรือ null ถ้าไม่ระบุ",
  "location": "สถานที่ ถ้ามีพูดถึง ไม่งั้นใส่ null",
  "category": "หมวดหมู่แบบสั้นๆ 1-2 คำ เช่น ประชุม, คำสั่ง, หนังสือเวียน, อื่นๆ ถ้าพอเดาได้ ไม่งั้นใส่ null",
  "assignee": "ชื่อคนที่ถูกมอบหมาย/รับผิดชอบ ถ้าพูดถึงชัดเจน ไม่งั้นใส่ null"
}}

กติกาตีความเวลา: "บ่าย 3"=15:00, "บ่ายโมง"=13:00, "เที่ยง"=12:00, "เช้า" (ไม่ระบุเวลา)=09:00,
"เย็น" (ไม่ระบุเวลา)=17:00, "ทุ่ม" นับจาก 19:00 (1 ทุ่ม=19:00, 2 ทุ่ม=20:00, ...), "ค่ำ" (ไม่ระบุ)=19:00

วันเวลาปัจจุบันคือ: {now} ให้ใช้อ้างอิงวันที่สัมพัทธ์ (วันนี้/พรุ่งนี้/มะรืนนี้/จันทร์หน้า ฯลฯ)
ห้ามเดาวันที่เป็นปีอื่นหรือวันอื่นที่ไม่สัมพันธ์กับวันเวลาปัจจุบันนี้เด็ดขาด

ถ้าฟังไม่ออกว่าพูดถึงวันเวลานัดหมายที่ชัดเจนพอ ให้ตอบ {{"subject": null, "date": null, "time": null, "location": null, "category": null, "assignee": null}}
"""


def is_available():
    return _client is not None


def extract_meeting_info_from_audio(audio_bytes: bytes, mime_type: str = "audio/m4a"):
    """Returns a dict with subject/date/time/location, or None if
    unavailable / parsing failed / not confident enough."""
    if not _client:
        return None

    now_str = datetime.now().strftime("%A %d %B %Y %H:%M")
    prompt = PROMPT_TEMPLATE.format(now=now_str)

    try:
        response = _client.models.generate_content(
            model=_MODEL,
            contents=[
                types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                prompt,
            ],
        )
    except Exception as exc:
        print(f"[voice_extract] Gemini call failed: {exc}")
        return None

    text = (response.text or "").strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    if not data.get("date") or not data.get("time"):
        return None
    return data
