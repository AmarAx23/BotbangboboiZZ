"""Scoped AI Q&A. Only triggered when a message starts with "ถามบอท:" so
the bot doesn't auto-reply to normal conversation in a group."""

import anthropic

from config import ANTHROPIC_API_KEY

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = (
    "คุณเป็นผู้ช่วย AI อยู่ในกลุ่มแชทงานของเจ้าหน้าที่หน่วยงานราชการ "
    "ตอบคำถามให้สั้น กระชับ สุภาพ ตรงประเด็น และเป็นทางการพอสมควร "
    "ถ้าไม่แน่ใจคำตอบ ให้บอกตามตรงว่าไม่แน่ใจ อย่าเดา"
)


def ask(question: str) -> str:
    response = _client.messages.create(
        model=_MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": question}],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return text or "ขออภัยครับ ตอบคำถามนี้ไม่ได้ในตอนนี้"
