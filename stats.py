"""Расчёт статистики для мини-апа. Чистые функции: entries на входе, цифры на выходе.

Все деньги — «чистыми»: доходы минус расходы за период. День считается
«сменой», если в нём есть хотя бы один доход-чай.
"""
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


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


def _shift_days_split(entries: list[dict]) -> dict[date, dict]:
    """Дни с чаем → {'cash': сумма, 'card': сумма} за день."""
    days: dict[date, dict] = {}
    for e in entries:
        if e["kind"] == "income" and e["category"] == "Чаевые":
            d = _entry_date(e)
            rec = days.setdefault(d, {"cash": 0.0, "card": 0.0})
            rec[e["account"]] = rec.get(e["account"], 0.0) + float(e["signed_amount"])
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

    # Чай по дням недели (среднее за смену в этот день недели, весь журнал),
    # с разбивкой нал/карта — чтобы каждый график показывал состав чая.
    all_shifts = _shift_days(entries)
    all_split = _shift_days_split(entries)
    by_weekday: list[list[float]] = [[] for _ in range(7)]
    wd_cash: list[list[float]] = [[] for _ in range(7)]
    wd_card: list[list[float]] = [[] for _ in range(7)]
    for d, tips in all_shifts.items():
        by_weekday[d.weekday()].append(tips)
        rec = all_split.get(d, {})
        wd_cash[d.weekday()].append(rec.get("cash", 0.0))
        wd_card[d.weekday()].append(rec.get("card", 0.0))
    summary["weekday_avg_tips"] = [
        round(sum(v) / len(v)) if v else 0 for v in by_weekday
    ]
    summary["weekday_split"] = [
        {
            "cash": round(sum(c) / len(c)) if c else 0,
            "card": round(sum(k) / len(k)) if k else 0,
        }
        for c, k in zip(wd_cash, wd_card)
    ]


    # Календарный ряд месяца: чай по дням с разбивкой нал/карта.
    # Пустые дни = отдых (тоже информация).
    month_shifts = _shift_days(month_entries)
    month_split = _shift_days_split(month_entries)
    days_in_month = monthrange(today.year, today.month)[1]
    summary["month_days"] = [
        {
            "day": day,
            "tips": round(month_shifts.get(today.replace(day=day), 0)),
            "cash": round(month_split.get(today.replace(day=day), {}).get("cash", 0)),
            "card": round(month_split.get(today.replace(day=day), {}).get("card", 0)),
        }
        for day in range(1, days_in_month + 1)
    ]
    summary["today_day"] = today.day
    summary["days_in_month"] = days_in_month

    shift_vals = list(month_shifts.values())
    summary["avg_shift_tips"] = round(sum(shift_vals) / len(shift_vals)) if shift_vals else None
    if shift_vals:
        best_day = max(month_shifts, key=month_shifts.get)
        summary["record"] = {
            "day": best_day.day,
            "tips": round(month_shifts[best_day]),
            "weekday": WEEKDAYS[best_day.weekday()],
        }
    else:
        summary["record"] = None

    # Дельта среднего процента к прошлому месяцу
    prev_pcts = [float(e["tip_percent"]) for e in prev_month_entries if e.get("tip_percent")]
    if pcts and prev_pcts:
        summary["avg_tip_pct_delta"] = round(
            summary["avg_tip_pct"] - sum(prev_pcts) / len(prev_pcts), 1
        )
    else:
        summary["avg_tip_pct_delta"] = None

    # Траты смен за месяц и их доля от чая
    shift_spend = -sum(
        float(e["signed_amount"]) for e in month_entries
        if e["kind"] == "expense" and e.get("note") == "трата смены"
    )
    summary["shift_spend_month"] = round(shift_spend)
    summary["month_tips"] = round(month_tips)
    summary["shift_spend_pct"] = round(shift_spend / month_tips * 100) if month_tips > 0 else None

    # Лучший день недели (по среднему чаю за смену, весь журнал)
    wa = summary["weekday_avg_tips"]
    summary["best_weekday"] = WEEKDAYS[wa.index(max(wa))] if max(wa) > 0 else None
    cash_tips = sum(
        float(e["signed_amount"]) for e in month_entries
        if e["kind"] == "income" and e["category"] == "Чаевые" and e["account"] == "cash"
    )
    card_tips = sum(
        float(e["signed_amount"]) for e in month_entries
        if e["kind"] == "income" and e["category"] == "Чаевые" and e["account"] == "card"
    )
    tips_total = cash_tips + card_tips
    summary["tips_split"] = {
        "cash": round(cash_tips),
        "card": round(card_tips),
        "cash_pct": round(cash_tips / tips_total * 100) if tips_total > 0 else None,
    }

    # Динамика процента от чека: средний % по дням, последние 14 смен с данными
    pct_days: dict[date, list[float]] = {}
    for e in entries:
        if e.get("tip_percent") and _entry_date(e) <= today:
            pct_days.setdefault(_entry_date(e), []).append(float(e["tip_percent"]))
    daily = [
        {"date": d.isoformat(), "pct": round(sum(v) / len(v), 1)}
        for d, v in sorted(pct_days.items())
    ]
    summary["tip_pct_daily"] = daily[-14:]

    # Теплокарта: последние 28 дней, чай по дням
    heatmap = []
    for i in range(27, -1, -1):
        d = today - timedelta(days=i)
        heatmap.append({"date": d.isoformat(), "tips": round(all_shifts.get(d, 0))})
    summary["heatmap"] = heatmap

    return summary
