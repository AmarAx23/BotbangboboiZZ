"""Builds LINE Flex Message cards for:
  - reminder_card: reminder pushes (subject, time, location, category,
    document photo, and a "ดูปฏิทิน" button linking to the Google Calendar
    event) instead of a plain text message + separate image.
  - confirmation_card: the "is this right?" preview shown right after a
    photo/voice message/"นัด..." text (or after editing), with the document
    photo as a hero image and ยืนยัน/แก้ไขข้อมูล/ยกเลิก as in-card buttons.
  - document_carousel: "รายการเอกสาร"/"ค้นหา ..." results as a horizontally
    scrollable set of cards with the actual document photo, instead of a
    plain text list of links.

Kept isolated from scheduler.py/app.py so those files just call the builder
and get back something they can drop straight into a `messages=[...]` list
for reply_message/push_message."""

from linebot.v3.messaging import (
    FlexMessage,
    FlexBubble,
    FlexCarousel,
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


def _image_strip(urls: list, max_thumbs: int = 4):
    """A row of small square thumbnails for photos after the first one
    (which is already shown full-width as the card's hero image). Tapping
    a thumbnail opens that photo full-size. If there are more photos than
    max_thumbs, the last tile shows a "+N" badge instead of overflowing."""
    shown = urls[:max_thumbs]
    contents = [
        FlexImage(
            url=u,
            size="full",
            aspect_ratio="1:1",
            aspect_mode="cover",
            action=URIAction(uri=u, label="เปิดรูป"),
        )
        for u in shown
    ]
    extra = len(urls) - len(shown)
    if extra > 0:
        contents.append(
            FlexBox(
                layout="vertical",
                background_color="#00000066",
                corner_radius="4px",
                justify_content="center",
                align_items="center",
                contents=[FlexText(text=f"+{extra}", color="#ffffff", size="sm", weight="bold")],
            )
        )
    return FlexBox(layout="horizontal", spacing="xs", margin="md", contents=contents)


def reminder_card(
    header_text: str,
    subject: str,
    remind_at: str,
    location: str = None,
    category: str = None,
    assignee: str = None,
    image_url: str = None,
    image_urls: list = None,
    calendar_link: str = None,
    alt_text: str = None,
):
    """header_text: short label shown above the subject, e.g. "🔔 แจ้งเตือน"
    or "⏰ อีก 15 นาที ถึงเวลานัดหมาย". remind_at: already-formatted string,
    e.g. "2026-08-14 14:00:00". image_urls: every photo attached to this
    reminder (preferred over the singular image_url when both are given) -
    the first is shown as the hero image, the rest as a thumbnail strip.
    Returns a FlexMessage ready to push."""
    images = image_urls or ([image_url] if image_url else [])

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
    if len(images) > 1:
        body_rows.append(_image_strip(images[1:]))

    bubble_kwargs = {
        "body": FlexBox(layout="vertical", contents=body_rows),
    }

    if images:
        bubble_kwargs["hero"] = FlexImage(
            url=images[0],
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
    message/"นัด..." text, or after editing - with the attached document
    photo(s), if any, as a hero image plus a thumbnail strip for the rest,
    and ยืนยัน/แก้ไขข้อมูล/ยกเลิก as real buttons.
    quick_reply: an optional QuickReply to also attach (kept for people who
    prefer tapping the chip row over scrolling to the card's buttons)."""
    subject = pending.get("subject") or "-"
    meeting_date = date_fmt.to_thai_date(pending.get("meeting_date")) or "-"
    meeting_time = pending.get("meeting_time") or "-"
    location = pending.get("location") or "-"
    category = pending.get("category") or "-"
    assignee = pending.get("assignee") or "-"
    images = pending.get("image_urls") or ([pending["image_url"]] if pending.get("image_url") else [])

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
    if len(images) > 1:
        body_rows.append(_image_strip(images[1:]))

    attach_label = "แนบรูปเพิ่ม" if images else "แนบเอกสาร"

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

    if images:
        bubble_kwargs["hero"] = FlexImage(
            url=images[0],
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


def _document_bubble(index: int, doc: dict):
    subject = doc.get("subject") or "(ไม่มีชื่อเรื่อง)"
    date_part = date_fmt.to_thai_date(doc.get("meeting_date")) or "-"
    category = doc.get("category")
    assignee = doc.get("assignee")
    images = doc.get("image_urls") or ([doc.get("image_url")] if doc.get("image_url") else [])
    image_url = images[0] if images else None

    header = FlexBox(
        layout="horizontal",
        padding_all="12px",
        contents=[
            FlexText(text=f"#{index}", size="xs", color="#aaaaaa", weight="bold"),
            *(
                [FlexText(text=category, size="xs", color=_ACCENT_COLOR, weight="bold", align="end")]
                if category
                else []
            ),
        ],
    )

    body_rows = [
        FlexText(text=subject, size="md", weight="bold", wrap=True, max_lines=3),
        FlexText(text=date_part, size="xs", color="#aaaaaa", margin="sm"),
    ]
    if assignee:
        body_rows.append(FlexText(text=f"มอบหมาย: {assignee}", size="xs", color="#aaaaaa"))
    if not image_url:
        body_rows.append(FlexText(text="(ไม่มีรูปแนบ)", size="xs", color="#cccccc", margin="sm"))
    elif len(images) > 1:
        body_rows.append(_image_strip(images[1:]))

    bubble_kwargs = {
        "header": header,
        "body": FlexBox(layout="vertical", spacing="xs", contents=body_rows),
    }

    if image_url:
        bubble_kwargs["hero"] = FlexImage(url=image_url, size="full", aspect_ratio="20:13", aspect_mode="cover")
        bubble_kwargs["footer"] = FlexBox(
            layout="vertical",
            contents=[
                FlexButton(
                    style="link",
                    height="sm",
                    action=URIAction(label="เปิดรูปเต็ม", uri=image_url),
                )
            ],
        )

    return FlexBubble(**bubble_kwargs)


def document_carousel(documents: list, alt_text: str):
    """documents: list of dicts from db.get_recent_documents/search_documents
    (already in display order - the returned carousel's card order is what
    list_cache should be keyed to for "ลบเอกสาร <เลข>"). Max 10 bubbles per
    LINE's carousel limit, which matches the existing query limits."""
    bubbles = [_document_bubble(i, doc) for i, doc in enumerate(documents, start=1)]
    return FlexMessage(alt_text=alt_text, contents=FlexCarousel(contents=bubbles))
