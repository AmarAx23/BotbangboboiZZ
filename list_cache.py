"""In-memory mapping from a numbered list shown to a user back to real
row ids, so follow-up commands like "เลื่อนนัด 2 เป็น ...", "ยกเลิกนัด 2",
"ลบเอกสาร 3", or "ยกเลิกนัดประจำ 1" can reference an item by the number the
user actually sees.

Keyed by (namespace, LINE userId) so the three separate lists we show
("รายการนัดหมาย" reminders, "รายการเอกสาร"/"ค้นหา" documents, and
"รายการนัดประจำ" recurring rules) don't clobber each other. Not persisted -
if the bot restarts, users just need to re-run the matching list command
before referencing a number, which is a fine trade-off for how short-lived
this mapping is."""

_lists = {}


def set_list(user_id, ids, namespace="reminders"):
    _lists[(namespace, user_id)] = list(ids)


def resolve(user_id, index, namespace="reminders"):
    """index is 1-based, as shown to the user."""
    ids = _lists.get((namespace, user_id))
    if not ids or index < 1 or index > len(ids):
        return None
    return ids[index - 1]
