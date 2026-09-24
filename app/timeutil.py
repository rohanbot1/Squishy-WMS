"""
UTC time helpers -- the only place "now" and database-timestamp
normalization are defined.

SQLModel 0.0.47 (pinned in requirements.txt) maps plain `datetime` fields
to a UTC column type that rejects naive datetimes on write and returns
timezone-aware UTC on read. Every timestamp this app records is therefore
created with utc_now(), never datetime.utcnow() (naive).

as_utc() exists for the read side of comparisons: rows written before
this change are naive UTC in the database, and an older SQLModel (e.g. an
un-upgraded local venv) returns naive values from SQLite. Comparing one of
those against an aware utc_now() would raise TypeError, so every
"is this expired / has this delay passed" check normalizes the stored
value first. Stored timestamps have always been UTC, so attaching UTC to a
naive one is exact, not a guess.

FinancialRecord.stream_started_at / stream_ended_at are deliberately NOT
UTC -- they're wall-clock times typed into a datetime-local input, stored
naive exactly as entered (see app/models.py).
"""
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
