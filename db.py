"""SQLite storage for pending confirmations, confirmed reminders, the
document archive, and recurring-reminder rules."""

import os
import sqlite3
from contextlib import contextmanager

from config import DATABASE_PATH, BACKUP_ENABLED


def restore_from_backup_if_missing():
    """On hosts with an ephemeral disk (e.g. Render's free tier, which can
    wipe local files on every redeploy), the local reminders.db may not
    exist yet even though we've been running for a while - restore the
    most recent nightly R2 backup (see scheduler.backup_database) before
    init_db() creates a fresh empty one. No-ops if a local file already
    exists (never overwrites live data) or if backups aren't enabled."""
    if not BACKUP_ENABLED:
        return
    if os.path.exists(DATABASE_PATH) and os.path.getsize(DATABASE_PATH) > 0:
        return

    import storage  # imported lazily to avoid a hard dependency at module load

    key = storage.latest_backup_key()
    if not key:
        return

    db_bytes = storage.download_file(key)
    if not db_bytes:
        return

    with open(DATABASE_PATH, "wb") as f:
        f.write(db_bytes)
    print(f"[db] restored {DATABASE_PATH} from R2 backup {key}")


@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_confirmations (
                user_id TEXT PRIMARY KEY,
                subject TEXT,
                meeting_date TEXT,
                meeting_time TEXT,
                location TEXT,
                image_url TEXT,
                awaiting_edit INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                subject TEXT,
                remind_at TEXT NOT NULL,
                location TEXT,
                image_url TEXT,
                sent INTEGER DEFAULT 0,
                advance_notified INTEGER DEFAULT 0,
                calendar_event_link TEXT,
                calendar_event_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                subject TEXT,
                meeting_date TEXT,
                meeting_time TEXT,
                location TEXT,
                image_url TEXT,
                received_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Migrations for DBs created before these columns/tables existed.
        # sqlite has no "ADD COLUMN IF NOT EXISTS", so we just try and
        # swallow the "duplicate column" error.
        for ddl in (
            "ALTER TABLE pending_confirmations ADD COLUMN awaiting_edit INTEGER DEFAULT 0",
            "ALTER TABLE pending_confirmations ADD COLUMN category TEXT",
            "ALTER TABLE reminders ADD COLUMN advance_notified INTEGER DEFAULT 0",
            "ALTER TABLE reminders ADD COLUMN calendar_event_link TEXT",
            "ALTER TABLE reminders ADD COLUMN calendar_event_id TEXT",
            "ALTER TABLE reminders ADD COLUMN category TEXT",
            "ALTER TABLE reminders ADD COLUMN recurring_rule_id INTEGER",
            "ALTER TABLE documents ADD COLUMN category TEXT",
            "ALTER TABLE pending_confirmations ADD COLUMN assignee TEXT",
            "ALTER TABLE reminders ADD COLUMN assignee TEXT",
            "ALTER TABLE documents ADD COLUMN assignee TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column already exists

        # Recurring appointment rules, e.g. "ทุกวันจันทร์ 9 โมง ประชุมทีม".
        # scheduler.py turns these into a real `reminders` row each day the
        # weekday matches, so they show up in "รายการนัดหมาย" and get
        # reminded/synced to Calendar exactly like any other reminder.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recurring_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                subject TEXT,
                weekday INTEGER NOT NULL,
                time_str TEXT NOT NULL,
                location TEXT,
                category TEXT,
                assignee TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        try:
            conn.execute("ALTER TABLE recurring_reminders ADD COLUMN assignee TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists

        # Web report summary pages (report_page.py) - a long random token
        # instead of a sequential id so the URL itself is the only thing
        # that grants access (no login system; see report.py's docstring).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS report_pages (
                token TEXT PRIMARY KEY,
                report_type TEXT,
                label TEXT,
                start_date TEXT,
                end_date TEXT,
                xlsx_url TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def save_pending(user_id, subject, meeting_date, meeting_time, location, image_url, category=None, assignee=None):
    with get_db() as conn:
        if category is None or assignee is None:
            existing = conn.execute(
                "SELECT category, assignee FROM pending_confirmations WHERE user_id = ?", (user_id,)
            ).fetchone()
            if category is None:
                category = existing["category"] if existing else None
            if assignee is None:
                assignee = existing["assignee"] if existing else None
        conn.execute(
            """
            INSERT INTO pending_confirmations
                (user_id, subject, meeting_date, meeting_time, location, image_url, category, assignee, awaiting_edit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                subject=excluded.subject,
                meeting_date=excluded.meeting_date,
                meeting_time=excluded.meeting_time,
                location=excluded.location,
                image_url=excluded.image_url,
                category=excluded.category,
                assignee=excluded.assignee,
                awaiting_edit=0
            """,
            (user_id, subject, meeting_date, meeting_time, location, image_url, category, assignee),
        )


def get_pending(user_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM pending_confirmations WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_pending(user_id):
    with get_db() as conn:
        conn.execute("DELETE FROM pending_confirmations WHERE user_id = ?", (user_id,))


def set_awaiting_edit(user_id, value: bool):
    with get_db() as conn:
        conn.execute(
            "UPDATE pending_confirmations SET awaiting_edit = ? WHERE user_id = ?",
            (1 if value else 0, user_id),
        )


def create_reminder(
    user_id,
    subject,
    remind_at,
    location,
    image_url,
    calendar_event_link=None,
    calendar_event_id=None,
    category=None,
    recurring_rule_id=None,
    assignee=None,
):
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO reminders
                (user_id, subject, remind_at, location, image_url, calendar_event_link,
                 calendar_event_id, category, recurring_rule_id, assignee)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, subject, remind_at, location, image_url,
                calendar_event_link, calendar_event_id, category, recurring_rule_id, assignee,
            ),
        )
        return cur.lastrowid


def get_due_reminders(now_str):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE remind_at <= ? AND sent = 0", (now_str,)
        ).fetchall()
        return [dict(r) for r in rows]


def mark_sent(reminder_id):
    with get_db() as conn:
        conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))


