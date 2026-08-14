"""Display-only date formatting: everywhere internally (SQLite storage,
Google Calendar API calls, date-range queries, "นัดประจำ" scheduling, etc.)
keeps using ISO "YYYY-MM-DD" / "YYYY-MM-DD HH:MM:SS" - that's what sorts
correctly and what Calendar/AI parsing expects. These two helpers are only
for the text actually shown to the user in LINE messages, which should
read day-month-year instead."""


def to_thai_date(date_str):
    """"YYYY-MM-DD" -> "DD-MM-YYYY". Returns the input unchanged (including
    None) if it isn't a plain ISO date - callers already handle the
    "value or '-'" fallback for missing dates."""
    if not date_str:
        return date_str
    parts = date_str.strip()[:10].split("-")
    if len(parts) != 3:
        return date_str
    year, month, day = parts
    if not (len(year) == 4 and year.isdigit() and month.isdigit() and day.isdigit()):
        return date_str
    return f"{day}-{month}-{year}"


def to_thai_datetime(remind_at_str):
    """"YYYY-MM-DD HH:MM:SS" -> "DD-MM-YYYY HH:MM". Returns the input
    unchanged if it doesn't look like that format."""
    if not remind_at_str:
        return remind_at_str
    date_part, _, time_part = remind_at_str.partition(" ")
    formatted_date = to_thai_date(date_part)
    if formatted_date == date_part:  # didn't parse as a date - bail out safely
        return remind_at_str
    return f"{formatted_date} {time_part[:5]}" if time_part else formatted_date
