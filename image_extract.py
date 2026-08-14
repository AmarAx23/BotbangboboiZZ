"""DEPRECATED - AI reading of document photos was removed. It kept
misreading dotted/blank fill-in lines on scanned Thai official documents
(the leader-dot placeholders like "ณ ห้องประชุม ...........") as if they
were the actual subject/location/category/assignee, producing junk "..."
values in the confirmation card. Nothing in this project imports this file
anymore - sending a photo now just uploads it to R2 and attaches it to the
in-progress nadd (or starts a blank one) for manual fill-in; see
app.py's handle_image(). Safe to delete by hand if you want to clean it up.

The extract_meeting_info() function below is left in place for reference/
in case someone wants to revisit AI photo reading later (e.g. with a
prompt that explicitly tells the model to treat dotted/blank lines as
null instead of literal content)."""

import base64
import json
import re

import anthropic

from config import ANTHROPIC_API_KEY

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
_MODEL = "claude-haiku-4-5-20251001"

EXTRACT_PROMPT = """
คุณเป็นผู้ช่วยอ่านหนังสือราชการภาษาไทย จากรูปภาพที่แนบมา ให้สกัดข้อมูลต่อไปนี้ออกมาเป็น JSON เท่านั้น
ห้ามมีข้อความอื่นใดนอกเหนือจาก JSON object นี้:

{
  "subject": "เรื่องของหนังสือ/การประชุม",
  "date": "วันที่ประชุม รูปแบบ YYYY-MM-DD (ถ้าปีในเอกสารเป็น พ.ศ. ให้แปลงเป็น ค.ศ. โดยลบ 543)",
  "time": "เวลาประชุม รูปแบบ HH:MM แบบ 24 ชั่วโมง",
  "location": "สถานที่จัดประชุม",
  "category": "หมวดหมู่เอกสารแบบสั้นๆ 1-2 คำ เลือกให้ใกล้เคียงที่สุด เช่น ประชุม, คำสั่ง, หนังสือเวียน, ประกาศ, อื่นๆ",
  "assignee": "ชื่อผู้ที่เอกสารระบุว่าเป็นผู้รับผิดชอบ/ผู้ได้รับมอบหมาย/ผู้ที่ต้องดำเนินการ ถ้าระบุไว้ชัดเจนในเอกสาร (เช่น เรียน, มอบหมายให้, ผู้รับผิดชอบ) ไม่งั้นใส่ null"
}

ถ้าหาข้อมูลข้อใดไม่เจอในเอกสาร ให้ใส่ค่า null สำหรับข้อนั้น
"""


def extract_meeting_info(image_bytes: bytes, mime_type: str = "image/jpeg"):
    """Returns a dict with subject/date/time/location, or None if parsing failed."""
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    response = _client.messages.create(
        model=_MODEL,
        max_tokens=500,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": EXTRACT_PROMPT},
                ],
            }
        ],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