def get_reminders_needing_advance_notice(window_start_str, window_end_str):
    """Reminders whose remind_at falls inside the given window, haven't
    fired yet, and haven't already gotten their advance heads-up."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM reminders
            WHERE remind_at BETWEEN ? AND ?
              AND sent = 0
              AND advance_notified = 0
            """,
            (window_start_str, window_end_str),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_advance_notified(reminder_id):
    with get_db() as conn:
        conn.execute("UPDATE reminders SET advance_notified = 1 WHERE id = ?", (reminder_id,))


def get_today_reminders(date_str):
    """All not-yet-fired reminders whose remind_at falls on date_str (YYYY-MM-DD),
    ordered by time - used for the morning brief."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE remind_at LIKE ? AND sent = 0 ORDER BY remind_at",
            (f"{date_str}%",),
        ).fetchall()
        return [dict(r) for r in rows]


def get_reminders_near(remind_at_str, window_minutes=60, exclude_id=None):
    """Other not-yet-fired reminders whose remind_at is within
    +/-window_minutes of remind_at_str - used for the "นัดชนกัน" warning
    shown on confirm/reschedule. Compares as plain "YYYY-MM-DD HH:MM:SS"
    strings shifted by minutes, so no extra date-parsing libs needed."""
    from datetime import datetime, timedelta

    try:
        center = datetime.strptime(remind_at_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return []

    window_start = (center - timedelta(minutes=window_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    window_end = (center + timedelta(minutes=window_minutes)).strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as conn:
        if exclude_id is not None:
            rows = conn.execute(
                """
                SELECT * FROM reminders
                WHERE remind_at BETWEEN ? AND ?
                  AND sent = 0
                  AND id != ?
                ORDER BY remind_at
                """,
                (window_start, window_end, exclude_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM reminders
                WHERE remind_at BETWEEN ? AND ?
                  AND sent = 0
                ORDER BY remind_at
                """,
                (window_start, window_end),
            ).fetchall()
        return [dict(r) for r in rows]


