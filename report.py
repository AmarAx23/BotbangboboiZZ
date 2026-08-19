"""Generate an Excel summary of every document the bot processed (whether
or not it was ever confirmed as a reminder), upload it to R2, and return a
public download link - plus a link to a nicer web summary page
(report_page.py, served at /report/<token>) covering the same data.

Two flavors, sharing the same workbook builder:
  - Monthly: on demand ("รายงานเดือนนี้" / "รายงานเดือน YYYY-MM") or
    automatically by scheduler.py on the configured day each month.
  - Weekly: on demand ("รายงานสัปดาห์นี้") or automatically by scheduler.py
    every Monday, covering the prior Mon-Sun week.

The web page link uses a long random token instead of a sequential id, and
there's no login system - same trust model as the R2 links this bot already
sends (see config.BASE_URL's docstring). Requires BASE_URL to be set; if
it's blank, generate_*_report() just returns page_url=None and callers fall
back to sharing only the raw Excel link, exactly like before this feature
existed."""

import calendar
import io
import secrets
from datetime import date, timedelta

from openpyxl import Workbook
from openpyxl.styles import Font

import db
from config import BASE_URL
from storage import upload_file

THAI_MONTHS = [
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]
THAI_MONTHS_SHORT = [
    "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
]


def _month_range(year: int, month: int):
    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)
    return start.isoformat(), end.isoformat()


def _week_range(any_date: date):
    """Returns (monday, sunday) of the week containing any_date."""
    monday = any_date - timedelta(days=any_date.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _build_workbook(title: str, documents: list):
    wb = Workbook()
    ws = wb.active
    ws.title = "รายงาน"

    header = ["ลำดับ", "เรื่อง", "หมวดหมู่", "มอบหมาย", "วันที่นัด", "เวลานัด", "สถานที่", "วันที่ได้รับเอกสาร", "ลิงก์เอกสาร/รูป"]
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for i, doc in enumerate(documents, start=1):
        images = doc.get("image_urls") or ([doc.get("image_url")] if doc.get("image_url") else [])
        ws.append([
            i,
            doc.get("subject") or "-",
            doc.get("category") or "-",
            doc.get("assignee") or "-",
            doc.get("meeting_date") or "-",
            doc.get("meeting_time") or "-",
            doc.get("location") or "-",
            doc.get("received_at") or "-",
            "\n".join(images) if images else "-",
        ])

    for col_cells in ws.columns:
        length = max((len(str(c.value)) for c in col_cells if c.value is not None), default=10)
        ws.column_dimensions[col_cells[0].column_letter].width = min(length + 2, 60)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_report_page(report_type: str, label: str, start_date: str, end_date: str, xlsx_url: str):
    """Stores a report_pages row and returns the public page URL, or None
    if BASE_URL isn't configured yet."""
    if not BASE_URL:
        return None
    token = secrets.token_urlsafe(24)
    db.create_report_page(token, report_type, label, start_date, end_date, xlsx_url)
    return f"{BASE_URL}/report/{token}"


def generate_monthly_report(year: int, month: int):
    """Returns (xlsx_url, page_url, document_count), or (None, None, 0) if
    the upload fails. page_url is None if BASE_URL isn't configured."""
    start_date, end_date = _month_range(year, month)
    documents = db.get_documents_in_range(start_date, end_date)
    label = month_label(year, month)

    xlsx_bytes = _build_workbook(label, documents)
    key = f"reports/{year:04d}-{month:02d}.xlsx"

    try:
        url = upload_file(
            xlsx_bytes,
            key,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as exc:
        print(f"[report] failed to upload monthly report: {exc}")
        return None, None, 0

    page_url = _make_report_page("monthly", label, start_date, end_date, url)
    return url, page_url, len(documents)


def generate_weekly_report(monday: date):
    """monday: a date that is (or gets normalized to) the Monday of the
    target week. Returns (xlsx_url, page_url, document_count), or
    (None, None, 0) if the upload fails. page_url is None if BASE_URL isn't
    configured."""
    monday, sunday = _week_range(monday)
    label = week_label(monday)
    documents = db.get_documents_in_range(monday.isoformat(), sunday.isoformat())

    xlsx_bytes = _build_workbook(label, documents)
    key = f"reports/week-{monday.isoformat()}.xlsx"

    try:
        url = upload_file(
            xlsx_bytes,
            key,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as exc:
        print(f"[report] failed to upload weekly report: {exc}")
        return None, None, 0

    page_url = _make_report_page("weekly", label, monday.isoformat(), sunday.isoformat(), url)
    return url, page_url, len(documents)


def month_label(year: int, month: int) -> str:
    return f"{THAI_MONTHS[month - 1]} {year + 543}"


def week_label(monday: date) -> str:
    monday, sunday = _week_range(monday)
    if monday.month == sunday.month:
        return f"{monday.day}-{sunday.day} {THAI_MONTHS_SHORT[monday.month - 1]} {monday.year + 543}"
    return (
        f"{monday.day} {THAI_MONTHS_SHORT[monday.month - 1]} - "
        f"{sunday.day} {THAI_MONTHS_SHORT[sunday.month - 1]} {sunday.year + 543}"
    )
