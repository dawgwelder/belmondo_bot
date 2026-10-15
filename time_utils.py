"""Application time and canonical timestamps for SQLite text comparisons."""

from datetime import datetime
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def moscow_now() -> datetime:
    return datetime.now(MOSCOW)


def moscow_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(MOSCOW).isoformat(timespec="microseconds")