def log_document(user_id, subject, meeting_date, meeting_time, location, image_url, category=None, assignee=None):
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO documents (user_id, subject, meeting_date, meeting_time, location, image_url, category, assignee)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, subject, meeting_date, meeting_time, location, image_url, category, assignee),
        )
        return cur.lastrowid


def get_document(document_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return dict(row) if row else None


def delete_document(document_id):
    with get_db() as conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))


def get_recent_documents(limit=5):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def search_documents(keyword, limit=10):
    """Search past documents by subject, location, category, or assignee,
    most recent first - used by the "ค้นหา ..." command."""
    with get_db() as conn:
        like = f"%{keyword}%"
        rows = conn.execute(
            """
            SELECT * FROM documents
            WHERE subject LIKE ? OR location LIKE ? OR category LIKE ? OR assignee LIKE ?
            ORDER BY id DESC LIMIT ?
            """,
            (like, like, like, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_documents_in_range(start_date_str, end_date_str):
    """Documents received with meeting_date within [start, end] (inclusive),
    used for the monthly/weekly report."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM documents
            WHERE meeting_date BETWEEN ? AND ?
            ORDER BY meeting_date, meeting_time
            """,
            (start_date_str, end_date_str),
        ).fetchall()
        return [dict(r) for r in rows]


def list_upcoming_reminders(limit=10):
    """Not-yet-fired reminders, soonest first - used by "รายการนัดหมาย"
    and as the source list for reschedule/cancel by index."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE sent = 0 ORDER BY remind_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_reminder(reminder_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return dict(row) if row else None


def delete_reminder(reminder_id):
    with get_db() as conn:
        conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))


def update_reminder_time(reminder_id, new_remind_at):
    """Reschedule: change remind_at and reset advance_notified so the
    15-minutes-before heads-up fires again relative to the new time."""
    with get_db() as conn:
        conn.execute(
            "UPDATE reminders SET remind_at = ?, advance_notified = 0 WHERE id = ?",
            (new_remind_at, reminder_id),
        )


# --- Recurring appointment rules ---

def create_recurring_rule(user_id, subject, weekday, time_str, location=None, category=None, assignee=None):
    """weekday: 0=Monday ... 6=Sunday. time_str: "HH:MM"."""
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO recurring_reminders (user_id, subject, weekday, time_str, location, category, assignee, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (user_id, subject, weekday, time_str, location, category, assignee),
        )
        return cur.lastrowid


def list_recurring_rules(active_only=True):
    with get_db() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM recurring_reminders WHERE active = 1 ORDER BY weekday, time_str"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM recurring_reminders ORDER BY weekday, time_str"
            ).fetchall()
        return [dict(r) for r in rows]


def get_recurring_rule(rule_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM recurring_reminders WHERE id = ?", (rule_id,)
        ).fetchone()
        return dict(row) if row else None


def deactivate_recurring_rule(rule_id):
    with get_db() as conn:
        conn.execute("UPDATE recurring_reminders SET active = 0 WHERE id = ?", (rule_id,))


def create_report_page(token, report_type, label, start_date, end_date, xlsx_url):
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO report_pages (token, report_type, label, start_date, end_date, xlsx_url)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (token, report_type, label, start_date, end_date, xlsx_url),
        )


def get_report_page(token):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM report_pages WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None


def reminder_exists_for_rule_on(rule_id, date_str):
    """True if we've already generated today's instance for this recurring
    rule - keeps the once-a-day scheduler job idempotent even if it runs
    more than once on the same day (e.g. after a restart)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM reminders WHERE recurring_rule_id = ? AND remind_at LIKE ? LIMIT 1",
            (rule_id, f"{date_str}%"),
        ).fetchone()
        return row is not None
