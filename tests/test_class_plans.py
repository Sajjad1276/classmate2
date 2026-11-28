from datetime import date, datetime, timedelta, timezone

from classmate.services.class_plans import (
    REMINDER_INTERVAL,
    build_class104_plan_body,
    next_class104_reminder_at,
)


def test_plan_contains_only_the_known_items() -> None:
    body = build_class104_plan_body(date(2026, 11, 28))

    assert "یکشنبه | ۸ آذر ۱۴۰۵" in body
    assert "جغرافیا — مطالعهٔ درس‌های ۲ و ۳" in body
    assert "امتحان زبان انگلیسی" in body
    assert "امتحان دینی" in body
    assert "سه‌شنبه | ۱۰ آذر ۱۴۰۵" in body
    assert "تکلیف یا امتحان تازه‌ای اعلام نشده" in body
    assert "شیمی — تدریس" not in body
    assert "مرور جزوه و مطالعهٔ کتاب تا صفحهٔ ۱۶" in body
    assert "تکمیل ۳ انشا" in body
    assert "سه‌شنبه | ۲۴ آذر ۱۴۰۵" in body
    assert "امتحان عربی" in body
    assert "ریاضی" not in body


def test_daily_reminder_excludes_the_day_it_is_sent_on() -> None:
    # Sunday 8 Azar 1405: on that day, the next reminder must start from Monday.
    body = build_class104_plan_body(date(2026, 11, 29))

    assert "یکشنبه | ۸ آذر ۱۴۰۵" not in body
    assert "دوشنبه | ۹ آذر ۱۴۰۵" in body
    assert "سه‌شنبه | ۱۰ آذر ۱۴۰۵" in body


def test_plan_refresh_hides_passed_dates() -> None:
    body = build_class104_plan_body(date(2026, 12, 1))

    assert "یکشنبه | ۸ آذر ۱۴۰۵" not in body
    assert "دوشنبه | ۹ آذر ۱۴۰۵" not in body
    assert "سه‌شنبه | ۱۰ آذر ۱۴۰۵" not in body
    assert "چهارشنبه | ۱۱ آذر ۱۴۰۵" in body
    assert "امتحان عربی" in body


def test_arabic_exam_remains_after_geometry_date_passes() -> None:
    body = build_class104_plan_body(date(2026, 12, 3))

    assert "امتحان هندسه" not in body
    assert "تکمیل ۳ انشا" not in body
    assert "امتحان عربی" in body


def test_plan_ends_after_arabic_exam_date() -> None:
    assert build_class104_plan_body(date(2026, 12, 16)) == ""


def test_reminder_interval_is_eight_hours() -> None:
    assert REMINDER_INTERVAL == timedelta(hours=8)


def test_next_reminder_is_anchored_to_last_successful_delivery() -> None:
    first_published = datetime(2026, 11, 28, 18, 0, tzinfo=timezone.utc)
    last_sent = datetime(2026, 11, 28, 18, 5, tzinfo=timezone.utc)

    assert next_class104_reminder_at(first_published) == first_published
    assert next_class104_reminder_at(first_published, last_sent) == (
        last_sent + timedelta(hours=8)
    )


def test_plan_body_keeps_tomorrow_until_that_day_arrives() -> None:
    saturday = build_class104_plan_body(date(2026, 11, 28))
    sunday = build_class104_plan_body(date(2026, 11, 29))

    assert "یکشنبه | ۸ آذر ۱۴۰۵" in saturday
    assert "یکشنبه | ۸ آذر ۱۴۰۵" not in sunday
