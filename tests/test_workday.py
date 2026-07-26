"""Тесты операционного дня. Запуск: python tests/test_workday.py"""
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stats import compute_stats
from workday import MSK, entry_op_date, op_date, op_day_start, op_day_start_utc_iso


def dt(s):
    return datetime.fromisoformat(s)


def test_evening_is_same_day():
    assert op_date(dt("2026-07-27T22:00:00+03:00")) == date(2026, 7, 27)


def test_after_midnight_belongs_to_previous_day():
    assert op_date(dt("2026-07-28T00:30:00+03:00")) == date(2026, 7, 27)
    assert op_date(dt("2026-07-28T03:15:00+03:00")) == date(2026, 7, 27)


def test_boundary_is_six_in_the_morning():
    assert op_date(dt("2026-07-28T05:59:59+03:00")) == date(2026, 7, 27)
    assert op_date(dt("2026-07-28T06:00:00+03:00")) == date(2026, 7, 28)


def test_month_and_year_rollover():
    assert op_date(dt("2026-08-01T02:00:00+03:00")) == date(2026, 7, 31)
    assert op_date(dt("2026-01-01T01:00:00+03:00")) == date(2025, 12, 31)


def test_other_timezone_is_converted():
    # то же мгновение, что 00:30 МСК → всё ещё вчерашняя смена
    assert entry_op_date("2026-07-27T21:30:00+00:00") == date(2026, 7, 27)


def test_day_start_is_six_msk():
    start = op_day_start(date(2026, 7, 27))
    assert (start.hour, start.minute) == (6, 0)
    assert start.tzinfo == MSK
    assert op_day_start_utc_iso(date(2026, 7, 27)).startswith("2026-07-27T03:00")


def test_night_shift_counts_as_one():
    """Главное, ради чего всё затевалось: смена через полночь — одна смена."""
    entries = [
        {"kind": "income", "account": "cash", "signed_amount": 2000, "category": "Чаевые",
         "created_at": "2026-07-27T22:00:00+03:00"},
        {"kind": "income", "account": "card", "signed_amount": 1500, "category": "Чаевые",
         "created_at": "2026-07-28T01:30:00+03:00"},
    ]
    s = compute_stats(entries, today=date(2026, 7, 27))
    assert s["shifts_count"] == 1
    assert s["today_net"] == 3500
    assert s["avg_shift_tips"] == 3500


def test_night_expense_lands_on_the_shift():
    """Трата, внесённая при закрытии смены в час ночи, — расход той же смены."""
    entries = [
        {"kind": "income", "account": "cash", "signed_amount": 3000, "category": "Чаевые",
         "created_at": "2026-07-27T21:00:00+03:00"},
        {"kind": "expense", "account": "cash", "signed_amount": -400, "category": "Такси",
         "note": "трата смены", "created_at": "2026-07-28T01:00:00+03:00"},
    ]
    s = compute_stats(entries, today=date(2026, 7, 27))
    assert s["today_net"] == 2600
    assert s["today_spent"] == 400
    assert s["shift_spend_month"] == 400


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as ex:
                failed += 1
                print(f"FAIL  {name}: {ex}")
    print("\nFAILED" if failed else "\nALL PASSED")
    sys.exit(1 if failed else 0)
