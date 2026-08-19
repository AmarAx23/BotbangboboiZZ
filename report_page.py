"""Renders the public web report summary page - what report.py's
generate_monthly_report()/generate_weekly_report() link to instead of (well,
in addition to) the raw Excel download. Served by app.py's /report/<token>
route.

No login system: the token itself (a long random string, see report.py) is
the only thing gating access, same trust model as the R2 document/image
links the bot already sends. Anyone with the link can view it."""

import html as html_lib

import date_fmt


def _category_counts(documents):
    counts = {}
    for d in documents:
        cat = d.get("category") or "ไม่ระบุหมวดหมู่"
        counts[cat] = counts.get(cat, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _row_html(i: int, doc: dict) -> str:
    subject = html_lib.escape(doc.get("subject") or "-")
    category = html_lib.escape(doc.get("category") or "-")
    assignee = html_lib.escape(doc.get("assignee") or "-")
    date_part = html_lib.escape(date_fmt.to_thai_date(doc.get("meeting_date")) or "-")
    time_part = html_lib.escape(doc.get("meeting_time") or "-")
    location = html_lib.escape(doc.get("location") or "-")
    images = doc.get("image_urls") or ([doc.get("image_url")] if doc.get("image_url") else [])
    if not images:
        doc_link = "-"
    elif len(images) == 1:
        doc_link = f'<a href="{html_lib.escape(images[0])}" target="_blank" rel="noopener">ดูเอกสาร</a>'
    else:
        doc_link = " ".join(
            f'<a href="{html_lib.escape(u)}" target="_blank" rel="noopener">รูป {n}</a>'
            for n, u in enumerate(images, start=1)
        )

    return f"""
        <tr>
          <td>{i}</td>
          <td class="subject-cell">{subject}</td>
          <td><span class="tag">{category}</span></td>
          <td>{assignee}</td>
          <td>{date_part}</td>
          <td>{time_part}</td>
          <td>{location}</td>
          <td>{doc_link}</td>
        </tr>"""


def render_report_html(label: str, documents: list, xlsx_url: str) -> str:
    count = len(documents)
    cat_counts = _category_counts(documents)
    cat_chips = "".join(
        f'<span class="chip">{html_lib.escape(cat)} · {n}</span>' for cat, n in cat_counts
    )
    rows = "".join(_row_html(i, d) for i, d in enumerate(documents, start=1))
    safe_label = html_lib.escape(label)
    safe_xlsx_url = html_lib.escape(xlsx_url)

    empty_state = (
        '<p class="empty">ยังไม่มีเอกสารในช่วงนี้ครับ</p>' if not documents else ""
    )
    table = (
        ""
        if not documents
        else f"""
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th><th>เรื่อง</th><th>หมวดหมู่</th><th>มอบหมาย</th>
                <th>วันที่นัด</th><th>เวลา</th><th>สถานที่</th><th>เอกสาร</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>"""
    )

    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>รายงาน{safe_label}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: 'Sarabun', sans-serif;
    background: #f4f6f8;
    color: #222;
    margin: 0;
    padding: 24px 16px 60px;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #777; font-size: 14px; margin: 0 0 20px; }}
  .summary {{
    background: #fff; border-radius: 12px; padding: 16px 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 20px;
  }}
  .count {{ font-size: 32px; font-weight: 700; color: #1DB446; }}
  .count-label {{ font-size: 14px; color: #777; margin-left: 6px; }}
  .chips {{ margin-top: 12px; }}
  .chip {{
    display: inline-block; background: #eef7f0; color: #1a7f3c;
    font-size: 13px; padding: 4px 10px; border-radius: 999px;
    margin: 0 6px 6px 0;
  }}
  .btn {{
    display: inline-block; background: #1DB446; color: #fff;
    text-decoration: none; font-weight: 600; font-size: 14px;
    padding: 10px 18px; border-radius: 8px; margin-top: 8px;
  }}
  .table-wrap {{
    background: #fff; border-radius: 12px; overflow-x: auto;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }}
  table {{ border-collapse: collapse; width: 100%; font-size: 14px; white-space: nowrap; }}
  th, td {{ text-align: left; padding: 10px 14px; border-bottom: 1px solid #eee; vertical-align: middle; }}
  td.subject-cell {{ white-space: normal; word-break: break-word; max-width: 260px; }}
  th {{ background: #fafafa; color: #555; font-weight: 600; }}
  tr:last-child td {{ border-bottom: none; }}
  .tag {{
    background: #eef2ff; color: #3549b1; padding: 2px 8px;
    border-radius: 999px; font-size: 12px;
  }}
  a {{ color: #1a73e8; }}
  .empty {{ color: #999; text-align: center; padding: 40px 0; }}
  footer {{ text-align: center; color: #aaa; font-size: 12px; margin-top: 24px; }}
</style>
</head>
<body>
  <div class="wrap">
    <h1>รายงาน{safe_label}</h1>
    <p class="sub">สรุปเอกสาร/นัดหมายที่บอทบันทึกไว้ในช่วงนี้</p>

    <div class="summary">
      <span class="count">{count}</span><span class="count-label">รายการ</span>
      <div class="chips">{cat_chips}</div>
      <div><a class="btn" href="{safe_xlsx_url}">ดาวน์โหลดไฟล์ Excel</a></div>
    </div>

    {empty_state}
    {table}

    <footer>สร้างโดยบอทแจ้งเตือนอัตโนมัติ</footer>
  </div>
</body>
</html>"""
