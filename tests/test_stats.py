"""Тесты статистики. Запуск: python tests/test_stats.py"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stats import compute_stats

TODAY = date(2026, 7, 20)


def e(day, kind, amount, category="Чаевые", order=None, pct=None):
    return {
        "kind": kind,
        "account": "card",
        "signed_amount": amount,
        "category": category,
        "order_amount": order,
        "tip_percent": pct,
        "created_at": f"2026-07-{day:02d}T18:00:00+03:00",
    }


def test_empty():
    s = compute_stats([], today=TODAY)
    assert s["today_net"] == 0
    assert s["total_net"] == 0
    assert s["shifts_count"] == 0
    assert len(s["heatmap"]) == 28


def test_today_net():
    entries = [
        e(20, "income", 2340),
        e(20, "expense", -680, category="Бар"),
    ]
    s = compute_stats(entries, today=TODAY)
    assert s["today_net"] == 1660
    assert s["today_income"] == 2340
    assert s["today_spent"] == 680


def test_adjustment_not_counted():
    entries = [
        {"kind": "adjustment", "account": "cash", "signed_amount": 5000,
         "category": "Сверка", "created_at": "2026-07-01T10:00:00+03:00"},
        e(20, "income", 1000),
    ]
    s = compute_stats(entries, today=TODAY)
    assert s["total_net"] == 1000


def test_periods():
    entries = [
        e(20, "income", 1000),   # сегодня (пн=20? 20.07.2026 — понедельник)
        e(19, "income", 2000),   # вчера, вс — прошлая неделя
        e(1, "income", 4000),    # начало месяца
    ]
    s = compute_stats(entries, today=TODAY)
    assert s["today_net"] == 1000
    # 20.07.2026 — понедельник, неделя начинается сегодня
    assert s["week_net"] == 1000
    assert s["month_net"] == 7000
    assert s["total_net"] == 7000


def test_goal():
    entries = [e(20, "income", 1660)]
    s = compute_stats(entries, today=TODAY, shift_goal=2000)
    assert s["goal_pct"] == 83
    s2 = compute_stats(entries, today=TODAY, shift_goal=1000)
    assert s2["goal_pct"] == 100


def test_avg_tip_pct():
    entries = [
        e(18, "income", 500, order=7690, pct=7.0),
        e(18, "income", 800, order=12000, pct=16.0),
        e(19, "income", 300, pct=20.0),
        e(19, "income", 100),
    ]
    s = compute_stats(entries, today=TODAY)
    assert s["avg_tip_pct"] == 14.3


def test_weekday_avg():
    entries = [
        e(13, "income", 1000),  # пн
        e(20, "income", 3000),  # пн
        e(17, "income", 5000),  # пт
    ]
    s = compute_stats(entries, today=TODAY)
    assert s["weekday_avg_tips"][0] == 2000
    assert s["weekday_avg_tips"][4] == 5000
    assert s["weekday_avg_tips"][1] == 0


def test_tips_split():
    entries = [
        {"kind": "income", "account": "cash", "signed_amount": 3000, "category": "Чаевые",
         "created_at": "2026-07-18T18:00:00+03:00"},
        {"kind": "income", "account": "card", "signed_amount": 7000, "category": "Чаевые",
         "created_at": "2026-07-19T18:00:00+03:00"},
        {"kind": "income", "account": "card", "signed_amount": 30000, "category": "Зарплата",
         "created_at": "2026-07-19T18:00:00+03:00"},
    ]
    s = compute_stats(entries, today=TODAY)
    assert s["tips_split"] == {"cash": 3000, "card": 7000, "cash_pct": 30}


def test_tips_split_empty():
    s = compute_stats([], today=TODAY)
    assert s["tips_split"]["cash_pct"] is None


def test_tip_pct_daily():
    entries = [
        e(18, "income", 500, pct=7.0),
        e(18, "income", 300, pct=9.0),
        e(19, "income", 400, pct=12.0),
    ]
    s = compute_stats(entries, today=TODAY)
    assert s["tip_pct_daily"] == [
        {"date": "2026-07-18", "pct": 8.0},
        {"date": "2026-07-19", "pct": 12.0},
    ]


def test_month_days_calendar():
    entries = [
        e(1, "income", 1000),
        e(18, "income", 4120),
        e(20, "income", 1660),
    ]
    s = compute_stats(entries, today=TODAY)
    assert len(s["month_days"]) == 31
    assert s["month_days"][0] == {"day": 1, "tips": 1000, "cash": 0, "card": 1000}
    assert s["month_days"][1] == {"day": 2, "tips": 0, "cash": 0, "card": 0}
    assert s["month_days"][17]["tips"] == 4120
    assert s["today_day"] == 20
    assert s["avg_shift_tips"] == round((1000 + 4120 + 1660) / 3)
    assert s["record"] == {"day": 18, "tips": 4120, "weekday": "Сб"}


def test_month_days_cash_card_split():
    # день 1: 300 налом + 700 картой = 1000
    entries = [
        {"kind": "income", "account": "cash", "signed_amount": 300, "category": "Чаевые",
         "order_amount": None, "tip_percent": None, "created_at": "2026-07-01T18:00:00+03:00"},
        {"kind": "income", "account": "card", "signed_amount": 700, "category": "Чаевые",
         "order_amount": None, "tip_percent": None, "created_at": "2026-07-01T19:00:00+03:00"},
    ]
    s = compute_stats(entries, today=TODAY)
    assert s["month_days"][0] == {"day": 1, "tips": 1000, "cash": 300, "card": 700}


def test_weekday_split():
    entries = [
        {"kind": "income", "account": "cash", "signed_amount": 400, "category": "Чаевые",
         "order_amount": None, "tip_percent": None, "created_at": "2026-07-17T18:00:00+03:00"},
        {"kind": "income", "account": "card", "signed_amount": 600, "category": "Чаевые",
         "order_amount": None, "tip_percent": None, "created_at": "2026-07-17T19:00:00+03:00"},
    ]
    s = compute_stats(entries, today=TODAY)
    assert s["weekday_split"][4] == {"cash": 400, "card": 600}  # пятница
    assert s["weekday_split"][0] == {"cash": 0, "card": 0}      # понедельник


def test_avg_pct_delta():
    entries = [
        e(18, "income", 500, pct=9.0),
        {"kind": "income", "account": "card", "signed_amount": 400, "category": "Чаевые",
         "order_amount": None, "tip_percent": 7.0, "created_at": "2026-06-15T18:00:00+03:00"},
    ]
    s = compute_stats(entries, today=TODAY)
    assert s["avg_tip_pct"] == 9.0
    assert s["avg_tip_pct_delta"] == 2.0


def test_shift_spend():
    entries = [
        e(18, "income", 10000),
        {"kind": "expense", "account": "cash", "signed_amount": -600, "category": "Бар",
         "note": "трата смены", "created_at": "2026-07-18T23:00:00+03:00"},
        {"kind": "expense", "account": "card", "signed_amount": -500, "category": "Еда",
         "note": None, "created_at": "2026-07-18T23:00:00+03:00"},
    ]
    s = compute_stats(entries, today=TODAY)
    assert s["shift_spend_month"] == 600
    assert s["shift_spend_pct"] == 6
    assert s["month_tips"] == 10000


def test_best_weekday():
    entries = [
        e(17, "income", 5000),  # пт
        e(13, "income", 1000),  # пн
    ]
    s = compute_stats(entries, today=TODAY)
    assert s["best_weekday"] == "Пт"
    assert compute_stats([], today=TODAY)["best_weekday"] is None


def test_heatmap():
    entries = [e(20, "income", 1500)]
    s = compute_stats(entries, today=TODAY)
    assert s["heatmap"][-1] == {"date": "2026-07-20", "tips": 1500}
    assert s["heatmap"][0]["date"] == "2026-06-23"


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
