"""Расчёт статистики для мини-апа. Чистые функции: entries на входе, цифры на выходе.

Все деньги — «чистыми»: доходы минус расходы за период. День считается
«сменой», если в нём есть хотя бы один доход-чай.
"""
from datetime import date, datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

ACH_WHALE_ORDER = 10_000   # «Кит»: чек заказа от
ACH_GENEROUS_PCT = 15      # «Щедрость»: чай от %
ACH_BOTTOM_TIPS = 500      # «Дно»: чай за смену меньше


def _entry_date(e: dict) -> date:
    dt = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00"))
    return dt.astimezone(MSK).date()


def _net(entries: list[dict]) -> float:
    return round(sum(float(e["signed_amount"]) for e in entries if e["kind"] != "adjustment"), 2)


def _tips(entries: list[dict]) -> float:
    return round(sum(
        float(e["signed_amount"]) for e in entries
        if e["kind"] == "income" and e["category"] == "Чаевые"
    ), 2)


def _income(entries: list[dict]) -> float:
    return round(sum(float(e["signed_amount"]) for e in entries if e["kind"] == "income"), 2)


def _spent(entries: list[dict]) -> float:
    return round(-sum(float(e["signed_amount"]) for e in entries if e["kind"] == "expense"), 2)


def _in_period(entries: list[dict], start: date, end: date | None = None) -> list[dict]:
    return [e for e in entries if start <= _entry_date(e) and (end is None or _entry_date(e) <= end)]


def _shift_days(entries: list[dict]) -> dict[date, float]:
    """Дни, в которые был чай → сумма чая за день."""
    days: dict[date, float] = {}
    for e in entries:
        if e["kind"] == "income" and e["category"] == "Чаевые":
            d = _entry_date(e)
            days[d] = days.get(d, 0) + float(e["signed_amount"])
    return days


def _day_net(entries: list[dict]) -> dict[date, float]:
    days: dict[date, float] = {}
    for e in entries:
        if e["kind"] == "adjustment":
            continue
        d = _entry_date(e)
        days[d] = days.get(d, 0) + float(e["signed_amount"])
    return days


def compute_stats(entries: list[dict], today: date | None = None, shift_goal: float | None = None) -> dict:
    today = today or datetime.now(MSK).date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    today_entries = _in_period(entries, today)
    month_entries = _in_period(entries, month_start)
    prev_month_entries = _in_period(entries, prev_month_start, prev_month_end)

    # Главные цифры: чистыми за периоды
    summary = {
        "today_net": _net(today_entries),
        "today_income": _income(today_entries),
        "today_spent": _spent(today_entries),
        "week_net": _net(_in_period(entries, week_start)),
        "month_net": _net(month_entries),
        "total_net": _net(entries),
    }

    # План смены
    goal = float(shift_goal) if shift_goal else None
    summary["shift_goal"] = goal
    summary["goal_pct"] = round(min(summary["today_income"] / goal, 1.0) * 100) if goal else None

    # Средний процент чая за месяц
    pcts = [float(e["tip_percent"]) for e in month_entries if e.get("tip_percent")]
    summary["avg_tip_pct"] = round(sum(pcts) / len(pcts), 1) if pcts else None

    # Смены месяца и сравнение с прошлым
    shifts = _shift_days(month_entries)
    summary["shifts_count"] = len(shifts)
    month_tips = sum(shifts.values())
    prev_tips = sum(_shift_days(prev_month_entries).values())
    summary["vs_prev_month_pct"] = (
        round((month_tips - prev_tips) / prev_tips * 100) if prev_tips > 0 else None
    )

    # Чай по дням недели (среднее за смену в этот день недели, весь журнал)
    all_shifts = _shift_days(entries)
    by_weekday: list[list[float]] = [[] for _ in range(7)]
    for d, tips in all_shifts.items():
        by_weekday[d.weekday()].append(tips)
    summary["weekday_avg_tips"] = [
        round(sum(v) / len(v)) if v else 0 for v in by_weekday
    ]

    # Достижения (за месяц; серия — на текущий момент)
    summary["achievements"] = {
        "whale": sum(
            1 for e in month_entries
            if e.get("order_amount") and float(e["order_amount"]) >= ACH_WHALE_ORDER
        ),
        "generous": sum(
            1 for e in month_entries
            if e.get("tip_percent") and float(e["tip_percent"]) >= ACH_GENEROUS_PCT
        ),
        "streak": _current_streak(entries, today),
        "bottom": sum(1 for tips in shifts.values() if tips < ACH_BOTTOM_TIPS),
    }

    # Теплокарта: последние 28 дней, чай по дням
    heatmap = []
    for i in range(27, -1, -1):
        d = today - timedelta(days=i)
        heatmap.append({"date": d.isoformat(), "tips": round(all_shifts.get(d, 0))})
    summary["heatmap"] = heatmap

    return summary


def _current_streak(entries: list[dict], today: date) -> int:
    """Сколько последних смен подряд закрыты в плюс (чистыми > 0)."""
    day_net = _day_net(entries)
    shift_days = sorted(_shift_days(entries).keys(), reverse=True)
    streak = 0
    for d in shift_days:
        if d > today:
            continue
        if day_net.get(d, 0) > 0:
            streak += 1
        else:
            break
    return streak
