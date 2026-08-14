"""Builds LINE Flex Message cards for:
  - reminder_card: reminder pushes (subject, time, location, category,
    document photo, and a "ดูปฏิทิน" button linking to the Google Calendar
    event) instead of a plain text message + separate image.
  - confirmation_card: the "is this right?" preview shown right after a
    photo/voice message/"นัด..." text (or after editing), with the document
    photo as a hero image and ยืนยัน/แก้ไขข้อมูล/ยกเลิก as in-card buttons.

Kept isolated from scheduler.py/app.py so those files just call the builder
and get back something they can drop straight into a `messages=[...]` list
for reply_message/push_message."""

from linebot.v3.messaging import (
    FlexMessage,
    FlexBubble,
    FlexBox,
    FlexText,
    FlexButton,
    FlexImage,
    FlexSeparator,
    URIAction,
    MessageAction,
)

import date_fmt

_ACCENT_COLOR = "#1DB446"


def _info_row(label: str, value: str):
    return FlexBox(
        layout="baseline",
        spacing="sm",
        contents=[
            FlexText(text=label, color="#aaaaaa", size="sm", flex=2),
            FlexText(text=value, color="#666666", size="sm", flex=5, wrap=True),
        ],
    )


def reminder_card(
    header_text: str,
    subject: str,
    remind_at: str,
    location: str = None,
    category: str = None,
    assignee: str = None,
    image_url: str = None,
    calendar_link: str = None,
    alt_text: str = None,
):
    """header_text: short label shown above the subject, e.g. "🔔 แจ้งเตือน"
    or "⏰ อีก 15 นาที ถึงเวลานัดหมาย". remind_at: already-formatted string,
    e.g. "2026-08-14 14:00:00". Returns a FlexMessage ready to push."""
    body_rows = [
        FlexText(text=header_text, size="sm", color=_ACCENT_COLOR, weight="bold"),
        FlexText(text=subject or "การประชุม", size="lg", weight="bold", wrap=True, margin="sm"),
        FlexSeparator(margin="md"),
        FlexBox(
            layout="vertical",
            margin="md",
            spacing="sm",
            contents=[
                _info_row("เวลา", remind_at),
                *([_info_row("สถานที่", location)] if location else []),
                *([_info_row("หมวดหมู่", category)] if category else []),
                *([_info_row("มอบหมาย", assignee)] if assignee else []),
            ],
        ),
    ]

    bubble_kwargs = {
        "body": FlexBox(layout="vertical", contents=body_rows),
    }

    if image_url:
        bubble_kwargs["hero"] = FlexImage(
            url=image_url,
            size="full",
            aspect_ratio="20:13",
            aspect_mode="cover",
        )

    if calendar_link:
        bubble_kwargs["footer"] = FlexBox(
            layout="vertical",
            spacing="sm",
            contents=[
                FlexButton(
                    style="link",
                    height="sm",
                    action=URIAction(label="ดูปฏิทิน", uri=calendar_link),
                )
            ],
        )

    bubble = FlexBubble(**bubble_kwargs)

    return FlexMessage(
        alt_text=alt_text or f"{header_text}: {subject or 'การประชุม'} เวลา {remind_at}",
        contents=bubble,
    )


def confirmation_card(pending: dict, quick_reply=None):
    """The "ข้อมูลตอนนี้เป็นดังนี้ครับ" preview shown after a photo/voice
    message/"นัด..." text, or after editing - with the document photo (if
    any) as a hero image and ยืนยัน/แก้ไขข้อมูล/ยกเลิก as real buttons.
    quick_reply: an optional QuickReply to also attach (kept for people who
    prefer tapping the chip row over scrolling to the card's buttons)."""
    subject = pending.get("subject") or "-"
    meeting_date = date_fmt.to_thai_date(pending.get("meeting_date")) or "-"
    meeting_time = pending.get("meeting_time") or "-"
    location = pending.get("location") or "-"
    category = pending.get("category") or "-"
    assignee = pending.get("assignee") or "-"

    body_rows = [
        FlexText(text="📋 ตรวจสอบข้อมูลนัดหมาย", size="sm", color=_ACCENT_COLOR, weight="bold"),
        FlexText(text=subject, size="lg", weight="bold", wrap=True, margin="sm"),
        FlexSeparator(margin="md"),
        FlexBox(
            layout="vertical",
            margin="md",
            spacing="sm",
            contents=[
                _info_row("วันที่", meeting_date),
                _info_row("เวลา", meeting_time),
                _info_row("สถานที่", location),
                _info_row("หมวดหมู่", category),
                _info_row("มอบหมาย", assignee),
            ],
        ),
    ]

    attach_label = "เปลี่ยนเอกสาร" if pending.get("image_url") else "แนบเอกสาร"

    bubble_kwargs = {
        "body": FlexBox(layout="vertical", contents=body_rows),
        "footer": FlexBox(
            layout="vertical",
            spacing="sm",
            contents=[
                FlexButton(
                    style="primary",
                    color=_ACCENT_COLOR,
                    height="sm",
                    action=MessageAction(label="ยืนยัน", text="ยืนยัน"),
                ),
                FlexButton(
                    style="secondary",
                    height="sm",
                    action=MessageAction(label=attach_label, text="แนบเอกสาร"),
                ),
                FlexButton(
                    style="secondary",
                    height="sm",
                    action=MessageAction(label="แก้ไขข้อมูล", text="แก้ไขข้อมูล"),
                ),
                FlexButton(
                    style="secondary",
                    height="sm",
                    action=MessageAction(label="ยกเลิก", text="ยกเลิก"),
                ),
            ],
        ),
    }

    if pending.get("image_url"):
        bubble_kwargs["hero"] = FlexImage(
            url=pending["image_url"],
            size="full",
            aspect_ratio="20:13",
            aspect_mode="cover",
        )

    bubble = FlexBubble(**bubble_kwargs)

    return FlexMessage(
        alt_text=f"ตรวจสอบข้อมูลนัดหมาย: {subject}",
        contents=bubble,
        quick_reply=quick_reply,
    )
