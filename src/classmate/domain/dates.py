"""Pure date helpers for Persian school calendar."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import jdatetime


def persian_weekday(d: date) -> int:
    """Return weekday in Persian school week: 0=Saturday ... 6=Friday."""
    # Python: Monday=0 ... Sunday=6
    # We want Saturday=0
    return (d.weekday() + 2) % 7


def jalali_date(d: date | None) -> str:
    if d is None:
        return "نامشخص"
    j = jdatetime.date.fromgregorian(date=d)
    return f"{j.year}/{j.month:02d}/{j.day:02d}"


def jalali_datetime(dt: datetime, tz_name: str = "Asia/Tehran") -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    local = dt.astimezone(ZoneInfo(tz_name))
    j = jdatetime.datetime.fromgregorian(datetime=local)
    return f"{j.year}/{j.month:02d}/{j.day:02d} {j.hour:02d}:{j.minute:02d}"


def parse_school_date(raw: str) -> date:
    """Accept YYYY/MM/DD or YYYY-MM-DD (Jalali)."""
    raw = raw.strip().replace("-", "/")
    parts = raw.split("/")
    if len(parts) != 3:
        raise ValueError("تاریخ باید به صورت ۱۴۰۵/۰۷/۱۵ باشد")
    y, m, d = (int(p) for p in parts)
    return jdatetime.date(y, m, d).togregorian()


def parse_school_datetime(raw: str, tz_name: str = "Asia/Tehran") -> datetime:
    """Accept 'YYYY/MM/DD HH:MM' Jalali."""
    raw = raw.strip()
    if " " not in raw:
        raise ValueError("زمان باید به صورت ۱۴۰۵/۰۷/۱۵ ۱۸:۳۰ باشد")
    date_part, time_part = raw.rsplit(" ", 1)
    d = parse_school_date(date_part)
    h, mi = (int(x) for x in time_part.split(":"))
    return datetime.combine(d, time(h, mi), tzinfo=ZoneInfo(tz_name))
