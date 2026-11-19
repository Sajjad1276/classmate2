from datetime import date

from classmate.domain.dates import jalali_date, persian_weekday


def test_persian_weekday_saturday():
    # 2026-11-21 is a Saturday
    d = date(2026, 11, 21)
    assert persian_weekday(d) == 0


def test_jalali_date_format():
    s = jalali_date(date(2026, 3, 21))
    assert "/" in s
    assert s != "نامشخص"
